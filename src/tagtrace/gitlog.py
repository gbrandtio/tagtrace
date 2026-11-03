"""Reads commits between two tags, using a cached bare clone of each repository and the `git` command."""

import asyncio
import hashlib
import os
import shutil
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from tagtrace.errors import GitError
from tagtrace.pulldeps import hide_url_password

DEFAULT_CACHE = Path.home() / ".cache" / "tagtrace" / "repos"

_RECORD = "\x1e"
_FIELD = "\x1f"
_LOG_FORMAT = f"{_RECORD}%H{_FIELD}%an{_FIELD}%aI{_FIELD}%P{_FIELD}%B{_RECORD}"


class ChangedFile(BaseModel):
    """One file a commit touched. Binary files have no line counts."""

    path: str
    added: int | None
    deleted: int | None


class Commit(BaseModel):
    """One commit. `direction` is `removed` when the commit is in the old tag but not in the new one."""

    sha: str
    author: str
    date: str
    message: str
    is_merge: bool
    direction: Literal["added", "removed"]
    files: list[ChangedFile]
    jira_keys: list[str] = []


def _git_env() -> dict[str, str]:
    """Build git's environment: no credential prompts, and no URL types that can run commands such as `ext::`."""
    return {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ALLOW_PROTOCOL": "file:git:http:https:ssh"}


async def _git(*args: str, cwd: Path | None = None) -> tuple[int, str, str]:
    """Run git and return its exit code, stdout and stderr."""
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            "-c",
            "core.quotePath=false",
            *args,
            cwd=cwd,
            env=_git_env(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as error:
        raise GitError(f"could not run git: {error}. Check that git is installed.") from error
    try:
        stdout, stderr = await process.communicate()
    except asyncio.CancelledError:
        process.kill()
        raise
    code = process.returncode if process.returncode is not None else -1
    return code, stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace")


async def _checked(*args: str, cwd: Path | None = None) -> str:
    """Run git and return stdout, raising `GitError` if it fails."""
    code, stdout, stderr = await _git(*args, cwd=cwd)
    if code != 0:
        raise GitError(hide_url_password(f"git {args[0]} failed: {stderr.strip()}"))
    return stdout


class RepoCache:
    """Keeps one bare clone per repository URL and refreshes each at most once per run."""

    def __init__(self, root: Path = DEFAULT_CACHE) -> None:
        """Remember where clones live. Nothing is created until a repository is opened."""
        self._root = root
        self._ready: dict[str, asyncio.Task[Path]] = {}

    async def open(self, url: str) -> Path:
        """Return an up-to-date bare clone of `url`. Components that share a repository share one fetch."""
        if url not in self._ready:
            self._ready[url] = asyncio.create_task(self._prepare(url))
        return await self._ready[url]

    async def _prepare(self, url: str) -> Path:
        """Clone the repository if needed, then fetch all branches and tags."""
        path = self._root / hashlib.sha256(url.encode()).hexdigest()[:24]
        if not path.exists():
            # Clone beside the final folder, so an interrupted clone is never mistaken for a good one.
            partial = self._root / f"{path.name}.{uuid.uuid4().hex}.partial"
            try:
                self._root.mkdir(parents=True, exist_ok=True)
                await _checked("clone", "--bare", "--quiet", "--", url, str(partial))
                partial.rename(path)
            except OSError as error:
                # Another tagtrace process may have finished the same clone first, which is fine.
                if not path.exists():
                    raise GitError(f"could not save the clone in {self._root}: {error}") from error
            finally:
                shutil.rmtree(partial, ignore_errors=True)
        await _checked(
            "fetch", "--quiet", "--force", "--prune", "--tags", "origin", "+refs/heads/*:refs/heads/*", cwd=path
        )
        return path


async def _resolve_tag(repo: Path, tag: str) -> str:
    """Return the commit a tag points to. The `refs/tags/` prefix stops a tag being read as a git option."""
    code, stdout, _ = await _git(
        "rev-parse", "--verify", "--quiet", "--end-of-options", f"refs/tags/{tag}^{{commit}}", cwd=repo
    )
    if code != 0:
        raise GitError(f"tag '{tag}' was not found")
    return stdout.strip()


def _parse_files(numstat: str) -> list[ChangedFile]:
    """Parse the `--numstat` lines of one commit."""
    files: list[ChangedFile] = []
    for line in numstat.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            added, deleted, path = parts
            files.append(
                ChangedFile(
                    path=path,
                    added=None if added == "-" else int(added),
                    deleted=None if deleted == "-" else int(deleted),
                )
            )
    return files


def _parse_log(output: str, direction: Literal["added", "removed"]) -> list[Commit]:
    """Parse `git log` output. Each commit is a header record followed by its numstat record."""
    records = output.split(_RECORD)[1:]
    if len(records) % 2 != 0:
        raise GitError("could not read git log: a commit message contains a reserved control character")
    commits: list[Commit] = []
    for header, numstat in zip(records[::2], records[1::2], strict=True):
        fields = header.split(_FIELD, 4)
        if len(fields) != 5:
            raise GitError("could not read git log: a commit has an unexpected layout")
        sha, author, date, parents, message = fields
        commits.append(
            Commit(
                sha=sha,
                author=author,
                date=date,
                message=message.strip(),
                is_merge=len(parents.split()) > 1,
                direction=direction,
                files=_parse_files(numstat),
            )
        )
    return commits


async def _log(repo: Path, exclude: str, include: str, direction: Literal["added", "removed"]) -> list[Commit]:
    """Read the commits reachable from `include` but not from `exclude`. Both are resolved SHAs."""
    output = await _checked(
        "log", f"--format={_LOG_FORMAT}", "--numstat", "--no-renames", f"{exclude}..{include}", cwd=repo
    )
    return _parse_log(output, direction)


async def commits_between(cache: RepoCache, url: str, old_tag: str, new_tag: str) -> list[Commit]:
    """Return what the new tag adds, then what it lost. The second list catches downgrades and dropped hotfixes."""
    repo = await cache.open(url)
    old_sha = await _resolve_tag(repo, old_tag)
    new_sha = await _resolve_tag(repo, new_tag)
    return [*await _log(repo, old_sha, new_sha, "added"), *await _log(repo, new_sha, old_sha, "removed")]
