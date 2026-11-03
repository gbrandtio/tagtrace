"""Tests for finding Jira keys and reading tickets, with no network."""

import stat
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr

from tagtrace import browser_login
from tagtrace.config import JiraConfig
from tagtrace.errors import ConfigError, JiraAuthError, TagtraceError
from tagtrace.jira import JiraClient, find_keys

BASE = "https://jira.example.com"


def _token_config(**changes: Any) -> JiraConfig:
    return JiraConfig(url=BASE, auth="token", token=SecretStr("tok"), **changes)


@pytest.mark.parametrize(
    ("message", "projects", "expected"),
    [
        ("ACTXXX-1234 fix login", [], ["ACTXXX-1234"]),
        ("[AB-2] and AB-1, again AB-2", [], ["AB-1", "AB-2"]),
        ("feature/AB-7: body mentions A1B-9", [], ["A1B-9", "AB-7"]),
        ("no ticket here", [], []),
        ("lowercase ab-1 is not a key", [], []),
        ("encoding UTF-8 with ACT-5", [], ["ACT-5", "UTF-8"]),
        ("encoding UTF-8 with ACT-5", ["ACT"], ["ACT-5"]),
        ("encoding UTF-8 only", ["ACT"], []),
    ],
)
def test_find_keys(message: str, projects: list[str], expected: list[str]) -> None:
    assert find_keys(message, projects) == expected


@respx.mock
async def test_data_center_token_uses_bearer() -> None:
    route = respx.get(f"{BASE}/rest/api/2/myself").respond(json={"name": "me"})
    async with JiraClient(_token_config()) as client:
        await client.check_login()
    assert route.calls[0].request.headers["Authorization"] == "Bearer tok"


@respx.mock
async def test_cloud_token_uses_basic_with_email() -> None:
    route = respx.get(f"{BASE}/rest/api/2/myself").respond(json={"name": "me"})
    async with JiraClient(_token_config(email="me@example.com")) as client:
        await client.check_login()
    assert route.calls[0].request.headers["Authorization"].startswith("Basic ")


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401),
        httpx.Response(302, headers={"Location": "https://sso.example.com/login"}),
        httpx.Response(200, text="<html>login</html>", headers={"Content-Type": "text/html"}),
        httpx.Response(403),
    ],
)
@respx.mock
async def test_check_login_recognises_a_lost_session(response: httpx.Response) -> None:
    respx.get(f"{BASE}/rest/api/2/myself").mock(return_value=response)
    async with JiraClient(_token_config()) as client:
        with pytest.raises(JiraAuthError, match="did not accept"):
            await client.check_login()


@respx.mock
async def test_redirect_is_never_followed() -> None:
    respx.get(f"{BASE}/rest/api/2/myself").respond(302, headers={"Location": "https://evil.example.org/"})
    evil = respx.get("https://evil.example.org/").respond(200)
    async with JiraClient(_token_config()) as client:
        with pytest.raises(JiraAuthError):
            await client.check_login()
    assert not evil.called


@respx.mock
async def test_unreachable_jira_is_explained() -> None:
    respx.get(f"{BASE}/rest/api/2/myself").mock(side_effect=httpx.ConnectError("no route"))
    async with JiraClient(_token_config()) as client:
        with pytest.raises(TagtraceError, match="Could not reach"):
            await client.check_login()


@respx.mock
async def test_ticket_fields_are_read_and_comments_limited() -> None:
    respx.get(url__startswith=f"{BASE}/rest/api/2/issue/ACT-1").respond(
        json={
            "fields": {
                "summary": "Fix login",
                "description": "Long text",
                "issuetype": {"name": "Bug"},
                "status": {"name": "Done"},
                "priority": {"name": "High"},
                "resolution": None,
                "components": [{"name": "Auth"}],
                "labels": ["urgent"],
                "fixVersions": [{"name": "2.0"}],
                "issuelinks": [
                    {
                        "type": {"outward": "blocks", "inward": "is blocked by"},
                        "outwardIssue": {"key": "ACT-2", "fields": {"summary": "Other"}},
                    },
                    {
                        "type": {"outward": "blocks", "inward": "is blocked by"},
                        "inwardIssue": {"key": "ACT-3", "fields": {"summary": "Third"}},
                    },
                ],
                "comment": {
                    "comments": [
                        {"author": {"displayName": "A"}, "created": "2026-01-01", "body": "old"},
                        {"author": {"displayName": "B"}, "created": "2026-01-02", "body": "middle"},
                        {"author": {"displayName": "C"}, "created": "2026-01-03", "body": "new"},
                    ]
                },
            }
        }
    )
    async with JiraClient(_token_config(max_comments=2)) as client:
        ticket = await client.get_ticket("ACT-1")
    assert ticket.fetch_status == "ok"
    assert ticket.url == f"{BASE}/browse/ACT-1"
    assert (ticket.summary, ticket.issue_type, ticket.status, ticket.priority) == ("Fix login", "Bug", "Done", "High")
    assert ticket.resolution == ""
    assert ticket.components == ["Auth"]
    assert ticket.labels == ["urgent"]
    assert ticket.fix_versions == ["2.0"]
    assert ticket.links == ["blocks ACT-2: Other", "is blocked by ACT-3: Third"]
    assert [c.body for c in ticket.comments] == ["middle", "new"]
    assert ticket.omitted_comments == 1


@respx.mock
async def test_ticket_failures_are_recorded_not_raised() -> None:
    respx.get(url__startswith=f"{BASE}/rest/api/2/issue/ACT-404").respond(404)
    respx.get(url__startswith=f"{BASE}/rest/api/2/issue/ACT-500").respond(500)
    respx.get(url__startswith=f"{BASE}/rest/api/2/issue/ACT-BAD").respond(200, json=["unexpected"])
    respx.get(url__startswith=f"{BASE}/rest/api/2/issue/ACT-NET").mock(side_effect=httpx.ReadTimeout("slow"))
    async with JiraClient(_token_config()) as client:
        assert (await client.get_ticket("ACT-404")).fetch_status == "not_found"
        failed = await client.get_ticket("ACT-500")
        assert (failed.fetch_status, failed.fetch_error) == ("error", "Jira answered 500")
        assert (await client.get_ticket("ACT-BAD")).fetch_status == "error"
        assert (await client.get_ticket("ACT-NET")).fetch_status == "error"


@respx.mock
async def test_lost_session_during_a_run_stops_it() -> None:
    respx.get(url__startswith=f"{BASE}/rest/api/2/issue/ACT-1").respond(401)
    async with JiraClient(_token_config()) as client:
        with pytest.raises(JiraAuthError):
            await client.get_ticket("ACT-1")


@respx.mock
async def test_rate_limit_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr("tagtrace.jira.asyncio.sleep", fake_sleep)
    respx.get(url__startswith=f"{BASE}/rest/api/2/issue/ACT-1").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(429),
            httpx.Response(200, json={"fields": {"summary": "ok"}}),
        ]
    )
    async with JiraClient(_token_config()) as client:
        assert (await client.get_ticket("ACT-1")).summary == "ok"
    assert waits == [7.0, 2.0]


COOKIES: list[dict[str, Any]] = [
    {"name": "JSESSIONID", "value": "abc", "domain": "jira.example.com", "path": "/", "expires": -1, "secure": True},
    {"name": "sso", "value": "def", "domain": ".example.com", "path": "/", "expires": 4102444800, "secure": True},
    {"name": "other", "value": "x", "domain": "wiki.example.com", "path": "/", "expires": -1, "secure": True},
    {"name": "lookalike", "value": "y", "domain": "notjira.example.com", "path": "/", "expires": -1, "secure": True},
]


def _browser_config() -> JiraConfig:
    return JiraConfig(url=BASE, auth="browser")


@pytest.fixture
def state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep saved sessions out of the real home folder."""
    monkeypatch.setattr(browser_login, "STATE_ROOT", tmp_path / "state")
    return tmp_path / "state"


def _save(cookies: list[dict[str, Any]]) -> None:
    folder = browser_login._state_folder(BASE)  # pyright: ignore[reportPrivateUsage]
    folder.mkdir(parents=True)
    browser_login._save_session(BASE, cookies)  # pyright: ignore[reportPrivateUsage]


class FakeLogin:
    """Stands in for the browser window and counts how often it opened."""

    def __init__(self, cookies: list[dict[str, Any]]) -> None:
        self.cookies = cookies
        self.opened = 0

    async def __call__(self, config: JiraConfig) -> list[dict[str, Any]]:
        self.opened += 1
        return self.cookies


@respx.mock
async def test_saved_session_is_reused_without_opening_a_browser(state_root: Path) -> None:
    _save(COOKIES)
    route = respx.get(f"{BASE}/rest/api/2/myself").respond(json={"name": "me"})
    login = FakeLogin([])
    async with JiraClient(_browser_config(), login=login) as client:
        await client.check_login()
    assert login.opened == 0
    header = route.calls[0].request.headers["Cookie"]
    assert "JSESSIONID=abc" in header
    assert "sso=def" in header
    assert "other=" not in header
    assert "lookalike=" not in header
    assert "Authorization" not in route.calls[0].request.headers


def _session_gate(request: httpx.Request) -> httpx.Response:
    """Act like Jira: accept only a request that carries the session cookie."""
    if "JSESSIONID=abc" in request.headers.get("Cookie", ""):
        return httpx.Response(200, json={"name": "me"})
    return httpx.Response(302, headers={"Location": "https://sso.example.com/login"})


@respx.mock
async def test_first_run_opens_the_browser_then_uses_the_session(state_root: Path) -> None:
    respx.get(f"{BASE}/rest/api/2/myself").mock(side_effect=_session_gate)
    issue = respx.get(url__startswith=f"{BASE}/rest/api/2/issue/ACT-1").respond(json={"fields": {"summary": "ok"}})
    login = FakeLogin(COOKIES)
    async with JiraClient(_browser_config(), login=login) as client:
        await client.check_login()
        assert (await client.get_ticket("ACT-1")).summary == "ok"
    assert login.opened == 1
    assert "JSESSIONID=abc" in issue.calls[0].request.headers["Cookie"]


@respx.mock
async def test_expired_session_opens_the_browser_again(state_root: Path) -> None:
    _save([{"name": "JSESSIONID", "value": "old", "domain": "jira.example.com", "path": "/", "secure": True}])
    respx.get(f"{BASE}/rest/api/2/myself").mock(side_effect=_session_gate)
    login = FakeLogin(COOKIES)
    async with JiraClient(_browser_config(), login=login) as client:
        await client.check_login()
    assert login.opened == 1


@respx.mock
async def test_session_rejected_outside_the_browser_is_explained_and_does_not_loop(state_root: Path) -> None:
    respx.get(f"{BASE}/rest/api/2/myself").respond(401)
    login = FakeLogin(COOKIES)
    async with JiraClient(_browser_config(), login=login) as client:
        with pytest.raises(JiraAuthError, match=r"use jira\.auth 'token'"):
            await client.check_login()
    assert login.opened == 1


@respx.mock
async def test_unreachable_jira_does_not_open_the_browser(state_root: Path) -> None:
    respx.get(f"{BASE}/rest/api/2/myself").mock(side_effect=httpx.ConnectError("no route"))
    login = FakeLogin(COOKIES)
    async with JiraClient(_browser_config(), login=login) as client:
        with pytest.raises(TagtraceError, match="Could not reach"):
            await client.check_login()
    assert login.opened == 0


@respx.mock
async def test_token_failure_never_opens_the_browser(state_root: Path) -> None:
    respx.get(f"{BASE}/rest/api/2/myself").respond(401)
    login = FakeLogin(COOKIES)
    async with JiraClient(_token_config(), login=login) as client:
        with pytest.raises(JiraAuthError):
            await client.check_login()
    assert login.opened == 0


def test_damaged_session_file_means_signing_in_again(state_root: Path) -> None:
    folder = browser_login._state_folder(BASE)  # pyright: ignore[reportPrivateUsage]
    folder.mkdir(parents=True)
    (folder / "session.json").write_text("not json", encoding="utf-8")
    assert browser_login.load_session(BASE) == []


def test_session_file_is_private(state_root: Path) -> None:
    _save(COOKIES)
    folder = browser_login._state_folder(BASE)  # pyright: ignore[reportPrivateUsage]
    assert stat.S_IMODE((folder / "session.json").stat().st_mode) == 0o600


async def test_missing_browser_extra_is_explained(state_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "playwright.async_api", None)
    with pytest.raises(ConfigError, match="'browser' extra"):
        await browser_login.open_login_window(_browser_config())


@pytest.mark.parametrize("status", [500, 503, 404])
@respx.mock
async def test_jira_errors_do_not_open_the_browser(state_root: Path, status: int) -> None:
    respx.get(f"{BASE}/rest/api/2/myself").respond(status)
    login = FakeLogin(COOKIES)
    async with JiraClient(_browser_config(), login=login) as client:
        with pytest.raises(TagtraceError, match=f"answered {status}") as caught:
            await client.check_login()
    assert not isinstance(caught.value, JiraAuthError)
    assert login.opened == 0


@pytest.mark.parametrize("url", ["http://jira", "http://localhost:8080"])
@respx.mock
async def test_session_works_on_a_host_name_without_a_dot(
    state_root: Path, monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    host = url.split("//")[1].split(":")[0]
    cookies = [{"name": "JSESSIONID", "value": "abc", "domain": host, "path": "/", "expires": -1, "secure": False}]
    route = respx.get(f"{url}/rest/api/2/myself").respond(json={"name": "me"})
    monkeypatch.setattr("tagtrace.jira.load_session", lambda _: cookies)
    async with JiraClient(JiraConfig(url=url, auth="browser"), login=FakeLogin([])) as client:
        await client.check_login()
    assert "JSESSIONID=abc" in route.calls[0].request.headers["Cookie"]


@pytest.mark.parametrize(
    ("domain", "expected"),
    [
        ("jira.example.com", True),
        (".example.com", True),
        (".jira.example.com", True),
        ("JIRA.example.com", True),
        ("example.com", False),
        (".other.com", False),
        ("notjira.example.com", False),
        (".ample.com", False),
    ],
)
def test_host_matching_follows_browser_rules(domain: str, expected: bool) -> None:
    assert browser_login.host_matches("jira.example.com", domain) is expected


def test_valid_json_of_the_wrong_shape_means_signing_in_again(state_root: Path) -> None:
    folder = browser_login._state_folder(BASE)  # pyright: ignore[reportPrivateUsage]
    folder.mkdir(parents=True)
    (folder / "session.json").write_text('[1, "x", {"name": "a"}]', encoding="utf-8")
    assert browser_login.load_session(BASE) == [{"name": "a"}]


def test_existing_loose_permissions_are_tightened(state_root: Path) -> None:
    folder = browser_login._state_folder(BASE)  # pyright: ignore[reportPrivateUsage]
    folder.mkdir(parents=True, mode=0o755)
    session = folder / "session.json"
    session.write_text("[]", encoding="utf-8")
    session.chmod(0o644)
    browser_login._prepare_folder(folder)  # pyright: ignore[reportPrivateUsage]
    browser_login._save_session(BASE, COOKIES)  # pyright: ignore[reportPrivateUsage]
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert stat.S_IMODE(session.stat().st_mode) == 0o600
