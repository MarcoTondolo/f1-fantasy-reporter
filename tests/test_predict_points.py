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


def test_calibrate_noise_scale_requires_field_size_or_strengths():
    with pytest.raises(ValueError):
        calibrate_noise_scale(0.9, trials=10, seed=0)


def test_calibrate_noise_scale_with_real_strengths_needs_far_less_noise_than_a_uniform_ladder():
    """The whole reason ``strengths`` exists: real competitors are not
    evenly spaced. A tightly-clustered real field (most drivers within 1.0
    of each other, like this project's own 2026 top group in quali-gap%
    terms) needs a much smaller noise_scale to hit the same target
    Spearman than a uniform ladder spanning the same range, because
    Spearman over a large field barely moves when a tight cluster gets
    reshuffled -- the ladder path was silently overestimating how much
    noise real, clustered fields need (the bug this test guards against)."""
    clustered = {f"D{i}": float(i) * 0.05 for i in range(10)}  # spans 0.0-0.45

    ladder_noise = calibrate_noise_scale(0.9, field_size=10, trials=400, seed=0)
    clustered_noise = calibrate_noise_scale(0.9, strengths=clustered, trials=400, seed=0)

    assert clustered_noise < ladder_noise


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


def test_constructor_points_add_the_measured_bonus_to_its_drivers_sum():
    """A constructor scores its two drivers' total plus the constructor-only
    sources (pit-stop award chief among them), measured at +7.57/race across
    2026's 143 constructor-rounds. Modelling it as a bare sum under-projects
    every two-constructor team by ~15 points.
    """
    from f1_fantasy.predict.points import CONSTRUCTOR_BONUS_MEAN, constructor_points_from_drivers

    totals = constructor_points_from_drivers(
        {"A": 10.0, "B": 4.0, "C": 6.0},
        {"A": "Team A", "B": "Team A", "C": "Team B"},
    )

    assert totals["Team A"] == pytest.approx(14.0 + CONSTRUCTOR_BONUS_MEAN)
    assert totals["Team B"] == pytest.approx(6.0 + CONSTRUCTOR_BONUS_MEAN)


def test_constructor_points_skips_drivers_with_no_known_constructor():
    from f1_fantasy.predict.points import constructor_points_from_drivers

    totals = constructor_points_from_drivers({"A": 10.0, "orphan": 99.0}, {"A": "Team A"})

    assert set(totals) == {"Team A"}


def test_fastest_lap_concentrates_on_the_fastest_car_not_the_front_row():
    """The earlier grid-bucket model gave the pole-sitter ~0.17 of fastest
    laps regardless of car pace. Real 2026: only four drivers took any
    fastest lap all season and Antonelli alone took 7 of 12. Strength here is
    form.py's gap-to-best %, so 0.0 is the quickest car.
    """
    from f1_fantasy.predict.points import FASTEST_LAP_TEMPERATURE, _strength_softmax

    strengths = {"quick": 0.0, "mid": 1.0, "slow": 2.0}
    shares = dict(zip(strengths, _strength_softmax(list(strengths), strengths, FASTEST_LAP_TEMPERATURE)))

    assert shares["quick"] > shares["mid"] > shares["slow"]
    # Steeply concentrated, not merely monotonic: the quickest car takes the
    # clear majority of a three-car field.
    assert shares["quick"] > 0.9


def test_fit_event_temperature_recovers_a_planted_concentration():
    """Known-answer check on the in-module fitter: winners drawn only from
    the strongest driver must fit a low (highly concentrated) temperature.
    """
    from f1_fantasy.predict.points import fit_event_temperature

    strengths_by_round = {r: {"quick": 0.0, "mid": 1.0, "slow": 2.0} for r in range(1, 9)}
    always_quick = {r: ["quick"] for r in range(1, 9)}
    spread_evenly = {1: ["quick"], 2: ["mid"], 3: ["slow"], 4: ["quick"], 5: ["mid"], 6: ["slow"]}

    concentrated, _ = fit_event_temperature(always_quick, strengths_by_round)
    diffuse, _ = fit_event_temperature(spread_evenly, strengths_by_round)

    assert concentrated < diffuse
