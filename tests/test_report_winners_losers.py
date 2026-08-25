"""Content correctness for the winners & losers card.

No report module had assembly-logic tests before this one -- only rendering
checks. This module has the most branching of the five (a same-person guard,
a cross-reference between chip plays and points, a captain suppression rule),
so it's the one to start with.
"""

from __future__ import annotations

import pytest

from f1_fantasy.api.models import Chip, Phase
from f1_fantasy.report.winners_losers import build_winners_losers
from tests.conftest import make_snapshot, make_team

BASE = ["1", "2", "3", "5", "101", "103"]


def test_headline_picks_the_highest_and_lowest_points_scored():
    previous = make_snapshot(
        10, {"a": make_team("a", 10, BASE), "b": make_team("b", 10, BASE)}
    )
    current = make_snapshot(
        11,
        {"a": make_team("a", 11, BASE), "b": make_team("b", 11, BASE)},
        standings={"a": (1, 130.0), "b": (2, 105.0)},
        phase=Phase.FINAL,
    )
    # previous standings default to (rank, 0.0), so points_gained == final points.

    context = build_winners_losers(current, previous)

    assert context["headline_winner"]["name"] == "member-a"
    assert context["headline_loser"]["name"] == "member-b"


def test_headline_does_not_crown_the_same_person_twice_in_a_solo_league():
    previous = make_snapshot(10, {"a": make_team("a", 10, BASE)})
    current = make_snapshot(
        11, {"a": make_team("a", 11, BASE)}, standings={"a": (1, 40.0)}, phase=Phase.FINAL
    )

    context = build_winners_losers(current, previous)

    assert context["headline_winner"]["name"] == "member-a"
    assert context["headline_loser"] is None


def test_headline_loser_is_none_when_nobody_has_scored():
    """A previous snapshot with no prior points and current points of 0 --
    points_gained is 0, so there's no winner to crown, but there's still a
    'lowest' entry unless everyone tied at zero is suppressed as a loser too.
    """
    current = make_snapshot(11, {"a": make_team("a", 11, BASE)}, standings={"a": (1, 0.0)})

    context = build_winners_losers(current, None)

    assert context["headline_winner"] is None


def test_chip_bet_is_cross_referenced_against_that_members_points_this_race():
    previous = make_snapshot(10, {"a": make_team("a", 10, BASE)})
    current = make_snapshot(
        11,
        {"a": make_team("a", 11, BASE, chips={Chip.LIMITLESS: 11})},
        standings={"a": (1, 41.5)},
        phase=Phase.FINAL,
    )

    context = build_winners_losers(current, previous)

    (bet,) = context["chip_bets"]
    assert bet["chip"] == "Limitless"
    assert bet["name"] == "member-a"
    assert bet["points"] == 41.5


def test_chip_bet_played_at_an_earlier_race_does_not_appear_this_week():
    current = make_snapshot(11, {"a": make_team("a", 11, BASE, chips={Chip.WILDCARD: 8})})

    context = build_winners_losers(current, None)

    assert context["chip_bets"] == []


def test_worst_captain_is_omitted_when_there_is_only_one_captain_call():
    current = make_snapshot(11, {"a": make_team("a", 11, BASE, captain="1")}, phase=Phase.FINAL)

    context = build_winners_losers(current, None)

    assert context["best_captain"] is not None
    assert context["worst_captain"] is None


def test_worst_captain_is_omitted_when_tied_with_the_best():
    """Two members, identical captain bonus -- there's no meaningful "worst"."""
    current = make_snapshot(
        11,
        {
            "a": make_team("a", 11, BASE, captain="1"),
            "b": make_team("b", 11, BASE, captain="1"),
        },
        phase=Phase.FINAL,
        points={"1": 20.0},
    )

    context = build_winners_losers(current, None)

    assert context["worst_captain"] is None


def test_first_capture_sets_the_caveat_and_suppresses_the_headline():
    """diff_standings always reports points_gained=0 with no previous snapshot
    (see test_diff.py) -- so there's nothing to crown a winner from yet,
    however large the member's season total already is.
    """
    current = make_snapshot(11, {"a": make_team("a", 11, BASE)}, standings={"a": (1, 55.0)})

    context = build_winners_losers(current, None)

    assert "First scored race" in context["caveat"]
    assert context["headline_winner"] is None


def test_transfer_verdicts_are_capped_and_split_by_sign():
    """a: 1(20)->4(25) = +5.  b: 1(20)->6(2) = -18.  c: unchanged.
    d: 1+2(20+15=35) -> 4+6(25+2=27) = -8.
    """
    previous = make_snapshot(
        10,
        {g: make_team(g, 10, BASE) for g in ("a", "b", "c", "d")},
    )
    current = make_snapshot(
        11,
        {
            "a": make_team("a", 11, ["4", "2", "3", "5", "101", "103"]),
            "b": make_team("b", 11, ["6", "2", "3", "5", "101", "103"]),
            "c": make_team("c", 11, BASE),
            "d": make_team("d", 11, ["4", "6", "3", "5", "101", "103"]),
        },
        phase=Phase.FINAL,
        points={"1": 20.0, "4": 25.0, "6": 2.0, "2": 15.0},
    )

    context = build_winners_losers(current, previous)

    assert {t["name"] for t in context["best_transfers"]} == {"member-a"}
    assert context["best_transfers"][0]["delta"] == pytest.approx(5.0)

    assert [t["name"] for t in context["worst_transfers"]] == ["member-b", "member-d"]
    assert context["worst_transfers"][0]["delta"] == pytest.approx(-18.0)
    assert context["worst_transfers"][1]["delta"] == pytest.approx(-8.0)

    everyone_shown = {t["name"] for t in context["best_transfers"] + context["worst_transfers"]}
    assert "member-c" not in everyone_shown
