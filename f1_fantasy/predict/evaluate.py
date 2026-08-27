"""Walk-forward evaluation: score a round only on a model fit before it ran.

Distinct from ``pace/model.py``'s leave-one-round-out, which is safe for
practice-session features that only ever describe the round they came from.
The moment a feature is a *season-to-date* form (mean qualifying gap over
prior rounds, a reliability rate, anything cumulative), leave-one-round-out
leaks the future: "every round except round 5" includes rounds 6-12, so
predicting round 5 with knowledge of round 11 is not a fair test. Walk-forward
fits on rounds ``1..N-1`` only, for every ``N`` in the sequence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd
from scipy.stats import spearmanr


@dataclass
class RoundScore:
    round_number: int
    n_drivers: int
    spearman: float | None
    mae_positions: float | None


def score_predictions(
    predicted: dict[str, float], actual: dict[str, int]
) -> tuple[float | None, float | None]:
    """Spearman correlation and mean absolute rank error between a predicted
    score per driver (lower predicted = better) and an actual position."""
    drivers = [d for d in predicted if d in actual]
    if len(drivers) < 3:
        return None, None

    predicted_values = [predicted[d] for d in drivers]
    actual_values = [actual[d] for d in drivers]
    correlation, _ = spearmanr(predicted_values, actual_values)

    predicted_rank = pd.Series(predicted_values).rank().to_numpy()
    actual_rank = pd.Series(actual_values).rank().to_numpy()
    mae = float(np.mean(np.abs(predicted_rank - actual_rank)))

    return (float(correlation) if not np.isnan(correlation) else None), mae


def walk_forward(
    rounds: list[int],
    predict_fn: Callable[[list[int], int], dict[str, float]],
    actual_fn: Callable[[int], dict[str, int]],
) -> list[RoundScore]:
    """Evaluate *predict_fn* walk-forward across *rounds*.

    ``predict_fn(train_rounds, target_round) -> {driver: predicted_score}``
    is called with only the rounds strictly before ``target_round`` -- the
    first round in the sequence is therefore never evaluated, since there is
    nothing yet to train on. ``actual_fn(round_number) -> {driver: position}``
    supplies what actually happened.
    """
    results = []
    for index, round_number in enumerate(rounds):
        train_rounds = rounds[:index]
        if not train_rounds:
            continue
        predicted = predict_fn(train_rounds, round_number)
        if not predicted:
            continue
        actual = actual_fn(round_number)
        correlation, mae = score_predictions(predicted, actual)
        results.append(RoundScore(round_number, len(actual), correlation, mae))
    return results


def summarize(scores: list[RoundScore]) -> dict:
    correlations = [s.spearman for s in scores if s.spearman is not None]
    maes = [s.mae_positions for s in scores if s.mae_positions is not None]
    return {
        "rounds_evaluated": len(scores),
        "mean_spearman": float(np.mean(correlations)) if correlations else None,
        "mean_mae": float(np.mean(maes)) if maes else None,
    }
