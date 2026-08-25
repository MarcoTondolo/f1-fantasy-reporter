"""Per-driver segment time and gap computation against a synthetic profile.

Uses a hand-built two-corner-one-straight TrackProfile (bypassing telemetry
entirely) so the elapsed-time interpolation and gap arithmetic are pinned
independently of how the profile itself gets derived.
"""

from __future__ import annotations

import pandas as pd
import pytest

from f1_fantasy.pace.segments import round_segment_profiles, segment_times
from f1_fantasy.pace.track_profile import Corner, Straight, TrackProfile


def _profile() -> TrackProfile:
    return TrackProfile(
        season=2026,
        round_number=1,
        event_name="Test GP",
        lap_distance=300.0,
        corners=[
            Corner(1, apex_distance=50, min_speed=100, speed_band="slow", direction="left",
                   entry_distance=0, exit_distance=100),
            Corner(2, apex_distance=250, min_speed=230, speed_band="fast", direction="right",
                   entry_distance=200, exit_distance=300),
        ],
        straights=[Straight(100, 200)],
    )


def _telemetry(distance: list[float], time_s: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"Distance": distance, "Time": pd.to_timedelta(time_s, unit="s")})


def test_segment_times_splits_elapsed_time_by_corner_and_straight():
    profile = _profile()
    # Uniform 1 unit-distance-per-second pace: corner 1 = 100s, straight = 100s, corner 2 = 100s.
    tel = _telemetry(list(range(0, 301, 10)), [d / 1.0 for d in range(0, 301, 10)])

    times = segment_times(tel, profile)

    assert times["slow"] == pytest.approx(100.0)
    assert times["straight"] == pytest.approx(100.0)
    assert times["fast"] == pytest.approx(100.0)
    assert times["medium"] == 0.0


def test_a_lap_that_does_not_cover_the_full_distance_skips_the_missing_segment():
    profile = _profile()
    # This lap only has telemetry up to distance 150 -- corner 2 is unreachable.
    tel = _telemetry(list(range(0, 151, 10)), [d / 1.0 for d in range(0, 151, 10)])

    times = segment_times(tel, profile)

    assert times["slow"] == pytest.approx(100.0)
    assert times["fast"] == 0.0


def test_round_segment_profiles_ranks_drivers_by_gap_to_the_class_best():
    profile = _profile()
    fast_driver = _telemetry(list(range(0, 301, 10)), [d / 2.0 for d in range(0, 301, 10)])  # 2x speed
    slow_driver = _telemetry(list(range(0, 301, 10)), [d / 1.0 for d in range(0, 301, 10)])  # 1x speed

    profiles = {p.driver: p for p in round_segment_profiles(profile, {"FAST": fast_driver, "SLOW": slow_driver})}

    assert profiles["FAST"].gap_pct["slow"] == pytest.approx(0.0)
    assert profiles["SLOW"].gap_pct["slow"] == pytest.approx(100.0)  # took twice as long
    assert profiles["SLOW"].gap_s["slow"] == pytest.approx(50.0, abs=0.5)


def test_a_class_nobody_has_a_time_for_reports_none_not_a_crash():
    profile = _profile()
    tel = _telemetry(list(range(0, 101, 10)), [float(d) for d in range(0, 101, 10)])  # never reaches corner 2

    (result,) = round_segment_profiles(profile, {"A": tel})

    assert result.gap_pct["fast"] is None
    assert result.gap_s["fast"] is None
