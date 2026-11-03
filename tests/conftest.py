"""Shared test helpers: real temporary git repositories and a scripted model."""

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import BaseModel

from tagtrace.analyze import AffectedArea, ComponentAssessment, Gap, ReleaseAssessment, Risk, Testing
from tagtrace.errors import LLMError


class GitRepo:
    """A real git repository the tests can commit to and tag."""

    def __init__(self, path: Path) -> None:
        """Create an empty repository at `path`."""
        self.path = path
        path.mkdir(parents=True)
        self.git("init", "--quiet", "--initial-branch=main")

    def git(self, *args: str) -> str:
        """Run git in the repository with a fixed identity."""
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "Test Author",
            "GIT_AUTHOR_EMAIL": "author@example.com",
            "GIT_COMMITTER_NAME": "Test Author",
            "GIT_COMMITTER_EMAIL": "author@example.com",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }
        result = subprocess.run(["git", *args], cwd=self.path, env=env, check=True, capture_output=True, text=True)
        return result.stdout.strip()

    def commit(self, message: str, file: str = "file.txt", content: str | None = None) -> str:
        """Commit one file change and return the commit SHA."""
        target = self.path / file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content if content is not None else f"{message}\n", encoding="utf-8")
        self.git("add", "--all")
        self.git("commit", "--quiet", "-m", message)
        return self.git("rev-parse", "HEAD")

    def tag(self, name: str) -> None:
        """Tag the current commit."""
        self.git("tag", name)

    @property
    def url(self) -> str:
        """The address tagtrace clones from."""
        return str(self.path)


@pytest.fixture
def make_repo(tmp_path: Path) -> Callable[[str], GitRepo]:
    """Return a function that creates a named repository."""

    def create(name: str) -> GitRepo:
        return GitRepo(tmp_path / "origin" / name)

    return create


def write_pulldeps(path: Path, components: list[tuple[str, str, str]]) -> Path:
    """Write a pulldeps file from (name, repo, tag) rows."""
    rows = [{"name": name, "repo": repo, "tag": tag} for name, repo, tag in components]
    path.write_text(json.dumps({"components": rows}), encoding="utf-8")
    return path


def component_assessment(commits: list[str] | None = None, tickets: list[str] | None = None) -> ComponentAssessment:
    """Build a valid component assessment citing the given evidence."""
    return ComponentAssessment(
        summary="Component summary",
        affected_areas=[
            AffectedArea(
                area="Login", why="Login changed", confidence="high", commits=commits or [], tickets=tickets or []
            )
        ],
        risk=Risk(level="medium", rationale="Touches login", factors=["auth"]),
        testing=Testing(effort="M", rationale="Some flows", recommended_tests=["Login"], regression_areas=["Session"]),
        gaps=[Gap(kind="other", detail="A gap", commits=[], tickets=[])],
    )


def release_assessment(commits: list[str] | None = None, tickets: list[str] | None = None) -> ReleaseAssessment:
    """Build a valid release assessment citing the given evidence."""
    return ReleaseAssessment(
        summary="Release summary",
        affected_areas=[
            AffectedArea(
                area="Login", why="Login changed", confidence="high", commits=commits or [], tickets=tickets or []
            )
        ],
        risk=Risk(level="high", rationale="Several parts", factors=[]),
        testing=Testing(effort="L", rationale="Broad", recommended_tests=[], regression_areas=[]),
        cross_component_risks=["Shared session handling"],
        gaps=[],
    )


class ScriptedLLM:
    """A model whose answers are set by the test. It records everything it is sent."""

    def __init__(
        self,
        component: Callable[[str], ComponentAssessment] | None = None,
        release: Callable[[str], ReleaseAssessment] | None = None,
    ) -> None:
        """Answer with valid objects unless a test passes its own functions, which may raise `LLMError`."""
        self.component = component or (lambda _: component_assessment())
        self.release = release or (lambda _: release_assessment())
        self.component_prompts: list[str] = []
        self.release_prompts: list[str] = []
        self.systems: list[str] = []
        self.fallback_models: set[str] = set()

    async def complete[T: BaseModel](self, system: str, user: str, schema: type[T]) -> T:
        """Record the prompt and return the scripted answer."""
        self.systems.append(system)
        if schema is ComponentAssessment:
            self.component_prompts.append(user)
            answer: BaseModel = self.component(user)
        elif schema is ReleaseAssessment:
            self.release_prompts.append(user)
            answer = self.release(user)
        else:
            raise LLMError(f"Unexpected schema {schema}")
        return schema.model_validate(answer.model_dump())
