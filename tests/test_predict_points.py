"""The joint field sampler, against synthetic inputs with known answers.

calibrate_noise_scale and the real constants it derived (QUALI_NOISE_SCALE_2026,
RACE_NOISE_SCALE) are exercised by a cheap monotonicity check here, not a
reproduction of the exact real-world numbers (that would make these tests
flaky against RNG and trial-count choices) -- the real calibration is
documented in the module docstring and was run once to freeze the constants.
"""

from __future__ import annotations

import numpy as np
import pytest

from f1_fantasy.predict import points as points_module
from f1_fantasy.predict.points import (
    build_round_distributions,
    calibrate_noise_scale,
    plackett_luce_order,
    sample_field,
)


def test_plackett_luce_order_with_zero_noise_reproduces_the_strength_ranking():
    strengths = {"A": 2.0, "B": 0.0, "C": 1.0}  # lower is better
    rng = np.random.default_rng(0)

    for _ in range(10):
        assert plackett_luce_order(strengths, 0.0, rng) == ["B", "C", "A"]


def test_plackett_luce_order_is_a_valid_permutation():
    strengths = {f"D{i}": float(i) for i in range(15)}
    rng = np.random.default_rng(1)

    order = plackett_luce_order(strengths, noise_scale=5.0, rng=rng)

    assert sorted(order) == sorted(strengths)


def test_calibrate_noise_scale_is_monotonic_in_target_spearman():
    # A lower target correlation requires *more* randomization, i.e. a
    # larger noise_scale -- this is the property the binary search relies on.
    loose = calibrate_noise_scale(0.5, field_size=10, trials=300, seed=0)
    tight = calibrate_noise_scale(0.95, field_size=10, trials=300, seed=0)

    assert loose > tight


def test_sample_field_is_empty_for_an_empty_field():
    rng = np.random.default_rng(0)
    assert sample_field({}, {}, {}, rng=rng) == {}


def test_sample_field_a_certain_dnf_always_takes_the_flat_charge():
    """A constructor with probability 1.0 DNFs every draw, every time, and
    never earns position or positions-gained points."""
    strengths = {"A": 0.0, "B": 1.0, "C": 2.0}
    constructor_of = {"A": "Unlucky", "B": "Team", "C": "Team"}
    dnf_probabilities = {"Unlucky": 1.0, "Team": 0.0}
    rng = np.random.default_rng(2)

    for _ in range(20):
        draw = sample_field(strengths, constructor_of, dnf_probabilities, rng=rng)
        assert draw["A"].dnf == -20.0
        assert draw["A"].position == 0.0
        assert draw["A"].positions_gained == 0.0


def test_sample_field_a_guaranteed_survivor_never_dnfs():
    strengths = {"A": 0.0, "B": 1.0, "C": 2.0}
    constructor_of = {"A": "Team", "B": "Team", "C": "Team"}
    dnf_probabilities = {"Team": 0.0}
    rng = np.random.default_rng(3)

    for _ in range(20):
        draw = sample_field(strengths, constructor_of, dnf_probabilities, rng=rng)
        assert draw["A"].dnf == 0.0


def test_sample_field_awards_exactly_one_fastest_lap_and_one_dotd_per_draw():
    strengths = {f"D{i}": float(i) for i in range(12)}
    constructor_of = {d: "Team" for d in strengths}
    dnf_probabilities = {"Team": 0.0}
    rng = np.random.default_rng(4)

    draw = sample_field(strengths, constructor_of, dnf_probabilities, rng=rng)

    assert sum(1 for bd in draw.values() if bd.fastest_lap) == 1
    assert sum(1 for bd in draw.values() if bd.driver_of_the_day) == 1


def test_build_round_distributions_is_empty_with_no_form_history(monkeypatch):
    monkeypatch.setattr(points_module, "rolling_form", lambda season, rounds: {})

    assert build_round_distributions(2026, [1], 2) == {}


def test_build_round_distributions_reduces_many_draws_to_one_summary_per_driver(monkeypatch):
    from f1_fantasy.results import QualifyingResult

    monkeypatch.setattr(points_module, "rolling_form", lambda season, rounds: {"A": 0.0, "B": 1.0, "C": 2.0})
    monkeypatch.setattr(
        points_module,
        "fetch_qualifying",
        lambda season, rnd: [
            QualifyingResult(driver_code="A", driver_name="A", constructor="Team A", position=1),
            QualifyingResult(driver_code="B", driver_name="B", constructor="Team B", position=2),
            QualifyingResult(driver_code="C", driver_name="C", constructor="Team B", position=3),
        ],
    )
    monkeypatch.setattr(points_module, "constructor_history", lambda season, rounds: {})

    result = build_round_distributions(2026, [1], 2, n_samples=50, seed=0)

    assert set(result) == {"A", "B", "C"}
    assert result["A"].constructor == "Team A"
    # The fastest driver (A) should, on average, out-score the slowest (C).
    assert result["A"].mean > result["C"].mean
    for distribution in result.values():
        assert 0.0 <= distribution.p_dnf <= 1.0
