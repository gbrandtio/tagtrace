"""Tests for reading commits, using real temporary git repositories."""

from collections.abc import Callable
from pathlib import Path

import pytest

from conftest import GitRepo
from tagtrace.errors import GitError
from tagtrace.gitlog import RepoCache, commits_between

MakeRepo = Callable[[str], GitRepo]


@pytest.fixture
def cache(tmp_path: Path) -> RepoCache:
    return RepoCache(tmp_path / "cache")


async def test_reads_commits_and_changed_files(make_repo: MakeRepo, cache: RepoCache) -> None:
    repo = make_repo("a")
    repo.commit("first")
    repo.tag("v1")
    sha = repo.commit("ACT-1 add login\n\nLonger body", file="src/login é.py", content="one\ntwo\n")
    repo.tag("v2")

    commits = await commits_between(cache, repo.url, "v1", "v2")

    assert [c.sha for c in commits] == [sha]
    commit = commits[0]
    assert commit.message == "ACT-1 add login\n\nLonger body"
    assert commit.author == "Test Author"
    assert commit.direction == "added"
    assert not commit.is_merge
    assert [(f.path, f.added, f.deleted) for f in commit.files] == [("src/login é.py", 2, 0)]


async def test_binary_files_have_no_line_counts(make_repo: MakeRepo, cache: RepoCache) -> None:
    repo = make_repo("a")
    repo.commit("first")
    repo.tag("v1")
    (repo.path / "image.bin").write_bytes(b"\x00\x01\x02")
    repo.git("add", "--all")
    repo.git("commit", "--quiet", "-m", "binary")
    repo.tag("v2")
    commits = await commits_between(cache, repo.url, "v1", "v2")
    assert [(f.path, f.added, f.deleted) for f in commits[0].files] == [("image.bin", None, None)]


async def test_downgrade_reports_removed_commits(make_repo: MakeRepo, cache: RepoCache) -> None:
    repo = make_repo("a")
    repo.commit("first")
    repo.tag("v1")
    sha = repo.commit("second")
    repo.tag("v2")
    commits = await commits_between(cache, repo.url, "v2", "v1")
    assert [(c.sha, c.direction) for c in commits] == [(sha, "removed")]


async def test_diverged_tags_report_both_directions(make_repo: MakeRepo, cache: RepoCache) -> None:
    repo = make_repo("a")
    repo.commit("base")
    repo.git("checkout", "--quiet", "-b", "hotfix")
    hotfix = repo.commit("hotfix", file="h.txt")
    repo.tag("v1.1")
    repo.git("checkout", "--quiet", "main")
    feature = repo.commit("feature", file="f.txt")
    repo.tag("v2")
    commits = await commits_between(cache, repo.url, "v1.1", "v2")
    assert {(c.sha, c.direction) for c in commits} == {(feature, "added"), (hotfix, "removed")}


async def test_merge_commit_is_marked(make_repo: MakeRepo, cache: RepoCache) -> None:
    repo = make_repo("a")
    repo.commit("base")
    repo.tag("v1")
    repo.git("checkout", "--quiet", "-b", "side")
    repo.commit("side", file="s.txt")
    repo.git("checkout", "--quiet", "main")
    repo.commit("main", file="m.txt")
    repo.git("merge", "--quiet", "--no-edit", "side")
    repo.tag("v2")
    commits = await commits_between(cache, repo.url, "v1", "v2")
    assert sum(c.is_merge for c in commits) == 1
    assert len(commits) == 3


async def test_same_commit_under_two_tags_has_no_commits(make_repo: MakeRepo, cache: RepoCache) -> None:
    repo = make_repo("a")
    repo.commit("only")
    repo.tag("v1")
    repo.tag("v1-again")
    assert await commits_between(cache, repo.url, "v1", "v1-again") == []


async def test_annotated_tag_works(make_repo: MakeRepo, cache: RepoCache) -> None:
    repo = make_repo("a")
    repo.commit("first")
    repo.git("tag", "-a", "v1", "-m", "release one")
    sha = repo.commit("second")
    repo.git("tag", "-a", "v2", "-m", "release two")
    assert [c.sha for c in await commits_between(cache, repo.url, "v1", "v2")] == [sha]


@pytest.mark.parametrize("tag", ["missing", "--upload-pack=touch /tmp/x", "HEAD"])
async def test_unknown_or_hostile_tag_is_reported(make_repo: MakeRepo, cache: RepoCache, tag: str) -> None:
    repo = make_repo("a")
    repo.commit("first")
    repo.tag("v1")
    with pytest.raises(GitError, match="was not found"):
        await commits_between(cache, repo.url, "v1", tag)


async def test_later_runs_see_new_commits_and_moved_tags(make_repo: MakeRepo, tmp_path: Path) -> None:
    repo = make_repo("a")
    repo.commit("first")
    repo.tag("v1")
    second = repo.commit("second")
    repo.tag("v2")
    root = tmp_path / "cache"
    assert [c.sha for c in await commits_between(RepoCache(root), repo.url, "v1", "v2")] == [second]

    third = repo.commit("third")
    repo.git("tag", "--force", "v2")
    assert [c.sha for c in await commits_between(RepoCache(root), repo.url, "v1", "v2")] == [third, second]


async def test_command_running_url_is_blocked(cache: RepoCache, tmp_path: Path) -> None:
    marker = tmp_path / "pwned"
    with pytest.raises(GitError):
        await commits_between(cache, f"ext::sh -c 'touch {marker}'", "v1", "v2")
    assert not marker.exists()


async def test_failed_clone_hides_password_and_leaves_nothing(tmp_path: Path) -> None:
    root = tmp_path / "cache"
    with pytest.raises(GitError) as caught:
        await commits_between(RepoCache(root), "file://user:hunter2@/does/not/exist", "v1", "v2")
    assert "hunter2" not in str(caught.value)
    assert list(root.iterdir()) == []


async def test_missing_git_is_explained(make_repo: MakeRepo, cache: RepoCache, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = make_repo("a")
    monkeypatch.setenv("PATH", "/nonexistent")
    with pytest.raises(GitError, match="Check that git is installed"):
        await commits_between(cache, repo.url, "v1", "v2")


async def test_reserved_character_in_message_is_an_error(make_repo: MakeRepo, cache: RepoCache) -> None:
    repo = make_repo("a")
    repo.commit("first")
    repo.tag("v1")
    repo.commit("broken \x1e message")
    repo.tag("v2")
    with pytest.raises(GitError, match="could not read git log"):
        await commits_between(cache, repo.url, "v1", "v2")
