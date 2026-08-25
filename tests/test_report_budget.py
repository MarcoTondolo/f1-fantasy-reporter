"""Content correctness for the budget cap card.

Total budget cap is a straight sum of two fields already on Team (value and
bank), so the risk here isn't the arithmetic -- it's ranking, peak-relative
bar scaling, and the highest/lowest picks degrading sensibly for small or
partial leagues.
"""

from __future__ import annotations

import pytest

from f1_fantasy.report.budget import build_budget
from tests.conftest import make_snapshot, make_team

BASE = ["1", "2", "3", "5", "101", "103"]


def test_rows_are_ranked_by_budget_cap_descending():
    snapshot = make_snapshot(
        11,
        {
            "a": make_team("a", 11, BASE, value=100.0),
            "b": make_team("b", 11, BASE, value=120.0),
            "c": make_team("c", 11, BASE, value=110.0),
        },
    )

    context = build_budget(snapshot)

    assert [row["name"] for row in context["rows"]] == ["member-b", "member-c", "member-a"]


def test_budget_cap_is_value_plus_bank():
    snapshot = make_snapshot(11, {"a": make_team("a", 11, BASE, value=100.0)})

    context = build_budget(snapshot)

    # make_team's default bank is 2.0.
    assert context["rows"][0]["budget_cap"] == pytest.approx(102.0)
    assert context["rows"][0]["budget_cap_label"] == "$102.0M"


def test_highest_and_top_bar_are_the_same_row():
    snapshot = make_snapshot(
        11, {"a": make_team("a", 11, BASE, value=100.0), "b": make_team("b", 11, BASE, value=120.0)}
    )

    context = build_budget(snapshot)

    assert context["highest"]["name"] == "member-b"
    assert context["rows"][0]["bar_pct"] == 100.0


def test_lowest_is_none_for_a_solo_league():
    """One team has nothing to be "tightest" relative to."""
    snapshot = make_snapshot(11, {"a": make_team("a", 11, BASE)})

    context = build_budget(snapshot)

    assert context["highest"] is not None
    assert context["lowest"] is None


def test_a_member_with_no_readable_team_is_left_out_and_flagged():
    snapshot = make_snapshot(11, {"a": make_team("a", 11, BASE)})
    snapshot.members = list(snapshot.members) + [
        snapshot.members[0].model_copy(update={"guid": "b", "user_name": "member-b"})
    ]

    context = build_budget(snapshot)

    assert len(context["rows"]) == 1
    assert "1 of 2" in context["caveat"]


def test_caption_names_the_highest_and_lowest_budgets():
    snapshot = make_snapshot(
        11, {"a": make_team("a", 11, BASE, value=100.0), "b": make_team("b", 11, BASE, value=120.0)}
    )
    context = build_budget(snapshot)

    from f1_fantasy.report.budget import caption

    text = caption(context)

    assert "member-b" in text
    assert "member-a" in text
