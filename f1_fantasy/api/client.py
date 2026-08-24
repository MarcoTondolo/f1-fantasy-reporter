"""HTTP client for the F1 Fantasy service endpoints.

Auth is a session cookie captured by hand from the browser (see README) because
F1 accounts sit behind Imperva bot protection and scripted password login is not
reliable. The cookie lasts roughly five days, so expiry is a normal operating
condition rather than an error -- it gets its own exception type so callers can
tell "your cookie went stale" apart from "the API broke".
"""

from __future__ import annotations

import json
import logging
import random
import time
from typing import Any, Mapping

import httpx

log = logging.getLogger(__name__)

BASE_URL = "https://fantasy.formula1.com"
COOKIE_NAME = "F1_FANTASY_007"

# Sent on every request. The service is inconsistent about rejecting default
# client user-agents, so we always look like a browser XHR.
DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Referer": f"{BASE_URL}/",
    "X-Requested-With": "XMLHttpRequest",
}

RETRY_STATUS = {429, 500, 502, 503, 504}


class FantasyError(RuntimeError):
    """Any failure talking to the Fantasy API."""


class AuthExpired(FantasyError):
    """The session cookie is missing, rejected, or has expired.

    Expected roughly every five days. The CLI turns this into actionable refresh
    instructions rather than a stack trace.
    """


class NotShared(FantasyError):
    """The API accepted the request but refused this particular resource.

    Raised when another league member's data is not readable with our session --
    the case Phase 0 exists to discover.
    """


class FantasyClient:
    """Cookie-authenticated, retrying JSON client."""

    def __init__(
        self,
        token: str,
        guid: str,
        *,
        base_url: str = BASE_URL,
        timeout: float = 20.0,
        max_retries: int = 4,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not token or not guid:
            raise AuthExpired(
                "Missing credentials: set F1_FANTASY_TOKEN and F1_USER_GUID "
                "(see README for how to capture them)."
            )
        self.guid = guid
        self.max_retries = max_retries
        self._client = httpx.Client(
            base_url=base_url,
            headers=DEFAULT_HEADERS,
            cookies={COOKIE_NAME: token},
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
        )

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "FantasyClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- requests ----------------------------------------------------------

    def get_raw(self, path: str) -> Any:
        """GET *path* and return the decoded JSON body, retrying transient errors."""
        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            if attempt:
                # Exponential backoff with jitter, so parallel league fetches do
                # not retry in lockstep against a struggling endpoint.
                delay = min(2**attempt, 16) * (0.5 + random.random() / 2)
                log.debug("retrying %s in %.1fs (attempt %d)", path, delay, attempt + 1)
                time.sleep(delay)

            try:
                response = self._client.get(path)
            except httpx.HTTPError as exc:
                last_error = exc
                continue

            # A redirect to the login page is how an expired cookie usually
            # presents -- the service does not bother with a 401.
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location", "")
                if "login" in location.lower() or "account" in location.lower():
                    raise AuthExpired(f"Redirected to login ({location}) -- refresh the cookie.")
                last_error = FantasyError(f"Unexpected redirect to {location}")
                continue

            if response.status_code in (401, 403):
                raise AuthExpired(
                    f"HTTP {response.status_code} for {path} -- the session cookie is "
                    "rejected or expired."
                )

            if response.status_code in RETRY_STATUS:
                last_error = FantasyError(f"HTTP {response.status_code} for {path}")
                continue

            if response.status_code >= 400:
                raise FantasyError(f"HTTP {response.status_code} for {path}: {response.text[:200]}")

            try:
                return response.json()
            except (json.JSONDecodeError, ValueError) as exc:
                # HTML where JSON was promised means we were served the login or
                # an error page.
                body = response.text.lstrip()[:200]
                if body.lower().startswith(("<!doctype", "<html")):
                    raise AuthExpired(
                        f"{path} returned an HTML page instead of JSON -- the cookie has "
                        "almost certainly expired."
                    ) from exc
                raise FantasyError(f"{path} returned unparseable JSON: {body}") from exc

        raise FantasyError(f"{path} failed after {self.max_retries} attempts: {last_error}")

    def get(self, path: str) -> Any:
        """GET *path* and unwrap the ``{Data: {Value: ...}}`` envelope."""
        payload = self.get_raw(path)
        return unwrap(payload, path=path)


def unwrap(payload: Any, *, path: str = "") -> Any:
    """Strip the API's response envelope, raising on its several failure shapes.

    Successful bodies look like ``{"Data": {"Value": ...}, "Meta": {...}}``. On
    failure ``Data`` is null and ``Meta`` carries the reason, so an HTTP 200 is
    not by itself a success.
    """
    if not isinstance(payload, Mapping):
        return payload

    if "Data" not in payload:
        return payload

    data = payload.get("Data")
    if data is None:
        meta = payload.get("Meta") or {}
        message = ""
        if isinstance(meta, Mapping):
            message = str(meta.get("Message") or meta.get("message") or meta)
        else:
            message = str(meta)

        lowered = message.lower()
        if any(word in lowered for word in ("login", "session", "token", "unauthor", "expire")):
            raise AuthExpired(f"{path}: {message}")
        if any(word in lowered for word in ("permission", "not allowed", "access")):
            raise NotShared(f"{path}: {message}")
        raise FantasyError(f"{path}: {message or 'API returned no data'}")

    if isinstance(data, Mapping) and "Value" in data:
        return data["Value"]
    return data
