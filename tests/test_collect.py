"""Tests for the collection stage: real git repositories and a mocked Jira."""

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
import respx
from pydantic import SecretStr

from conftest import GitRepo, write_pulldeps
from tagtrace.collect import collect, folder_name
from tagtrace.config import JiraConfig
from tagtrace.errors import ConfigError, JiraAuthError
from tagtrace.gitlog import RepoCache

BASE = "https://jira.example.com"
MakeRepo = Callable[[str], GitRepo]


@pytest.fixture
def jira() -> JiraConfig:
    return JiraConfig(url=BASE, auth="token", token=SecretStr("tok"), projects=["ACT"])


def _read(path: Path) -> list[dict[str, object]]:
    return json.loads(path.read_text(encoding="utf-8"))


def _mock_jira() -> None:
    respx.get(f"{BASE}/rest/api/2/myself").respond(json={"name": "me"})
    respx.get(url__startswith=f"{BASE}/rest/api/2/issue/ACT-1").respond(json={"fields": {"summary": "One"}})
    respx.get(url__startswith=f"{BASE}/rest/api/2/issue/ACT-404").respond(404)


@respx.mock
async def test_splits_commits_and_saves_ticket_context(make_repo: MakeRepo, tmp_path: Path, jira: JiraConfig) -> None:
    _mock_jira()
    shared = make_repo("shared")
    shared.commit("first")
    shared.tag("v1")
    linked = shared.commit("ACT-1 fix login")
    unlinked = shared.commit("tidy up UTF-8 handling")
    missing = shared.commit("ACT-404 unknown ticket")
    shared.tag("v2")
    other = make_repo("other")
    other.commit("first")
    other.tag("v1")
    other.commit("also ACT-1")
    other.tag("v2")
    old = write_pulldeps(
        tmp_path / "old.json",
        [
            ("shared", shared.url, "v1"),
            ("other", other.url, "v1"),
            ("same", other.url, "v1"),
            ("gone", other.url, "v1"),
        ],
    )
    new = write_pulldeps(
        tmp_path / "new.json",
        [
            ("shared", shared.url, "v2"),
            ("other", other.url, "v2"),
            ("same", other.url, "v1"),
            ("fresh", other.url, "v2"),
        ],
    )
    out = tmp_path / "run"

    manifest = await collect(old, new, jira, out, RepoCache(tmp_path / "cache"))

    assert manifest.is_complete
    assert [r.component.name for r in manifest.components] == ["other", "shared"]
    assert [c.name for c in manifest.added] == ["fresh"]
    assert [c.name for c in manifest.removed] == ["gone"]
    assert manifest.tickets_not_found == ["ACT-404"]

    folder = out / "components" / "shared"
    assert {c["sha"] for c in _read(folder / "commits_with_jira.json")} == {linked, missing}
    assert [c["sha"] for c in _read(folder / "commits_without_jira.json")] == [unlinked]
    context = {t["key"]: t for t in _read(folder / "jira_context.json")}
    assert context["ACT-1"]["summary"] == "One"
    assert context["ACT-404"]["fetch_status"] == "not_found"

    # A key mentioned by two components is fetched once.
    issue_calls = [c for c in respx.calls if "/issue/ACT-1?" in str(c.request.url)]
    assert len(issue_calls) == 1
    assert (out / "manifest.json").exists()


@respx.mock
async def test_failed_component_does_not_stop_the_others(make_repo: MakeRepo, tmp_path: Path, jira: JiraConfig) -> None:
    _mock_jira()
    good = make_repo("good")
    good.commit("first")
    good.tag("v1")
    good.commit("second")
    good.tag("v2")
    old = write_pulldeps(tmp_path / "o.json", [("good", good.url, "v1"), ("bad", good.url, "nope1")])
    new = write_pulldeps(tmp_path / "n.json", [("good", good.url, "v2"), ("bad", good.url, "nope2")])

    manifest = await collect(old, new, jira, tmp_path / "run", RepoCache(tmp_path / "cache"))

    assert not manifest.is_complete
    results = {r.component.name: r for r in manifest.components}
    assert results["good"].status == "ok"
    assert results["good"].commits_without_jira == 1
    assert results["bad"].status == "failed"
    assert "was not found" in (results["bad"].error or "")


@respx.mock
async def test_ticket_error_marks_the_run_incomplete(make_repo: MakeRepo, tmp_path: Path, jira: JiraConfig) -> None:
    respx.get(f"{BASE}/rest/api/2/myself").respond(json={"name": "me"})
    respx.get(url__startswith=f"{BASE}/rest/api/2/issue/ACT-1").respond(500)
    repo = make_repo("a")
    repo.commit("first")
    repo.tag("v1")
    repo.commit("ACT-1 change")
    repo.tag("v2")
    old = write_pulldeps(tmp_path / "o.json", [("a", repo.url, "v1")])
    new = write_pulldeps(tmp_path / "n.json", [("a", repo.url, "v2")])
    manifest = await collect(old, new, jira, tmp_path / "run", RepoCache(tmp_path / "cache"))
    assert manifest.tickets_failed == ["ACT-1"]
    assert not manifest.is_complete


@respx.mock
async def test_login_failure_stops_before_any_git_work(tmp_path: Path, jira: JiraConfig) -> None:
    respx.get(f"{BASE}/rest/api/2/myself").mock(return_value=httpx.Response(401))
    old = write_pulldeps(tmp_path / "o.json", [("a", "/does/not/exist", "v1")])
    new = write_pulldeps(tmp_path / "n.json", [("a", "/does/not/exist", "v2")])
    with pytest.raises(JiraAuthError):
        await collect(old, new, jira, tmp_path / "run", RepoCache(tmp_path / "cache"))
    assert not (tmp_path / "cache").exists()
    assert not (tmp_path / "run" / "manifest.json").exists()


async def test_existing_run_is_not_overwritten(tmp_path: Path, jira: JiraConfig) -> None:
    out = tmp_path / "run"
    out.mkdir()
    (out / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ConfigError, match="already holds a run"):
        await collect(tmp_path / "o.json", tmp_path / "n.json", jira, out, RepoCache(tmp_path / "cache"))


@pytest.mark.parametrize("name", ["../../etc/passwd", "..", ".", "a/b", "name with space", "", "a\\b", ".hidden"])
def test_folder_name_is_always_safe_and_inside_the_run(name: str) -> None:
    folder = folder_name(name)
    assert folder not in ("", ".", "..")
    assert "/" not in folder
    assert "\\" not in folder
    assert not folder.startswith(".")


def test_folder_name_keeps_plain_names_and_separates_lookalikes() -> None:
    assert folder_name("payments-core") == "payments-core"
    assert folder_name("a/b") != folder_name("a_b")


def test_folder_names_differ_on_case_insensitive_disks() -> None:
    assert folder_name("Foo").lower() != folder_name("foo").lower()


@respx.mock
async def test_manifest_hides_url_passwords(make_repo: MakeRepo, tmp_path: Path, jira: JiraConfig) -> None:
    _mock_jira()
    secret_url = "https://bot:TOKEN@127.0.0.1:9/x.git"
    old = write_pulldeps(tmp_path / "o.json", [("a", secret_url, "v1"), ("gone", secret_url, "v1")])
    new = write_pulldeps(tmp_path / "n.json", [("a", secret_url, "v2"), ("fresh", secret_url, "v1")])
    await collect(old, new, jira, tmp_path / "run", RepoCache(tmp_path / "cache"))
    assert "TOKEN" not in (tmp_path / "run" / "manifest.json").read_text(encoding="utf-8")
