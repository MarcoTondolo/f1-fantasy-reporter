"""Upgrade-package detection and constructor/round attribution.

Reuses ``bulletins.py``'s exact fetch mechanism -- ``urllib.request`` +
``feedparser``, not WebFetch -- confirmed live this session that these
specific sources are reachable the same way F1.com's own feed already is.
Extended across four sources rather than one, because upgrade/technical-
development news is richer on team-media-focused outlets than F1.com's own
general feed, and adds a constructor-attribution step ``bulletins.py`` never
needed (penalty/reliability headlines don't need pinning to one team; this
feature's whole point is team attribution).
"""

from __future__ import annotations

import calendar as _calendar
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Mapping

import feedparser

from f1_fantasy.api.models import Model
from f1_fantasy.calendar import RaceEvent

log = logging.getLogger(__name__)

#: `the-race.com` is deliberately left out -- it 301-redirected under a
#: plain curl test and its content was never actually confirmed live the way
#: these three plus F1.com's own feed were. Add it once confirmed working,
#: not on the assumption a redirect resolves cleanly.
FEED_URLS: tuple[str, ...] = (
    "https://www.formula1.com/en/latest/all.xml",
    "https://www.autosport.com/rss/f1/news/",
    "https://www.motorsport.com/rss/f1/news/",
    "https://www.racefans.net/feed/",
)

#: Specific component/program nouns, not generic racing vocabulary -- bare
#: "development"/"upgrade"/"specification" match too much unrelated F1 news
#: ("development driver", "tyre specification", "development of the regs").
#: Kept only as compounds that reliably mean an upgrade story. A starting
#: list tuned by inspection of real sample headlines, not backtested --
#: revisit after a week or two of real digest output.
KEYWORDS: tuple[str, ...] = (
    "upgrade",
    "upgrade package",
    "new floor",
    "new front wing",
    "new rear wing",
    "new sidepod",
    "sidepod",
    "floor upgrade",
    "aero upgrade",
    "specification upgrade",
    "new spec",
    "b-spec",
    "major upgrade",
    "bring an upgrade",
    "introduces a new",
    "brings a new",
)

#: Jolpica's canonical Constructor.name -> surface forms seen in news
#: headlines. Matching is substring-on-lowercased-text -- each alias must be
#: specific enough not to false-positive against an unrelated team ("red
#: bull" is not a substring of "racing bulls" or vice versa, so the two
#: don't collide as written; re-check by hand whenever a team name changes).
CONSTRUCTOR_ALIASES: dict[str, tuple[str, ...]] = {
    "McLaren": ("mclaren",),
    "Ferrari": ("ferrari",),
    "Red Bull": ("red bull", "redbull"),
    "Mercedes": ("mercedes", "merc"),
    "Aston Martin": ("aston martin", "aston"),
    "Alpine": ("alpine",),
    "Williams": ("williams",),
    "Racing Bulls": ("racing bulls", "rb f1", "visa cash app"),
    "Haas F1 Team": ("haas",),
    "Audi": ("audi", "sauber"),  # 2026 rebrand -- outlets still say "Sauber" out of habit
    "Cadillac": ("cadillac",),
}


class UpgradeMention(Model):
    source: str
    title: str
    summary: str = ""
    link: str = ""
    published_at: datetime | None = None
    matched_keywords: tuple[str, ...] = ()
    constructors: tuple[str, ...] = ()
    attributed_round: int | None = None


def _matches(text: str) -> tuple[str, ...]:
    lowered = text.lower()
    return tuple(kw for kw in KEYWORDS if kw in lowered)


def _attribute_constructors(text: str) -> tuple[str, ...]:
    lowered = text.lower()
    return tuple(
        name for name, aliases in CONSTRUCTOR_ALIASES.items() if any(alias in lowered for alias in aliases)
    )


def _published_at(entry: Mapping) -> datetime | None:
    """feedparser exposes a parsed UTC 9-tuple at entry["published_parsed"]
    when the source carries a recognisable date header -- timegm, not
    mktime, since feedparser already normalises it to UTC, not local time."""
    parsed = entry.get("published_parsed")
    if not parsed:
        return None
    return datetime.fromtimestamp(_calendar.timegm(parsed), tz=timezone.utc)


class RawEntry(Model):
    """One feed entry before any keyword filtering -- the shared shape
    every category-specific scanner (upgrades here, lineup/penalty news in
    news/digest.py) classifies independently from a single fetch pass, so
    a multi-category digest doesn't refetch the same feeds once per
    category."""

    source: str
    title: str
    summary: str = ""
    link: str = ""
    published_at: datetime | None = None


def fetch_raw_entries(
    feed_urls: tuple[str, ...] = FEED_URLS, *, limit: int = 40, timeout: float = 20.0
) -> list[RawEntry]:
    """Every entry across every source, completely unfiltered.

    One dead feed logs a warning and is skipped rather than aborting the
    whole fetch -- this is multi-source specifically so no single outlet
    going down (redirect, block, timeout) blanks the result.
    """
    out: list[RawEntry] = []
    for feed_url in feed_urls:
        request = urllib.request.Request(feed_url, headers={"User-Agent": "f1-fantasy-reporter/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            log.warning("feed unreachable, skipping: %s (%s)", feed_url, exc)
            continue
        parsed = feedparser.parse(raw)
        for entry in parsed.entries[:limit]:
            out.append(
                RawEntry(
                    source=feed_url,
                    title=entry.get("title", ""),
                    summary=entry.get("summary", ""),
                    link=entry.get("link", ""),
                    published_at=_published_at(entry),
                )
            )
    return out


def fetch_mentions(
    feed_urls: tuple[str, ...] = FEED_URLS, *, limit: int = 40, timeout: float = 20.0
) -> list[UpgradeMention]:
    """Every keyword-matched entry across every source, not yet attributed to
    a round (round attribution needs a calendar fetch -- kept as a separate
    step so this function alone is testable/mockable per source without a
    calendar dependency, the same split bulletins.py keeps between
    ``fetch_headlines`` and ``headlines``).
    """
    out: list[UpgradeMention] = []
    for entry in fetch_raw_entries(feed_urls, limit=limit, timeout=timeout):
        matched = _matches(f"{entry.title} {entry.summary}")
        if not matched:
            continue
        out.append(
            UpgradeMention(
                source=entry.source,
                title=entry.title,
                summary=entry.summary,
                link=entry.link,
                published_at=entry.published_at,
                matched_keywords=matched,
                constructors=_attribute_constructors(f"{entry.title} {entry.summary}"),
            )
        )
    return out


def attribute_round(mention: UpgradeMention, events: list[RaceEvent]) -> int | None:
    """Bucket a mention to the nearest race event by publish date -- not
    floor/ceiling. A story published the Tuesday before a race is about that
    upcoming race; one published the Monday after is about the race just
    run. Both are "nearest event", which a pure floor (always the last past
    event) or ceiling (always the next event) would get wrong for roughly
    half of all mentions.
    """
    if mention.published_at is None or not events:
        return None
    return min(events, key=lambda e: abs((e.starts_at - mention.published_at).total_seconds())).round


def attribute_mentions(mentions: list[UpgradeMention], events: list[RaceEvent]) -> list[UpgradeMention]:
    return [m.model_copy(update={"attributed_round": attribute_round(m, events)}) for m in mentions]


def group_by_constructor_and_round(mentions: list[UpgradeMention]) -> dict[tuple[str, int], list[UpgradeMention]]:
    """Multiple sources corroborating the same real upgrade is a feature,
    not noise -- 3 independent outlets naming McLaren + round 9 is stronger
    evidence than 1. Grouped for display; nothing is dropped or deduped.
    Mentions with no attributed round, or no attributed constructor, are
    excluded here (nothing quantitative can use them) but stay in the full
    ``mentions`` list for a human to still see.
    """
    groups: dict[tuple[str, int], list[UpgradeMention]] = {}
    for mention in mentions:
        if mention.attributed_round is None:
            continue
        for constructor in mention.constructors:
            groups.setdefault((constructor, mention.attributed_round), []).append(mention)
    return groups


def track_upgrades(season: int) -> dict:
    """Fetch, filter, attribute, and group -- one call for the CLI."""
    from f1_fantasy.calendar import fetch_calendar

    events = fetch_calendar(season)
    mentions = attribute_mentions(fetch_mentions(), events)
    groups = group_by_constructor_and_round(mentions)
    return {"season": season, "mentions": mentions, "groups": groups}
