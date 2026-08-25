"""Constructor direction-sensitivity correlation, against synthetic rounds
with a known relationship built in."""

from __future__ import annotations

import pandas as pd

from f1_fantasy.pace.tyre_asymmetry import constructor_direction_sensitivity, team_by_driver


def test_team_by_driver_reads_the_laps_team_column():
    laps = pd.DataFrame({"Driver": ["VER", "PER"], "Team": ["Red Bull", "Red Bull"]})

    assert team_by_driver(laps) == {"VER": "Red Bull", "PER": "Red Bull"}


def test_a_constructor_whose_degradation_tracks_direction_balance_shows_positive_correlation():
    # Degradation rises in lockstep with corner-direction balance -- a
    # textbook right-side-limited signature.
    rounds = [
        {"balance": -0.5, "degradation": {"VER": ("Red Bull", 0.1)}},
        {"balance": 0.0, "degradation": {"VER": ("Red Bull", 0.3)}},
        {"balance": 0.5, "degradation": {"VER": ("Red Bull", 0.5)}},
        {"balance": 1.0, "degradation": {"VER": ("Red Bull", 0.7)}},
    ]

    result = constructor_direction_sensitivity(rounds)

    assert result["Red Bull"]["correlation"] > 0.99
    assert result["Red Bull"]["n"] == 4


def test_an_inverse_relationship_shows_negative_correlation():
    rounds = [
        {"balance": -0.5, "degradation": {"HAM": ("Ferrari", 0.7)}},
        {"balance": 0.0, "degradation": {"HAM": ("Ferrari", 0.5)}},
        {"balance": 0.5, "degradation": {"HAM": ("Ferrari", 0.3)}},
        {"balance": 1.0, "degradation": {"HAM": ("Ferrari", 0.1)}},
    ]

    result = constructor_direction_sensitivity(rounds)

    assert result["Ferrari"]["correlation"] < -0.99


def test_too_few_rounds_reports_no_correlation_rather_than_a_noisy_one():
    rounds = [
        {"balance": -0.5, "degradation": {"NOR": ("McLaren", 0.2)}},
        {"balance": 0.5, "degradation": {"NOR": ("McLaren", 0.4)}},
    ]

    result = constructor_direction_sensitivity(rounds)

    assert result["McLaren"]["correlation"] is None
    assert result["McLaren"]["n"] == 2


def test_a_blank_team_name_is_excluded_rather_than_bucketed_as_a_fake_team():
    """Confirmed live: FP1's Team column comes back blank for some
    driver/round pairs. Bucketing those under "" would mix unrelated
    drivers' degradation into one meaningless "team"."""
    rounds = [
        {"balance": -0.5, "degradation": {"A": ("", 0.1), "VER": ("Red Bull", 0.2)}},
        {"balance": 0.5, "degradation": {"B": ("", 0.9), "VER": ("Red Bull", 0.4)}},
    ]

    result = constructor_direction_sensitivity(rounds)

    assert "" not in result
    assert "Red Bull" in result


def test_missing_degradation_values_are_skipped_not_treated_as_zero():
    rounds = [
        {"balance": -0.5, "degradation": {"NOR": ("McLaren", 0.2)}},
        {"balance": 0.0, "degradation": {"NOR": ("McLaren", None)}},
        {"balance": 0.5, "degradation": {"NOR": ("McLaren", 0.4)}},
        {"balance": 1.0, "degradation": {"NOR": ("McLaren", 0.6)}},
    ]

    result = constructor_direction_sensitivity(rounds)

    assert result["McLaren"]["n"] == 3
