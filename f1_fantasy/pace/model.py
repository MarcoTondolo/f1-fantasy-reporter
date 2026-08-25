"""A pace-based ranking model, fit and evaluated across real rounds.

Deliberately not a black box: with ~12 rounds of data and two features, a
two-coefficient linear regression is the most complexity this data can
support without overfitting -- a bigger model would just be memorising 12
rounds' idiosyncrasies. The point being tested is a narrow, falsifiable
claim: does *how* a driver looked in practice (one-lap pace, long-run pace)
predict *where* they qualified and finished, better than one-lap pace alone
would predict it?

Evaluation is leave-one-round-out: fit on every round except one, predict
that one, repeat for each round, and only ever score a round on a model that
never saw it. A single split would risk the result being an artifact of
which round happened to be held out.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

FEATURE_COLUMNS = ("one_lap_gap_pct", "long_run_gap_pct")


@dataclass
class RoundEvaluation:
    round_number: int
    event_name: str
    n_drivers: int
    combined_spearman: float | None
    one_lap_only_spearman: float | None
    combined_mae: float | None
    one_lap_only_mae: float | None


def _design_matrix(rows: pd.DataFrame, columns: tuple[str, ...]) -> np.ndarray:
    return np.column_stack([np.ones(len(rows))] + [rows[c].to_numpy(dtype=float) for c in columns])


def fit_ols(rows: pd.DataFrame, target: str, columns: tuple[str, ...] = FEATURE_COLUMNS) -> np.ndarray:
    """Ordinary least squares: target ~ intercept + columns. Returns coefficients."""
    x = _design_matrix(rows, columns)
    y = rows[target].to_numpy(dtype=float)
    coefficients, *_ = np.linalg.lstsq(x, y, rcond=None)
    return coefficients


def predict(rows: pd.DataFrame, coefficients: np.ndarray, columns: tuple[str, ...] = FEATURE_COLUMNS) -> np.ndarray:
    return _design_matrix(rows, columns) @ coefficients


def _evaluate_predictions(rows: pd.DataFrame, predicted: np.ndarray, target: str) -> tuple[float | None, float | None]:
    actual = rows[target].to_numpy(dtype=float)
    if len(actual) < 3:
        return None, None
    correlation, _ = spearmanr(predicted, actual)
    predicted_rank = pd.Series(predicted).rank().to_numpy()
    actual_rank = pd.Series(actual).rank().to_numpy()
    mae = float(np.mean(np.abs(predicted_rank - actual_rank)))
    return (float(correlation) if not np.isnan(correlation) else None), mae


def leave_one_round_out(
    rounds: dict[int, pd.DataFrame], target: str, event_names: dict[int, str]
) -> list[RoundEvaluation]:
    """Fit on every round but one, evaluate on the held-out round, per round.

    *rounds* maps round number to a frame with FEATURE_COLUMNS plus *target*,
    one row per driver, already restricted to rows with no missing feature or
    target (a driver with no representative long-run stint, or a DNF with no
    classified race position, can't be scored and isn't silently imputed).
    """
    evaluations = []
    round_numbers = sorted(rounds)
    for held_out in round_numbers:
        train = pd.concat([rounds[r] for r in round_numbers if r != held_out], ignore_index=True)
        test = rounds[held_out]
        if len(train) < 10 or len(test) < 3:
            continue

        combined_coef = fit_ols(train, target, FEATURE_COLUMNS)
        combined_pred = predict(test, combined_coef, FEATURE_COLUMNS)
        combined_r, combined_mae = _evaluate_predictions(test, combined_pred, target)

        one_lap_coef = fit_ols(train, target, ("one_lap_gap_pct",))
        one_lap_pred = predict(test, one_lap_coef, ("one_lap_gap_pct",))
        one_lap_r, one_lap_mae = _evaluate_predictions(test, one_lap_pred, target)

        evaluations.append(
            RoundEvaluation(
                round_number=held_out,
                event_name=event_names.get(held_out, ""),
                n_drivers=len(test),
                combined_spearman=combined_r,
                one_lap_only_spearman=one_lap_r,
                combined_mae=combined_mae,
                one_lap_only_mae=one_lap_mae,
            )
        )
    return evaluations


def summarize(evaluations: list[RoundEvaluation]) -> dict:
    def _mean(values: list[float | None]) -> float | None:
        present = [v for v in values if v is not None]
        return float(np.mean(present)) if present else None

    return {
        "rounds_evaluated": len(evaluations),
        "mean_combined_spearman": _mean([e.combined_spearman for e in evaluations]),
        "mean_one_lap_only_spearman": _mean([e.one_lap_only_spearman for e in evaluations]),
        "mean_combined_mae": _mean([e.combined_mae for e in evaluations]),
        "mean_one_lap_only_mae": _mean([e.one_lap_only_mae for e in evaluations]),
    }
