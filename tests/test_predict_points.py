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
    build_grid_conditioned_distributions,
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
        assert distribution.p10 <= distribution.mean <= distribution.p90


def test_sample_field_with_fixed_grid_uses_it_instead_of_simulating_one():
    from f1_fantasy.predict.scoring import qualifying_points

    strengths = {"A": 0.0, "B": 1.0, "C": 2.0}  # A is fastest by form
    constructor_of = {"A": "Team A", "B": "Team B", "C": "Team B"}
    fixed_grid = {"A": 3, "B": 1, "C": 2}  # but A actually qualified last
    rng = np.random.default_rng(0)

    for _ in range(20):
        draw = sample_field(strengths, constructor_of, {}, fixed_grid=fixed_grid, rng=rng)
        # The grid a real qualifying result set must be respected verbatim,
        # regardless of what the simulated quali order would have been.
        for driver, grid_position in fixed_grid.items():
            assert draw[driver].qualifying == qualifying_points(grid_position)


def test_sample_field_with_fixed_grid_missing_a_driver_raises():
    strengths = {"A": 0.0, "B": 1.0}
    rng = np.random.default_rng(0)

    with pytest.raises(ValueError, match="missing drivers"):
        sample_field(strengths, {}, {}, fixed_grid={"A": 1}, rng=rng)


def test_build_grid_conditioned_distributions_conditions_on_the_real_grid(monkeypatch):
    from f1_fantasy.results import QualifyingResult

    # Form says A is fastest, C is slowest -- but the real grid (what
    # actually happened) starts C on pole and A at the back.
    monkeypatch.setattr(points_module, "rolling_form", lambda season, rounds: {"A": 0.0, "B": 1.0, "C": 2.0})
    real_grid = [
        QualifyingResult(driver_code="C", driver_name="C", constructor="Team B", position=1),
        QualifyingResult(driver_code="B", driver_name="B", constructor="Team B", position=2),
        QualifyingResult(driver_code="A", driver_name="A", constructor="Team A", position=3),
    ]
    monkeypatch.setattr(points_module, "fetch_qualifying", lambda season, rnd: real_grid)
    monkeypatch.setattr(points_module, "constructor_history", lambda season, rounds: {})

    result = build_grid_conditioned_distributions(2026, [1], 2, n_samples=200, seed=0)

    assert set(result) == {"A", "B", "C"}
    # A is the fastest car starting dead last: it should show far more
    # simulated positions-gained than C, the slowest car starting on pole --
    # build_round_distributions, which simulates its own qualifying, would
    # mostly never put A at the back to begin with.
    assert result["A"].components["positions_gained"] > result["C"].components["positions_gained"]


def test_build_grid_conditioned_distributions_raises_without_a_real_qualifying_result(monkeypatch):
    from f1_fantasy.results import QualifyingResult

    monkeypatch.setattr(points_module, "rolling_form", lambda season, rounds: {"A": 0.0, "B": 1.0})
    train_round_result = [QualifyingResult(driver_code="A", driver_name="A", constructor="Team A", position=1)]

    def fetch_qualifying(season, rnd):
        return [] if rnd == 2 else train_round_result

    monkeypatch.setattr(points_module, "fetch_qualifying", fetch_qualifying)
    monkeypatch.setattr(points_module, "constructor_history", lambda season, rounds: {})

    with pytest.raises(ValueError, match="no qualifying result"):
        build_grid_conditioned_distributions(2026, [1], 2)


def test_constructor_points_add_five_per_driver_reaching_q3():
    """A real rule, not a fitted residual: across 2026 rounds 1-13 a
    constructor's QualifyingPoints exceeded the sum of its two drivers' by
    exactly 5 per car classified in the top 10, in 76 of 77 cases.
    """
    from f1_fantasy.predict.points import (
        CONSTRUCTOR_Q3_BONUS,
        CONSTRUCTOR_RACE_RESIDUAL_MEAN,
        constructor_points_from_drivers,
    )

    both_in_q3 = constructor_points_from_drivers(
        {"A": 10.0, "B": 4.0},
        {"A": "Team A", "B": "Team A"},
        p_q3={"A": 1.0, "B": 1.0},
    )
    neither_in_q3 = constructor_points_from_drivers(
        {"A": 10.0, "B": 4.0},
        {"A": "Team A", "B": "Team A"},
        p_q3={"A": 0.0, "B": 0.0},
    )

    assert both_in_q3["Team A"] == pytest.approx(14.0 + 2 * CONSTRUCTOR_Q3_BONUS + CONSTRUCTOR_RACE_RESIDUAL_MEAN)
    assert neither_in_q3["Team A"] == pytest.approx(14.0 + CONSTRUCTOR_RACE_RESIDUAL_MEAN)
    # A Q3 probability is an expectation, so it scales the bonus.
    half = constructor_points_from_drivers(
        {"A": 10.0}, {"A": "Team A"}, p_q3={"A": 0.5}
    )
    assert half["Team A"] == pytest.approx(10.0 + 0.5 * CONSTRUCTOR_Q3_BONUS + CONSTRUCTOR_RACE_RESIDUAL_MEAN)


def test_constructor_points_exclude_driver_of_the_day():
    """Constructors do not receive Driver of the Day -- Mercedes' cumulative
    dotd_pts is 0 while Antonelli's alone is 30 -- so a driver's DOTD
    expectation must come back out of its constructor's total.
    """
    from f1_fantasy.predict.points import CONSTRUCTOR_RACE_RESIDUAL_MEAN, constructor_points_from_drivers

    totals = constructor_points_from_drivers(
        {"A": 20.0},
        {"A": "Team A"},
        driver_dotd_points={"A": 3.0},
    )

    assert totals["Team A"] == pytest.approx(20.0 - 3.0 + CONSTRUCTOR_RACE_RESIDUAL_MEAN)


def test_constructor_points_skip_the_rule_terms_rather_than_guessing_them():
    """Omitting p_q3/dotd must drop those terms, not substitute a default --
    a caller working from realised points needs the bare sum.
    """
    from f1_fantasy.predict.points import constructor_points_from_drivers

    totals = constructor_points_from_drivers({"A": 20.0}, {"A": "Team A"}, race_residual=0.0)

    assert totals["Team A"] == pytest.approx(20.0)


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


def test_overtake_race_factor_is_shared_by_the_whole_field_not_drawn_per_driver():
    """Overtaking is 30% of all points in this game and its per-race field
    total ranges 41-300 in real 2026 data. Independent per-driver draws give
    a field total with CV ~0.18 against the real 0.70, so every simulated
    race looked average. One shared factor per scenario fixes the dispersion
    -- this pins the sharing, which is the part that matters.
    """
    import numpy as np

    from f1_fantasy.predict.points import (
        OVERTAKE_MEAN,
        _sample_overtake_points,
        _sample_race_overtake_factor,
    )

    rng = np.random.default_rng(11)
    shared, independent = [], []
    for _ in range(1500):
        factor = _sample_race_overtake_factor(rng)
        shared.append(sum(_sample_overtake_points(rng, OVERTAKE_MEAN * factor) for _ in range(23)))
        independent.append(sum(_sample_overtake_points(rng, OVERTAKE_MEAN) for _ in range(23)))

    def cv(values):
        return float(np.std(values) / np.mean(values))

    # Same mean, far wider field-total spread.
    assert np.mean(shared) == pytest.approx(np.mean(independent), rel=0.1)
    assert cv(shared) > 3 * cv(independent)
    # And it lands near the real 0.70 rather than merely being "bigger".
    assert 0.55 < cv(shared) < 0.85


def test_overtake_points_scale_with_the_scenario_mean():
    import numpy as np

    from f1_fantasy.predict.points import OVERTAKE_MEAN, _sample_overtake_points

    rng = np.random.default_rng(3)
    quiet = [_sample_overtake_points(rng, OVERTAKE_MEAN * 0.3) for _ in range(3000)]
    busy = [_sample_overtake_points(rng, OVERTAKE_MEAN * 2.5) for _ in range(3000)]

    assert np.mean(quiet) < np.mean(busy)
    assert np.mean(busy) / np.mean(quiet) == pytest.approx(2.5 / 0.3, rel=0.25)


def test_passable_cars_ahead_counts_only_cars_you_can_actually_pass():
    """The ex-ante overtaking feature: a car ahead counts only if you are
    faster than it by more than the pace margin. Strengths are gap-to-best
    percentages, so lower is quicker.
    """
    from f1_fantasy.predict.points import passable_cars_ahead

    grid = {"quick_but_last": 3, "slow_pole": 1, "slow_second": 2}
    strengths = {"quick_but_last": 0.0, "slow_pole": 3.0, "slow_second": 3.0}

    counts = passable_cars_ahead(grid, strengths, margin=0.25)

    # The quick car starts behind two cars it is 3.0 faster than.
    assert counts["quick_but_last"] == 2
    # The slow cars have nothing ahead they are quicker than.
    assert counts["slow_pole"] == 0
    assert counts["slow_second"] == 0


def test_passable_cars_ahead_scores_a_genuinely_slow_backmarker_near_zero():
    """This is what separates the feature from a places-gained term, which
    handed back-markers the most overtake points. A slow car starting last
    has many cars ahead and is faster than none of them.
    """
    from f1_fantasy.predict.points import passable_cars_ahead

    grid = {f"fast{i}": i for i in range(1, 6)}
    strengths = {f"fast{i}": 0.1 * i for i in range(1, 6)}
    grid["slow"] = 6
    strengths["slow"] = 9.0

    counts = passable_cars_ahead(grid, strengths)

    assert counts["slow"] == 0
    # Whereas the quickest car, were it to start last, scores every car it
    # clears the margin against: fast4 (0.4), fast5 (0.5) and slow (9.0),
    # but not fast2/fast3, which it only out-paces by 0.1 and 0.2.
    grid["fast1"] = 99
    assert passable_cars_ahead(grid, strengths)["fast1"] == 3


def test_passable_pace_margin_excludes_marginally_slower_cars():
    """A car you only barely out-pace does not count -- the margin is what
    the 'enough pace differential to pass' idea operationalises, even though
    the data could not pin a specific threshold.
    """
    from f1_fantasy.predict.points import passable_cars_ahead

    grid = {"me": 2, "ahead": 1}
    barely_slower = {"me": 0.0, "ahead": 0.1}
    clearly_slower = {"me": 0.0, "ahead": 2.0}

    assert passable_cars_ahead(grid, barely_slower, margin=0.25)["me"] == 0
    assert passable_cars_ahead(grid, clearly_slower, margin=0.25)["me"] == 1
