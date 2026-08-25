"""Keyword filtering and bulletin assembly, offline.

fetch_headlines does the network I/O; everything testable without a live feed
is split into _matches and build_bulletins so the keyword logic and the
context shape are both pinned without hitting formula1.com in the suite.
"""

from __future__ import annotations

from f1_fantasy.news.bulletins import Headline, _matches, build_bulletins
from f1_fantasy.results import GridPenalty


def test_a_penalty_keyword_in_the_title_matches():
    assert "penalty" in _matches("Colapinto rues 'harsh' penalty")


def test_matching_is_case_insensitive():
    assert "engine" in _matches("New ENGINE regulations for 2026")


def test_an_unrelated_headline_matches_nothing():
    assert _matches("Norris wins from pole at Zandvoort") == ()


def test_multiple_keywords_can_match_the_same_headline():
    matched = _matches("Stewards hand grid penalty over gearbox change")
    assert "stewards" in matched
    assert "grid penalty" in matched
    assert "gearbox" in matched


def test_build_bulletins_shapes_penalties_and_headlines_for_the_template():
    penalties = [GridPenalty(driver_code="HAD", driver_name="Isack Hadjar", quali_position=3, grid_position=13)]
    news = [Headline(title="Engine penalty for Hadjar", summary="", link="", matched=("engine", "penalty"))]

    context = build_bulletins(penalties, news)

    assert context["penalties"] == [
        {"driver": "Isack Hadjar", "from": 3, "to": 13, "places_lost": 10}
    ]
    assert context["headlines"][0]["title"] == "Engine penalty for Hadjar"
    assert context["headlines"][0]["matched"] == ["engine", "penalty"]


def test_build_bulletins_handles_no_news_at_all():
    context = build_bulletins([], [])

    assert context["penalties"] == []
    assert context["headlines"] == []
