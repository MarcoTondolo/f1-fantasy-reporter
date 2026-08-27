"""Proving the scoring table against the game's own published points.

This is the gate the rest of the phase depends on: if reconstructed points do
not match what F1 Fantasy actually awarded, every downstream expected-points
number is built on a wrong rule.

Jolpica carries no lap-by-lap positions, so **overtakes cannot be computed
from it**. Rather than guess, reconciliation is done on everything else and
the leftover is reported as a residual. If the scoring table is right, that
residual should be a small non-negative integer for every driver -- exactly
what an overtake count looks like. A residual that is negative, fractional,
or large means a rule is wrong, not that someone overtook a lot.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from f1_fantasy.predict.scoring import PointsBreakdown, qualifying_points, race_points
from f1_fantasy.results import fetch_qualifying, fetch_race_results

log = logging.getLogger(__name__)

DRIVER_FEED = "https://fantasy.formula1.com/feeds/drivers/{race_id}_en.json"

#: Lawson's cumulative totals were restated mid-season after a seat change,
#: leaving his feed figures internally inconsistent (his session points sum to
#: 140 against a reported season total of 12). Excluded as a known data
#: artifact rather than treated as a scoring-rule failure.
KNOWN_INCONSISTENT_DRIVERS = frozenset({"LAW"})


@dataclass
class DriverReconciliation:
    round_number: int
    driver: str
    actual_qualifying: float
    expected_qualifying: float
    actual_race: float
    expected_race_without_overtakes: float

    @property
    def qualifying_matches(self) -> bool:
        return abs(self.actual_qualifying - self.expected_qualifying) < 0.01

    @property
    def residual(self) -> float:
        """Actual minus expected race points: the unmodelled remainder."""
        return self.actual_race - self.expected_race_without_overtakes

    @property
    def residual_looks_like_overtakes(self) -> bool:
        r = self.residual
        return r >= -0.01 and abs(r - round(r)) < 0.01


def _fetch_feed_payload(race_id: int, *, cache_dir: Path | str | None = None) -> dict:
    """The raw driver-feed payload (driver *and* constructor rows together),
    fetched once and shared by fetch_driver_feed and
    fetch_constructor_feed_rows. Cached on disk when *cache_dir* is given,
    since these are immutable once a round has been scored.
    """
    cache_path = None
    if cache_dir is not None:
        cache_path = Path(cache_dir) / f"ffeed_{race_id}.json"
        if cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))

    with urllib.request.urlopen(DRIVER_FEED.format(race_id=race_id), timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def fetch_driver_feed(race_id: int, *, cache_dir: Path | str | None = None) -> dict[str, dict]:
    """Driver rows from the public fantasy feed, keyed by three-letter code.

    Public and unauthenticated.
    """
    payload = _fetch_feed_payload(race_id, cache_dir=cache_dir)
    rows = payload.get("Data", payload).get("Value") or []
    return {
        row["DriverTLA"]: row
        for row in rows
        if row.get("PositionName") == "DRIVER" and row.get("DriverTLA")
    }


def fetch_constructor_feed_rows(race_id: int, *, cache_dir: Path | str | None = None) -> dict[str, dict]:
    """Constructor rows from the same feed, keyed by full team name (e.g.
    "Red Bull Racing") -- matching the naming Jolpica's own Constructor.name
    field uses, so callers can join against form.py/reliability.py's
    constructor keys directly rather than the feed's own 3-letter team code.
    """
    payload = _fetch_feed_payload(race_id, cache_dir=cache_dir)
    rows = payload.get("Data", payload).get("Value") or []
    return {
        row["FUllName"]: row
        for row in rows
        if row.get("PositionName") == "CONSTRUCTOR" and row.get("FUllName")
    }


def _as_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def reconstruct_points(season: int, round_number: int) -> dict[str, PointsBreakdown]:
    """Per-driver qualifying+race PointsBreakdown from Jolpica alone.

    No overtakes (Jolpica carries no lap-by-lap positions -- see the module
    docstring) and no public-feed dependency, unlike reconcile_round. This is
    the path backtest_points.py uses for 2024/2025, where the public fantasy
    feed does not exist at all (it only ever serves the current season).
    """
    quali = {q.driver_code: q for q in fetch_qualifying(season, round_number)}
    race = fetch_race_results(season, round_number)
    winner_laps = max((r.laps for r in race), default=0)

    breakdowns: dict[str, PointsBreakdown] = {}
    for result in race:
        q = quali.get(result.driver_code)
        breakdown = race_points(
            grid=result.grid,
            position=result.position,
            status=result.status,
            overtakes=0,
            fastest_lap=result.fastest_lap,
            # Driver of the Day is fan-voted and appears in no results feed --
            # see driver_of_the_day_candidates for how it's recovered instead.
            driver_of_the_day=False,
            laps=result.laps,
            winner_laps=winner_laps,
        )
        breakdown.qualifying = qualifying_points(
            q.position if q else None, set_a_time=q.set_a_time if q else False
        )
        breakdowns[result.driver_code] = breakdown
    return breakdowns


def reconcile_round(
    season: int, round_number: int, *, cache_dir: Path | str | None = None
) -> list[DriverReconciliation]:
    """Reconstruct one round's points and compare against the feed."""
    feed = fetch_driver_feed(round_number, cache_dir=cache_dir)
    reconstructed = reconstruct_points(season, round_number)

    out: list[DriverReconciliation] = []
    for code, row in feed.items():
        if code in KNOWN_INCONSISTENT_DRIVERS:
            continue
        breakdown = reconstructed.get(code)
        if breakdown is None:
            continue

        out.append(
            DriverReconciliation(
                round_number=round_number,
                driver=code,
                actual_qualifying=_as_float(row.get("QualifyingPoints")),
                expected_qualifying=breakdown.qualifying,
                actual_race=_as_float(row.get("RacePoints")),
                expected_race_without_overtakes=breakdown.total - breakdown.qualifying,
            )
        )
    return out


def driver_of_the_day_candidates(
    rows: list[DriverReconciliation], overtake_points: dict[str, float]
) -> list[str]:
    """Drivers whose residual exceeds their overtake points by the DOTD award.

    Driver of the Day is fan-voted and published in no results feed, but it
    is recoverable: once position, positions gained, DNF, fastest lap and
    overtakes are all accounted for, exactly one driver per round is left
    over by exactly 10 points. Verified across rounds 3, 8, 10 and 11, which
    identified Piastri, Verstappen, Leclerc and Verstappen respectively.
    """
    from f1_fantasy.predict.scoring import DRIVER_OF_THE_DAY_POINTS

    found = []
    for row in rows:
        overtakes = overtake_points.get(row.driver)
        if overtakes is None:
            continue
        if abs((row.residual - overtakes) - DRIVER_OF_THE_DAY_POINTS) < 0.01:
            found.append(row.driver)
    return found


def summarise(rows: list[DriverReconciliation]) -> dict:
    quali_ok = sum(1 for r in rows if r.qualifying_matches)
    plausible = sum(1 for r in rows if r.residual_looks_like_overtakes)
    residuals = [r.residual for r in rows]
    return {
        "drivers": len(rows),
        "qualifying_exact": quali_ok,
        "qualifying_mismatches": len(rows) - quali_ok,
        "residual_plausible_as_overtakes": plausible,
        "residual_implausible": len(rows) - plausible,
        "residual_min": min(residuals) if residuals else None,
        "residual_max": max(residuals) if residuals else None,
    }
