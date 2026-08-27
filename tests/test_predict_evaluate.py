"""The walk-forward harness, against synthetic rounds with a known answer.

The property that matters here is the one leave-one-round-out doesn't have:
a round is only ever predicted from rounds strictly *before* it.
"""

from __future__ import annotations

import pytest

from f1_fantasy.predict.evaluate import score_predictions, summarize, walk_forward


def test_score_predictions_is_perfect_when_order_matches_exactly():
    predicted = {"A": 1.0, "B": 2.0, "C": 3.0}
    actual = {"A": 1, "B": 2, "C": 3}

    correlation, mae = score_predictions(predicted, actual)

    assert correlation == pytest.approx(1.0)
    assert mae == pytest.approx(0.0)


def test_score_predictions_is_negative_when_order_is_reversed():
    predicted = {"A": 1.0, "B": 2.0, "C": 3.0}
    actual = {"A": 3, "B": 2, "C": 1}

    correlation, _ = score_predictions(predicted, actual)

    assert correlation == pytest.approx(-1.0)


def test_score_predictions_ignores_drivers_missing_from_either_side():
    predicted = {"A": 1.0, "B": 2.0, "C": 3.0, "GHOST": 9.0}
    actual = {"A": 1, "B": 2, "C": 3}

    correlation, _ = score_predictions(predicted, actual)

    assert correlation == pytest.approx(1.0)


def test_score_predictions_returns_none_with_too_few_common_drivers():
    assert score_predictions({"A": 1.0}, {"A": 1}) == (None, None)


def test_walk_forward_never_trains_on_the_round_it_predicts():
    """A predict_fn that cheats by looking at the target round's own answer
    would ace every round; walk_forward must never hand it that chance."""
    actual_by_round = {1: {"A": 1, "B": 2}, 2: {"A": 2, "B": 1}, 3: {"A": 1, "B": 2}}
    seen_train_rounds = []

    def predict_fn(train_rounds, target_round):
        seen_train_rounds.append((tuple(train_rounds), target_round))
        return {"A": 1.0, "B": 2.0}

    walk_forward([1, 2, 3], predict_fn, lambda r: actual_by_round[r])

    assert seen_train_rounds == [((1,), 2), ((1, 2), 3)]


def test_walk_forward_skips_the_first_round_with_nothing_to_train_on():
    scores = walk_forward(
        [1, 2],
        lambda train, target: {"A": 1.0, "B": 2.0},
        lambda r: {"A": 1, "B": 2},
    )

    assert [s.round_number for s in scores] == [2]


def test_walk_forward_skips_a_round_the_predictor_declines_to_score():
    """An empty prediction (e.g. no prior-round data yet for these drivers)
    is skipped rather than crashing the sweep."""
    scores = walk_forward(
        [1, 2, 3],
        lambda train, target: {} if target == 2 else {"A": 1.0, "B": 2.0},
        lambda r: {"A": 1, "B": 2},
    )

    assert [s.round_number for s in scores] == [3]


def test_summarize_averages_only_present_values():
    from f1_fantasy.predict.evaluate import RoundScore

    scores = [
        RoundScore(1, 5, 1.0, 0.0),
        RoundScore(2, 5, 0.5, 1.0),
        RoundScore(3, 2, None, None),
    ]

    summary = summarize(scores)

    assert summary["rounds_evaluated"] == 3
    assert summary["mean_spearman"] == pytest.approx(0.75)
    assert summary["mean_mae"] == pytest.approx(0.5)


def test_summarize_handles_no_scores_at_all():
    assert summarize([])["mean_spearman"] is None
