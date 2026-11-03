"""Tests for the model providers, using the real SDKs against a local fake server."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, SecretStr

from tagtrace.config import AIConfig
from tagtrace.errors import ConfigError, LLMError
from tagtrace.models import AnthropicLLM, OpenAICompatibleLLM, create_llm


class Answer(BaseModel):
    """A small schema for the tests."""

    model_config = ConfigDict(extra="forbid")

    value: str


class FakeServer:
    """Answers each request with the next queued reply, and records what it received."""

    def __init__(self) -> None:
        self.replies: list[tuple[int, str, str]] = []
        self.requests: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []
        self.url = ""


@pytest.fixture
def server() -> Iterator[FakeServer]:
    state = FakeServer()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            state.requests.append(json.loads(self.rfile.read(length)))
            state.headers.append({key.lower(): value for key, value in self.headers.items()})
            status, content_type, body = state.replies.pop(0)
            encoded = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.url = f"http://127.0.0.1:{httpd.server_address[1]}"
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield state
    httpd.shutdown()
    httpd.server_close()


def _claude_stream(text: str, stop_reason: str = "end_turn") -> tuple[int, str, str]:
    """Build the server-sent events of one Claude answer."""
    events: list[dict[str, Any]] = [
        {
            "type": "message_start",
            "message": {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5-5",
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 0},
            },
        },
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": 1},
        },
        {"type": "message_stop"},
    ]
    body = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)
    return 200, "text/event-stream", body


def _openai_reply(content: str | None, finish_reason: str = "stop") -> tuple[int, str, str]:
    body = {
        "id": "c1",
        "object": "chat.completion",
        "created": 1,
        "model": "local",
        "choices": [{"index": 0, "finish_reason": finish_reason, "message": {"role": "assistant", "content": content}}],
    }
    return 200, "application/json", json.dumps(body)


def _claude(server: FakeServer, **changes: Any) -> AnthropicLLM:
    config = AIConfig(provider="anthropic", base_url=server.url, api_key=SecretStr("key"), **changes)
    return AnthropicLLM(config)


def _openai(server: FakeServer) -> OpenAICompatibleLLM:
    config = AIConfig(provider="openai_compatible", base_url=f"{server.url}/v1", model="local")
    return OpenAICompatibleLLM(config)


async def test_claude_request_shape_and_answer(server: FakeServer) -> None:
    server.replies.append(_claude_stream('{"value": "ok"}'))
    answer = await _claude(server).complete("sys", "user text", Answer)
    assert answer.value == "ok"
    sent = server.requests[0]
    assert sent["model"] == "claude-opus-5-5"
    assert sent["system"] == "sys"
    assert sent["stream"] is True
    assert sent["messages"] == [{"role": "user", "content": "user text"}]
    assert sent["output_config"]["effort"] == "high"
    assert sent["output_config"]["format"]["type"] == "json_schema"
    assert sent["output_config"]["format"]["schema"]["additionalProperties"] is False
    assert sent["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in server.headers[0]["anthropic-beta"]


async def test_claude_fallback_can_be_turned_off(server: FakeServer) -> None:
    server.replies.append(_claude_stream('{"value": "ok"}'))
    await _claude(server, refusal_fallback=False).complete("sys", "user", Answer)
    assert "fallbacks" not in server.requests[0]
    assert "server-side-fallback" not in server.headers[0].get("anthropic-beta", "")


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        (_claude_stream("", "refusal"), "declined"),
        (_claude_stream('{"val', "max_tokens"), "cut off"),
        (_claude_stream('{"wrong": 1}'), "expected format"),
        ((400, "application/json", '{"type":"error","error":{"type":"invalid_request_error","message":"bad"}}'), "400"),
        (
            (401, "application/json", '{"type":"error","error":{"type":"authentication_error","message":"no"}}'),
            "credentials",
        ),
    ],
)
async def test_claude_failures_become_llm_errors(server: FakeServer, reply: tuple[int, str, str], message: str) -> None:
    server.replies.append(reply)
    with pytest.raises(LLMError, match=message):
        await _claude(server).complete("sys", "user", Answer)


async def test_openai_request_shape_and_answer(server: FakeServer) -> None:
    server.replies.append(_openai_reply('```json\n{"value": "ok"}\n```'))
    answer = await _openai(server).complete("sys", "user text", Answer)
    assert answer.value == "ok"
    sent = server.requests[0]
    assert sent["model"] == "local"
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["response_format"]["json_schema"]["name"] == "Answer"
    assert sent["messages"][0]["role"] == "system"
    assert '"value"' in sent["messages"][0]["content"]
    assert sent["messages"][1] == {"role": "user", "content": "user text"}


async def test_openai_invalid_answer_is_retried_with_the_error(server: FakeServer) -> None:
    server.replies += [_openai_reply('{"wrong": 1}'), _openai_reply('{"value": "fixed"}')]
    assert (await _openai(server).complete("sys", "user", Answer)).value == "fixed"
    retry = server.requests[1]["messages"]
    assert retry[-2] == {"role": "assistant", "content": '{"wrong": 1}'}
    assert "invalid" in retry[-1]["content"]


async def test_openai_second_invalid_answer_fails(server: FakeServer) -> None:
    server.replies += [_openai_reply("nope"), _openai_reply("still nope")]
    with pytest.raises(LLMError, match="after a retry"):
        await _openai(server).complete("sys", "user", Answer)
    assert len(server.requests) == 2


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        (_openai_reply('{"val', "length"), "cut off"),
        (_openai_reply(None), "no text"),
        ((400, "application/json", '{"error": {"message": "bad schema"}}'), "400"),
        ((401, "application/json", '{"error": {"message": "no"}}'), "credentials"),
    ],
)
async def test_openai_failures_become_llm_errors(server: FakeServer, reply: tuple[int, str, str], message: str) -> None:
    server.replies.append(reply)
    with pytest.raises(LLMError, match=message):
        await _openai(server).complete("sys", "user", Answer)


async def test_unreachable_server_is_explained() -> None:
    config = AIConfig(provider="openai_compatible", base_url="http://127.0.0.1:9/v1", model="local")
    llm = OpenAICompatibleLLM(config)
    llm._client = llm._client.with_options(max_retries=0)  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(LLMError, match="Could not reach"):
        await llm.complete("sys", "user", Answer)


def test_create_llm_picks_the_configured_provider() -> None:
    claude = AIConfig(provider="anthropic", api_key=SecretStr("k"))
    other = AIConfig(provider="openai_compatible", base_url="http://localhost/v1", model="local")
    assert isinstance(create_llm(claude), AnthropicLLM)
    assert isinstance(create_llm(other), OpenAICompatibleLLM)


@pytest.mark.parametrize(
    ("module", "config", "extra"),
    [
        ("anthropic", {"provider": "anthropic"}, "'anthropic' extra"),
        (
            "openai",
            {"provider": "openai_compatible", "base_url": "http://localhost/v1", "model": "m"},
            "'openai' extra",
        ),
    ],
)
def test_missing_extra_is_explained(
    monkeypatch: pytest.MonkeyPatch, module: str, config: dict[str, str], extra: str
) -> None:
    monkeypatch.setitem(__import__("sys").modules, module, None)
    with pytest.raises(ConfigError, match=extra):
        create_llm(AIConfig.model_validate(config))
