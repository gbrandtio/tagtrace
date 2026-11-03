"""Tests for reading and comparing pulldeps files."""

from pathlib import Path

import pytest

from conftest import write_pulldeps
from tagtrace.errors import ConfigError
from tagtrace.pulldeps import Component, compare, load_pulldeps


def _components(*rows: tuple[str, str, str]) -> dict[str, Component]:
    return {name: Component(name=name, repo=repo, tag=tag) for name, repo, tag in rows}


def test_compare_sorts_every_kind_of_difference() -> None:
    old = _components(("same", "r1", "1"), ("bumped", "r2", "1"), ("gone", "r3", "1"), ("moved", "r4", "1"))
    new = _components(("same", "r1", "1"), ("bumped", "r2", "2"), ("fresh", "r5", "1"), ("moved", "r9", "2"))
    result = compare(old, new)
    assert [(c.name, c.old_tag, c.new_tag) for c in result.changed] == [("bumped", "1", "2")]
    assert [c.name for c in result.added] == ["fresh"]
    assert [c.name for c in result.removed] == ["gone"]
    assert [(c.name, c.old_repo, c.new_repo) for c in result.repo_changed] == [("moved", "r4", "r9")]


def test_load_ignores_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "a.json"
    path.write_text('{"components": [{"name": "a", "repo": "r", "tag": "1", "extra": true}], "x": 1}', encoding="utf-8")
    assert load_pulldeps(path)["a"].tag == "1"


def test_duplicate_name_is_an_error(tmp_path: Path) -> None:
    path = write_pulldeps(tmp_path / "a.json", [("a", "r", "1"), ("a", "r", "2")])
    with pytest.raises(ConfigError, match="more than once"):
        load_pulldeps(path)


@pytest.mark.parametrize("text", ["not json", "[]", '{"components": [{"name": "a"}]}'])
def test_invalid_file_is_an_error(tmp_path: Path, text: str) -> None:
    path = tmp_path / "a.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="not a valid pulldeps"):
        load_pulldeps(path)


def test_missing_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Cannot read"):
        load_pulldeps(tmp_path / "nope.json")


@pytest.mark.parametrize(
    ("url", "secret"),
    [
        ("https://bot:TOKEN@git.corp/x.git", "TOKEN"),
        ("https://user:p@ss/word@host/x", "ss/word"),
        ("https://user:pa/ss@host/x", "pa/ss"),
    ],
)
def test_saved_components_never_hold_url_passwords(url: str, secret: str) -> None:
    component = Component(name="a", repo=url, tag="1")
    assert secret not in component.model_dump_json()
    assert component.repo == url
