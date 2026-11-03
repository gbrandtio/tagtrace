"""Finds Jira keys in commit messages and reads the tickets through Jira's REST API v2.

Version 2 is used on both Cloud and Data Center, because on both it returns text rather than Cloud v3's document
format. The two differ only in how a token is sent.
"""

import asyncio
import base64
import re
from collections.abc import Awaitable, Callable, Iterable
from http.cookiejar import Cookie, CookieJar
from types import TracebackType
from typing import Any, Literal, Self, cast
from urllib.parse import quote, urlsplit

import httpx
from pydantic import BaseModel

from tagtrace.browser_login import host_matches, load_session, open_login_window
from tagtrace.config import JiraConfig
from tagtrace.errors import JiraAuthError, TagtraceError

_KEY = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")
_FIELDS = "summary,description,issuetype,status,priority,resolution,components,labels,fixVersions,issuelinks,comment"
_MAX_RATE_LIMIT_RETRIES = 3
_MAX_RETRY_WAIT_SECONDS = 60.0


class JiraComment(BaseModel):
    """One ticket comment."""

    author: str
    created: str
    body: str


class Ticket(BaseModel):
    """What Jira says about one key. `fetch_status` is `ok` only when the other fields were read."""

    key: str
    url: str
    fetch_status: Literal["ok", "not_found", "error"] = "ok"
    fetch_error: str | None = None
    summary: str = ""
    description: str = ""
    issue_type: str = ""
    status: str = ""
    priority: str = ""
    resolution: str = ""
    components: list[str] = []
    labels: list[str] = []
    fix_versions: list[str] = []
    links: list[str] = []
    comments: list[JiraComment] = []
    omitted_comments: int = 0


def find_keys(message: str, projects: Iterable[str] = ()) -> list[str]:
    """Return the sorted unique Jira keys in a message, limited to `projects` when any are given."""
    allowed = set(projects)
    keys = set(_KEY.findall(message))
    return sorted(key for key in keys if not allowed or key.rsplit("-", 1)[0] in allowed)


def _to_jar(cookies: Iterable[dict[str, Any]], host: str) -> CookieJar:
    """Build a cookie jar holding only the cookies that apply to `host`."""
    jar = CookieJar()
    for cookie in cookies:
        domain, expires = str(cookie.get("domain", "")), cookie.get("expires")
        if not host_matches(host, domain):
            continue
        # The cookie library treats a host name with no dot, such as http://jira/, as `jira.local`.
        if "." not in domain:
            domain = f"{domain}.local"
        jar.set_cookie(
            Cookie(
                version=0,
                name=str(cookie.get("name", "")),
                value=str(cookie.get("value", "")),
                port=None,
                port_specified=False,
                domain=domain,
                domain_specified=domain.startswith("."),
                domain_initial_dot=domain.startswith("."),
                path=str(cookie.get("path", "/")),
                path_specified=True,
                secure=bool(cookie.get("secure", False)),
                # Browsers report a session cookie, which has no expiry, with a negative number.
                expires=int(expires) if isinstance(expires, int | float) and expires > 0 else None,
                discard=False,
                comment=None,
                comment_url=None,
                rest={},
            )
        )
    return jar


def _names(items: object, field: str = "name") -> list[str]:
    """Pull one string field out of a Jira list of objects."""
    if not isinstance(items, list):
        return []
    result: list[str] = []
    for item in cast(list[object], items):
        if isinstance(item, dict):
            value = cast(dict[str, object], item).get(field)
            if isinstance(value, str):
                result.append(value)
    return result


def _name(item: object, field: str = "name") -> str:
    """Pull one string field out of a Jira object that may be missing."""
    names = _names([item], field)
    return names[0] if names else ""


def _text(value: object) -> str:
    """Return a text field. Anything unexpected is kept as its string form, so no content is lost."""
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _links(items: object) -> list[str]:
    """Describe linked issues as text, for example `blocks ACT-2: Fix login`."""
    if not isinstance(items, list):
        return []
    links: list[str] = []
    for raw in cast(list[object], items):
        if not isinstance(raw, dict):
            continue
        link = cast(dict[str, Any], raw)
        direction = "outward" if "outwardIssue" in link else "inward"
        other = link.get(f"{direction}Issue")
        if isinstance(other, dict):
            issue = cast(dict[str, Any], other)
            verb = _name(link.get("type"), direction) or "relates to"
            links.append(f"{verb} {issue.get('key', '')}: {_name(issue.get('fields'), 'summary')}".strip())
    return links


def _parse_issue(key: str, url: str, body: dict[str, Any], max_comments: int) -> Ticket:
    """Turn a Jira issue response into a `Ticket`, keeping the newest comments."""
    fields = cast(dict[str, Any], body.get("fields") or {})
    comment_section = cast(dict[str, Any], fields.get("comment") or {})
    comments: list[JiraComment] = []
    for raw in cast(list[dict[str, Any]], comment_section.get("comments") or []):
        comments.append(
            JiraComment(
                author=_name(raw.get("author"), "displayName"),
                created=_text(raw.get("created")),
                body=_text(raw.get("body")),
            )
        )
    newest = comments[-max_comments:] if max_comments > 0 else []
    return Ticket(
        key=key,
        url=url,
        summary=_text(fields.get("summary")),
        description=_text(fields.get("description")),
        issue_type=_name(fields.get("issuetype")),
        status=_name(fields.get("status")),
        priority=_name(fields.get("priority")),
        resolution=_name(fields.get("resolution")),
        components=_names(fields.get("components")),
        labels=[label for label in cast(list[object], fields.get("labels") or []) if isinstance(label, str)],
        fix_versions=_names(fields.get("fixVersions")),
        links=_links(fields.get("issuelinks")),
        comments=newest,
        omitted_comments=len(comments) - len(newest),
    )


class JiraClient:
    """Reads tickets from one Jira. Use inside `async with`."""

    def __init__(
        self,
        config: JiraConfig,
        login: Callable[[JiraConfig], Awaitable[list[dict[str, Any]]]] = open_login_window,
    ) -> None:
        """Keep the config and the function that signs in through a browser. Credentials are read in `__aenter__`."""
        self._config = config
        self._login = login
        self._host = (urlsplit(config.url).hostname or "").lower()
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> Self:
        """Prepare the sign-in. Redirects stay off so that cookies and tokens never reach another host."""
        options: dict[str, Any] = {}
        if self._config.auth == "browser":
            options["cookies"] = httpx.Cookies(_to_jar(load_session(self._config.url), self._host))
        else:
            options["headers"] = {"Authorization": self._authorization()}
        self._client = httpx.AsyncClient(base_url=self._config.url, follow_redirects=False, timeout=30.0, **options)
        return self

    def _authorization(self) -> str:
        """Build the header for token sign-in: Basic with an email (Cloud), otherwise Bearer (Data Center)."""
        assert self._config.token is not None  # noqa: S101 - enforced by JiraConfig
        secret = self._config.token.get_secret_value()
        if self._config.email:
            return f"Basic {base64.b64encode(f'{self._config.email}:{secret}'.encode()).decode()}"
        return f"Bearer {secret}"

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        """Close the connection."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _not_signed_in(self, reason: str) -> JiraAuthError:
        """Explain how to fix a failed sign-in for the configured method."""
        action = (
            "Run tagtrace again to sign in through the browser."
            if self._config.auth == "browser"
            else "Check jira.token, and jira.email on Jira Cloud."
        )
        return JiraAuthError(f"Jira did not accept the sign-in ({reason}). {action}")

    async def _get(self, path: str) -> httpx.Response:
        """Send a GET, waiting and retrying when Jira limits the request rate."""
        if self._client is None:
            raise RuntimeError("Use JiraClient inside 'async with'")
        for attempt in range(_MAX_RATE_LIMIT_RETRIES + 1):
            response = await self._client.get(path)
            if response.status_code != 429 or attempt == _MAX_RATE_LIMIT_RETRIES:
                return response
            await asyncio.sleep(self._retry_wait(response, attempt))
        raise AssertionError("unreachable")

    @staticmethod
    def _retry_wait(response: httpx.Response, attempt: int) -> float:
        """Use Jira's `Retry-After` when it is a number, otherwise back off."""
        try:
            wait = float(response.headers.get("Retry-After", ""))
        except ValueError:
            wait = 2.0**attempt
        return min(max(wait, 0.0), _MAX_RETRY_WAIT_SECONDS)

    def _check_signed_in(self, response: httpx.Response) -> None:
        """Recognise every way Jira reports a missing sign-in, including sending its HTML login page."""
        if response.status_code == 401:
            raise self._not_signed_in("401")
        if response.is_redirect:
            raise self._not_signed_in("redirected to a login page")
        if response.status_code == 200 and "json" not in response.headers.get("Content-Type", "").lower():
            raise self._not_signed_in("received a web page instead of data")

    async def _check_session(self) -> None:
        """Ask Jira whether the current credentials are accepted."""
        try:
            response = await self._get("/rest/api/2/myself")
        except httpx.HTTPError as error:
            raise TagtraceError(f"Could not reach Jira at {self._config.url}: {error}") from error
        self._check_signed_in(response)
        if response.status_code == 403:
            raise self._not_signed_in("403")
        # Anything else means Jira itself has a problem, which signing in again cannot fix.
        if response.status_code != 200:
            raise TagtraceError(f"Jira at {self._config.url} answered {response.status_code}. Try again later.")

    async def check_login(self) -> None:
        """Confirm the sign-in before any other work starts, opening the browser if a new sign-in is needed."""
        try:
            await self._check_session()
            return
        except JiraAuthError:
            if self._config.auth != "browser":
                raise
        cookies = await self._login(self._config)
        assert self._client is not None  # noqa: S101 - _check_session already required it
        self._client.cookies = httpx.Cookies(_to_jar(cookies, self._host))
        try:
            await self._check_session()
        except JiraAuthError as error:
            raise JiraAuthError(
                "You signed in, but Jira rejected the session outside the browser. Your SSO may tie the session to "
                "the browser, so use jira.auth 'token' instead."
            ) from error

    async def get_ticket(self, key: str) -> Ticket:
        """Read one ticket. Only a lost sign-in raises. Other failures are recorded on the returned `Ticket`."""
        url = f"{self._config.url}/browse/{quote(key, safe='')}"
        try:
            response = await self._get(f"/rest/api/2/issue/{quote(key, safe='')}?fields={_FIELDS}")
        except httpx.HTTPError as error:
            return Ticket(key=key, url=url, fetch_status="error", fetch_error=f"{type(error).__name__}: {error}")
        self._check_signed_in(response)
        if response.status_code == 404:
            return Ticket(key=key, url=url, fetch_status="not_found")
        if response.status_code != 200:
            return Ticket(key=key, url=url, fetch_status="error", fetch_error=f"Jira answered {response.status_code}")
        try:
            return _parse_issue(key, url, response.json(), self._config.max_comments)
        except (ValueError, TypeError, AttributeError) as error:
            return Ticket(key=key, url=url, fetch_status="error", fetch_error=f"Unreadable response: {error}")
