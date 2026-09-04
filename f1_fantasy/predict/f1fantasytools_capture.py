"""Automated f1fantasytools.com capture: screenshot, then vision extraction.

``predict/benchmarks.py`` documents why this table can't be scraped: it
loads client-side post-hydration, is absent from both the server-rendered
HTML and the Next.js RSC streaming payload, and every guessed REST path
404s. A screenshot sidesteps all of that -- it captures whatever actually
renders in the browser, the same table a person copying numbers by hand
would see, regardless of how the page produced it. The Claude API's vision
input reads that screenshot the same way a person would.

**Built without ever seeing the real page.** The dev sandbox this project
is built in blocks f1fantasytools.com outright at the network level
(confirmed live: an explicit ``EGRESS_BLOCKED`` error naming the proxy) --
so the wait/selector logic below is a best-effort guess about the page's
layout, not something verified end-to-end from here. This runs from
GitHub Actions instead (see ``.github/workflows/f1-fantasy.yml``'s
``news`` job), which has ordinary outbound access and can actually reach
the site. Every failure mode -- a blocked/changed page, a missing API key,
a malformed model response -- degrades to ``None``/an empty dict with a
logged reason, never raises, matching ``predict/odds.py``'s standard for a
collector nothing downstream may depend on succeeding. Re-tune the wait
delay/prompt once real screenshots from an actual run can be inspected
(the workflow uploads the screenshot as a build artifact for exactly this).
"""

from __future__ import annotations

import base64
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx

from f1_fantasy.predict.benchmarks import ExternalBenchmarkSnapshot

log = logging.getLogger(__name__)

DEFAULT_URL = "https://www.f1fantasytools.com/"
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
#: Vision-capable; matches this project's own model at the time this was
#: written. Override via extract_driver_points's model= if that changes.
DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_SCREENSHOT_PATH = Path("data/pace/f1fantasytools_screenshot.jpg")
#: The Anthropic API rejects oversized images (confirmed live: a 4.67MB
#: full-page PNG of the real site produced a 400 Bad Request on the very
#: first real run). Kept well under the ~5MB documented limit since
#: base64 encoding inflates the payload by ~1.33x on top of this.
MAX_IMAGE_BYTES = 3_500_000

EXTRACTION_PROMPT = (
    "This is a screenshot of an F1 fantasy points-projection website. Find any table or list "
    "that shows driver names alongside projected fantasy points (labels may vary: "
    "'projection', 'proj pts', 'points', 'elite data', or similar). Respond with ONLY a JSON "
    "object mapping each driver's 3-letter code (e.g. VER, NOR, HAM -- infer the code from the "
    "full name if only the full name is shown) to their projected points as a plain number. "
    "Respond with {} if no such driver/points table is visible anywhere in the image. Do not "
    "include any text before or after the JSON object."
)


#: The Anthropic API's own documented hard cap on any single image
#: dimension (confirmed live: a 1.46MB JPEG -- comfortably under
#: MAX_IMAGE_BYTES -- still drew a 400 because ``full_page=True`` on a
#: very long real page produced a height past this). A byte-size guard
#: alone cannot catch this: a tall, sparse page compresses small in bytes
#: while still being far too many pixels tall. Kept with a safety margin
#: below the documented 8000, not exactly at it.
MAX_IMAGE_DIMENSION_PX = 7900
VIEWPORT_WIDTH = 1600
VIEWPORT_HEIGHT = 2400


def capture_screenshot(
    url: str = DEFAULT_URL,
    out_path: Path | str = DEFAULT_SCREENSHOT_PATH,
    *,
    timeout_ms: int = 30_000,
    settle_ms: int = 4_000,
) -> Path | None:
    """Loads *url* in headless Chromium, waits for network idle plus a
    fixed settle delay for client-side hydration, and saves a JPEG (not
    PNG -- real-world pages compress far smaller as JPEG, and staying
    under the API's size/dimension limits matters more here than lossless
    quality). Returns None (logging why) on any failure -- a site outage,
    network block, or layout change must not break the rest of the
    collection pipeline.

    Two independent guards, because they catch different real failures
    (both hit live on the first two runs against the real site):
    - **Dimension**: measures the page's actual scroll height before
      capturing. A full-page shot only happens when it fits under
      MAX_IMAGE_DIMENSION_PX; otherwise this clips from the top of the
      page down to that height instead -- still covers far more of the
      page than a bare single-viewport shot would, while staying under
      the API's hard per-dimension cap.
    - **Size**: if the resulting file is still over MAX_IMAGE_BYTES
      (a tall clip can still be a large file if the content is dense),
      falls back further to a plain viewport-only capture, which is
      bounded on both dimension and, in practice, size.
    """
    from playwright.sync_api import sync_playwright

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT})
                page.goto(url, timeout=timeout_ms, wait_until="networkidle")
                page.wait_for_timeout(settle_ms)

                scroll_height = page.evaluate("document.documentElement.scrollHeight")
                if scroll_height and scroll_height > MAX_IMAGE_DIMENSION_PX:
                    log.info(
                        "%s is %dpx tall, over the %dpx cap -- clipping instead of a full-page capture",
                        url, scroll_height, MAX_IMAGE_DIMENSION_PX,
                    )
                    page.screenshot(
                        path=str(out_path), type="jpeg", quality=70,
                        clip={"x": 0, "y": 0, "width": VIEWPORT_WIDTH, "height": MAX_IMAGE_DIMENSION_PX},
                    )
                else:
                    page.screenshot(path=str(out_path), full_page=True, type="jpeg", quality=70)

                if out_path.stat().st_size > MAX_IMAGE_BYTES:
                    log.info(
                        "screenshot of %s was %d bytes, over the limit -- retrying viewport-only",
                        url, out_path.stat().st_size,
                    )
                    page.screenshot(path=str(out_path), full_page=False, type="jpeg", quality=70)
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001 -- a screenshot failure must degrade, not raise
        log.warning("could not capture screenshot of %s: %s", url, exc)
        return None

    size = out_path.stat().st_size
    log.info("captured screenshot of %s: %s (%d bytes)", url, out_path, size)
    if size > MAX_IMAGE_BYTES:
        log.warning(
            "screenshot %s is still %d bytes after the viewport-only fallback -- "
            "extraction will likely fail on the API's size limit",
            out_path, size,
        )
    return out_path


_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def extract_driver_points(
    image_path: Path | str,
    *,
    api_key: str,
    model: str = DEFAULT_MODEL,
    timeout: float = 60.0,
) -> dict[str, float]:
    """Sends the screenshot to the Claude API and parses a driver-code ->
    points JSON object out of the response. Never raises: a missing key,
    an HTTP error, or an unparseable response all return {} with a logged
    reason -- this is a collector, not something anything downstream may
    depend on succeeding."""
    if not api_key:
        log.warning("extract_driver_points: no Anthropic API key configured, skipping")
        return {}

    image_path = Path(image_path)
    if not image_path.exists():
        log.warning("extract_driver_points: %s does not exist", image_path)
        return {}

    image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")

    try:
        response = httpx.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 1024,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64},
                            },
                            {"type": "text", "text": EXTRACTION_PROMPT},
                        ],
                    }
                ],
            },
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        text = "".join(block.get("text", "") for block in payload.get("content", []) if block.get("type") == "text")
    except httpx.HTTPStatusError as exc:
        # The response body carries the actual validation error (e.g. "image
        # exceeds 5 MB maximum") -- log it, not just the status line, or a
        # future 400 is as undiagnosable from the logs as this one was.
        log.warning(
            "extract_driver_points: Anthropic API call failed: %s -- %s",
            exc, exc.response.text[:500],
        )
        return {}
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("extract_driver_points: Anthropic API call failed: %s", exc)
        return {}

    cleaned = _JSON_FENCE.sub("", text.strip())
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        log.warning("extract_driver_points: could not parse model response as JSON: %r", text[:300])
        return {}

    if not isinstance(parsed, dict):
        log.warning("extract_driver_points: model response JSON was not an object: %r", parsed)
        return {}

    entries: dict[str, float] = {}
    for code, value in parsed.items():
        try:
            entries[str(code).strip().upper()] = float(value)
        except (TypeError, ValueError):
            log.warning("extract_driver_points: ignoring non-numeric entry %r: %r", code, value)
    return entries


def capture_f1fantasytools_snapshot(
    season: int,
    round_number: int,
    *,
    api_key: str,
    session_label: str = "",
    url: str = DEFAULT_URL,
    screenshot_path: Path | str = DEFAULT_SCREENSHOT_PATH,
) -> ExternalBenchmarkSnapshot | None:
    """The one-call entry point: screenshot, extract, build a snapshot.
    Returns None (logging why) if the screenshot fails or nothing gets
    extracted -- an empty snapshot would be indistinguishable from "the
    site genuinely has nothing to show," which is worth not pretending to
    know."""
    screenshot = capture_screenshot(url, screenshot_path)
    if screenshot is None:
        return None

    entries = extract_driver_points(screenshot, api_key=api_key)
    if not entries:
        log.warning("capture_f1fantasytools_snapshot: no driver/points table extracted from %s", screenshot)
        return None

    return ExternalBenchmarkSnapshot(
        source="f1fantasytools",
        season=season,
        round_number=round_number,
        session_label=session_label,
        captured_at=datetime.now(timezone.utc),
        entries=entries,
        note=f"auto-captured via screenshot + vision extraction from {url}",
    )
