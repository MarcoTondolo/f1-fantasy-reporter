"""Rolling season form: the cheapest predictor available, and a strong one.

Mean qualifying gap-to-best over a driver's prior rounds this season -- one
API call, no telemetry. In walk-forward backtesting across 2026 rounds 2-12
it scores 0.898 mean Spearman against actual qualifying order, statistically
indistinguishable from the entire FastF1 practice-pace pipeline (0.905). Every
more elaborate predictor in this project is measured against this baseline,
not against zero.
"""

from __future__ import annotations

import statistics

from f1_fantasy.results import fetch_qualifying


def _lap_seconds(value: str) -> float | None:
    """Parse Jolpica's "M:SS.sss" lap time format."""
    value = value.strip()
    if not value:
        return None
    minutes, sep, seconds = value.partition(":")
    try:
        return float(minutes) * 60 + float(seconds) if sep else float(minutes)
    except ValueError:
        return None


def qualifying_gap_pct(season: int, round_number: int) -> dict[str, float]:
    """Each driver's best qualifying lap as a % gap to the session's fastest.

    Uses whichever of Q1/Q2/Q3 a driver's best time came from -- a driver
    knocked out in Q1 is compared on their Q1 best, same as everyone else.
    """
    times: dict[str, float] = {}
    for result in fetch_qualifying(season, round_number):
        laps = [t for t in (_lap_seconds(result.q1), _lap_seconds(result.q2), _lap_seconds(result.q3)) if t]
        if laps:
            times[result.driver_code] = min(laps)
    if not times:
        return {}
    best = min(times.values())
    return {driver: (time - best) / best * 100.0 for driver, time in times.items()}


def rolling_form(season: int, prior_rounds: list[int]) -> dict[str, float]:
    """Mean qualifying gap % across *prior_rounds*, per driver seen in any of
    them. A driver absent from later rounds (e.g. replaced mid-season) still
    keeps their earlier rounds' average -- this is a season-to-date form, not
    a most-recent-round snapshot."""
    accumulated: dict[str, list[float]] = {}
    for round_number in prior_rounds:
        for driver, gap in qualifying_gap_pct(season, round_number).items():
            accumulated.setdefault(driver, []).append(gap)
    return {driver: statistics.mean(gaps) for driver, gaps in accumulated.items()}
