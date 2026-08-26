"""The scoring table, pinned to the cases that established each value.

Every constant in ``predict/scoring.py`` was solved from the game's own
published points rather than from documentation, so these tests are written
against the specific real observations that proved each rule. Where a test
names a driver and round, that is the case the value was derived from.

The full reconciliation (2026 rounds 1-12) is run by
``tests/test_predict_reconcile.py``; this file covers the pure functions.
"""

from __future__ import annotations

import pytest

from f1_fantasy.predict.scoring import (
    CLASSIFICATION_DISTANCE_FRACTION,
    DNF_POINTS,
    SPRINT_DNF_POINTS,
    is_classified,
    qualifying_points,
    race_points,
)


# -- qualifying ------------------------------------------------------------


@pytest.mark.parametrize(
    "position, expected",
    [(1, 10), (2, 9), (5, 6), (9, 2), (10, 1), (11, 0), (15, 0), (22, 0)],
)
def test_qualifying_is_ten_down_to_one_over_the_top_ten(position, expected):
    """Exact across all 252 driver-rounds of 2026 so far, no exceptions."""
    assert qualifying_points(position) == expected


def test_setting_no_qualifying_time_is_charged_not_zeroed():
    """Hadjar, round 4: classified P22 with empty Q1/Q2/Q3, scored -5.

    The distinction matters -- qualifying 22nd having set a time scores 0,
    but setting no time at all is a penalty.
    """
    assert qualifying_points(22, set_a_time=True) == 0
    assert qualifying_points(22, set_a_time=False) == -5


def test_being_absent_from_qualifying_is_charged_the_same():
    """Stroll, Verstappen and Sainz at round 1: absent from the
    classification entirely, each scored -5."""
    assert qualifying_points(None) == -5


# -- classification --------------------------------------------------------


def test_a_lapped_driver_who_went_the_distance_is_classified():
    assert is_classified("Lapped", laps=71, winner_laps=72) is True


def test_a_lapped_driver_who_retired_early_is_not_classified():
    """Albon, round 7: 55 of 66 laps (83%), tagged "Lapped", scored -20.

    The status alone is not enough -- the results feed labels some
    retirements "Lapped", so distance covered is the deciding signal.
    """
    assert is_classified("Lapped", laps=55, winner_laps=66) is False


def test_stroll_round_one_is_the_other_mislabelled_retirement():
    """43 of 58 laps (74%), tagged "Lapped", scored -20."""
    assert is_classified("Lapped", laps=43, winner_laps=58) is False


def test_the_threshold_is_ninety_percent_of_the_winners_distance():
    assert CLASSIFICATION_DISTANCE_FRACTION == 0.9
    assert is_classified("Lapped", laps=90, winner_laps=100) is True
    assert is_classified("Lapped", laps=89, winner_laps=100) is False


def test_classification_falls_back_to_status_when_laps_are_unknown():
    assert is_classified("Finished") is True
    assert is_classified("Lapped") is True
    assert is_classified("Retired") is False
    assert is_classified("Did not start") is False
    assert is_classified("Disqualified") is False


# -- race ------------------------------------------------------------------


def test_race_positions_use_the_real_f1_points_table():
    for position, expected in {1: 25, 2: 18, 3: 15, 4: 12, 5: 10,
                               6: 8, 7: 6, 8: 4, 9: 2, 10: 1, 11: 0}.items():
        got = race_points(grid=position, position=position, status="Finished")
        assert got.position == expected


def test_positions_gained_score_one_point_per_place():
    """The ratio of points to places gained is exactly 1.0 wherever
    non-zero, measured against the *grid*, not the qualifying order."""
    gained = race_points(grid=10, position=4, status="Finished")
    assert gained.positions_gained == 6

    lost = race_points(grid=4, position=10, status="Finished")
    assert lost.positions_gained == -6


def test_positions_are_measured_from_the_grid_not_qualifying():
    """A driver penalised from P2 on the grid to P7 who finishes P5 has
    gained two places, not lost three."""
    scored = race_points(grid=7, position=5, status="Finished")
    assert scored.positions_gained == 2


def test_a_retirement_takes_the_flat_charge_and_no_position_change():
    """Confirmed live: drivers classified 17th-22nd after retiring from
    midfield grid slots scored zero position-change points, not a large
    negative on top of the DNF charge."""
    scored = race_points(grid=3, position=20, status="Retired")

    assert scored.dnf == DNF_POINTS
    assert scored.position == 0
    assert scored.positions_gained == 0


def test_a_sprint_retirement_is_charged_half():
    scored = race_points(grid=3, position=20, status="Retired", sprint=True)
    assert scored.dnf == SPRINT_DNF_POINTS


def test_fastest_lap_and_driver_of_the_day_are_ten_each():
    scored = race_points(
        grid=1, position=1, status="Finished", fastest_lap=True, driver_of_the_day=True
    )
    assert scored.fastest_lap == 10
    assert scored.driver_of_the_day == 10


def test_overtakes_score_one_point_each():
    scored = race_points(grid=1, position=1, status="Finished", overtakes=7)
    assert scored.overtakes == 7


def test_total_sums_every_component():
    """A win from pole with the fastest lap, DOTD and three overtakes."""
    scored = race_points(
        grid=4, position=1, status="Finished",
        overtakes=3, fastest_lap=True, driver_of_the_day=True,
    )
    # 25 position + 3 gained + 3 overtakes + 10 FL + 10 DOTD
    assert scored.total == 51
