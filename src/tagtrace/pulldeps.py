"""Reads `pulldeps.json` files and works out which components differ between two versions."""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ValidationError, field_serializer

from tagtrace.errors import ConfigError

_URL_PASSWORD = re.compile(r"://\S*@")


def hide_url_password(text: str) -> str:
    """Remove credentials embedded in a URL from text that may be shown or saved."""
    return _URL_PASSWORD.sub("://***@", text)


class Component(BaseModel):
    """One component of an application at one version. Saved files never hold a password from the URL."""

    name: str
    repo: str
    tag: str

    @field_serializer("repo")
    def _hide_repo_password(self, repo: str) -> str:
        return hide_url_password(repo)


class _PulldepsFile(BaseModel):
    """The layout of a `pulldeps.json` file. Unknown fields are ignored."""

    components: list[Component]


class ChangedComponent(BaseModel):
    """A component that is in both versions at different tags."""

    name: str
    repo: str
    old_tag: str
    new_tag: str

    @field_serializer("repo")
    def _hide_repo_password(self, repo: str) -> str:
        return hide_url_password(repo)


class RepoChange(BaseModel):
    """A component whose repository changed, so its two tags cannot be compared."""

    name: str
    old_repo: str
    new_repo: str
    old_tag: str
    new_tag: str

    @field_serializer("old_repo", "new_repo")
    def _hide_repo_password(self, repo: str) -> str:
        return hide_url_password(repo)


@dataclass
class Comparison:
    """How two versions of an application differ."""

    changed: list[ChangedComponent] = field(default_factory=list[ChangedComponent])
    added: list[Component] = field(default_factory=list[Component])
    removed: list[Component] = field(default_factory=list[Component])
    repo_changed: list[RepoChange] = field(default_factory=list[RepoChange])


def load_pulldeps(path: Path) -> dict[str, Component]:
    """Read one file and return its components by name."""
    try:
        parsed = _PulldepsFile.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except OSError as error:
        raise ConfigError(f"Cannot read {path}: {error}") from error
    except (ValueError, ValidationError) as error:
        raise ConfigError(f'{path} is not a valid pulldeps file. Expected {{"components": [...]}}: {error}') from error
    components: dict[str, Component] = {}
    for component in parsed.components:
        if component.name in components:
            raise ConfigError(f"{path} lists the component '{component.name}' more than once.")
        components[component.name] = component
    return components


def compare(old: dict[str, Component], new: dict[str, Component]) -> Comparison:
    """Sort every component into changed, added, removed or repo-changed. Identical components are left out."""
    result = Comparison()
    for name in sorted(old.keys() | new.keys()):
        before, after = old.get(name), new.get(name)
        if before is None:
            assert after is not None  # noqa: S101 - the name came from one of the two dicts
            result.added.append(after)
        elif after is None:
            result.removed.append(before)
        elif before.repo != after.repo:
            result.repo_changed.append(
                RepoChange(name=name, old_repo=before.repo, new_repo=after.repo, old_tag=before.tag, new_tag=after.tag)
            )
        elif before.tag != after.tag:
            result.changed.append(ChangedComponent(name=name, repo=after.repo, old_tag=before.tag, new_tag=after.tag))
    return result
