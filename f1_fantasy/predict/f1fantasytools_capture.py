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
so this runs from GitHub Actions instead (see
``.github/workflows/f1-fantasy.yml``'s ``news`` job), which has ordinary
outbound access and can actually reach the site. Every failure mode -- a
blocked/changed page, a missing API key, a malformed model response --
degrades to ``None``/an empty dict with a logged reason, never raises,
matching ``predict/odds.py``'s standard for a collector nothing downstream
may depend on succeeding.

**The four real pages, confirmed by the user** (not guessed -- an earlier
version of this module pointed at the bare homepage and, on the first live
run, silently captured a *marketing screenshot embedded in the homepage's
own hero section* -- a promotional image labelled "R24 ... See you in
2025!" from the prior season, not live data. Real driver codes, wrong
table entirely, which is exactly the kind of wrong-but-plausible failure a
human has to catch by actually looking at the screenshot, not just
checking whether extraction returned *something*):

- ``/statistics`` -- actual points scored history
- ``/team-calculator`` -- this site's own modelled/projected fantasy
  points per player and team (the real analogue to what this project's
  own ``predict/points.py`` produces -- the most direct benchmark)
- ``/budget-builder`` -- budget/price impact based on points
- ``/elite-data`` -- global top-500 ranked team composition/ownership

Each gets its own tailored extraction prompt (a generic "find a
points table" prompt is exactly what mistook the marketing screenshot for
real data) and its own distinct ``source`` label, so the benchmark card
(``report/benchmarks.py``, which already groups purely by ``source``
string -- no code there needed to change) shows all four side by side
rather than one overwriting another.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from f1_fantasy.predict.benchmarks import ExternalBenchmarkSnapshot

log = logging.getLogger(__name__)

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
#: Vision-capable; matches this project's own model at the time this was
#: written. Override via extract_driver_points's model= if that changes.
DEFAULT_MODEL = "claude-sonnet-5"

#: The Anthropic API rejects oversized images (confirmed live: a 4.67MB
#: full-page PNG of the real site produced a 400 Bad Request on the very
#: first real run). Kept well under the ~5MB documented limit since
#: base64 encoding inflates the payload by ~1.33x on top of this.
MAX_IMAGE_BYTES = 3_500_000
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


@dataclass(frozen=True)
class F1FTPage:
    key: str
    url: str
    extraction_prompt: str
    source: str


_CODE_HINT = (
    "Respond with ONLY a JSON object mapping each driver's 3-letter code (e.g. VER, NOR, HAM -- "
    "infer the code from the full name if only the full name is shown) to the number described "
    "above. Respond with {} if no such table is visible anywhere in the image. Do not include "
    "any text before or after the JSON object."
)

F1FT_PAGES: dict[str, F1FTPage] = {
    "team_calculator": F1FTPage(
        key="team_calculator",
        url="https://f1fantasytools.com/team-calculator",
        extraction_prompt=(
            "This is a screenshot of the F1 Fantasy Tools 'Team Calculator' page, which shows "
            "this site's own modelled/projected fantasy points for each driver for an upcoming "
            "or recent race. Find the table or list of drivers with their modelled/projected "
            "points (not a price, not an ownership percentage -- the points projection itself). "
            + _CODE_HINT
        ),
        source="f1fantasytools_team_calculator",
    ),
    "statistics": F1FTPage(
        key="statistics",
        url="https://f1fantasytools.com/statistics",
        extraction_prompt=(
            "This is a screenshot of the F1 Fantasy Tools 'Statistics' page, showing actual "
            "fantasy points scored by each driver across real past races this season. Find the "
            "table showing each driver's actual scored points -- use whichever single number is "
            "most prominently their overall/total actual points (not a projection, not a price). "
            + _CODE_HINT
        ),
        source="f1fantasytools_statistics",
    ),
    "budget_builder": F1FTPage(
        key="budget_builder",
        url="https://f1fantasytools.com/budget-builder",
        extraction_prompt=(
            "This is a screenshot of the F1 Fantasy Tools 'Budget Builder' page, showing each "
            "driver's projected price/budget change based on points. Find the table showing each "
            "driver alongside a price or budget delta (e.g. in $ millions -- can be negative). "
            + _CODE_HINT
        ),
        source="f1fantasytools_budget_builder",
    ),
    "elite_data": F1FTPage(
        key="elite_data",
        url="https://f1fantasytools.com/elite-data",
        extraction_prompt=(
            "This is a screenshot of the F1 Fantasy Tools 'Elite Data' page, showing ownership/"
            "selection statistics for the globally top-ranked (top 500) fantasy teams. Find the "
            "table showing each driver alongside how many or what percentage of these top teams "
            "own or have selected them. "
            + _CODE_HINT
        ),
        source="f1fantasytools_elite_data",
    ),
}


def _screenshot_path(page_key: str) -> Path:
    return Path(f"data/pace/f1fantasytools_{page_key}_screenshot.jpg")


def capture_screenshot(
    url: str,
    out_path: Path | str,
    *,
    timeout_ms: int = 30_000,
    settle_ms: int = 6_000,
) -> Path | None:
    """Loads *url* in headless Chromium, waits for the DOM to parse plus a
    fixed settle delay for client-side hydration (see the inline comment
    on the goto() call for why not "networkidle"), and saves a JPEG (not
    PNG -- real-world pages compress far smaller as JPEG, and staying
    under the API's size/dimension limits matters more here than lossless
    quality). Returns None (logging why) on any failure -- a site outage,
    network block, or layout change must not break the rest of the
    collection pipeline.

    Two independent guards, because they catch different real failures
    (both hit live against the real site):
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
                # "networkidle" (no connections for 500ms) is too strict for
                # these pages -- confirmed live: 3 of the 4 real F1FT_PAGES
                # timed out waiting for it on the very first multi-page run,
                # apparently never going quiet (live-updating dashboards --
                # a price ticker, analytics beacon, or similar short-interval
                # background request). "domcontentloaded" fires as soon as
                # the DOM is parsed and doesn't wait on ongoing network
                # activity; the fixed settle_ms delay below is what actually
                # covers client-side hydration instead.
                page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
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
    prompt: str,
    model: str = DEFAULT_MODEL,
    timeout: float = 60.0,
) -> dict[str, float]:
    """Sends the screenshot to the Claude API with *prompt* and parses a
    driver-code -> value JSON object out of the response. Never raises: a
    missing key, an HTTP error, or an unparseable response all return {}
    with a logged reason -- this is a collector, not something anything
    downstream may depend on succeeding."""
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
                            {"type": "text", "text": prompt},
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
        # future 400 is as undiagnosable from the logs as earlier ones were.
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
    page: str,
    session_label: str = "",
    screenshot_path: Path | str | None = None,
) -> ExternalBenchmarkSnapshot | None:
    """The one-call entry point for a single page: screenshot, extract,
    build a snapshot. *page* must be a key in F1FT_PAGES. Returns None
    (logging why) if the page key is unknown, the screenshot fails, or
    nothing gets extracted -- an empty snapshot would be indistinguishable
    from "the site genuinely has nothing to show," which is worth not
    pretending to know."""
    spec = F1FT_PAGES.get(page)
    if spec is None:
        log.warning("capture_f1fantasytools_snapshot: unknown page %r (known: %s)", page, sorted(F1FT_PAGES))
        return None

    path = Path(screenshot_path) if screenshot_path is not None else _screenshot_path(page)
    screenshot = capture_screenshot(spec.url, path)
    if screenshot is None:
        return None

    entries = extract_driver_points(screenshot, api_key=api_key, prompt=spec.extraction_prompt)
    if not entries:
        log.warning("capture_f1fantasytools_snapshot: no data extracted from %s (%s)", spec.url, screenshot)
        return None

    return ExternalBenchmarkSnapshot(
        source=spec.source,
        season=season,
        round_number=round_number,
        session_label=session_label,
        captured_at=datetime.now(timezone.utc),
        entries=entries,
        note=f"auto-captured via screenshot + vision extraction from {spec.url}",
    )


def capture_all_f1fantasytools_snapshots(
    season: int,
    round_number: int,
    *,
    api_key: str,
    session_label: str = "",
) -> list[ExternalBenchmarkSnapshot]:
    """Captures every page in F1FT_PAGES independently -- one page failing
    (a layout change, a transient block) never stops the others, matching
    every other multi-source collector in this project (news/upgrades.py's
    per-feed try/except, cmd_collect_benchmark_snapshot's per-source
    try/except)."""
    results = []
    for key in F1FT_PAGES:
        try:
            snapshot = capture_f1fantasytools_snapshot(
                season, round_number, api_key=api_key, page=key, session_label=session_label
            )
        except Exception as exc:  # noqa: BLE001 -- one page's failure must not abort the rest
            log.warning("capture_all_f1fantasytools_snapshots: page %r raised: %s", key, exc)
            continue
        if snapshot is not None:
            results.append(snapshot)
    return results
