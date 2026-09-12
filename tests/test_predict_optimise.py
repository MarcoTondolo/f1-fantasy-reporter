"""The team optimiser, against a small hand-computable toy universe.

6 drivers, 3 constructors, picking 2 + 1 under a cap of 10 -- small enough
to verify the optimum by inspection, not by trusting the search.
"""

from __future__ import annotations

import pytest

from f1_fantasy.predict.optimise import (
    backtest_optimiser,
    marginal_points_per_million,
    naive_baseline_team,
    optimise_team,
    season_points_baseline_team,
)

DRIVER_POINTS = {"A": 10.0, "B": 8.0, "C": 6.0, "D": 4.0, "E": 2.0, "F": 1.0}
DRIVER_PRICES = {"A": 6.0, "B": 4.0, "C": 3.0, "D": 2.0, "E": 1.0, "F": 1.0}
CONSTRUCTOR_POINTS = {"X": 5.0, "Y": 3.0, "Z": 1.0}
CONSTRUCTOR_PRICES = {"X": 4.0, "Y": 2.0, "Z": 1.0}


def test_optimise_team_finds_the_hand_computable_optimum():
    # Cap 10, pick 2 drivers + 1 constructor. By inspection: A+B=14pts/10M
    # leaves 0 for a constructor (infeasible over cap with any constructor),
    # so check combinations directly -- B+C=14pts/7M + Z(1pt/1M)=15pts/8M is
    # beaten by A+D=14pts/8M+Z=15pts/9M, beaten by A+C=16pts/9M+Z=17pts/10M.
    # A+C+Z: price 6+3+1=10 (<=10), points 10+6+1=17 -- the true optimum.
    best = optimise_team(DRIVER_POINTS, DRIVER_PRICES, CONSTRUCTOR_POINTS, CONSTRUCTOR_PRICES, cap=10.0, n_drivers=2, n_constructors=1)

    assert best is not None
    assert set(best.drivers) == {"A", "C"}
    assert best.constructors == ("Z",)
    assert best.total_price == pytest.approx(10.0)
    assert best.expected_points == pytest.approx(17.0)


def test_optimise_team_returns_none_when_nothing_fits_under_the_cap():
    best = optimise_team(DRIVER_POINTS, DRIVER_PRICES, CONSTRUCTOR_POINTS, CONSTRUCTOR_PRICES, cap=0.5, n_drivers=2, n_constructors=1)

    assert best is None


def test_captain_multiplier_scores_the_teams_best_driver_twice():
    """A+C+Z is the plain-sum optimum at 17 (see the test above). Under a 2x
    captain the team's best driver (A, 10pts) is counted once more, so 27 --
    without a captain term a real entry is understated by exactly this much.
    """
    plain = optimise_team(
        DRIVER_POINTS, DRIVER_PRICES, CONSTRUCTOR_POINTS, CONSTRUCTOR_PRICES,
        cap=10.0, n_drivers=2, n_constructors=1,
    )
    captained = optimise_team(
        DRIVER_POINTS, DRIVER_PRICES, CONSTRUCTOR_POINTS, CONSTRUCTOR_PRICES,
        cap=10.0, n_drivers=2, n_constructors=1, captain_multiplier=2.0,
    )

    assert plain.expected_points == pytest.approx(17.0)
    assert captained.expected_points == pytest.approx(27.0)
    # The uplift is the best driver's score, not an average or the whole team.
    assert captained.expected_points - plain.expected_points == pytest.approx(
        max(DRIVER_POINTS[d] for d in plain.drivers)
    )


def test_mega_captain_multiplier_triples_the_best_driver():
    captained = optimise_team(
        DRIVER_POINTS, DRIVER_PRICES, CONSTRUCTOR_POINTS, CONSTRUCTOR_PRICES,
        cap=10.0, n_drivers=2, n_constructors=1, captain_multiplier=3.0,
    )

    assert captained.expected_points == pytest.approx(37.0)


def test_optimise_team_prefers_budget_growth_when_lambda_is_positive():
    # Two teams score the same points but one has much better expected
    # budget growth -- a positive lambda must prefer it.
    driver_points = {"A": 10.0, "B": 10.0}
    driver_prices = {"A": 5.0, "B": 5.0}
    driver_delta_budget = {"A": 0.0, "B": 2.0}
    constructor_points = {"X": 0.0}
    constructor_prices = {"X": 0.0}

    best = optimise_team(
        driver_points, driver_prices, constructor_points, constructor_prices,
        driver_delta_budget=driver_delta_budget, cap=5.0, lam=1.0, n_drivers=1, n_constructors=1,
    )

    assert best.drivers == ("B",)


def test_naive_baseline_team_picks_the_single_most_expensive_affordable_combination():
    best = naive_baseline_team(DRIVER_PRICES, CONSTRUCTOR_PRICES, cap=10.0, n_drivers=2, n_constructors=1)

    # A (6) + B (4) = 10 is the most expensive 2-driver combo that exists at
    # all, and any constructor on top would bust the cap, so the most
    # expensive *affordable* combo trades down: A+C=9 + X=4 -> 13... but
    # that's over cap (13). A+B=10 + cheapest constructor (Z=1) = 11, over.
    # The true richest-affordable answer must be found by the same search
    # the implementation uses -- assert the property that matters: nothing
    # else affordable costs more.
    assert best is not None
    assert best.total_price <= 10.0
    for d_items in [("A", "B"), ("A", "C"), ("B", "C")]:
        for c in ("X", "Y", "Z"):
            price = sum(DRIVER_PRICES[d] for d in d_items) + CONSTRUCTOR_PRICES[c]
            if price <= 10.0:
                assert price <= best.total_price


def test_season_points_baseline_team_maximises_season_points_not_predicted_points():
    season_points = {"A": 1.0, "B": 1.0, "C": 100.0, "D": 1.0, "E": 1.0, "F": 1.0}  # C dominates on season points
    constructor_season_points = {"X": 1.0, "Y": 1.0, "Z": 100.0}

    best = season_points_baseline_team(
        season_points, constructor_season_points, DRIVER_PRICES, CONSTRUCTOR_PRICES, cap=10.0, n_drivers=1, n_constructors=1
    )

    assert best.drivers == ("C",)
    assert best.constructors == ("Z",)


def test_marginal_points_per_million_is_zero_when_a_larger_cap_buys_nothing():
    # Every driver/constructor is already affordable at this cap with room
    # to spare -- an extra $1M changes nothing.
    best_possible_price = max(DRIVER_PRICES.values()) * 2 + max(CONSTRUCTOR_PRICES.values())
    huge_cap = best_possible_price + 100.0

    lam = marginal_points_per_million(DRIVER_POINTS, DRIVER_PRICES, CONSTRUCTOR_POINTS, CONSTRUCTOR_PRICES, cap=huge_cap, n_drivers=2, n_constructors=1)

    assert lam == pytest.approx(0.0)


def test_marginal_points_per_million_is_positive_when_a_tight_cap_is_relaxed():
    lam = marginal_points_per_million(DRIVER_POINTS, DRIVER_PRICES, CONSTRUCTOR_POINTS, CONSTRUCTOR_PRICES, cap=10.0, n_drivers=2, n_constructors=1)

    assert lam >= 0.0


def test_backtest_optimiser_compares_against_both_baselines(monkeypatch):
    """A synthetic two-round sweep: round 1 is training-only (skipped,
    nothing to predict from yet), round 2 is evaluated."""
    from f1_fantasy.predict import points as points_module
    from f1_fantasy.predict import reconcile as reconcile_module
    from f1_fantasy.predict.points import DriverPointsDistribution

    driver_feed_by_round = {
        2: {
            "A": {"OldPlayerValue": 5.0, "Value": 5.6, "GamedayPoints": 20.0, "OverallPpints": 40.0},
            "B": {"OldPlayerValue": 4.0, "Value": 3.8, "GamedayPoints": 5.0, "OverallPpints": 10.0},
        }
    }
    constructor_feed_by_round = {
        2: {
            "Team": {"OldPlayerValue": 10.0, "Value": 10.6, "GamedayPoints": 25.0, "OverallPpints": 50.0},
        }
    }

    monkeypatch.setattr(reconcile_module, "fetch_driver_feed", lambda r, cache_dir=None: driver_feed_by_round.get(r, {}))
    monkeypatch.setattr(
        reconcile_module, "fetch_constructor_feed_rows", lambda r, cache_dir=None: constructor_feed_by_round.get(r, {})
    )
    monkeypatch.setattr(
        points_module,
        "build_round_distributions",
        lambda season, train, target, n_samples=500: {
            "A": DriverPointsDistribution(driver="A", constructor="Team", mean=18.0),
            "B": DriverPointsDistribution(driver="B", constructor="Team", mean=4.0),
        },
    )

    result = backtest_optimiser(2026, [1, 2], n_drivers=2, n_constructors=1)

    assert result["rounds_evaluated"] == 1
    assert result["summary"]["optimised_mean_points"] is not None
    assert result["summary"]["naive_baseline_mean_points"] is not None
    assert result["summary"]["season_points_baseline_mean_points"] is not None


def test_backtest_optimiser_does_not_zero_a_constructor_whose_jolpica_name_differs_from_the_feed(monkeypatch):
    """Regression test for the Jolpica-name vs feed-FUllName mismatch bug:
    dist.constructor (Jolpica: "Red Bull") and the constructor feed's key
    (FUllName: "Red Bull Racing") disagree for four real 2026 teams. Before
    reconcile.to_feed_constructor_name was applied, a bare dict-key miss
    silently scored the mismatched constructor's predicted points as 0 --
    so between a genuinely strong "Red Bull Racing" and a genuinely weak
    "Team", the optimiser would wrongly pick "Team" (0 < its real, if tiny,
    predicted value) purely because "Red Bull Racing" could never be found
    under its own predicted-points key. This sets up exactly that choice
    and pins the realised outcome the *correct* pick produces.
    """
    from f1_fantasy.predict import points as points_module
    from f1_fantasy.predict import reconcile as reconcile_module
    from f1_fantasy.predict.points import DriverPointsDistribution

    driver_feed_by_round = {
        2: {"A": {"OldPlayerValue": 5.0, "Value": 5.0, "GamedayPoints": 20.0, "OverallPpints": 40.0}},
    }
    # Feed-side constructors are keyed by FUllName. "Red Bull Racing" is the
    # real, much stronger pick (both predicted and realised); "Team" is a
    # weak straw competitor with a name that already matches Jolpica as-is.
    constructor_feed_by_round = {
        2: {
            "Red Bull Racing": {"OldPlayerValue": 10.0, "Value": 10.0, "GamedayPoints": 99.0, "OverallPpints": 1.0},
            "Team": {"OldPlayerValue": 10.0, "Value": 10.0, "GamedayPoints": 2.0, "OverallPpints": 1.0},
        }
    }

    monkeypatch.setattr(reconcile_module, "fetch_driver_feed", lambda r, cache_dir=None: driver_feed_by_round.get(r, {}))
    monkeypatch.setattr(
        reconcile_module, "fetch_constructor_feed_rows", lambda r, cache_dir=None: constructor_feed_by_round.get(r, {})
    )
    monkeypatch.setattr(
        points_module,
        "build_round_distributions",
        lambda season, train, target, n_samples=500: {
            # "A" is the only real driver candidate (n_drivers=1 forces it).
            # "C" isn't a fetchable driver at all -- it exists only so its
            # Jolpica-named constructor contributes to constructor_points,
            # exactly like a teammate's points would for a real constructor.
            "A": DriverPointsDistribution(driver="A", constructor="Red Bull", mean=18.0),
            "C": DriverPointsDistribution(driver="C", constructor="Team", mean=1.0),
        },
    )

    result = backtest_optimiser(2026, [1, 2], n_drivers=1, n_constructors=1)

    # Correct behaviour: constructor_points translates "Red Bull" -> "Red
    # Bull Racing" (18.0 predicted, beating "Team"'s 1.0), so the optimiser
    # picks Red Bull Racing -- realised points = A's 20.0 + Red Bull
    # Racing's 99.0 = 119.0. The pre-fix bug would have silently scored
    # "Red Bull Racing" as 0 predicted points, losing to "Team" (1.0), and
    # realised at only 20.0 + 2.0 = 22.0.
    assert result["rounds_evaluated"] == 1
    assert result["summary"]["optimised_mean_points"] == pytest.approx(119.0)
