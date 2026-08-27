"""Clipping detection, against synthetic telemetry with a known answer.

Each fixture is built directly from (time, speed, throttle) triples rather
than real telemetry, so the exact clipping window is known in advance and
the test is checking the detection logic, not reproducing a real lap.
"""

from __future__ import annotations

import pandas as pd
import pytest

from f1_fantasy.pace.energy import (
    clipping_fraction,
    clipping_seconds,
    lap_duration,
    time_at_max_throttle,
)


def _telemetry(rows: list[tuple[float, float, float]]) -> pd.DataFrame:
    """rows: (time_s, speed_kph, throttle_pct)."""
    return pd.DataFrame(
        {
            "Time": pd.to_timedelta([r[0] for r in rows], unit="s"),
            "Speed": [r[1] for r in rows],
            "Throttle": [r[2] for r in rows],
        }
    )


def test_full_throttle_and_accelerating_is_not_clipping():
    tel = _telemetry([(0.0, 200.0, 100.0), (0.5, 210.0, 100.0), (1.0, 220.0, 100.0), (1.5, 230.0, 100.0)])
    assert clipping_seconds(tel) == 0.0


def test_full_throttle_and_sustained_deceleration_is_clipping():
    # Speed drops steadily for a full second at full throttle -- well past
    # the 0.3s minimum duration.
    tel = _telemetry(
        [(0.0, 300.0, 100.0), (0.3, 295.0, 100.0), (0.6, 290.0, 100.0), (0.9, 285.0, 100.0), (1.2, 280.0, 100.0)]
    )
    # Four intervals of 0.3s each all qualify (each drop is ~-16.7 km/h/s).
    assert clipping_seconds(tel) == pytest.approx(1.2)


def test_a_single_noisy_sample_below_minimum_duration_is_not_clipping():
    # One brief dip, then straight back to accelerating -- shorter than
    # MIN_CLIPPING_DURATION (0.3s).
    tel = _telemetry(
        [(0.0, 300.0, 100.0), (0.1, 298.0, 100.0), (0.2, 305.0, 100.0), (0.5, 310.0, 100.0)]
    )
    assert clipping_seconds(tel) == 0.0


def test_deceleration_under_partial_throttle_is_not_clipping():
    # A braking zone: decelerating hard, but throttle is off, not clipping.
    tel = _telemetry([(0.0, 300.0, 0.0), (0.3, 250.0, 0.0), (0.6, 200.0, 0.0), (0.9, 150.0, 0.0)])
    assert clipping_seconds(tel) == 0.0


def test_time_at_max_throttle_sums_full_throttle_intervals_regardless_of_accel():
    # Accelerating for the first second, clipping for the next -- both count.
    # Each interval is attributed by the throttle reading at its *start*:
    # [0,1)=1.0s full (100%), [1,1.5)=0.5s full (100%), [1.5,2.0)=0.5s full
    # (still 100% at t=1.5 -- it's the *next* sample, at t=2.0, that drops).
    tel = _telemetry(
        [(0.0, 200.0, 100.0), (1.0, 280.0, 100.0), (1.5, 275.0, 100.0), (2.0, 270.0, 50.0)]
    )
    assert time_at_max_throttle(tel) == pytest.approx(2.0)


def test_lap_duration_is_the_telemetry_time_span():
    tel = _telemetry([(0.0, 200.0, 100.0), (5.0, 250.0, 100.0), (90.0, 220.0, 80.0)])
    assert lap_duration(tel) == pytest.approx(90.0)


def test_clipping_fraction_divides_by_lap_duration():
    tel = _telemetry(
        [(0.0, 300.0, 100.0), (0.5, 295.0, 100.0), (1.0, 290.0, 100.0), (99.0, 290.0, 0.0)]
    )
    # 1.0s of clipping out of a 99s lap.
    assert clipping_fraction(tel) == pytest.approx(1.0 / 99.0)


def test_clipping_fraction_is_zero_for_an_empty_lap():
    assert clipping_fraction(pd.DataFrame({"Time": pd.to_timedelta([], unit="s"), "Speed": [], "Throttle": []})) == 0.0
