"""The hindsight card's context assembly, against the shared snapshot fixtures.

The optimal-team search itself is already exhaustively tested in
test_predict_optimise.py -- these tests check that build_hindsight wires
realised Player points/prices into it correctly and reports the right
gap-to-optimal per member, not the search's own correctness.
"""

from __future__ import annotations

from f1_fantasy.predict.optimise import optimise_team
from f1_fantasy.report.hindsight import _split_players, build_hindsight
from tests.conftest import make_snapshot, make_team

BASE = ["1", "2", "3", "5", "101", "103"]


def test_build_hindsight_optimal_team_matches_a_direct_optimise_call():
    points = {"1": 30.0, "2": 25.0, "3": 20.0, "4": 15.0, "5": 10.0, "6": 5.0, "101": 12.0, "102": 10.0, "103": 8.0}
    current = make_snapshot(11, {"a": make_team("a", 11, BASE)}, points=points, standings={"a": (1, 50.0)})

    context = build_hindsight(current, None, cap=200.0)

    driver_points, driver_prices, constructor_points, constructor_prices = _split_players(current.players)
    expected = optimise_team(driver_points, driver_prices, constructor_points, constructor_prices, cap=200.0)

    assert set(context["optimal_team"]) == set(expected.drivers) | set(expected.constructors)
    assert context["optimal_total"] is not None
    # The captain must be the highest-scoring driver among those actually selected.
    assert context["optimal_captain"] == max(expected.drivers, key=lambda d: driver_points[d])


def test_build_hindsight_reports_closest_and_furthest_member_gap():
    points = {"1": 10.0, "101": 5.0}
    previous = make_snapshot(10, {"a": make_team("a", 10, BASE), "b": make_team("b", 10, BASE)})
    current = make_snapshot(
        11,
        {"a": make_team("a", 11, BASE), "b": make_team("b", 11, BASE)},
        points=points,
        standings={"a": (1, 100.0), "b": (2, 10.0)},
    )

    context = build_hindsight(current, previous, cap=200.0)

    assert context["closest"]["name"] == "member-a"
    assert context["furthest"]["name"] == "member-b"
    assert context["closest"]["gap"] < context["furthest"]["gap"]


def test_build_hindsight_average_gap_is_the_mean_of_every_members_gap():
    previous = make_snapshot(10, {"a": make_team("a", 10, BASE), "b": make_team("b", 10, BASE)})
    current = make_snapshot(
        11,
        {"a": make_team("a", 11, BASE), "b": make_team("b", 11, BASE)},
        standings={"a": (1, 20.0), "b": (2, 40.0)},
    )

    context = build_hindsight(current, previous, cap=200.0)

    expected_average = (context["closest"]["gap"] + context["furthest"]["gap"]) / 2
    assert context["average_gap"] == round(expected_average, 2)


def test_build_hindsight_caveat_when_no_previous_snapshot():
    current = make_snapshot(11, {"a": make_team("a", 11, BASE)}, standings={"a": (1, 0.0)})

    context = build_hindsight(current, None, cap=200.0)

    assert "First scored race" in context["caveat"]


def test_build_hindsight_handles_no_players_at_all():
    current = make_snapshot(11, {}, standings={})
    current = current.model_copy(update={"players": {}})

    context = build_hindsight(current, None, cap=200.0)

    assert context["optimal_team"] == []
    assert context["optimal_total"] is None
    assert "Not enough priced players" in context["caveat"]
