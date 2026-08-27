"""The picks card's context assembly, against synthetic simulation summaries."""

from __future__ import annotations

from datetime import datetime, timezone

from f1_fantasy.calendar import RaceEvent
from f1_fantasy.predict.optimise import TeamSelection
from f1_fantasy.predict.simulate import SimulationSummary
from f1_fantasy.report.picks import build_picks, caption

EVENT = RaceEvent(
    season=2026, round=13, name="Test Grand Prix", circuit="Test Circuit", locality="Testville",
    starts_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
)


def _summary(driver, mean, p10, p90, price, p_price_rise=0.0, mean_delta_budget=0.0):
    return SimulationSummary(driver, price, mean, p10, p90, p_price_rise, mean_delta_budget, n_samples=100)


def test_build_picks_ranks_top_picks_by_mean_descending():
    summaries = {
        "A": _summary("A", 20.0, 10.0, 30.0, 10.0),
        "B": _summary("B", 10.0, 5.0, 15.0, 8.0),
    }

    context = build_picks(EVENT, summaries, None)

    assert [row["driver"] for row in context["top_picks"]] == ["A", "B"]
    assert context["title"] == "Test Grand Prix"
    assert context["subtitle"] == "Test Circuit, Testville"


def test_build_picks_excludes_drivers_with_zero_price_rise_probability():
    summaries = {
        "A": _summary("A", 20.0, 10.0, 30.0, 10.0, p_price_rise=0.0),
        "B": _summary("B", 10.0, 5.0, 15.0, 8.0, p_price_rise=0.8, mean_delta_budget=0.3),
    }

    context = build_picks(EVENT, summaries, None)

    assert [row["driver"] for row in context["price_risers"]] == ["B"]


def test_build_picks_suggests_the_highest_ev_captain():
    summaries = {
        "A": _summary("A", 20.0, 10.0, 30.0, 10.0),
        "B": _summary("B", 10.0, 5.0, 15.0, 8.0),
    }

    context = build_picks(EVENT, summaries, None)

    assert context["captain_suggestion"]["driver"] == "A"
    assert context["captain_suggestion"]["ev"] == 40.0


def test_build_picks_includes_the_optimal_team_when_given_one():
    selection = TeamSelection(drivers=("A", "B"), constructors=("X",), total_price=20.0, expected_points=30.0, expected_delta_budget=0.0, objective=30.0)

    context = build_picks(EVENT, {}, selection)

    assert context["optimal_team"]["drivers"] == ["A", "B"]
    assert context["optimal_team"]["constructors"] == ["X"]
    assert context["optimal_team"]["expected_points"] == 30.0


def test_build_picks_has_no_optimal_team_section_when_none_given():
    context = build_picks(EVENT, {}, None)

    assert context["optimal_team"] is None


def test_caption_includes_the_headline_picks_and_captain():
    summaries = {"A": _summary("A", 20.0, 10.0, 30.0, 10.0)}
    context = build_picks(EVENT, summaries, None)

    text = caption(context)

    assert "Test Grand Prix" in text
    assert "A" in text
