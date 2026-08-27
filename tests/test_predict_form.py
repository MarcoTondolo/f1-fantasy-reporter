"""Rolling qualifying-gap form, against a real captured qualifying payload.

The fixture is trimmed from 2026 round 11 (Hungarian GP) -- the same shape
used in test_predict_reconcile.py -- reused here because a driver's best
lap can come from any of Q1/Q2/Q3 depending how far they advanced, and this
payload has all three cases represented (a Q3 pole lap, a Q3 midfield lap,
and a Q1-only exit).
"""

from __future__ import annotations

import pytest

from f1_fantasy.predict.form import _lap_seconds, qualifying_gap_pct, rolling_form
from tests.test_predict_reconcile import RAW_QUALI_R11


def test_lap_seconds_parses_minutes_and_seconds():
    assert _lap_seconds("1:13.0") == pytest.approx(73.0)


def test_lap_seconds_handles_a_blank_time():
    assert _lap_seconds("") is None
    assert _lap_seconds("   ") is None


def test_gap_pct_is_zero_for_the_sessions_fastest_driver(monkeypatch):
    from f1_fantasy.predict import form as form_module
    from f1_fantasy.results import parse_qualifying

    monkeypatch.setattr(form_module, "fetch_qualifying", lambda season, rnd: parse_qualifying(RAW_QUALI_R11))

    gaps = qualifying_gap_pct(2026, 11)

    assert gaps["NOR"] == pytest.approx(0.0)  # 1:13.0 is the fastest of the three


def test_gap_pct_uses_whichever_segment_was_a_drivers_best(monkeypatch):
    """Sainz exits in Q1 (1:15.9); Hamilton and Norris reach Q3. Each is
    compared on their own best lap, not forced onto a segment they never ran."""
    from f1_fantasy.predict import form as form_module
    from f1_fantasy.results import parse_qualifying

    monkeypatch.setattr(form_module, "fetch_qualifying", lambda season, rnd: parse_qualifying(RAW_QUALI_R11))

    gaps = qualifying_gap_pct(2026, 11)

    best = 73.0  # NOR's 1:13.0
    assert gaps["SAI"] == pytest.approx((75.9 - best) / best * 100.0, abs=0.01)
    assert gaps["HAM"] == pytest.approx((73.8 - best) / best * 100.0, abs=0.01)


def test_qualifying_gap_pct_is_empty_when_no_one_set_a_time(monkeypatch):
    from f1_fantasy.predict import form as form_module
    from f1_fantasy.results import parse_qualifying

    empty = {"MRData": {"RaceTable": {"Races": [{"QualifyingResults": []}]}}}
    monkeypatch.setattr(form_module, "fetch_qualifying", lambda season, rnd: parse_qualifying(empty))

    assert qualifying_gap_pct(2026, 1) == {}


def test_rolling_form_averages_across_prior_rounds(monkeypatch):
    from f1_fantasy.predict import form as form_module

    calls = {1: {"A": 0.0, "B": 2.0}, 2: {"A": 2.0, "B": 0.0}}
    monkeypatch.setattr(form_module, "qualifying_gap_pct", lambda season, rnd: calls[rnd])

    form = rolling_form(2026, [1, 2])

    assert form["A"] == pytest.approx(1.0)
    assert form["B"] == pytest.approx(1.0)


def test_rolling_form_only_averages_rounds_a_driver_actually_appeared_in(monkeypatch):
    from f1_fantasy.predict import form as form_module

    calls = {1: {"A": 0.0}, 2: {"A": 4.0, "B": 1.0}}
    monkeypatch.setattr(form_module, "qualifying_gap_pct", lambda season, rnd: calls[rnd])

    form = rolling_form(2026, [1, 2])

    assert form["A"] == pytest.approx(2.0)
    assert form["B"] == pytest.approx(1.0)
