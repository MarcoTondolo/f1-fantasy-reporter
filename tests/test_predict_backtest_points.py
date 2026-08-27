"""The points-accuracy backtest, against synthetic per-round data.

The real walk-forward numbers this module reports come from a separate,
manually-run sweep against live 2024/2025/2026 data -- these tests only
check the aggregation and season-branching logic (2026 uses the public
feed + optimiser backtest, 2024/2025 use reconstruct_points only).
"""

from __future__ import annotations

import pytest

from f1_fantasy.predict import backtest_points as backtest_points_module
from f1_fantasy.predict.points import DriverPointsDistribution


def test_backtest_points_season_skips_the_first_round_with_nothing_to_train_on(monkeypatch):
    monkeypatch.setattr(
        backtest_points_module.points_module,
        "build_round_distributions",
        lambda season, train, target, n_samples=500: {"A": DriverPointsDistribution("A", "Team", mean=10.0)},
    )
    monkeypatch.setattr(
        backtest_points_module, "_round_ground_truth", lambda season, r, cache_dir=None: ({"A": 10.0}, None)
    )

    result = backtest_points_module.backtest_points_season(2024, [1, 2])

    assert result["rounds_evaluated"] == 0  # only round 2 has train_rounds, but n_drivers<3 there too
    assert result["season"] == 2024


def test_backtest_points_season_2026_includes_the_optimiser_backtest(monkeypatch):
    monkeypatch.setattr(
        backtest_points_module.points_module,
        "build_round_distributions",
        lambda season, train, target, n_samples=500: {},
    )
    monkeypatch.setattr(backtest_points_module, "backtest_optimiser", lambda season, rounds, cache_dir=None: {"stub": True})

    result = backtest_points_module.backtest_points_season(2026, [1, 2])

    assert result["optimiser_backtest"] == {"stub": True}


def test_backtest_points_season_2024_has_no_optimiser_backtest_key():
    result = backtest_points_module.backtest_points_season(2024, [])

    assert "optimiser_backtest" not in result


def test_mae_and_correlation_matches_a_hand_computed_case():
    predicted = {"A": 10.0, "B": 20.0, "C": 30.0}
    actual = {"A": 12.0, "B": 18.0, "C": 33.0}

    mae, correlation = backtest_points_module._mae_and_correlation(predicted, actual)

    assert mae == pytest.approx((2.0 + 2.0 + 3.0) / 3)
    assert correlation == pytest.approx(1.0)  # order preserved exactly


def test_mae_and_correlation_returns_none_with_too_few_common_drivers():
    assert backtest_points_module._mae_and_correlation({"A": 1.0}, {"A": 1.0}) == (None, None)


def test_round_ground_truth_uses_the_public_feed_for_2026(monkeypatch):
    monkeypatch.setattr(
        backtest_points_module,
        "fetch_driver_feed",
        lambda round_number, cache_dir=None: {
            "A": {"GamedayPoints": "25", "ProjectedGamedayPoints": "20"},
        },
    )

    actual, projected = backtest_points_module._round_ground_truth(2026, 5)

    assert actual == {"A": 25.0}
    assert projected == {"A": 20.0}


def test_round_ground_truth_uses_reconstruct_points_for_earlier_seasons(monkeypatch):
    from f1_fantasy.predict.scoring import PointsBreakdown

    monkeypatch.setattr(
        backtest_points_module, "reconstruct_points", lambda season, r: {"A": PointsBreakdown(position=25.0, qualifying=10.0)}
    )

    actual, projected = backtest_points_module._round_ground_truth(2024, 5)

    assert actual == {"A": 35.0}
    assert projected is None
