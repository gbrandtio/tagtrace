"""End-to-end tests of the commands: real git repositories, mocked Jira and a scripted model."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest
import respx
from typer.testing import CliRunner

import tagtrace.cli as cli
from conftest import GitRepo, ScriptedLLM, write_pulldeps
from tagtrace.config import AIConfig
from tagtrace.gitlog import RepoCache
from tagtrace.models import LLM

BASE = "https://jira.example.com"
MakeRepo = Callable[[str], GitRepo]
runner = CliRunner()


@pytest.fixture
def llm(monkeypatch: pytest.MonkeyPatch) -> ScriptedLLM:
    scripted = ScriptedLLM()

    def fake_create(_: AIConfig) -> LLM:
        return scripted

    monkeypatch.setattr(cli, "create_llm", fake_create)
    return scripted


@pytest.fixture
def workspace(tmp_path: Path, make_repo: MakeRepo, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a config, two pulldeps files and one repository, and isolate the clone cache."""
    monkeypatch.setenv("TEST_JIRA_TOKEN", "tok")
    monkeypatch.setattr(cli, "RepoCache", lambda: RepoCache(tmp_path / "cache"))
    repo = make_repo("core")
    repo.commit("first")
    repo.tag("v1")
    repo.commit("ACT-1 fix login")
    repo.commit("tidy")
    repo.tag("v2")
    write_pulldeps(tmp_path / "old.json", [("core", repo.url, "v1")])
    write_pulldeps(tmp_path / "new.json", [("core", repo.url, "v2")])
    (tmp_path / "tagtrace.yaml").write_text(
        f"""
jira:
  url: {BASE}
  auth: token
  token: ${{TEST_JIRA_TOKEN}}
  projects: [ACT]
ai:
  provider: anthropic
""",
        encoding="utf-8",
    )
    return tmp_path


def _mock_jira() -> None:
    respx.get(f"{BASE}/rest/api/2/myself").respond(json={"name": "me"})
    respx.get(url__startswith=f"{BASE}/rest/api/2/issue/ACT-1").respond(
        json={"fields": {"summary": "Login fails", "status": {"name": "Done"}}}
    )


def _args(workspace: Path, command: str, run: Path) -> list[str]:
    return [
        command,
        str(workspace / "old.json"),
        str(workspace / "new.json"),
        "--config",
        str(workspace / "tagtrace.yaml"),
        "--out",
        str(run),
    ]


@respx.mock
def test_run_produces_the_whole_run_folder(workspace: Path, llm: ScriptedLLM) -> None:
    _mock_jira()
    run = workspace / "run"
    result = runner.invoke(cli.app, _args(workspace, "run", run))
    assert result.exit_code == 0, result.output
    assert "Overall risk: high" in result.output
    for name in ["manifest.json", "report.json", "report.md", "assessments/core.json"]:
        assert (run / name).exists()
    with_jira = json.loads((run / "components/core/commits_with_jira.json").read_text(encoding="utf-8"))
    without_jira = json.loads((run / "components/core/commits_without_jira.json").read_text(encoding="utf-8"))
    assert [c["jira_keys"] for c in with_jira] == [["ACT-1"]]
    assert len(without_jira) == 1
    assert "Login fails" in llm.component_prompts[0]


@respx.mock
def test_collect_then_analyze_separately(workspace: Path, llm: ScriptedLLM) -> None:
    _mock_jira()
    run = workspace / "run"
    collected = runner.invoke(cli.app, _args(workspace, "collect", run))
    assert collected.exit_code == 0, collected.output
    assert llm.component_prompts == []
    assert not (run / "report.md").exists()

    config = str(workspace / "tagtrace.yaml")
    analyzed = runner.invoke(cli.app, ["analyze", str(run), "--config", config])
    assert analyzed.exit_code == 0, analyzed.output
    assert (run / "report.md").exists()
    assert len(llm.component_prompts) == 1


@respx.mock
def test_partial_run_exits_with_code_2(workspace: Path, llm: ScriptedLLM) -> None:
    _mock_jira()
    repo_url = json.loads((workspace / "old.json").read_text(encoding="utf-8"))["components"][0]["repo"]
    write_pulldeps(workspace / "old.json", [("core", repo_url, "v1"), ("bad", repo_url, "nope1")])
    write_pulldeps(workspace / "new.json", [("core", repo_url, "v2"), ("bad", repo_url, "nope2")])
    run = workspace / "run"
    result = runner.invoke(cli.app, _args(workspace, "run", run))
    assert result.exit_code == 2, result.output
    assert "bad" in result.output
    assert (run / "report.md").exists()


@respx.mock
def test_login_failure_exits_with_code_1_and_no_trace(workspace: Path, llm: ScriptedLLM) -> None:
    respx.get(f"{BASE}/rest/api/2/myself").respond(401)
    result = runner.invoke(cli.app, _args(workspace, "run", workspace / "run"))
    assert result.exit_code == 1
    assert "did not accept the sign-in" in result.output
    assert "Traceback" not in result.output
    assert llm.component_prompts == []


def test_missing_config_exits_with_code_1(tmp_path: Path) -> None:
    result = runner.invoke(cli.app, ["analyze", str(tmp_path), "--config", str(tmp_path / "nope.yaml")])
    assert result.exit_code == 1
    assert "Cannot read the config file" in result.output


@respx.mock
def test_collect_and_analyze_report_partial_results_with_code_2(workspace: Path, llm: ScriptedLLM) -> None:
    _mock_jira()
    repo_url = json.loads((workspace / "old.json").read_text(encoding="utf-8"))["components"][0]["repo"]
    write_pulldeps(workspace / "old.json", [("core", repo_url, "v1"), ("bad", repo_url, "nope1")])
    write_pulldeps(workspace / "new.json", [("core", repo_url, "v2"), ("bad", repo_url, "nope2")])
    run = workspace / "run"
    assert runner.invoke(cli.app, _args(workspace, "collect", run)).exit_code == 2
    analyzed = runner.invoke(cli.app, ["analyze", str(run), "--config", str(workspace / "tagtrace.yaml")])
    assert analyzed.exit_code == 2


@respx.mock
def test_unknown_keys_without_a_project_list_are_warned_about(workspace: Path, llm: ScriptedLLM) -> None:
    respx.get(f"{BASE}/rest/api/2/myself").respond(json={"name": "me"})
    respx.get(url__startswith=f"{BASE}/rest/api/2/issue/").respond(404)
    config = workspace / "tagtrace.yaml"
    config.write_text(config.read_text(encoding="utf-8").replace("  projects: [ACT]\n", ""), encoding="utf-8")
    result = runner.invoke(cli.app, _args(workspace, "collect", workspace / "run"))
    assert "Set jira.projects" in result.output
