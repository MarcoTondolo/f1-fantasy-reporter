"""The Monte Carlo summary layer, against synthetic inputs with monkeypatched
form/reliability/qualifying -- real draws come from points.sample_field,
exercised directly elsewhere (test_predict_points.py); here the percentile
math and the price-rise wiring through prices.py are what's under test.
"""

from __future__ import annotations

import pytest

from f1_fantasy.predict import simulate as simulate_module
from f1_fantasy.predict.simulate import captaincy_ev, floor_ceiling_picks, simulate_round


def test_simulate_round_is_empty_with_no_form_history(monkeypatch):
    monkeypatch.setattr(simulate_module, "rolling_form", lambda season, rounds: {})

    result = simulate_round(2026, [1], 2, price_before={}, recent_price_history={}, n_samples=10)

    assert result == {}


def test_simulate_round_percentiles_are_ordered_and_mean_is_between_them(monkeypatch):
    from f1_fantasy.results import QualifyingResult

    monkeypatch.setattr(simulate_module, "rolling_form", lambda season, rounds: {"A": 0.0, "B": 1.0, "C": 2.0})
    monkeypatch.setattr(
        simulate_module,
        "fetch_qualifying",
        lambda season, rnd: [
            QualifyingResult(driver_code="A", driver_name="A", constructor="Team A", position=1),
            QualifyingResult(driver_code="B", driver_name="B", constructor="Team B", position=2),
            QualifyingResult(driver_code="C", driver_name="C", constructor="Team B", position=3),
        ],
    )
    monkeypatch.setattr(simulate_module, "constructor_history", lambda season, rounds: {})

    result = simulate_round(
        2026, [1], 2, price_before={"A": 10.0, "B": 8.0, "C": 6.0}, recent_price_history={}, n_samples=200, seed=0
    )

    for summary in result.values():
        assert summary.p10 <= summary.mean <= summary.p90
        assert 0.0 <= summary.p_price_rise <= 1.0
        assert summary.n_samples == 200


def test_simulate_round_gives_a_certain_winner_a_high_price_rise_probability(monkeypatch):
    """A driver who is far faster than the rest of an otherwise-even field
    should score enough, consistently, to make a price rise near-certain."""
    from f1_fantasy.results import QualifyingResult

    monkeypatch.setattr(
        simulate_module, "rolling_form", lambda season, rounds: {"A": 0.0, "B": 10.0, "C": 10.5, "D": 11.0}
    )
    monkeypatch.setattr(
        simulate_module,
        "fetch_qualifying",
        lambda season, rnd: [
            QualifyingResult(driver_code=d, driver_name=d, constructor="Team", position=i + 1)
            for i, d in enumerate("ABCD")
        ],
    )
    monkeypatch.setattr(simulate_module, "constructor_history", lambda season, rounds: {})

    result = simulate_round(
        2026,
        [1],
        2,
        price_before={"A": 5.0, "B": 5.0, "C": 5.0, "D": 5.0},  # cheap, budget tier -- big swings available
        recent_price_history={},
        n_samples=500,
        seed=1,
    )

    assert result["A"].p_price_rise > result["D"].p_price_rise


def test_captaincy_ev_doubles_the_mean_and_ranks_best_first():
    from f1_fantasy.predict.simulate import SimulationSummary

    summaries = {
        "A": SimulationSummary("A", 10.0, mean=5.0, p10=0.0, p90=10.0, p_price_rise=0.1, mean_delta_budget=0.0, n_samples=1),
        "B": SimulationSummary("B", 10.0, mean=12.0, p10=0.0, p90=20.0, p_price_rise=0.1, mean_delta_budget=0.0, n_samples=1),
    }

    ranked = captaincy_ev(summaries)

    assert ranked[0] == ("B", pytest.approx(24.0))
    assert ranked[1] == ("A", pytest.approx(10.0))


def test_floor_ceiling_picks_ranks_by_p10_and_p90_independently():
    from f1_fantasy.predict.simulate import SimulationSummary

    summaries = {
        "SAFE": SimulationSummary("SAFE", 10.0, mean=8.0, p10=6.0, p90=10.0, p_price_rise=0.0, mean_delta_budget=0.0, n_samples=1),
        "RISKY": SimulationSummary("RISKY", 10.0, mean=8.0, p10=-5.0, p90=30.0, p_price_rise=0.0, mean_delta_budget=0.0, n_samples=1),
    }

    picks = floor_ceiling_picks(summaries, n=2)

    assert picks["floor"][0] == "SAFE"
    assert picks["ceiling"][0] == "RISKY"
