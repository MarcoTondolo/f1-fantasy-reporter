"""Track geometry derivation against a synthetic reference lap.

Builds a simple lap by hand -- two straights and two corners with known
speed, direction and zone width -- so the corner/straight boundaries this
module derives can be checked against values that were designed in, not
inferred from real telemetry where the "correct" boundary isn't known.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from f1_fantasy.pace.track_profile import build_track_profile, classify_speed_band


def _synthetic_lap() -> pd.DataFrame:
    """A 1000m lap on an exact 2.5m grid (so a corner apex at a round number
    always lands on a real sample -- matching how a genuine flat-out kink
    collapses to an exactly-zero-length zone in real telemetry, where the
    apex distance is itself derived from the same reference lap):
    straight (0-180), a slow left-hander at 250 (apex 100 km/h),
    straight (320-580), a fast right-hander at 650 (apex 240 km/h),
    straight (720-1000).

    X/Y are built by integrating heading through a real turn rate (radians
    per metre) over each corner zone, so the path has genuine, consistent
    curvature through the corner rather than a straight-line kink -- a
    positive turn rate steers the heading counter-clockwise (left), negative
    clockwise (right).
    """
    distance = np.arange(0, 1000.0001, 2.5)
    speed = np.full_like(distance, 300.0)

    def dip(center, width, floor):
        return floor + (300.0 - floor) * np.clip(np.abs(distance - center) / width, 0, 1)

    speed = np.minimum(speed, dip(250, 100, 100.0))
    speed = np.minimum(speed, dip(650, 100, 240.0))

    # Throttle: full everywhere except a lift/brake window bracketing each apex.
    throttle = np.where((distance > 180) & (distance < 320), 40.0, 100.0)
    throttle = np.where((distance > 580) & (distance < 720), 60.0, throttle)

    # Integrate heading -> position: turn left (positive rate) through the
    # first corner zone, right (negative rate) through the second.
    turn_rate = np.zeros_like(distance)
    turn_rate[(distance > 180) & (distance < 320)] = 0.02  # left
    turn_rate[(distance > 580) & (distance < 720)] = -0.02  # right

    ds = np.diff(distance, prepend=distance[0])
    heading = np.cumsum(turn_rate * ds)
    x = np.cumsum(ds * np.cos(heading))
    y = np.cumsum(ds * np.sin(heading))

    # Time: monotonic, roughly consistent with speed (not physically exact,
    # just monotonic and slower where speed is lower).
    dt = np.diff(distance) / np.maximum(speed[:-1], 1.0)
    time_s = np.concatenate([[0.0], np.cumsum(dt)])

    return pd.DataFrame(
        {
            "Distance": distance,
            "Speed": speed,
            "Throttle": throttle,
            "X": x,
            "Y": y,
            "Time": pd.to_timedelta(time_s, unit="s"),
        }
    )


def _corners_frame() -> pd.DataFrame:
    return pd.DataFrame({"Number": [1, 2], "Distance": [250.0, 650.0]})


def test_classify_speed_band_thresholds():
    assert classify_speed_band(50) == "slow"
    assert classify_speed_band(144) == "slow"
    assert classify_speed_band(145) == "medium"
    assert classify_speed_band(214) == "medium"
    assert classify_speed_band(215) == "fast"
    assert classify_speed_band(320) == "fast"


def test_corners_are_classified_by_band_and_direction():
    tel = _synthetic_lap()
    profile = build_track_profile(2026, 1, "Test GP", _corners_frame(), tel)

    assert len(profile.corners) == 2
    slow, fast = profile.corners[0], profile.corners[1]
    assert slow.speed_band == "slow"
    assert slow.direction == "left"
    assert fast.speed_band == "fast"
    assert fast.direction == "right"


def test_corner_zones_span_the_lifted_throttle_window():
    tel = _synthetic_lap()
    profile = build_track_profile(2026, 1, "Test GP", _corners_frame(), tel)
    slow = profile.corners[0]

    assert slow.entry_distance == pytest.approx(180.0, abs=5)
    assert slow.exit_distance == pytest.approx(320.0, abs=5)


def test_straights_fill_the_gaps_between_corner_zones():
    tel = _synthetic_lap()
    profile = build_track_profile(2026, 1, "Test GP", _corners_frame(), tel)

    middle_straight = next(s for s in profile.straights if 310 <= s.start_distance < 400)
    assert middle_straight.start_distance == pytest.approx(320.0, abs=5)
    assert middle_straight.end_distance == pytest.approx(580.0, abs=5)


def test_band_distance_pct_sums_to_roughly_the_whole_lap():
    tel = _synthetic_lap()
    profile = build_track_profile(2026, 1, "Test GP", _corners_frame(), tel)

    total = sum(profile.band_distance_pct(band) for band in ("slow", "medium", "fast", "straight"))
    assert total == pytest.approx(100.0, abs=1.0)


def test_corner_direction_balance_reflects_which_way_the_lap_mostly_turns():
    tel = _synthetic_lap()
    profile = build_track_profile(2026, 1, "Test GP", _corners_frame(), tel)

    # One left corner, one right corner of comparable length -- close to balanced.
    assert -0.3 < profile.corner_direction_balance < 0.3


def test_a_corner_the_reference_lap_never_lifts_for_collapses_to_zero_length():
    """A flat-out kink -- Throttle never drops below 99 near the apex -- gets
    no dedicated zone; it folds into the surrounding straight."""
    tel = _synthetic_lap()
    corners = pd.DataFrame({"Number": [1, 2, 3], "Distance": [250.0, 450.0, 650.0]})

    profile = build_track_profile(2026, 1, "Test GP", corners, tel)

    flat_kink = next(c for c in profile.corners if c.number == 2)
    assert flat_kink.length == 0
