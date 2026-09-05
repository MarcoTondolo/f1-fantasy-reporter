"""Refresh F1_FANTASY_TOKEN/F1_USER_GUID by driving a real, visible browser.

The README's "Credentials" section documents why this project has never
scripted the login itself: F1 accounts sit behind Imperva bot mitigation,
which is built specifically to catch headless/datacenter traffic (missing
`navigator.webdriver` tells, no real input entropy, cloud IP ranges) --
fighting that is a losing, ongoing maintenance war for what currently costs
about 30 seconds every five days.

This does something different, and doesn't fight Imperva at all: it opens a
real, visible Chromium window and lets *you* log in exactly as you always
have, so any challenge gets solved by an actual human in an actual browser.
It only automates the tedious part that follows -- opening DevTools,
finding the login request, and copying `Token`/`GUID` out of its response by
hand. It never sees or stores a password.

Meant to be run locally, on a machine with a real display -- not from CI.
A CI runner is exactly the headless/datacenter profile Imperva is built to
block, and there is no display for a login form to appear on anyway.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, NamedTuple

log = logging.getLogger(__name__)

LOGIN_URL = "https://fantasy.formula1.com"
LOGIN_ENDPOINT = "/services/session/login"

#: A persistent browser profile so a second refresh within the token's
#: ~5-day lifetime doesn't necessarily repeat any 2FA/challenge step --
#: this keeps cookies/local storage on disk between runs, the same way a
#: real Chrome profile would.
DEFAULT_PROFILE_DIR = Path.home() / ".f1-fantasy" / "browser-profile"


class Captured(NamedTuple):
    token: str
    guid: str


def parse_login_response(body: Any) -> Captured | None:
    """Pull Token/GUID out of a `/services/session/login` JSON body.

    Matches the exact casing the README already documents copying by hand
    (`Token`, `GUID`), with lowercase fallbacks since this is inherently
    matching an undocumented third-party API shape.
    """
    if not isinstance(body, dict):
        return None
    token = body.get("Token") or body.get("token")
    guid = body.get("GUID") or body.get("guid") or body.get("SubscriberId")
    if not token or not guid:
        return None
    return Captured(token=str(token), guid=str(guid))


def _chromium_path() -> str | None:
    from f1_fantasy.render.shot import _chromium_path as _shared_chromium_path

    return _shared_chromium_path()


def capture_session(
    *,
    profile_dir: Path = DEFAULT_PROFILE_DIR,
    timeout_s: float = 300.0,
    login_url: str = LOGIN_URL,
) -> Captured:
    """Open a real, visible browser at the login page and wait for a real
    login to complete, returning the Token/GUID from the response that
    drove it -- exactly the response the manual process asks a human to
    find in DevTools and copy by hand.

    Blocks until either the token/guid are captured or `timeout_s` elapses
    with no successful login, in which case it raises `TimeoutError`.
    """
    from playwright.sync_api import sync_playwright

    profile_dir.mkdir(parents=True, exist_ok=True)
    captured: list[Captured] = []

    def on_response(response: Any) -> None:
        if LOGIN_ENDPOINT not in response.url:
            return
        try:
            body = response.json()
        except Exception:  # noqa: BLE001 - a non-JSON response just isn't the one we want
            return
        result = parse_login_response(body)
        if result is not None:
            captured.append(result)
            log.info("captured session token from %s", response.url)

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile_dir),
            headless=False,
            executable_path=_chromium_path(),
            viewport={"width": 1280, "height": 900},
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.on("response", on_response)
            page.goto(login_url, wait_until="domcontentloaded")

            print("A browser window has opened -- log in as you normally would.")
            print(f"Waiting up to {int(timeout_s)}s for the session token...")

            deadline = time.monotonic() + timeout_s
            while time.monotonic() < deadline and not captured:
                page.wait_for_timeout(500)

            if not captured:
                raise TimeoutError(
                    f"No successful login detected within {int(timeout_s)}s -- "
                    "did you finish logging in in the opened browser window?"
                )
            return captured[0]
        finally:
            context.close()


def write_env_file(captured: Captured, *, env_path: Path = Path(".env")) -> None:
    """Update or create .env with F1_FANTASY_TOKEN/F1_USER_GUID, preserving
    every other line untouched -- the same file `Credentials.from_env` reads
    for a local run.
    """
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    def _set(lines: list[str], key: str, value: str) -> list[str]:
        pattern = re.compile(rf"^{re.escape(key)}=")
        for i, line in enumerate(lines):
            if pattern.match(line):
                lines[i] = f"{key}={value}"
                return lines
        return [*lines, f"{key}={value}"]

    lines = _set(lines, "F1_FANTASY_TOKEN", captured.token)
    lines = _set(lines, "F1_USER_GUID", captured.guid)
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def push_github_secrets(captured: Captured, *, repo: str | None = None, runner: Any = subprocess.run) -> bool:
    """Push the captured token/guid to GitHub repo secrets via the `gh` CLI,
    so the same refresh also unblocks the next scheduled Actions run.

    Never raises: returns False if `gh` isn't installed/authenticated, or if
    either secret fails to set -- this is a convenience on top of the .env
    write, not a hard requirement (the .env write already succeeded by the
    time this runs).
    """
    if shutil.which("gh") is None:
        log.warning("gh CLI not found; skipping repo secret update (the .env file is still written)")
        return False

    command_prefix = ["gh", "secret", "set"]
    if repo:
        command_prefix += ["--repo", repo]

    for key, value in (("F1_FANTASY_TOKEN", captured.token), ("F1_USER_GUID", captured.guid)):
        result = runner([*command_prefix, key], input=value, text=True, capture_output=True)
        if result.returncode != 0:
            log.warning("gh secret set %s failed: %s", key, result.stderr.strip())
            return False
    return True
