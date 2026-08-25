"""The pure aggregation/prediction functions in track_backtest.py.

round_track_and_segments and backtest_track_model hit FastF1 and Jolpica
directly and are exercised live (see data/pace/track_backtest_2026_r1-12.json,
produced by a real run against the 2026 season); what's testable offline is
the arithmetic each of those calls into.
"""

from __future__ import annotations

import pytest

from f1_fantasy.pace.segments import DriverSegmentProfile
from f1_fantasy.pace.track_backtest import predict_ranking, strength_ratings, track_class_weights


def _profile(driver: str, round_number: int, **gaps: float | None) -> DriverSegmentProfile:
    full = {"slow": None, "medium": None, "fast": None, "straight": None}
    full.update(gaps)
    return DriverSegmentProfile(driver=driver, round_number=round_number, times={}, gap_pct=full, gap_s={})


def test_strength_ratings_averages_gap_pct_across_rounds():
    profiles = [
        _profile("VER", 1, slow=2.0, straight=1.0),
        _profile("VER", 2, slow=4.0, straight=3.0),
    ]

    ratings = strength_ratings(profiles)

    assert ratings["VER"]["slow"] == pytest.approx(3.0)
    assert ratings["VER"]["straight"] == pytest.approx(2.0)


def test_strength_ratings_skips_missing_classes_rather_than_treating_as_zero():
    profiles = [_profile("VER", 1, slow=2.0), _profile("VER", 2, slow=None)]

    ratings = strength_ratings(profiles)

    assert ratings["VER"]["slow"] == pytest.approx(2.0)  # only the round with data counts
    assert ratings["VER"]["fast"] is None


def test_track_class_weights_are_the_reference_laps_own_time_share():
    import pandas as pd

    from f1_fantasy.pace.track_profile import Corner, Straight, TrackProfile

    profile = TrackProfile(
        season=2026, round_number=1, event_name="Test", lap_distance=200,
        corners=[Corner(1, 50, 100, "slow", "left", 0, 100)],
        straights=[Straight(100, 200)],
    )
    tel = pd.DataFrame({
        "Distance": [0, 50, 100, 150, 200],
        "Time": pd.to_timedelta([0, 30, 60, 80, 100], unit="s"),
    })

    weights = track_class_weights(profile, tel)

    assert weights["slow"] == pytest.approx(0.6)
    assert weights["straight"] == pytest.approx(0.4)
    assert sum(weights.values()) == pytest.approx(1.0)


def test_predict_ranking_weights_each_class_by_its_track_share():
    ratings = {
        "A": {"slow": 0.0, "medium": None, "fast": None, "straight": 10.0},
        "B": {"slow": 10.0, "medium": None, "fast": None, "straight": 0.0},
    }
    # A track that's almost entirely a straight: B's straight-line strength dominates.
    weights = {"slow": 0.05, "medium": 0.0, "fast": 0.0, "straight": 0.95}

    scores = predict_ranking(ratings, weights)

    assert scores["B"] < scores["A"]  # B predicted faster overall


def test_predict_ranking_drops_a_driver_with_no_rated_classes_at_all():
    ratings = {"A": {"slow": None, "medium": None, "fast": None, "straight": None}}
    weights = {"slow": 0.25, "medium": 0.25, "fast": 0.25, "straight": 0.25}

    scores = predict_ranking(ratings, weights)

    assert "A" not in scores
