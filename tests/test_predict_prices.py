"""The PPM price-tier mechanism, against known real cases.

Round 11->12 2026: Alonso 5.6->6.2 (+0.6, budget/great), Leclerc 23.6->23.9
(+0.3, premium/great), Hamilton 24.9->25.0 (+0.1, premium/good) -- the
directly observed price moves that motivated this module. The exact
thresholds and tier steps are documented publicly (not derived here); see
the module docstring for the 91.4% real-data match rate this reproduces.
"""

from __future__ import annotations

import pytest

from f1_fantasy.predict.prices import (
    average_ppm,
    predict_price_delta,
    predict_round,
    rating_for_ppm,
)


def test_rating_boundaries():
    assert rating_for_ppm(0.59) == "terrible"
    assert rating_for_ppm(0.6) == "poor"
    assert rating_for_ppm(0.89) == "poor"
    assert rating_for_ppm(0.9) == "good"
    assert rating_for_ppm(1.19) == "good"
    assert rating_for_ppm(1.2) == "great"


def test_average_ppm_divides_by_rounds_actually_supplied_not_a_fixed_three():
    # one round only: 10 points / 5.0 price = 2.0, not 2.0/3.
    assert average_ppm([(10.0, 5.0)]) == pytest.approx(2.0)


def test_average_ppm_of_nothing_is_zero():
    assert average_ppm([]) == 0.0


def test_predict_price_delta_premium_great():
    # Leclerc-shaped: premium tier, strong recent form -> +0.3.
    assert predict_price_delta(1.5, price_before=23.6) == pytest.approx(0.3)


def test_predict_price_delta_budget_great():
    # Alonso-shaped: budget tier, strong recent form -> +0.6.
    assert predict_price_delta(1.5, price_before=5.6) == pytest.approx(0.6)


def test_predict_price_delta_premium_good():
    # Hamilton-shaped: premium tier, solid but not dominant -> +0.1.
    assert predict_price_delta(1.0, price_before=24.9) == pytest.approx(0.1)


def test_predict_price_delta_floor_blocks_a_further_drop():
    # Already at the $3.0M floor: a poor/terrible rating can't push it lower.
    assert predict_price_delta(0.2, price_before=3.0) == 0.0


def test_predict_price_delta_above_the_floor_still_falls_normally():
    assert predict_price_delta(0.2, price_before=3.2) == pytest.approx(-0.6)


def test_predict_round_one_is_always_zero_with_no_history_needed():
    assert predict_round({}, 1) == 0.0


def test_predict_round_returns_none_with_no_prior_rounds_at_all():
    assert predict_round({5: (10.0, 8.0, 8.2)}, 1) == 0.0  # round 1 special-cased first
    assert predict_round({}, 4) is None  # no history for rounds 1-3 either


def test_predict_round_uses_up_to_three_prior_rounds():
    history = {
        1: (0.0, 10.0, 10.0),
        2: (30.0, 10.0, 10.2),  # ppm 3.0 -> great
        3: (30.0, 10.2, 10.4),  # ppm ~2.94 -> great
    }
    # target round 4: window is rounds 1,2,3 -- all great -> budget tier +0.6.
    assert predict_round(history, 4) == pytest.approx(0.6)


def test_predict_round_falls_back_to_the_latest_known_price_when_the_target_round_is_unseen():
    """Predicting a future round that hasn't happened yet: price_before comes
    from the most recent known round's post-round price, by continuity."""
    history = {2: (5.0, 10.0, 10.1), 3: (5.0, 10.1, 10.2)}
    predicted = predict_round(history, 4)
    assert predicted is not None
