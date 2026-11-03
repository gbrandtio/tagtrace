"""Tests for loading the config file."""

from pathlib import Path

import pytest

from tagtrace.config import AIConfig, load_config
from tagtrace.errors import ConfigError


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "tagtrace.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_secret_comes_from_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_TOKEN", "s3cret")
    config = load_config(_write(tmp_path, "jira: {url: 'https://j.example.com/', auth: token, token: '${MY_TOKEN}'}"))
    jira = config.require_jira()
    assert jira.token is not None
    assert jira.token.get_secret_value() == "s3cret"
    assert jira.url == "https://j.example.com"
    assert "s3cret" not in repr(jira)


def test_unset_environment_variable_is_named(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_ONE", raising=False)
    with pytest.raises(ConfigError, match="MISSING_ONE"):
        load_config(_write(tmp_path, "jira: {url: 'https://j', auth: token, token: '${MISSING_ONE}'}"))


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("jira: {url: 'https://j', auth: token}", "needs jira.token"),
        ("jira: {url: 'https://j', auth: browser, browser: safari}", "browser"),
        ("jira: {url: 'j.example.com', auth: browser}", "must start with"),
        ("ai: {provider: openai_compatible}", "needs ai.base_url"),
        ("ai: {provider: openai_compatible, base_url: 'http://localhost'}", "needs ai.model"),
        ("ai: {provider: anthropic, concurrency: 0}", "concurrency"),
        ("ai: {provider: anthropic, max_input_chars: 0}", "max_input_chars"),
        ("ai: {provider: anthropic, redact_patterns: ['(']}", "invalid regex"),
        ("unknown_section: 1", "unknown_section"),
        ("a: [", "not valid YAML"),
    ],
)
def test_invalid_config_is_explained(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_config(_write(tmp_path, text))


def test_missing_file_and_sections_are_explained(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Cannot read"):
        load_config(tmp_path / "nope.yaml")
    empty = load_config(_write(tmp_path, ""))
    with pytest.raises(ConfigError, match="'jira'"):
        empty.require_jira()
    with pytest.raises(ConfigError, match="'ai'"):
        empty.require_ai()


@pytest.mark.parametrize(
    ("base_url", "remote"),
    [
        (None, True),
        ("https://api.openai.com/v1", True),
        ("http://localhost:8080/v1", False),
        ("http://127.0.0.1:1", False),
    ],
)
def test_is_remote(base_url: str | None, remote: bool) -> None:
    assert AIConfig(provider="anthropic", base_url=base_url).is_remote is remote


def test_claude_gets_a_default_model_and_projects_are_uppercased(tmp_path: Path) -> None:
    config = load_config(
        _write(
            tmp_path,
            "ai: {provider: anthropic}\njira: {url: 'https://j', auth: browser, projects: [act]}",
        )
    )
    assert config.require_ai().model == "claude-opus-5-5"
    assert config.require_jira().projects == ["ACT"]
