"""The collection stage: compares two versions, gathers commits and tickets, and writes the run folder."""

import asyncio
import hashlib
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, TypeAdapter

from tagtrace.config import JiraConfig
from tagtrace.errors import ConfigError, GitError
from tagtrace.gitlog import Commit, RepoCache, commits_between
from tagtrace.jira import JiraClient, Ticket, find_keys
from tagtrace.pulldeps import ChangedComponent, Component, RepoChange, compare, load_pulldeps

_GIT_CONCURRENCY = 4
_JIRA_CONCURRENCY = 8
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")

COMMITS_WITH_JIRA = "commits_with_jira.json"
COMMITS_WITHOUT_JIRA = "commits_without_jira.json"
JIRA_CONTEXT = "jira_context.json"
MANIFEST = "manifest.json"

CommitList = TypeAdapter(list[Commit])
TicketList = TypeAdapter(list[Ticket])


class ComponentResult(BaseModel):
    """What collection found for one changed component."""

    component: ChangedComponent
    folder: str
    status: Literal["ok", "failed"]
    error: str | None = None
    commits_with_jira: int = 0
    commits_without_jira: int = 0


class Manifest(BaseModel):
    """The index of a run folder, read by the analysis stage."""

    created_at: str
    old_file: str
    new_file: str
    components: list[ComponentResult]
    added: list[Component]
    removed: list[Component]
    repo_changed: list[RepoChange]
    tickets_not_found: list[str]
    tickets_failed: list[str]

    @property
    def is_complete(self) -> bool:
        """Whether everything that should have been collected was collected."""
        return (
            not self.tickets_failed
            and not self.repo_changed
            and all(result.status == "ok" for result in self.components)
        )


def folder_name(component: str) -> str:
    """Build a safe, unique folder name. Names come from the input file and may contain `/` or `..`."""
    safe = _UNSAFE_NAME.sub("_", component).strip(".")
    # Mixed case also gets a suffix, because `Foo` and `foo` are the same folder on macOS and Windows by default.
    if safe == component and safe and safe == safe.lower():
        return safe
    return f"{safe or 'component'}-{hashlib.sha256(component.encode()).hexdigest()[:8]}"


async def _read_commits(
    component: ChangedComponent, cache: RepoCache, limit: asyncio.Semaphore, projects: Iterable[str]
) -> list[Commit] | str:
    """Return a component's commits with their Jira keys, or the error text if git failed."""
    async with limit:
        try:
            commits = await commits_between(cache, component.repo, component.old_tag, component.new_tag)
        except GitError as error:
            return str(error)
    for commit in commits:
        commit.jira_keys = find_keys(commit.message, projects)
    return commits


async def _read_tickets(client: JiraClient, keys: Iterable[str]) -> dict[str, Ticket]:
    """Fetch each key once, however many components mention it."""
    limit = asyncio.Semaphore(_JIRA_CONCURRENCY)

    async def read(key: str) -> Ticket:
        async with limit:
            return await client.get_ticket(key)

    tickets = await asyncio.gather(*(read(key) for key in sorted(set(keys))))
    return {ticket.key: ticket for ticket in tickets}


def _write_component(folder: Path, commits: list[Commit], tickets: dict[str, Ticket]) -> tuple[int, int]:
    """Write one component's three files and return how many commits have and lack a Jira key."""
    folder.mkdir(parents=True, exist_ok=True)
    with_jira = [commit for commit in commits if commit.jira_keys]
    without_jira = [commit for commit in commits if not commit.jira_keys]
    keys = sorted({key for commit in with_jira for key in commit.jira_keys})
    (folder / COMMITS_WITH_JIRA).write_bytes(CommitList.dump_json(with_jira, indent=2))
    (folder / COMMITS_WITHOUT_JIRA).write_bytes(CommitList.dump_json(without_jira, indent=2))
    (folder / JIRA_CONTEXT).write_bytes(TicketList.dump_json([tickets[key] for key in keys], indent=2))
    return len(with_jira), len(without_jira)


def _write_manifest(out: Path, manifest: Manifest) -> None:
    """Write the manifest last, so a folder with a manifest is always a finished collection."""
    out.mkdir(parents=True, exist_ok=True)
    (out / MANIFEST).write_text(manifest.model_dump_json(indent=2), encoding="utf-8")


async def collect(old_file: Path, new_file: Path, jira: JiraConfig, out: Path, cache: RepoCache) -> Manifest:
    """Run the whole collection stage and return the manifest it wrote to `out`."""
    if (out / MANIFEST).exists():
        raise ConfigError(f"{out} already holds a run. Choose another --out folder.")
    comparison = compare(load_pulldeps(old_file), load_pulldeps(new_file))

    async with JiraClient(jira) as client:
        if comparison.changed:
            await client.check_login()
        limit = asyncio.Semaphore(_GIT_CONCURRENCY)
        outcomes = await asyncio.gather(
            *(_read_commits(component, cache, limit, jira.projects) for component in comparison.changed)
        )
        keys = [
            key
            for outcome in outcomes
            if not isinstance(outcome, str)
            for commit in outcome
            for key in commit.jira_keys
        ]
        tickets = await _read_tickets(client, keys)

    results: list[ComponentResult] = []
    for component, outcome in zip(comparison.changed, outcomes, strict=True):
        name = folder_name(component.name)
        if isinstance(outcome, str):
            results.append(ComponentResult(component=component, folder=name, status="failed", error=outcome))
            continue
        with_jira, without_jira = _write_component(out / "components" / name, outcome, tickets)
        results.append(
            ComponentResult(
                component=component,
                folder=name,
                status="ok",
                commits_with_jira=with_jira,
                commits_without_jira=without_jira,
            )
        )

    manifest = Manifest(
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        old_file=str(old_file),
        new_file=str(new_file),
        components=results,
        added=comparison.added,
        removed=comparison.removed,
        repo_changed=comparison.repo_changed,
        tickets_not_found=sorted(key for key, ticket in tickets.items() if ticket.fetch_status == "not_found"),
        tickets_failed=sorted(key for key, ticket in tickets.items() if ticket.fetch_status == "error"),
    )
    _write_manifest(out, manifest)
    return manifest
