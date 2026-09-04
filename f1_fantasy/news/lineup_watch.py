"""Data-driven lineup-change detection.

News RSS mentions (news/upgrades.py's fetch mechanism, extended for lineup
keywords in news/digest.py) catch a *rumoured* change before any session
data exists -- exactly the gap this project hit live this session: Hadjar's
Monza fitness wasn't decided in any fetchable session data as of Tuesday,
only in news coverage. This module is the complementary, data-driven half:
once real session data *does* exist, it compares two entry lists directly
and flags exactly what changed, with no judgement calls -- a fact, not a
rumour.

Two real data sources, in the order they become available before a race
weekend:

1. Practice session entry list (FastF1) -- the earliest automated signal,
   available from FP1 onward, before qualifying.
2. Qualifying classification (Jolpica, via results.fetch_qualifying) --
   this project's usual constructor_of source everywhere else, available
   from Saturday onward.

Both reduce to the same shape (``driver_code -> constructor``), so
``detect_lineup_changes`` is decoupled from where a mapping came from --
it works equally well diffing two practice sessions, two qualifying
sessions, or a session against a hand-entered mapping (e.g. from a news
check like this session's Hadjar/Tsunoda/Lawson correction).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from f1_fantasy.pace.sessions import SessionUnavailable, _ensure_cache
from f1_fantasy.results import fetch_qualifying

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LineupChange:
    driver: str
    round_number: int
    previous_constructor: str | None
    new_constructor: str | None  # None = driver absent in the newer mapping
    change_type: str  # "team_change" | "absence" | "new_entrant"

    @property
    def headline(self) -> str:
        if self.change_type == "absence":
            return f"{self.driver} not entered this round (was {self.previous_constructor})"
        if self.change_type == "new_entrant":
            return f"{self.driver} newly entered, driving for {self.new_constructor}"
        return f"{self.driver} moved from {self.previous_constructor} to {self.new_constructor}"


def constructor_of_for_round(season: int, round_number: int) -> dict[str, str]:
    """The real, per-round driver -> constructor mapping from Jolpica's
    qualifying classification -- this project's usual constructor_of
    source (points.py, simulate.py, race.py, etc.), reused here rather
    than re-derived."""
    return {q.driver_code: q.constructor for q in fetch_qualifying(season, round_number)}


def constructor_of_from_practice(season: int, round_number: int, session: str = "FP1") -> dict[str, str]:
    """The same shape, but from a practice session's entry list -- the
    earliest automated signal, available before qualifying. Follows
    pace/hers.py's own ``_load_quali_laps`` precedent for loading a
    session directly rather than through results.py, which only ever
    carries qualifying/race (Jolpica has no practice data at all).
    """
    import fastf1

    _ensure_cache()
    try:
        fastf1_session = fastf1.get_session(season, round_number, session)
        fastf1_session.load(laps=True, telemetry=False, weather=False, messages=False)
        # ``.load()`` can return without raising even when the session's data
        # genuinely isn't available yet -- confirmed live (round 13 FP1,
        # requested before FastF1's live-timing mirror had it): FastF1
        # swallows its own per-category SessionNotAvailableError internally
        # and just logs a warning, leaving ``_laps`` unset. Accessing
        # ``.laps`` is what actually raises in that case (DataNotLoadedError)
        # -- kept inside this same try so it converts to SessionUnavailable
        # too, instead of crashing the caller uncaught.
        laps = fastf1_session.laps
    except Exception as exc:  # FastF1 raises several distinct types for "no such session yet"
        raise SessionUnavailable(f"{session} R{round_number} {season}: {exc}") from exc

    if laps is None or laps.empty:
        raise SessionUnavailable(f"{session} R{round_number} {season}: no lap data")

    mapping: dict[str, str] = {}
    for driver in laps["Driver"].unique():
        driver_laps = laps.pick_drivers(driver)
        if driver_laps.empty:
            continue
        team = driver_laps.iloc[0]["Team"]
        if team:
            mapping[driver] = team
    return mapping


def detect_lineup_changes(
    round_number: int, before: dict[str, str], after: dict[str, str]
) -> list[LineupChange]:
    """Diffs two driver->constructor mappings, regardless of source.

    Symmetric in what it reports: a driver present in both but at a
    different constructor is a team_change; present before but missing
    after is an absence; present after but missing before is a
    new_entrant. No mapping is treated as more authoritative than the
    other -- the caller decides what "before" and "after" mean.
    """
    changes: list[LineupChange] = []
    for driver, team in before.items():
        if driver not in after:
            changes.append(LineupChange(driver, round_number, team, None, "absence"))
        elif after[driver] != team:
            changes.append(LineupChange(driver, round_number, team, after[driver], "team_change"))
    for driver, team in after.items():
        if driver not in before:
            changes.append(LineupChange(driver, round_number, None, team, "new_entrant"))
    return changes


def watch_round(season: int, previous_round: int, target_round: int) -> list[LineupChange]:
    """The one-call entry point once target_round's qualifying exists:
    confirmed changes vs. the last known-good round. Raises whatever
    fetch_qualifying raises if target_round hasn't been classified yet --
    callers watching for changes ahead of a session should use
    constructor_of_from_practice directly instead, which degrades via
    SessionUnavailable rather than an unhandled fetch error.
    """
    before = constructor_of_for_round(season, previous_round)
    after = constructor_of_for_round(season, target_round)
    return detect_lineup_changes(target_round, before, after)
