"""The model providers. Each sends a prompt and returns output validated against a Pydantic schema."""

import json
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from tagtrace.config import AIConfig
from tagtrace.errors import ConfigError, LLMError

_FALLBACK_BETA = "server-side-fallback-2026-07-01"
_MAX_OUTPUT_TOKENS = 64_000


class LLM(Protocol):
    """A model that answers with a validated object.

    `fallback_models` names any other model that answered because the configured one declined.
    """

    fallback_models: set[str]

    async def complete[T: BaseModel](self, system: str, user: str, schema: type[T]) -> T:
        """Send the prompt and return the parsed answer, or raise `LLMError`."""
        ...


def _parse[T: BaseModel](text: str, schema: type[T]) -> T:
    """Validate model output, ignoring anything around the outermost JSON object (such as Markdown fences)."""
    start, end = text.find("{"), text.rfind("}")
    return schema.model_validate_json(text[start : end + 1] if start != -1 and end > start else text)


class AnthropicLLM:
    """Calls Claude through the official SDK, streaming because release data and answers can be long."""

    def __init__(self, config: AIConfig) -> None:
        """Create the client. Credentials come from `ai.api_key` or the SDK's usual sources."""
        try:
            import anthropic
        except ImportError as error:
            raise ConfigError(
                "ai.provider 'anthropic' needs the 'anthropic' extra: uv sync --extra anthropic"
            ) from error
        self._sdk = anthropic
        self._config = config
        self.fallback_models: set[str] = set()
        self._client = anthropic.AsyncAnthropic(
            api_key=config.api_key.get_secret_value() if config.api_key else None, base_url=config.base_url
        )

    async def complete[T: BaseModel](self, system: str, user: str, schema: type[T]) -> T:
        """Request structured output, and treat a refused or cut-off answer as a failure."""
        sdk, config = self._sdk, self._config
        options: dict[str, Any] = {"betas": [_FALLBACK_BETA], "fallbacks": "default"} if config.refusal_fallback else {}
        try:
            async with self._client.beta.messages.stream(
                model=config.model,
                max_tokens=_MAX_OUTPUT_TOKENS,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={
                    "effort": config.effort,
                    "format": {"type": "json_schema", "schema": schema.model_json_schema()},
                },
                **options,
            ) as stream:
                message = await stream.get_final_message()
        except sdk.AuthenticationError as error:
            raise LLMError("Claude rejected the credentials. Set ai.api_key or ANTHROPIC_API_KEY.") from error
        except sdk.RateLimitError as error:
            raise LLMError("Claude's rate limit was still exceeded after retrying. Lower ai.concurrency.") from error
        except sdk.APIStatusError as error:
            raise LLMError(f"Claude answered {error.status_code}: {error.message}") from error
        except sdk.APIConnectionError as error:
            raise LLMError(f"Could not reach Claude: {error}") from error
        except sdk.AnthropicError as error:
            raise LLMError(f"The Claude request failed: {error}") from error
        except TypeError as error:
            # The SDK raises this at request time when it finds no credentials at all.
            raise LLMError(f"The Claude request could not be sent: {error}. Set ai.api_key.") from error

        if message.stop_reason == "refusal":
            raise LLMError("Claude declined this request, so this part has no assessment.")
        if any(entry.type == "fallback_message" for entry in message.usage.iterations or []):
            self.fallback_models.add(message.model)
        if message.stop_reason == "max_tokens":
            raise LLMError("Claude's answer was cut off at the output limit.")
        text = "".join(block.text for block in message.content if block.type == "text")
        try:
            return _parse(text, schema)
        except ValidationError as error:
            raise LLMError(f"Claude's answer did not match the expected format: {error}") from error


class OpenAICompatibleLLM:
    """Calls OpenAI or a local server such as llama.cpp, Ollama or LM Studio through the official OpenAI SDK.

    Local servers differ in how strictly they enforce a JSON schema. So the schema is also in the prompt, the answer
    is always validated, and one invalid answer is retried with the validation error.
    """

    def __init__(self, config: AIConfig) -> None:
        """Create the client. Local servers usually ignore the key, but the SDK requires one."""
        try:
            import openai
        except ImportError as error:
            raise ConfigError(
                "ai.provider 'openai_compatible' needs the 'openai' extra: uv sync --extra openai"
            ) from error
        self._sdk = openai
        self._config = config
        self.fallback_models: set[str] = set()
        self._client = openai.AsyncOpenAI(
            api_key=config.api_key.get_secret_value() if config.api_key else "not-needed", base_url=config.base_url
        )

    async def _ask(self, messages: list[dict[str, str]], schema: type[BaseModel]) -> str:
        """Send one request and return the answer text."""
        sdk = self._sdk
        request: Any = self._client.chat.completions.create
        try:
            response = await request(
                model=self._config.model,
                messages=messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema(), "strict": True},
                },
            )
        except sdk.AuthenticationError as error:
            raise LLMError("The model server rejected the credentials. Check ai.api_key.") from error
        except sdk.APIStatusError as error:
            raise LLMError(f"The model server answered {error.status_code}: {error.message}") from error
        except sdk.APIConnectionError as error:
            raise LLMError(f"Could not reach the model server at {self._config.base_url}: {error}") from error
        except sdk.OpenAIError as error:
            raise LLMError(f"The model request failed: {error}") from error
        if not response.choices:
            raise LLMError("The model server returned no answer.")
        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise LLMError("The model's answer was cut off at its output limit. Lower ai.max_input_chars.")
        if choice.message.content is None:
            raise LLMError("The model returned no text. It may have declined the request.")
        return choice.message.content

    async def complete[T: BaseModel](self, system: str, user: str, schema: type[T]) -> T:
        """Ask for an answer, retrying once if it does not match the schema."""
        schema_text = json.dumps(schema.model_json_schema())
        messages = [
            {"role": "system", "content": f"{system}\n\nAnswer with only JSON matching this schema:\n{schema_text}"},
            {"role": "user", "content": user},
        ]
        answer = await self._ask(messages, schema)
        try:
            return _parse(answer, schema)
        except ValidationError as error:
            messages.append({"role": "assistant", "content": answer})
            messages.append(
                {"role": "user", "content": f"That answer was invalid. Fix it and answer with only JSON: {error}"}
            )
        answer = await self._ask(messages, schema)
        try:
            return _parse(answer, schema)
        except ValidationError as error:
            raise LLMError(f"The model's answer did not match the expected format after a retry: {error}") from error


def create_llm(config: AIConfig) -> LLM:
    """Create the configured provider. There is no automatic switch to another provider on failure."""
    if config.provider == "anthropic":
        return AnthropicLLM(config)
    return OpenAICompatibleLLM(config)
