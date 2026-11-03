"""Runs the sign-in flow in a real browser against a small fake Jira. It is skipped if no browser can start."""

import json
import shutil
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from tagtrace import browser_login
from tagtrace.config import JiraConfig
from tagtrace.errors import JiraAuthError
from tagtrace.jira import JiraClient


@pytest.fixture
def fake_jira() -> Iterator[str]:
    """Serve a site where opening the home page signs you in with a session cookie, which has no expiry."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            signed_in = "JSESSIONID=real" in self.headers.get("Cookie", "")
            if self.path.startswith("/rest/api/2/myself"):
                self.send_response(200 if signed_in else 401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"name": "me"}).encode())
                return
            self.send_response(200)
            self.send_header("Set-Cookie", "JSESSIONID=real; Path=/; HttpOnly")
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html>signed in</html>")

        def log_message(self, format: str, *args: Any) -> None:
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


async def test_real_browser_sign_in_saves_a_session_that_works_without_a_browser(
    fake_jira: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(browser_login, "STATE_ROOT", tmp_path / "state")
    config = JiraConfig(url=fake_jira, auth="browser", browser="chrome")

    async def headless_login(settings: JiraConfig) -> list[dict[str, Any]]:
        return await browser_login.open_login_window(settings, headless=True)

    try:
        async with JiraClient(config, login=headless_login) as client:
            await client.check_login()
    except JiraAuthError as error:
        if "Could not open" in str(error):
            pytest.skip(f"No browser available: {error}")
        raise

    saved = browser_login.load_session(fake_jira)
    assert [cookie["name"] for cookie in saved] == ["JSESSIONID"]

    # The next run must work from the saved file alone, even with the browser profile gone.
    shutil.rmtree(browser_login._state_folder(fake_jira) / "profile")  # pyright: ignore[reportPrivateUsage]

    async def must_not_open(_: JiraConfig) -> list[dict[str, Any]]:
        raise AssertionError("The saved session should have been enough")

    async with JiraClient(config, login=must_not_open) as client:
        await client.check_login()


@pytest.fixture
def never_signed_in(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> JiraConfig:
    """Point sign-in at an address that never accepts the session, and keep its state in a temporary folder."""
    monkeypatch.setattr(browser_login, "STATE_ROOT", tmp_path / "state")
    monkeypatch.setattr(browser_login, "_POLL_SECONDS", 0.05)
    return JiraConfig(url="http://127.0.0.1:9", auth="browser", browser="chrome")


async def _login_or_skip(config: JiraConfig) -> None:
    try:
        await browser_login.open_login_window(config, headless=True)
    except JiraAuthError as error:
        if "Could not open" in str(error):
            pytest.skip(f"No browser available: {error}")
        raise


async def test_unfinished_sign_in_times_out(never_signed_in: JiraConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser_login, "LOGIN_TIMEOUT_SECONDS", 0.2)
    with pytest.raises(JiraAuthError, match="not detected within"):
        await _login_or_skip(never_signed_in)


async def test_closing_the_window_stops_the_sign_in(
    never_signed_in: JiraConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    check = browser_login._is_signed_in  # pyright: ignore[reportPrivateUsage]

    async def close_then_check(context: Any, url: str) -> bool:
        for page in context.pages:
            await page.close()
        return await check(context, url)

    monkeypatch.setattr(browser_login, "_is_signed_in", close_then_check)
    with pytest.raises(JiraAuthError, match="closed before"):
        await _login_or_skip(never_signed_in)


async def test_sign_in_continues_while_a_popup_outlives_the_first_tab(
    never_signed_in: JiraConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"count": 0}

    async def popup_then_signed_in(context: Any, url: str) -> bool:
        calls["count"] += 1
        if calls["count"] == 1:
            await context.new_page()
            await context.pages[0].close()
            return False
        return True

    monkeypatch.setattr(browser_login, "_is_signed_in", popup_then_signed_in)
    await _login_or_skip(never_signed_in)
    assert calls["count"] == 2
