"""Rendering guards.

These are cheap string assertions rather than pixel comparisons. They exist
because the failure mode they catch -- a card that renders successfully but
without styling -- produces a plausible-looking PNG and no error at all.
"""

from __future__ import annotations

import pytest

from f1_fantasy.demo import demo_snapshots
from f1_fantasy.render.shot import load_css, render_html
from f1_fantasy.render.teams import team_color
from f1_fantasy.report import recap as recap_report


@pytest.fixture(scope="module")
def html() -> str:
    previous, current = demo_snapshots()
    context = recap_report.build_recap(current, previous, race_label="Dutch Grand Prix")
    return render_html("recap.html.j2", context)


def test_stylesheet_is_not_html_escaped(html):
    """Autoescaping the CSS yields an unstyled card with no error -- guard it."""
    assert "&#39;" not in html
    assert "font-family: 'Barlow Condensed'" in html or "@font-face" in html
    assert "&lt;" not in html.split("<style>")[1].split("</style>")[0]


def test_fonts_are_embedded_not_linked(html):
    """A linked font would render differently on each host."""
    assert "data:font/woff2;base64," in html
    assert ".woff2') format" not in html.split("</style>")[0]


def test_css_declares_each_font_face_once():
    css = load_css()
    assert css.count("@font-face") == 3
    assert "format('woff2') format('woff2')" not in css


def test_card_element_is_present_for_screenshot_clipping(html):
    assert '<div class="card">' in html


def test_report_content_reaches_the_markup(html):
    assert "Sunday Drivers" in html
    assert "Dutch Grand Prix" in html
    assert "STANDINGS" in html or "Standings" in html


def test_movement_uses_a_glyph_so_direction_is_not_colour_alone(html):
    assert "▲" in html or "▼" in html or "–" in html


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Red Bull Racing", "#3671C6"),
        ("Oracle Red Bull Racing", "#3671C6"),
        ("McLaren", "#FF8000"),
        ("Scuderia Ferrari", "#E8002D"),
    ],
)
def test_team_colour_matches_on_substring(name, expected):
    assert team_color(name) == expected


def test_racing_bulls_beats_the_shorter_rb_key():
    """Longest-key-first matching, or "racing bulls" would hit the "rb" entry."""
    assert team_color("Racing Bulls") == "#6692FF"


def test_unknown_team_falls_back_to_muted_ink():
    assert team_color("Some New Team") == "#898781"
    assert team_color(None) == "#898781"
