"""The reliability-gated race-order predictor, against synthetic histories.

``predict_grid_rank`` and ``predict_race_order`` are tested with monkeypatched
form/reliability/qualifying inputs so the arithmetic is checked independently
of any network call; the real walk-forward numbers against 2026 data are a
separate, manual backtest (see the CLI's ``race-backtest`` command).
"""

from __future__ import annotations

import pytest

from f1_fantasy.predict import race as race_module
from f1_fantasy.predict.reliability import ReliabilityRecord


def test_predict_grid_rank_orders_by_form_lowest_gap_first(monkeypatch):
    monkeypatch.setattr(race_module, "rolling_form", lambda season, rounds: {"A": 2.0, "B": 0.0, "C": 1.0})

    predicted = race_module.predict_grid_rank(2026, [1], 2)

    assert predicted == {"B": 1.0, "C": 2.0, "A": 3.0}


def test_predict_grid_rank_is_empty_with_no_form_history(monkeypatch):
    monkeypatch.setattr(race_module, "rolling_form", lambda season, rounds: {})

    assert race_module.predict_grid_rank(2026, [1], 2) == {}


def test_predict_race_order_matches_grid_rank_when_no_constructor_ever_retires(monkeypatch):
    """Zero DNF history still carries the prior rate, so this checks the
    *relative* ordering survives unchanged, not literal equality to the
    baseline ranks."""
    monkeypatch.setattr(race_module, "rolling_form", lambda season, rounds: {"A": 0.0, "B": 1.0})
    monkeypatch.setattr(race_module, "fetch_qualifying", lambda season, rnd: [])
    monkeypatch.setattr(race_module, "constructor_history", lambda season, rounds: {})

    predicted = race_module.predict_race_order(2026, [1], 2)

    assert predicted["A"] < predicted["B"]


def test_predict_race_order_pushes_an_unreliable_constructor_toward_the_back(monkeypatch):
    """A pays for Team Unreliable's DNF record even though A is faster than B."""
    from f1_fantasy.results import QualifyingResult

    monkeypatch.setattr(race_module, "rolling_form", lambda season, rounds: {"A": 0.0, "B": 1.0, "C": 2.0})
    monkeypatch.setattr(
        race_module,
        "fetch_qualifying",
        lambda season, rnd: [
            QualifyingResult(driver_code="A", driver_name="A", constructor="Team Unreliable", position=1),
            QualifyingResult(driver_code="B", driver_name="B", constructor="Team Solid", position=2),
            QualifyingResult(driver_code="C", driver_name="C", constructor="Team Solid", position=3),
        ],
    )
    monkeypatch.setattr(
        race_module,
        "constructor_history",
        lambda season, rounds: {
            "Team Unreliable": ReliabilityRecord("Team Unreliable", races=20, dnfs=16),  # 80% DNF
            "Team Solid": ReliabilityRecord("Team Solid", races=20, dnfs=0),
        },
    )

    predicted = race_module.predict_race_order(2026, [1], 2)

    # A was fastest (rank 1) but its team retires 80% of the time -- pulled
    # far past Team Solid's slower-but-reliable B (rank 2).
    assert predicted["A"] > predicted["B"]
    assert predicted["B"] < predicted["C"]  # unaffected: same team, form order preserved
    assert predicted["C"] == pytest.approx(3.0)  # already last -- DNF risk can't move it further


def test_actual_race_positions_excludes_unclassified_rows(monkeypatch):
    from f1_fantasy.results import RaceResult

    monkeypatch.setattr(
        race_module,
        "fetch_race_results",
        lambda season, rnd: [
            RaceResult(driver_code="A", driver_name="A", constructor="Team", grid=1, position=1, status="Finished"),
            RaceResult(driver_code="B", driver_name="B", constructor="Team", grid=2, position=None, status="Disqualified"),
        ],
    )

    positions = race_module.actual_race_positions(2026, 1)

    assert positions == {"A": 1}
