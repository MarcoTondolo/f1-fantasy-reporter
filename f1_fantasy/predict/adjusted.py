"""Incident-adjusted reliability: a hypothesis, not an assumed improvement.

The simplest testable intervention, deliberately: a high-confidence
externally-caused DNF (a collision, per news/incidents.py's confidence-scored
classification) is *excluded* from the reliability tally entirely -- neither
a race nor a DNF for that constructor -- rather than reconstructed into an
estimated alternate finishing position. Mechanical, ambiguous, and
low-confidence DNFs count exactly as reliability.constructor_history already
counts them.

**Result: a second, stacked null.** Run against real 2026 rounds 1-12 via
backtest_adjusted_reliability: baseline grid-rank 0.636 mean Spearman,
raw reliability-gated (race.py, task #15) 0.624, and this
incident-adjusted version 0.623 -- statistically indistinguishable from the
raw version, and still below the baseline. race.py's plain reliability-
gating already failed to beat the grid-rank baseline by collapsing
"classified" and "DNF" into one discounted rank; swapping in a cleaner
reliability signal here keeps that same collapsing mechanism, and the
result confirms the *mechanism* was what didn't work, not the reliability
signal's honesty -- a cleaner DNF count doesn't fix a discounted-rank
formulation that was already the wrong way to use it. Reported plainly
rather than hidden, matching this project's practice for every other null
(task #15 itself, the form-baseline generalization gap, Gate A's weak
launch-performance correlation).
"""

from __future__ import annotations

from f1_fantasy.news.incidents import round_incidents
from f1_fantasy.predict import race
from f1_fantasy.predict.evaluate import summarize, walk_forward
from f1_fantasy.predict.reliability import ReliabilityRecord, dnf_probability
from f1_fantasy.predict.scoring import is_classified
from f1_fantasy.results import fetch_qualifying, fetch_race_results

#: A collision classification below this confidence is treated the same as
#: "unknown" -- left in the tally, not excluded. Matches reliability.py's
#: own practice of not forcing a low-evidence call into a downstream model.
DEFAULT_CONFIDENCE_THRESHOLD = 0.6


def adjusted_constructor_history(
    season: int, rounds: list[int], *, confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD
) -> dict[str, ReliabilityRecord]:
    """Identical to reliability.constructor_history, except a DNF classified
    "collision" at confidence >= confidence_threshold is excluded from the
    tally altogether -- treated as bad luck, not a car-reliability event.
    """
    tally: dict[str, list[int]] = {}
    for round_number in rounds:
        results = fetch_race_results(season, round_number)
        winner_laps = max((r.laps for r in results), default=0)
        incidents = round_incidents(season, round_number)
        for result in results:
            dnf = not is_classified(result.status, result.laps, winner_laps)
            if dnf:
                summary = incidents.get(result.driver_code)
                if (
                    summary is not None
                    and summary.retirement_cause == "collision"
                    and summary.retirement_confidence >= confidence_threshold
                ):
                    continue  # excluded entirely -- not this constructor's race, not its DNF
            races, dnfs = tally.get(result.constructor, [0, 0])
            races += 1
            if dnf:
                dnfs += 1
            tally[result.constructor] = [races, dnfs]
    return {constructor: ReliabilityRecord(constructor, races, dnfs) for constructor, (races, dnfs) in tally.items()}


def predict_race_order_adjusted(season: int, train_rounds: list[int], target_round: int) -> dict[str, float]:
    """A parallel copy of race.predict_race_order's plumbing, substituting
    adjusted_constructor_history -- kept separate rather than parameterising
    race.py, to avoid touching an already-frozen, already-validated
    null-result module."""
    grid_rank = race._grid_rank(season, train_rounds)
    if not grid_rank:
        return {}
    field_size = len(grid_rank)

    constructor_of = {q.driver_code: q.constructor for q in fetch_qualifying(season, train_rounds[-1])}
    history = adjusted_constructor_history(season, train_rounds)

    predicted = {}
    for driver, rank in grid_rank.items():
        constructor = constructor_of.get(driver)
        p_dnf = dnf_probability(history.get(constructor))
        predicted[driver] = (1 - p_dnf) * rank + p_dnf * field_size
    return predicted


def backtest_adjusted_reliability(season: int, rounds: list[int]) -> dict:
    """Walk-forward, exactly like race.py's own backtest: does excluding
    high-confidence collision DNFs from the reliability tally beat the
    plain grid-rank baseline, where the raw reliability-gated predictor did
    not? Reports all three side by side, honestly either way."""

    def actual(round_number: int) -> dict[str, int]:
        return race.actual_race_positions(season, round_number)

    baseline_scores = walk_forward(
        rounds, lambda train, target: race.predict_grid_rank(season, train, target), actual
    )
    raw_reliability_scores = walk_forward(
        rounds, lambda train, target: race.predict_race_order(season, train, target), actual
    )
    adjusted_scores = walk_forward(
        rounds, lambda train, target: predict_race_order_adjusted(season, train, target), actual
    )
    return {
        "baseline_grid_rank": summarize(baseline_scores),
        "raw_reliability_gated": summarize(raw_reliability_scores),
        "adjusted_reliability_gated": summarize(adjusted_scores),
    }
