"""Reliability-gated race-order prediction: the falsification test for task #15.

**Result: falsified.** Walk-forward over 2026 rounds 2-12, discounting the
form-predicted grid by constructor DNF probability scores *worse* than the
plain grid rank -- mean Spearman 0.624 vs the 0.636 baseline (mean rank MAE
3.88 vs 3.77). It helps on 4 of 11 rounds (R2, R5, R6, R11) and hurts on the
rest, worst on R3 and R4 (-0.10, -0.09). Net negative. The DNF-rate signal is
real (reliability.py's constructor split is well evidenced) but at 5-46%
probabilities for most teams, the expected-value shift it applies to a
driver's rank is small, and on a round where that driver does *not* retire
(the 55-95% case, depending on team) the shift is pure noise against a
rank-based metric -- it loses more on the rounds it's wrong about than it
gains on the rounds it's right about. Kept as a documented null result, not
removed, per this project's practice of reporting negative findings honestly.

``predict_grid_rank`` is the baseline -- form's predicted qualifying order,
standing in for race order because no walk-forward race-pace model in this
project beats it. ``predict_race_order`` blends that rank with each driver's
constructor's DNF risk: in expectation, a driver finishes at their
grid-predicted rank with probability (1 - p_dnf), and last with probability
p_dnf (a DNF scores -20 and, for ranking purposes, is the worst outcome on
track).
"""

from __future__ import annotations

from f1_fantasy.predict.evaluate import RoundScore, summarize, walk_forward
from f1_fantasy.predict.form import rolling_form
from f1_fantasy.predict.reliability import constructor_history, dnf_probability
from f1_fantasy.results import fetch_qualifying, fetch_race_results


def _grid_rank(season: int, train_rounds: list[int]) -> dict[str, int]:
    form = rolling_form(season, train_rounds)
    ranked = sorted(form, key=form.get)
    return {driver: index + 1 for index, driver in enumerate(ranked)}


def predict_grid_rank(season: int, train_rounds: list[int], target_round: int) -> dict[str, float]:
    """Baseline predictor: form-predicted grid rank, taken as the race-order guess."""
    return {driver: float(rank) for driver, rank in _grid_rank(season, train_rounds).items()}


def predict_race_order(season: int, train_rounds: list[int], target_round: int) -> dict[str, float]:
    """Grid rank discounted by the driver's constructor's DNF probability.

    Constructor lookup uses the most recent prior round's qualifying entry
    list, not the target round's -- the lineup is known before a weekend
    starts, but peeking at the target round's own feed would leak it.
    """
    grid_rank = _grid_rank(season, train_rounds)
    if not grid_rank:
        return {}
    field_size = len(grid_rank)

    constructor_of = {q.driver_code: q.constructor for q in fetch_qualifying(season, train_rounds[-1])}
    history = constructor_history(season, train_rounds)

    predicted = {}
    for driver, rank in grid_rank.items():
        constructor = constructor_of.get(driver)
        p_dnf = dnf_probability(history.get(constructor))
        predicted[driver] = (1 - p_dnf) * rank + p_dnf * field_size
    return predicted


def actual_race_positions(season: int, round_number: int) -> dict[str, int]:
    """Actual classified finishing order, DNFs included at whatever
    classification position the results feed assigns them."""
    return {
        result.driver_code: result.position
        for result in fetch_race_results(season, round_number)
        if result.position is not None
    }


def backtest_race_order(season: int, rounds: list[int]) -> dict:
    """Walk-forward comparison: does DNF-discounting beat the plain form-predicted grid?"""

    def actual(round_number: int) -> dict[str, int]:
        return actual_race_positions(season, round_number)

    baseline_scores = walk_forward(
        rounds, lambda train, target: predict_grid_rank(season, train, target), actual
    )
    reliability_scores = walk_forward(
        rounds, lambda train, target: predict_race_order(season, train, target), actual
    )
    return {
        "baseline_grid_rank": _serialise(baseline_scores),
        "reliability_gated": _serialise(reliability_scores),
    }


def _serialise(scores: list[RoundScore]) -> dict:
    return {
        "per_round": [
            {"round": s.round_number, "n_drivers": s.n_drivers, "spearman": s.spearman, "mae": s.mae_positions}
            for s in scores
        ],
        "summary": summarize(scores),
    }
