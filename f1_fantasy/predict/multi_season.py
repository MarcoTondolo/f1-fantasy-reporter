"""Multi-season robustness check: do the single-season numbers generalise?

Gate 1 (scoring) and Gate 2 (prices) are necessarily 2026-only -- the public
fantasy feeds carry only the current season. The form and reliability
predictors, by contrast, need nothing but Jolpica, which is public for any
season, so their walk-forward scores can be checked against 2024 and 2025
too. That is the only test available of the plan's own stated purpose for
earlier seasons: "test method robustness, never seed 2026 coefficients."

**Result: the form baseline does not generalise as strongly as advertised.**
Rolling season form predicting qualifying order scores 0.898 mean Spearman
walk-forward on 2026 (rounds 2-12) -- the number this whole project compares
every other predictor against. Run the identical, unmodified code against
2024 and 2025 and it scores 0.714 and 0.702 respectively, a real gap, not
noise from the smaller round count (2026 has only 11 evaluated rounds to
2024/25's 23 each, which if anything should make the *larger* samples more
reliable, not less). The likely cause: 2026's new regulations have produced
a wider, more entrenched spread of car performance in year one than the
settled grids of 2024-25, which rolling form (a pure ranking signal) finds
easier to track. The number is real for 2026, but "ties the telemetry
pipeline" should be read as a 2026 claim, not a general one.

**The reliability-gating null (task #15) replicates cleanly.** Discounting
the form-predicted grid by constructor DNF probability is flat-to-negative
in every season checked: 2024 0.656 -> 0.652, 2025 0.571 -> 0.568, 2026
0.636 -> 0.624. Unlike the form number, this finding *is* robust -- the
mechanism doesn't help in any of the three seasons tested.
"""

from __future__ import annotations

from f1_fantasy.predict.evaluate import summarize, walk_forward
from f1_fantasy.predict.form import rolling_form
from f1_fantasy.predict.race import actual_race_positions, predict_grid_rank, predict_race_order
from f1_fantasy.results import fetch_qualifying


def backtest_season(season: int, rounds: list[int]) -> dict:
    """Form->qualifying and both race-order predictors, walk-forward, for one season."""

    def quali_actual(round_number: int) -> dict[str, int]:
        return {q.driver_code: q.position for q in fetch_qualifying(season, round_number)}

    form_scores = walk_forward(rounds, lambda train, target: rolling_form(season, train), quali_actual)

    baseline_scores = walk_forward(
        rounds,
        lambda train, target: predict_grid_rank(season, train, target),
        lambda r: actual_race_positions(season, r),
    )
    reliability_scores = walk_forward(
        rounds,
        lambda train, target: predict_race_order(season, train, target),
        lambda r: actual_race_positions(season, r),
    )

    return {
        "season": season,
        "form_vs_qualifying": summarize(form_scores),
        "baseline_grid_rank_vs_race": summarize(baseline_scores),
        "reliability_gated_vs_race": summarize(reliability_scores),
    }


def backtest_seasons(seasons: dict[int, list[int]]) -> dict[int, dict]:
    return {season: backtest_season(season, rounds) for season, rounds in seasons.items()}
