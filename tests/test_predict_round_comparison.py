"""round_comparison: predicted vs actual on the same component basis.

Network-free -- build_grid_conditioned_distributions, reconstruct_points and
reconcile_round are all monkeypatched, since this module's own job is just
combining what they return, not fetching anything itself.
"""

from __future__ import annotations

import pytest

from f1_fantasy.predict import round_comparison as rc_module
from f1_fantasy.predict.points import DriverPointsDistribution
from f1_fantasy.predict.reconcile import DriverReconciliation
from f1_fantasy.predict.round_comparison import build_round_comparison
from f1_fantasy.predict.scoring import PointsBreakdown


def test_predicted_overtakes_and_dotd_are_summed_into_one_component(monkeypatch):
    predicted = {
        "A": DriverPointsDistribution(
            driver="A",
            constructor="Team A",
            mean=30.0,
            components={
                "position": 10.0,
                "positions_gained": 2.0,
                "overtakes": 5.0,
                "fastest_lap": 0.0,
                "driver_of_the_day": 10.0,
                "dnf": 0.0,
                "qualifying": 3.0,
            },
            p10=20.0,
            p90=40.0,
        ),
    }
    monkeypatch.setattr(rc_module, "build_grid_conditioned_distributions", lambda *a, **k: predicted)
    monkeypatch.setattr(
        rc_module,
        "reconstruct_points",
        lambda season, rnd: {"A": PointsBreakdown(position=10.0, positions_gained=2.0, qualifying=3.0)},
    )
    monkeypatch.setattr(
        rc_module,
        "reconcile_round",
        lambda season, rnd: [
            DriverReconciliation(
                round_number=rnd, driver="A", actual_qualifying=3.0, expected_qualifying=3.0,
                actual_race=27.0, expected_race_without_overtakes=12.0,
            ),
        ],
    )

    (comparison,) = build_round_comparison(2026, [1], 2, n_samples=50, seed=0)

    # predicted: 5 (overtakes) + 10 (DOTD); actual: residual 27 - 12
    assert comparison.predicted_components["overtakes_and_dotd"] == pytest.approx(15.0)
    assert comparison.actual_components["overtakes_and_dotd"] == pytest.approx(15.0)
    assert comparison.residual_confident is True
    assert comparison.actual_total == pytest.approx(10.0 + 2.0 + 15.0 + 3.0)
    assert comparison.predicted_total == pytest.approx(30.0)
    assert comparison.predicted_p10 == pytest.approx(20.0)


def test_a_driver_missing_from_either_side_is_dropped(monkeypatch):
    predicted = {
        "A": DriverPointsDistribution(driver="A", constructor="T", mean=10.0),
        "B": DriverPointsDistribution(driver="B", constructor="T", mean=10.0),
    }
    monkeypatch.setattr(rc_module, "build_grid_conditioned_distributions", lambda *a, **k: predicted)
    monkeypatch.setattr(rc_module, "reconstruct_points", lambda season, rnd: {"A": PointsBreakdown()})
    monkeypatch.setattr(rc_module, "reconcile_round", lambda season, rnd: [])

    result = build_round_comparison(2026, [1], 2)

    assert [c.driver for c in result] == ["A"]


def test_an_implausible_residual_is_shown_as_zero_not_trusted(monkeypatch):
    """Mirrors reconcile.py's own Albon-round-7 case: a negative residual is
    a wrong rule, not a real overtake count, and must not be reported as one.
    """
    predicted = {"A": DriverPointsDistribution(driver="A", constructor="T", mean=10.0)}
    monkeypatch.setattr(rc_module, "build_grid_conditioned_distributions", lambda *a, **k: predicted)
    monkeypatch.setattr(rc_module, "reconstruct_points", lambda season, rnd: {"A": PointsBreakdown(position=5.0)})
    monkeypatch.setattr(
        rc_module,
        "reconcile_round",
        lambda season, rnd: [
            DriverReconciliation(
                round_number=rnd, driver="A", actual_qualifying=0, expected_qualifying=0,
                actual_race=-16.0, expected_race_without_overtakes=0.0,
            ),
        ],
    )

    (comparison,) = build_round_comparison(2026, [1], 2)

    assert comparison.actual_components["overtakes_and_dotd"] == 0.0
    assert comparison.residual_confident is False


def test_results_are_sorted_by_actual_total_descending(monkeypatch):
    predicted = {
        "A": DriverPointsDistribution(driver="A", constructor="T", mean=10.0),
        "B": DriverPointsDistribution(driver="B", constructor="T", mean=10.0),
    }
    monkeypatch.setattr(rc_module, "build_grid_conditioned_distributions", lambda *a, **k: predicted)
    monkeypatch.setattr(
        rc_module,
        "reconstruct_points",
        lambda season, rnd: {"A": PointsBreakdown(position=5.0), "B": PointsBreakdown(position=20.0)},
    )
    monkeypatch.setattr(rc_module, "reconcile_round", lambda season, rnd: [])

    result = build_round_comparison(2026, [1], 2)

    assert [c.driver for c in result] == ["B", "A"]
