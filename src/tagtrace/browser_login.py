"""Signs in to Jira through a real browser window, so SSO and MFA work as they normally do.

The window uses its own browser profile, which keeps the SSO provider's login between runs. Once Jira accepts the
session, the cookies for the Jira address are saved to a private file, and later runs use them without a browser.
They are saved separately because a browser profile drops session cookies, such as Jira's, when it closes.
"""

import asyncio
import contextlib
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from tagtrace.config import JiraConfig
from tagtrace.errors import ConfigError, JiraAuthError

STATE_ROOT = Path.home() / ".tagtrace" / "jira"

LOGIN_TIMEOUT_SECONDS = 300.0
_POLL_SECONDS = 2.0
_SESSION_FILE = "session.json"


def host_matches(host: str, cookie_domain: str) -> bool:
    """Apply the browser rule: a leading dot covers subdomains, and any other cookie belongs to that one host."""
    domain = cookie_domain.lower()
    return host == domain.lstrip(".") or (domain.startswith(".") and host.endswith(domain))


def _state_folder(url: str) -> Path:
    """Return the private folder for one Jira address."""
    return STATE_ROOT / hashlib.sha256(url.encode()).hexdigest()[:24]


def load_session(url: str) -> list[dict[str, Any]]:
    """Read the saved cookies for a Jira address. A missing or damaged file just means signing in again."""
    try:
        saved = json.loads((_state_folder(url) / _SESSION_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(saved, list):
        return []
    return [cast(dict[str, Any], item) for item in cast(list[object], saved) if isinstance(item, dict)]


def _save_session(url: str, cookies: list[dict[str, Any]]) -> None:
    """Write the cookies so that only this user can read them."""
    path = _state_folder(url) / _SESSION_FILE
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        # The mode above applies only to a new file, so tighten an existing one before writing to it.
        os.chmod(path, 0o600)
        json.dump(cookies, file)


def _prepare_folder(folder: Path) -> None:
    """Create the private folder, tightening it if it already exists. It also holds the browser profile."""
    folder.mkdir(parents=True, exist_ok=True)
    folder.chmod(0o700)


async def _is_signed_in(context: Any, url: str) -> bool:
    """Ask Jira, from inside the browser, whether this session is accepted."""
    from playwright.async_api import Error

    try:
        response = await context.request.get(f"{url}/rest/api/2/myself", max_redirects=0)
    except Error:
        return False
    return response.status == 200 and "json" in response.headers.get("content-type", "").lower()


async def open_login_window(config: JiraConfig, *, headless: bool = False) -> list[dict[str, Any]]:
    """Open Jira in a browser, wait until the user has signed in, and return the saved cookies."""
    try:
        from playwright.async_api import Error, async_playwright
    except ImportError as error:
        raise ConfigError("jira.auth 'browser' needs the 'browser' extra: uv sync --extra browser") from error

    host = (urlsplit(config.url).hostname or "").lower()
    folder = _state_folder(config.url)
    await asyncio.to_thread(_prepare_folder, folder)
    print(
        f"Opening a browser window. Sign in to {config.url} there, and tagtrace continues by itself.", file=sys.stderr
    )
    try:
        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(
                str(folder / "profile"),
                channel=None if config.browser == "chromium" else config.browser,
                headless=headless,
            )
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                # A slow or redirecting SSO page is fine. The checks below decide the outcome.
                with contextlib.suppress(Error):
                    await page.goto(config.url)
                deadline = time.monotonic() + LOGIN_TIMEOUT_SECONDS
                while not await _is_signed_in(context, config.url):
                    # An SSO popup may outlive the first tab, so only all tabs being closed means the user gave up.
                    if not context.pages:
                        raise JiraAuthError("The browser window was closed before sign-in finished. Run again.")
                    if time.monotonic() > deadline:
                        raise JiraAuthError(
                            f"Sign-in was not detected within {LOGIN_TIMEOUT_SECONDS:.0f} seconds. "
                            f"If you did sign in, check that jira.url ({config.url}) is the exact address Jira uses."
                        )
                    await asyncio.sleep(_POLL_SECONDS)
                cookies = [cast(dict[str, Any], cookie) for cookie in await context.cookies()]
            finally:
                await context.close()
    except Error as error:
        raise JiraAuthError(
            f"Could not open {config.browser}: {error}. Check that it is installed and not already open with this "
            "profile, or use jira.auth 'token'."
        ) from error
    mine = [cookie for cookie in cookies if host_matches(host, str(cookie.get("domain", "")))]
    await asyncio.to_thread(_save_session, config.url, mine)
    return mine
