"""Pace feature derivation against synthetic (already-cleaned) lap frames.

sessions.clean_laps is the one place real FastF1 data gets filtered; these
tests start from data that's already past that filter, so what's pinned here
is the feature maths itself -- one-lap gap, long-run pace, degradation --
independent of the live API.
"""

from __future__ import annotations

import pandas as pd
import pytest

from f1_fantasy.pace.features import MIN_STINT_LAPS, session_pace


def _laps(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    frame["LapTime"] = pd.to_timedelta(frame["LapTime"], unit="s")
    return frame


def test_one_lap_pace_is_each_drivers_fastest_clean_lap():
    laps = _laps(
        [
            {"Driver": "A", "LapTime": 80.0, "TyreLife": 1, "Stint": "1"},
            {"Driver": "A", "LapTime": 79.0, "TyreLife": 2, "Stint": "1"},
            {"Driver": "B", "LapTime": 81.5, "TyreLife": 1, "Stint": "1"},
        ]
    )

    pace = {p.driver: p for p in session_pace(laps)}

    assert pace["A"].one_lap_time == pytest.approx(79.0)
    assert pace["A"].one_lap_gap_pct == pytest.approx(0.0)
    assert pace["B"].one_lap_gap_pct == pytest.approx((81.5 - 79.0) / 79.0 * 100)


def test_a_stint_shorter_than_the_minimum_is_not_a_long_run():
    short_stint = [
        {"Driver": "A", "LapTime": 80.0 + i * 0.1, "TyreLife": i + 1, "Stint": "1"}
        for i in range(MIN_STINT_LAPS - 1)
    ]
    laps = _laps(short_stint)

    (pace,) = session_pace(laps)

    assert pace.long_run_pace is None
    assert pace.long_run_gap_pct is None


def test_a_sufficiently_long_stint_produces_a_long_run_pace():
    stint = [
        {"Driver": "A", "LapTime": 80.0 + i * 0.2, "TyreLife": i + 1, "Stint": "1"}
        for i in range(MIN_STINT_LAPS)
    ]
    laps = _laps(stint)

    (pace,) = session_pace(laps)

    assert pace.long_run_pace is not None
    assert pace.long_run_gap_pct == pytest.approx(0.0)
    assert pace.stint_laps == MIN_STINT_LAPS


def test_degradation_slope_reflects_lap_time_rising_with_tyre_life():
    stint = [
        {"Driver": "A", "LapTime": 80.0 + i * 0.5, "TyreLife": i + 1, "Stint": "1"}
        for i in range(6)
    ]
    laps = _laps(stint)

    (pace,) = session_pace(laps)

    assert pace.degradation == pytest.approx(0.5, abs=1e-6)


def test_a_driver_gets_their_best_stint_not_their_average_stint():
    """A fast, clean sim run should outrank an earlier scrappy one."""
    slow_stint = [
        {"Driver": "A", "LapTime": 82.0 + i * 0.3, "TyreLife": i + 1, "Stint": "1"}
        for i in range(MIN_STINT_LAPS)
    ]
    fast_stint = [
        {"Driver": "A", "LapTime": 79.0 + i * 0.1, "TyreLife": i + 1, "Stint": "2"}
        for i in range(MIN_STINT_LAPS)
    ]
    laps = _laps(slow_stint + fast_stint)

    (pace,) = session_pace(laps)

    assert pace.long_run_pace == pytest.approx(79.0 + 0.1 * (MIN_STINT_LAPS - 1) / 2)


def test_gap_pct_is_relative_to_the_best_in_the_session():
    stint_a = [
        {"Driver": "A", "LapTime": 80.0, "TyreLife": i + 1, "Stint": "1"} for i in range(MIN_STINT_LAPS)
    ]
    stint_b = [
        {"Driver": "B", "LapTime": 88.0, "TyreLife": i + 1, "Stint": "1"} for i in range(MIN_STINT_LAPS)
    ]
    laps = _laps(stint_a + stint_b)

    pace = {p.driver: p for p in session_pace(laps)}

    assert pace["A"].long_run_gap_pct == pytest.approx(0.0)
    assert pace["B"].long_run_gap_pct == pytest.approx((88.0 - 80.0) / 80.0 * 100)


def test_empty_laps_produce_no_pace_rows():
    assert session_pace(pd.DataFrame(columns=["Driver", "LapTime", "TyreLife", "Stint"])) == []
