"""The field-relative upgrade-effect measurement, against synthetic per-round data.

Mirrors test_predict_adjusted.py's monkeypatching style and
test_pace_tyre_asymmetry.py's small-n honesty (None rather than a noisy
number from too little data).
"""

from __future__ import annotations

import pytest

from f1_fantasy.predict import upgrades as upgrades_module
from f1_fantasy.predict.upgrades import (
    _window,
    constructor_gap_pct,
    evaluate_attributed_upgrades,
    measure_upgrade_effect,
)


def test_constructor_gap_pct_averages_two_drivers(monkeypatch):
    from f1_fantasy.results import QualifyingResult

    monkeypatch.setattr(upgrades_module, "qualifying_gap_pct", lambda season, rnd: {"A": 0.2, "B": 0.4, "C": 1.0})
    monkeypatch.setattr(
        upgrades_module,
        "fetch_qualifying",
        lambda season, rnd: [
            QualifyingResult(driver_code="A", driver_name="A", constructor="Team", position=1),
            QualifyingResult(driver_code="B", driver_name="B", constructor="Team", position=2),
            QualifyingResult(driver_code="C", driver_name="C", constructor="Other", position=3),
        ],
    )

    result = constructor_gap_pct(2026, 5)

    assert result["Team"] == pytest.approx(0.3)
    assert result["Other"] == pytest.approx(1.0)


def test_constructor_gap_pct_uses_a_single_driver_when_the_other_is_absent(monkeypatch):
    from f1_fantasy.results import QualifyingResult

    monkeypatch.setattr(upgrades_module, "qualifying_gap_pct", lambda season, rnd: {"A": 0.5})
    monkeypatch.setattr(
        upgrades_module,
        "fetch_qualifying",
        lambda season, rnd: [QualifyingResult(driver_code="A", driver_name="A", constructor="Team", position=1)],
    )

    result = constructor_gap_pct(2026, 5)

    assert result["Team"] == pytest.approx(0.5)


def test_window_shrinks_near_a_season_boundary_rather_than_returning_empty():
    before = _window(2, available_rounds=[1, 2, 3, 4, 5], before=True, window=3)

    assert before == [1]


def test_window_returns_up_to_the_requested_size_closest_first():
    before = _window(10, available_rounds=[1, 2, 3, 4, 5, 6, 7, 8, 9], before=True, window=3)

    assert before == [9, 8, 7]


def test_window_returns_empty_below_the_minimum_floor():
    before = _window(1, available_rounds=[1, 2, 3], before=True, window=3)

    assert before == []


def test_measure_upgrade_effect_reports_none_when_before_window_is_empty(monkeypatch):
    monkeypatch.setattr(upgrades_module, "constructor_gap_pct", lambda season, r: {"Team": 0.5, "Other": 0.6})

    effect = measure_upgrade_effect(2026, "Team", upgrade_round=1, available_rounds=[1, 2, 3])

    assert effect.n_before == 0
    assert effect.constructor_before_mean is None
    assert effect.relative_delta is None


def test_measure_upgrade_effect_with_a_known_relative_improvement(monkeypatch):
    # Team's gap closes by 1.0pp (0.8 -> -0.2, impossible in reality but a
    # clean synthetic number); the field (Team + Other) closes by only
    # 0.1pp over the same rounds -- a real, field-relative improvement.
    per_round = {
        1: {"Team": 0.8, "Other": 0.4},
        2: {"Team": 0.8, "Other": 0.4},
        3: {"Team": -0.2, "Other": 0.3},
        4: {"Team": -0.2, "Other": 0.3},
    }
    monkeypatch.setattr(upgrades_module, "constructor_gap_pct", lambda season, r: per_round[r])

    effect = measure_upgrade_effect(2026, "Team", upgrade_round=2, available_rounds=[1, 2, 3, 4], window=1)

    # before=[1] (Team 0.8, field mean 0.6), after=[3] (Team -0.2, field mean 0.05)
    assert effect.constructor_delta == pytest.approx(-1.0)
    assert effect.field_delta == pytest.approx(-0.55)
    assert effect.relative_delta == pytest.approx(-0.45)
    assert effect.relative_delta < 0  # improved more than the field


def test_measure_upgrade_effect_reports_no_edge_when_constructor_and_field_move_together(monkeypatch):
    """The explicit null case: a plausible-looking upgrade with no real
    field-relative step-change -- this project's practice (see adjusted.py)
    is to make nulls representable as a first-class outcome, not an
    afterthought."""
    per_round = {
        1: {"Team": 0.8, "Other": 0.6},
        3: {"Team": 0.5, "Other": 0.3},  # both drop by 0.3 -- field-wide drift, not an upgrade effect
    }
    monkeypatch.setattr(upgrades_module, "constructor_gap_pct", lambda season, r: per_round[r])

    effect = measure_upgrade_effect(2026, "Team", upgrade_round=2, available_rounds=[1, 2, 3], window=1)

    assert effect.constructor_delta == pytest.approx(-0.3)
    assert effect.field_delta == pytest.approx(-0.3)
    assert effect.relative_delta == pytest.approx(0.0)


def test_evaluate_attributed_upgrades_skips_pairs_with_no_usable_window(monkeypatch):
    monkeypatch.setattr(upgrades_module, "constructor_gap_pct", lambda season, r: {"Team": 0.5})

    groups = {("Team", 1): [], ("Team", 5): []}  # round 1 has no "before" window at all

    effects = evaluate_attributed_upgrades(2026, groups, available_rounds=[1, 2, 3, 4, 5, 6], window=2)

    assert [e.upgrade_round for e in effects] == [5]
