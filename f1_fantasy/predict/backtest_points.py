"""The integrative gate for Phase 8: walk-forward expected-points accuracy.

Per season: mean absolute error and rank correlation between
``points.build_round_distributions``'s predicted mean and each driver's real
round total, and how often the model's #1 predicted driver actually finished
in the real top 3. 2026 also gets the full realised-points/budget-growth
optimiser comparison from ``optimise.backtest_optimiser``, since only 2026
has real recorded prices to score budget growth against at all.

2024/2025 use ``reconcile.reconstruct_points`` (Jolpica-only, no overtakes,
no public feed) rather than the real driver feed, which -- like every other
2024/2025 path in this project (prices.py, optimise.py's budget-growth gate)
-- does not exist outside the current season. Every number below is
reported per season, honestly, rather than forcing a 2024/2025 figure
(a budget-growth number) that cannot exist.

**The ``ProjectedGamedayPoints`` benchmark this module still computes
(``benchmark_projected_mean_mae``/``_spearman``) is not usable, and this was
checked before trusting it, not assumed.** The plan called for comparing
against the game's own projection, but the public feed does not retain a
pre-race snapshot of it: once a round is scored, ``ProjectedGamedayPoints``
is silently overwritten to exactly equal the real ``GamedayPoints`` for
every driver (confirmed live, 2026 round 9 -- every single row's two fields
are byte-identical). This is the same class of staleness this project has
hit before (the ``AdditionalStats`` round-5/7 staleness bug in Gate 1) --
there is no way to recover the field's actual pre-race value from this feed
after the fact, so the benchmark always reports a perfect MAE of 0.0 and
Spearman of 1.0, which is a data artifact, not the game's real accuracy.
Left in the output (mechanically correct given what the feed exposes) but
never treated as a real comparison in this module's own summary.

**Result, run against all three seasons (n_samples=500):** points.py's
expected-points model scores mean MAE ~11.6-13.2 points and mean Spearman
~0.43-0.46 per round, consistent across all three seasons -- unlike
form.py's quali baseline, this one does *not* show a 2026-specific gap.
Top-pick top-3 hit rate varies more: 52% (2024), 78% (2025), 55% (2026).
Moderate, real accuracy -- reported as such, not oversold.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy.stats import spearmanr

from f1_fantasy.predict import points as points_module
from f1_fantasy.predict.optimise import backtest_optimiser
from f1_fantasy.predict.reconcile import fetch_driver_feed, reconstruct_points


def _round_ground_truth(
    season: int, round_number: int, *, cache_dir: Path | str | None = None
) -> tuple[dict[str, float], dict[str, float] | None]:
    """(actual_points, projected_points_or_None) for one round.

    projected_points is the game's own ProjectedGamedayPoints, available
    only for 2026 (the public feed doesn't exist for earlier seasons).
    """
    if season == 2026:
        try:
            feed = fetch_driver_feed(round_number, cache_dir=cache_dir)
        except Exception:  # noqa: BLE001 -- a round the public feed doesn't have yet shouldn't abort the sweep
            return {}, None
        if not feed:
            return {}, None
        actual = {code: float(row.get("GamedayPoints") or 0) for code, row in feed.items()}
        projected = {code: float(row.get("ProjectedGamedayPoints") or 0) for code, row in feed.items()}
        return actual, projected

    breakdowns = reconstruct_points(season, round_number)
    return {code: breakdown.total for code, breakdown in breakdowns.items()}, None


def _mae_and_correlation(predicted: dict[str, float], actual: dict[str, float]) -> tuple[float | None, float | None]:
    drivers = [d for d in predicted if d in actual]
    if len(drivers) < 3:
        return None, None
    predicted_values = np.array([predicted[d] for d in drivers])
    actual_values = np.array([actual[d] for d in drivers])
    mae = float(np.mean(np.abs(predicted_values - actual_values)))
    correlation, _ = spearmanr(predicted_values, actual_values)
    return mae, (float(correlation) if not np.isnan(correlation) else None)


def backtest_points_season(
    season: int, rounds: list[int], *, cache_dir: Path | str | None = None, n_samples: int = 500
) -> dict:
    per_round = []
    for index, target_round in enumerate(rounds):
        train_rounds = rounds[:index]
        if not train_rounds:
            continue
        predicted = points_module.build_round_distributions(season, train_rounds, target_round, n_samples=n_samples)
        if not predicted:
            continue
        actual, projected = _round_ground_truth(season, target_round, cache_dir=cache_dir)
        if not actual:
            continue

        predicted_means = {d: dist.mean for d, dist in predicted.items()}
        mae, correlation = _mae_and_correlation(predicted_means, actual)
        if mae is None:
            continue

        projected_mae, projected_correlation = (None, None)
        if projected:
            projected_mae, projected_correlation = _mae_and_correlation(projected, actual)

        common = [d for d in predicted_means if d in actual]
        top_pick = max(common, key=lambda d: predicted_means[d])
        actual_rank = sorted(common, key=lambda d: -actual[d])
        top_pick_rank = actual_rank.index(top_pick) + 1

        per_round.append(
            {
                "round": target_round,
                "n_drivers": len(common),
                "mae": mae,
                "spearman": correlation,
                "projected_mae": projected_mae,
                "projected_spearman": projected_correlation,
                "top_pick_actual_rank": top_pick_rank,
                "top_pick_in_top3": top_pick_rank <= 3,
            }
        )

    def _mean(key: str) -> float | None:
        values = [r[key] for r in per_round if r[key] is not None]
        return float(np.mean(values)) if values else None

    result = {
        "season": season,
        "rounds_evaluated": len(per_round),
        "per_round": per_round,
        "summary": {
            "mean_mae": _mean("mae"),
            "mean_spearman": _mean("spearman"),
            "benchmark_projected_mean_mae": _mean("projected_mae"),
            "benchmark_projected_mean_spearman": _mean("projected_spearman"),
            "top_pick_top3_hit_rate": (
                float(np.mean([r["top_pick_in_top3"] for r in per_round])) if per_round else None
            ),
        },
    }

    if season == 2026:
        result["optimiser_backtest"] = backtest_optimiser(season, rounds, cache_dir=cache_dir)

    return result


def backtest_points_seasons(
    seasons: dict[int, list[int]], *, cache_dir: Path | str | None = None, n_samples: int = 500
) -> dict[int, dict]:
    return {
        season: backtest_points_season(season, rounds, cache_dir=cache_dir, n_samples=n_samples)
        for season, rounds in seasons.items()
    }
