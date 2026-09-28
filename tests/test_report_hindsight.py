"""The hindsight card's context assembly, against the shared snapshot fixtures.

The optimal-team search itself is already exhaustively tested in
test_predict_optimise.py -- these tests check that build_hindsight wires
realised Player points/prices into it correctly and reports the right
gap-to-optimal per member, each measured against *that member's own*
Team.budget_cap (value + bank), not a single flat cap shared by everyone --
see the module docstring for why a shared cap would be unfair.
"""

from __future__ import annotations

from f1_fantasy.predict.optimise import optimise_team
from f1_fantasy.report.hindsight import _optimal_with_captain, _split_players, build_hindsight
from tests.conftest import make_snapshot, make_team

BASE = ["1", "2", "3", "5", "101", "103"]


def test_build_hindsight_optimal_team_matches_a_direct_optimise_call():
    """The flat-cap reference figure (context["optimal_team"]/["optimal_total"])
    always uses the ``cap`` argument, independent of any member's own budget
    -- it's a fixed reference point, not a per-member gap."""
    points = {"1": 30.0, "2": 25.0, "3": 20.0, "4": 15.0, "5": 10.0, "6": 5.0, "101": 12.0, "102": 10.0, "103": 8.0}
    current = make_snapshot(11, {"a": make_team("a", 11, BASE)}, points=points, standings={"a": (1, 50.0)})

    context = build_hindsight(current, None, cap=200.0)

    driver_points, driver_prices, constructor_points, constructor_prices = _split_players(current.players)
    expected = optimise_team(driver_points, driver_prices, constructor_points, constructor_prices, cap=200.0)

    assert set(context["optimal_team"]) == set(expected.drivers) | set(expected.constructors)
    assert context["optimal_total"] is not None
    # The captain must be the highest-scoring driver among those actually selected.
    assert context["optimal_captain"] == max(expected.drivers, key=lambda d: driver_points[d])


def test_build_hindsight_measures_each_members_gap_against_their_own_budget():
    """Two members with genuinely different budget caps: the reported gap
    for each must come from the optimal team solved at *their* cap, not a
    single shared one -- this is the bug the budget ladder feature fixed
    (#5: 'optimal team changes relative to budget so can't compare optimal
    vs actual score')."""
    # The synthetic catalogue (6 drivers, 3 constructors -- see CATALOGUE)
    # needs >=121.2 to field any legal 5+2 team at all, so both budgets
    # below are chosen well above that floor while still being genuinely
    # different from each other.
    points = {"1": 30.0, "2": 25.0, "3": 20.0, "101": 12.0, "103": 8.0}
    previous = make_snapshot(10, {"a": make_team("a", 10, BASE), "b": make_team("b", 10, BASE)})
    current = make_snapshot(
        11,
        {
            "a": make_team("a", 11, BASE, value=200.0),  # budget_cap 202.0
            "b": make_team("b", 11, BASE, value=128.0),  # budget_cap 130.0
        },
        points=points,
        standings={"a": (1, 100.0), "b": (2, 10.0)},
    )

    context = build_hindsight(current, previous)
    by_name = {g["name"]: g for g in context["gaps"]}

    _, expected_a, _ = _optimal_with_captain(current.players, cap=202.0)
    _, expected_b, _ = _optimal_with_captain(current.players, cap=130.0)

    assert by_name["member-a"]["budget_cap"] == 202.0
    assert by_name["member-b"]["budget_cap"] == 130.0
    assert by_name["member-a"]["optimal_at_budget"] == round(expected_a, 2)
    assert by_name["member-b"]["optimal_at_budget"] == round(expected_b, 2)
    # A smaller cap can never buy a team that scores more than the same
    # pool at a larger cap -- this is the actual bug (#5): comparing every
    # member against one shared "optimal" silently punishes a smaller,
    # legitimately-earned budget.
    assert expected_b <= expected_a


def test_build_hindsight_reports_closest_and_furthest_member_gap():
    # value=150 (budget_cap 152.0) clears the synthetic catalogue's ~121.2
    # feasibility floor -- see the previous test's comment.
    points = {"1": 10.0, "101": 5.0}
    previous = make_snapshot(10, {"a": make_team("a", 10, BASE), "b": make_team("b", 10, BASE)})
    current = make_snapshot(
        11,
        {"a": make_team("a", 11, BASE, value=150.0), "b": make_team("b", 11, BASE, value=150.0)},
        points=points,
        standings={"a": (1, 100.0), "b": (2, 10.0)},
    )

    context = build_hindsight(current, previous)

    assert context["closest"]["name"] == "member-a"
    assert context["furthest"]["name"] == "member-b"
    assert context["closest"]["gap"] < context["furthest"]["gap"]


def test_build_hindsight_average_gap_is_the_mean_of_every_members_gap():
    previous = make_snapshot(10, {"a": make_team("a", 10, BASE), "b": make_team("b", 10, BASE)})
    current = make_snapshot(
        11,
        {"a": make_team("a", 11, BASE, value=150.0), "b": make_team("b", 11, BASE, value=150.0)},
        standings={"a": (1, 20.0), "b": (2, 40.0)},
    )

    context = build_hindsight(current, previous)

    expected_average = (context["closest"]["gap"] + context["furthest"]["gap"]) / 2
    assert context["average_gap"] == round(expected_average, 2)


def test_build_hindsight_budget_ladder_spans_the_leagues_real_budgets_in_5m_steps():
    current = make_snapshot(
        11,
        {
            "a": make_team("a", 11, BASE, value=93.0),  # budget_cap 95.0
            "b": make_team("b", 11, BASE, value=101.0),  # budget_cap 103.0
        },
        standings={"a": (1, 0.0), "b": (2, 0.0)},
    )

    context = build_hindsight(current, None)
    budgets = [rung["budget"] for rung in context["ladder"]]

    assert budgets[0] <= 95.0
    assert budgets[-1] >= 103.0
    steps = [round(b - a, 1) for a, b in zip(budgets, budgets[1:])]
    assert all(step == 5.0 for step in steps)


def test_build_hindsight_ladder_note_flags_a_non_monotonic_rung_when_it_happens():
    """The captain bonus is picked after the raw-points-optimal team, not
    jointly optimised with it (see module docstring), so a smaller cap can
    occasionally beat a larger one. The card must say so plainly rather than
    silently show numbers that look like a search bug."""
    ladder = [
        {"budget": 95.0, "optimal_total": 227.0},
        {"budget": 100.0, "optimal_total": 218.0},
        {"budget": 105.0, "optimal_total": 224.0},
    ]

    from f1_fantasy.report.hindsight import _ladder_note

    note = _ladder_note(ladder)

    assert "$95M" in note
    assert "$100M" in note


def test_build_hindsight_ladder_note_is_empty_when_the_ladder_is_monotonic():
    from f1_fantasy.report.hindsight import _ladder_note

    ladder = [
        {"budget": 95.0, "optimal_total": 200.0},
        {"budget": 100.0, "optimal_total": 210.0},
        {"budget": 105.0, "optimal_total": 224.0},
    ]

    assert _ladder_note(ladder) == ""


def test_build_hindsight_falls_back_to_flat_cap_when_a_members_team_is_missing():
    """A member with no team data (unreadable this round) still gets a gap
    entry, measured against the fallback ``cap`` -- not silently dropped.
    make_snapshot builds one Member per *standings* key even when *teams*
    has no matching entry, which is exactly this "unreadable team" case."""
    current = make_snapshot(11, {"a": make_team("a", 11, BASE)}, standings={"a": (1, 0.0), "ghost": (2, 0.0)})

    context = build_hindsight(current, None, cap=90.0)
    ghost = next(g for g in context["gaps"] if g["name"] == "member-ghost")

    assert ghost["budget_cap"] == 90.0
    assert "assumed" in ghost["budget_cap_label"]


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
