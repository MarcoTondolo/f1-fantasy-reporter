"""Rolling season form: the cheapest predictor available, and a strong one.

Mean qualifying gap-to-best over a driver's prior rounds this season -- one
API call, no telemetry. In walk-forward backtesting across 2026 rounds 2-12
it scores 0.898 mean Spearman against actual qualifying order, statistically
indistinguishable from the entire FastF1 practice-pace pipeline (0.905). Every
more elaborate predictor in this project is measured against this baseline,
not against zero.

**This number is specific to 2026, not general.** The identical code run
against 2024 and 2025 (see predict/multi_season.py) scores 0.714 and 0.702
respectively -- still a strong baseline, but a real and consistent gap below
2026, not sampling noise (2024/25 each have double 2026's evaluated rounds,
which should shrink the gap if it were noise, not preserve it). Likely cause:
2026's new regulations produced a wider, more entrenched spread of car
performance in year one than the settled 2024-25 grids, which a pure ranking
signal like rolling form tracks more easily. Treat "0.898" as a 2026 result;
treat "~0.7" as the number to expect in a settled-regulations season.
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
    a most-recent-round snapshot.

    **Tested and rejected: recency-weighting this average.** A driver on a
    genuine upswing (new upgrade, found form) is diluted by early-season
    rounds this unweighted mean treats identically -- confirmed live for
    Norris, whose round 15 rolling_form ranked him 5th (0.487% gap) despite
    being the outright fastest qualifier (0.000% gap) in three of the five
    most recent rounds; a last-3-rounds-only average put him 2nd (0.115%),
    just behind Antonelli. That is a real, demonstrable case. It is not,
    however, evidence that recency-weighting the *default* predictor is a net
    improvement: walk-forward exponential decay at every half-life from 2 to
    20 rounds, and hard windows of the last 3-6 rounds, were run through
    ``evaluate.walk_forward`` against real qualifying order for all three
    available seasons. 2026 and 2025 gained marginally (+0.002 to +0.009
    Spearman at the best-performing half-lives) while 2024 lost consistently
    and by more (-0.02 at the gentlest decay tested, -0.07 at the most
    aggressive) -- a real, repeated cost in the one season with no confirmed
    upswing story to explain it, not sampling noise. Hard windows were worse
    than unweighted in every season tried. Net: a wash-to-slight-negative
    change dressed up as an obviously-good idea, so the default stays
    unweighted -- same standard this project applies to every other tested
    variant (the reliability-gating null, the places-gained-redistribution
    reversion). A comparable open-source tool (dnpjr/f1_fantasy_optimizer)
    exposes exactly this kind of decay as a user-tunable knob with no
    default and states its own forecasts carry no "demonstrated
    predictive-accuracy guarantees" -- consistent with recency-weighting
    being a real, unsettled trade-off industry-wide, not a solved problem
    this project is uniquely missing.

    ``recent_form_gap_pct`` below exposes the last-N-rounds number as a
    transparent secondary signal instead -- worth showing next to this
    season-to-date average so a real form swing like Norris's is visible,
    without silently corrupting the backtested default everything else in
    ``predict/`` is built on.
    """
    accumulated: dict[str, list[float]] = {}
    for round_number in prior_rounds:
        for driver, gap in qualifying_gap_pct(season, round_number).items():
            accumulated.setdefault(driver, []).append(gap)
    return {driver: statistics.mean(gaps) for driver, gaps in accumulated.items()}


#: How many of the most recent rounds recent_form_gap_pct averages.
RECENT_FORM_WINDOW = 3


def recent_form_gap_pct(
    season: int, prior_rounds: list[int], *, window: int = RECENT_FORM_WINDOW
) -> dict[str, float]:
    """Mean qualifying gap % across only the last *window* of *prior_rounds*.

    A display-only companion to ``rolling_form``'s season-to-date average --
    see that function's docstring for why this is not used as the default
    predictor. Useful for surfacing a driver whose recent pace has clearly
    diverged from their season average (e.g. Norris, round 15: season 0.487%
    / 5th vs last-3 0.115% / 2nd) so a reader can judge the discrepancy
    themselves rather than have it silently averaged away.
    """
    return rolling_form(season, prior_rounds[-window:])
