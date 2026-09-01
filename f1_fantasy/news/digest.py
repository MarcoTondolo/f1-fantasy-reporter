"""The daily digest: everything from this session that might affect a race
weekend's scoring, in one place -- lineup changes, upgrade packages, and
penalties/reliability news.

Built from pieces this project already has, not re-derived:

- **Lineup changes** -- two signals, kept distinct rather than merged into
  one false-confidence number. ``news/lineup_watch.py``'s data-driven
  diff is a *fact* once real session data exists; RSS mentions matched
  against ``LINEUP_KEYWORDS`` below are a *rumour* that can arrive days
  earlier (this project's own live example: Hadjar's Monza fitness was
  in news coverage on the Tuesday, with no fetchable session data to
  confirm it until Thursday's FP1 at the earliest).
- **Upgrade packages** -- ``news/upgrades.py``'s existing tracker,
  unchanged.
- **Penalties/reliability** -- ``news/bulletins.py``'s existing
  ``KEYWORDS`` list (penalties, grid drops, power unit/gearbox issues,
  stewards, investigations), reused as-is rather than redefined, but
  applied across upgrades.py's four-source feed list instead of
  bulletins.py's single F1.com feed -- broader coverage for free, since
  ``fetch_raw_entries`` already pulls all four sources once for every
  category below.

One raw fetch, classified into three categories -- not three separate
fetches of the same feeds.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from f1_fantasy.api.models import Model
from f1_fantasy.calendar import RaceEvent
from f1_fantasy.news.bulletins import KEYWORDS as PENALTY_KEYWORDS
from f1_fantasy.news.lineup_watch import LineupChange
from f1_fantasy.news.upgrades import (
    FEED_URLS,
    RawEntry,
    _attribute_constructors,
    attribute_round,
    fetch_raw_entries,
)

log = logging.getLogger(__name__)

#: Specific enough to mean a driver-availability story, not generic
#: race-result prose -- "return" and "seat" alone would over-match, so
#: they're kept only as compounds. A starting list tuned by inspection of
#: this session's own real example (Hadjar/Tsunoda/Lawson), not
#: backtested -- revisit after a few weeks of real digest output, same
#: honesty standard as news/upgrades.py's own KEYWORDS.
LINEUP_KEYWORDS: tuple[str, ...] = (
    "replaces", "replaced by", "stand-in", "steps in", "stepping in",
    "ruled out", "will miss", "to miss", "fit to race", "fitness test",
    "return to the grid", "reserve driver", "seat swap", "driver swap",
    "promoted to", "called up", "injury", "injured", "fracture",
)


class DigestMention(Model):
    category: str  # "lineup" | "upgrade" | "penalty"
    source: str
    title: str
    summary: str = ""
    link: str = ""
    published_at: datetime | None = None
    constructors: tuple[str, ...] = ()
    attributed_round: int | None = None


def _matches(text: str, keywords: tuple[str, ...]) -> tuple[str, ...]:
    lowered = text.lower()
    return tuple(kw for kw in keywords if kw in lowered)


def classify_entries(entries: list[RawEntry]) -> list[DigestMention]:
    """Every entry that matches at least one category's keywords, tagged
    per category -- one entry can appear more than once if it genuinely
    matches more than one category (e.g. an injury story that also
    mentions a specific upgrade decision), since that's real, not
    duplication."""
    from f1_fantasy.news.upgrades import KEYWORDS as UPGRADE_KEYWORDS

    categories = (
        ("lineup", LINEUP_KEYWORDS),
        ("upgrade", UPGRADE_KEYWORDS),
        ("penalty", PENALTY_KEYWORDS),
    )
    out: list[DigestMention] = []
    for entry in entries:
        text = f"{entry.title} {entry.summary}"
        for category, keywords in categories:
            if _matches(text, keywords):
                out.append(
                    DigestMention(
                        category=category,
                        source=entry.source,
                        title=entry.title,
                        summary=entry.summary,
                        link=entry.link,
                        published_at=entry.published_at,
                        constructors=_attribute_constructors(text),
                    )
                )
    return out


def attribute_mentions(mentions: list[DigestMention], events: list[RaceEvent]) -> list[DigestMention]:
    """Reuses upgrades.py's nearest-event attribution function directly --
    it only ever reads ``mention.published_at``, so it works unchanged
    against a DigestMention despite being typed for UpgradeMention."""
    return [m.model_copy(update={"attributed_round": attribute_round(m, events)}) for m in mentions]


class DailyDigest(Model):
    season: int
    round_number: int | None
    generated_at: datetime
    lineup_mentions: list[DigestMention] = []
    upgrade_mentions: list[DigestMention] = []
    penalty_mentions: list[DigestMention] = []
    confirmed_lineup_changes: list[str] = []  # LineupChange.headline strings -- data-driven, not news
    lineup_watch_note: str = ""  # why confirmed_lineup_changes is empty, when it is


def build_digest(
    season: int,
    round_number: int | None,
    events: list[RaceEvent],
    *,
    confirmed_changes: list[LineupChange] | None = None,
    lineup_watch_note: str = "",
) -> DailyDigest:
    """The one-call entry point: fetch, classify, attribute, assemble.

    ``confirmed_changes``/``lineup_watch_note`` are passed in rather than
    computed here, since lineup_watch.watch_round needs two specific
    round numbers with real session data and a caller-specific decision
    about which two rounds to diff -- this function stays a pure
    news-plus-assembly step, matching news/upgrades.py's own
    track_upgrades split between fetching and the calendar-dependent
    attribution step.
    """
    entries = fetch_raw_entries(FEED_URLS)
    mentions = attribute_mentions(classify_entries(entries), events)

    return DailyDigest(
        season=season,
        round_number=round_number,
        generated_at=datetime.now(timezone.utc),
        lineup_mentions=[m for m in mentions if m.category == "lineup"],
        upgrade_mentions=[m for m in mentions if m.category == "upgrade"],
        penalty_mentions=[m for m in mentions if m.category == "penalty"],
        confirmed_lineup_changes=[c.headline for c in (confirmed_changes or [])],
        lineup_watch_note=lineup_watch_note,
    )
