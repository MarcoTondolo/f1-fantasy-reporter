"""News for the preview card: deterministic penalties plus filtered headlines.

Two different reliability tiers on purpose. Grid penalties are computed from
results data (see ``f1_fantasy.results``) -- exact, no scraping guesswork.
Headlines are prose from F1.com's own RSS feed, filtered to a keyword list
rather than summarised, so a false positive is at worst an irrelevant
headline shown, never a fabricated one.
"""

from __future__ import annotations

import logging
import urllib.request

import feedparser

from f1_fantasy.api.models import Model
from f1_fantasy.results import GridPenalty

log = logging.getLogger(__name__)

DEFAULT_FEED_URL = "https://www.formula1.com/en/latest/all.xml"

#: Case-insensitive substrings that make a headline or summary newsworthy for
#: a fantasy audience -- penalties and reliability drive fantasy decisions
#: (who to captain, who to avoid) more than most other F1 news.
KEYWORDS = (
    "penalty",
    "penalised",
    "penalized",
    "engine",
    "power unit",
    "grid drop",
    "grid penalty",
    "ice",
    "gearbox",
    "stewards",
    "disqualif",
    "reprimand",
    "investigation",
)


class Headline(Model):
    title: str
    summary: str = ""
    link: str = ""
    matched: tuple[str, ...] = ()


def _matches(text: str) -> tuple[str, ...]:
    lowered = text.lower()
    return tuple(kw for kw in KEYWORDS if kw in lowered)


def fetch_headlines(
    feed_url: str = DEFAULT_FEED_URL, *, limit: int = 40, timeout: float = 20.0
) -> list[Headline]:
    """Every entry in the feed, unfiltered -- filtering happens in ``headlines``."""
    request = urllib.request.Request(feed_url, headers={"User-Agent": "f1-fantasy-reporter/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    parsed = feedparser.parse(raw)
    out = []
    for entry in parsed.entries[:limit]:
        title = entry.get("title", "")
        summary = entry.get("summary", "")
        out.append(
            Headline(
                title=title,
                summary=summary,
                link=entry.get("link", ""),
                matched=_matches(f"{title} {summary}"),
            )
        )
    return out


def headlines(
    feed_url: str = DEFAULT_FEED_URL, *, limit: int = 40, timeout: float = 20.0
) -> list[Headline]:
    """Feed entries whose title or summary hits a fantasy-relevant keyword."""
    return [h for h in fetch_headlines(feed_url, limit=limit, timeout=timeout) if h.matched]


def build_bulletins(
    penalties: list[GridPenalty],
    news: list[Headline],
) -> dict:
    """Context for the news section of the preview card."""
    return {
        "penalties": [
            {
                "driver": p.driver_name,
                "from": p.quali_position,
                "to": p.grid_position,
                "places_lost": p.places_lost,
            }
            for p in penalties
        ],
        "headlines": [
            {"title": h.title, "summary": h.summary, "matched": list(h.matched)} for h in news
        ],
    }
