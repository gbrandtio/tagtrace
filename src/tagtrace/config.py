"""Settings loaded from `tagtrace.yaml`. Secrets are written there as `${ENV_VAR}` and held as `SecretStr`."""

import os
import re
from pathlib import Path
from typing import Literal, Self, cast
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator

from tagtrace.errors import ConfigError

_ENV_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

DEFAULT_CLAUDE_MODEL = "claude-opus-5-5"


class JiraConfig(BaseModel):
    """How to reach Jira and which commit keys count as tickets."""

    model_config = ConfigDict(extra="forbid")

    url: str
    auth: Literal["token", "browser"]
    browser: Literal["chrome", "msedge", "chromium"] = "chrome"
    email: str | None = None
    token: SecretStr | None = None
    projects: list[str] = []
    max_comments: int = Field(default=10, ge=0)

    @model_validator(mode="after")
    def _check_auth(self) -> Self:
        """Require the settings that the chosen sign-in method needs."""
        if self.auth == "token" and self.token is None:
            raise ValueError("jira.auth 'token' needs jira.token")
        if urlsplit(self.url).scheme not in ("http", "https"):
            raise ValueError("jira.url must start with http:// or https://")
        self.url = self.url.rstrip("/")
        self.projects = [project.upper() for project in self.projects]
        return self


class AIConfig(BaseModel):
    """Which model runs the analysis, and what may be sent to it."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["anthropic", "openai_compatible"]
    model: str = ""
    base_url: str | None = None
    api_key: SecretStr | None = None
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    refusal_fallback: bool = True
    max_input_chars: int = Field(default=400_000, gt=0)
    concurrency: int = Field(default=4, gt=0)
    redact: bool = True
    redact_patterns: list[str] = []

    @model_validator(mode="after")
    def _check_provider(self) -> Self:
        """An OpenAI-compatible endpoint has no sensible default address or model."""
        if self.provider == "openai_compatible":
            if self.base_url is None:
                raise ValueError("ai.provider 'openai_compatible' needs ai.base_url")
            if not self.model:
                raise ValueError("ai.provider 'openai_compatible' needs ai.model")
        elif not self.model:
            self.model = DEFAULT_CLAUDE_MODEL
        for pattern in self.redact_patterns:
            try:
                re.compile(pattern)
            except re.error as error:
                raise ValueError(f"ai.redact_patterns has an invalid regex {pattern!r}: {error}") from error
        return self

    @property
    def is_remote(self) -> bool:
        """Whether the data leaves this machine."""
        return self.base_url is None or urlsplit(self.base_url).hostname not in _LOCAL_HOSTS


class Config(BaseModel):
    """The whole config file. Each command checks that the section it needs is present."""

    model_config = ConfigDict(extra="forbid")

    jira: JiraConfig | None = None
    ai: AIConfig | None = None

    def require_jira(self) -> JiraConfig:
        """Return the Jira section, or explain that it is missing."""
        if self.jira is None:
            raise ConfigError("The config has no 'jira' section. See tagtrace.example.yaml.")
        return self.jira

    def require_ai(self) -> AIConfig:
        """Return the AI section, or explain that it is missing."""
        if self.ai is None:
            raise ConfigError("The config has no 'ai' section. See tagtrace.example.yaml.")
        return self.ai


def _expand_env(value: object) -> object:
    """Replace `${VAR}` in every string of the parsed YAML."""
    if isinstance(value, str):
        return _ENV_VAR.sub(_read_env, value)
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in cast(dict[object, object], value).items()}
    if isinstance(value, list):
        return [_expand_env(item) for item in cast(list[object], value)]
    return value


def _read_env(match: re.Match[str]) -> str:
    """Read one environment variable, refusing to continue with an empty secret."""
    name = match.group(1)
    if name not in os.environ:
        raise ConfigError(f"The config uses ${{{name}}}, but that environment variable is not set.")
    return os.environ[name]


def load_config(path: Path) -> Config:
    """Read and validate the config file."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ConfigError(f"Cannot read the config file {path}: {error}. See tagtrace.example.yaml.") from error
    except yaml.YAMLError as error:
        raise ConfigError(f"The config file {path} is not valid YAML: {error}") from error
    try:
        return Config.model_validate(_expand_env(raw or {}))
    except ValidationError as error:
        problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'config'}: {e['msg']}" for e in error.errors())
        raise ConfigError(f"The config file {path} is invalid: {problems}") from error
