"""The pace-ranking model: fitting, prediction, and leave-one-round-out scoring.

Built from synthetic rounds with a known ground-truth relationship (position
is exactly a function of the features) so a broken fit or a broken
evaluation loop shows up as a correlation that isn't 1.0, rather than
against real data where the "correct" answer isn't known in advance.
"""

from __future__ import annotations

import pandas as pd
import pytest

from f1_fantasy.pace.model import fit_ols, leave_one_round_out, predict, summarize


def _perfect_round(n: int, offset: float = 0.0) -> pd.DataFrame:
    """Position is exactly rank(one_lap_gap_pct) -- a model that fits this
    correctly should predict it with perfect correlation."""
    return pd.DataFrame(
        {
            "one_lap_gap_pct": [offset + i * 0.1 for i in range(n)],
            "long_run_gap_pct": [offset + i * 0.2 for i in range(n)],
            "quali_position": list(range(1, n + 1)),
        }
    )


def test_fit_and_predict_recover_an_exact_linear_relationship():
    rows = pd.DataFrame({"one_lap_gap_pct": [0.0, 1.0, 2.0, 3.0], "quali_position": [1.0, 3.0, 5.0, 7.0]})

    coefficients = fit_ols(rows, "quali_position", ("one_lap_gap_pct",))
    predicted = predict(rows, coefficients, ("one_lap_gap_pct",))

    assert predicted == pytest.approx([1.0, 3.0, 5.0, 7.0])


def test_leave_one_round_out_scores_each_round_on_a_model_that_never_saw_it():
    rounds = {r: _perfect_round(8, offset=r) for r in range(1, 6)}
    event_names = {r: f"Round {r}" for r in rounds}

    evaluations = leave_one_round_out(rounds, "quali_position", event_names)

    assert len(evaluations) == 5
    assert {e.round_number for e in evaluations} == set(rounds)
    for e in evaluations:
        assert e.combined_spearman == pytest.approx(1.0)
        assert e.n_drivers == 8


def test_a_round_with_too_few_drivers_is_skipped_not_crashed_on():
    rounds = {1: _perfect_round(8), 2: _perfect_round(8, offset=1), 3: _perfect_round(2, offset=2)}
    event_names = {r: f"R{r}" for r in rounds}

    evaluations = leave_one_round_out(rounds, "quali_position", event_names)

    assert {e.round_number for e in evaluations} == {1, 2}


def test_summarize_averages_only_the_present_values():
    rounds = {r: _perfect_round(8, offset=r) for r in range(1, 4)}
    evaluations = leave_one_round_out(rounds, "quali_position", {r: "" for r in rounds})

    summary = summarize(evaluations)

    assert summary["rounds_evaluated"] == 3
    assert summary["mean_combined_spearman"] == pytest.approx(1.0)


def test_summarize_handles_no_evaluations_at_all():
    summary = summarize([])

    assert summary["rounds_evaluated"] == 0
    assert summary["mean_combined_spearman"] is None
