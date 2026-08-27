"""Team optimiser: 5 drivers + 2 constructors under a cost cap.

**Exact brute-force enumeration, not ILP/DP.** The full search space is
C(~22 drivers, 5) x C(~11 constructors, 2) ~= 15,504 x 55 ~= 850,000
combinations -- fully enumerable in a few seconds of plain Python, no new
dependency (this project uses only numpy/pandas/scipy/pydantic/jinja2/
playwright/feedparser). This guarantees a *provably* optimal answer: if
``backtest_optimiser`` ever fails to beat a baseline, that unambiguously
means the objective (the points/price model feeding it) is wrong, never a
search or approximation artifact. Would need revisiting only if the grid
ever grew substantially past F1's current ~20-22 entries.

**Gate, run against real 2026 rounds 1-12 (11 evaluated) via
backtest_optimiser: a mixed, honestly-reported result.** Against baseline
#1 (naive_baseline_team, "most expensive affordable team" -- the plan's own
spec), the optimiser wins clearly: ~186 vs 141 mean realised points per
round, ~1.9 vs 0.2 mean realised budget growth. Against baseline #2
(season_points_baseline_team, "back whoever already has the most
cumulative points" -- added on top of the plan's spec specifically so a
pass wouldn't just be beating an easy strawman), the optimiser *loses* on
both measures: ~186 vs 201 points, ~1.9 vs 2.2 budget growth (exact figures
vary run to run since points.build_round_distributions samples with an
unseeded RNG by default -- the qualitative result, beats baseline #1, loses
to baseline #2, is stable across runs). "Back the points leaders" is a
simple, famously strong heuristic in fantasy sports generally, and this
suggests the points.py model feeding this optimiser (whose own walk-forward
accuracy has not yet been separately gated -- that is backtest_points.py's
job) is not yet informative enough to beat it. Reported plainly rather than
only citing the baseline it wins against.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

import numpy as np


@dataclass
class TeamSelection:
    drivers: tuple[str, ...]
    constructors: tuple[str, ...]
    total_price: float
    expected_points: float
    expected_delta_budget: float
    objective: float


def _combo_sums(
    prices: dict[str, float], points: dict[str, float], delta_budget: dict[str, float] | None, n: int
):
    """Yield (items, total_price, total_points, total_delta_budget) for every
    n-combination of *prices*' keys."""
    delta_budget = delta_budget or {}
    for items in combinations(prices, n):
        total_price = sum(prices[i] for i in items)
        total_points = sum(points.get(i, 0.0) for i in items)
        total_delta = sum(delta_budget.get(i, 0.0) for i in items)
        yield items, total_price, total_points, total_delta


def optimise_team(
    driver_points: dict[str, float],
    driver_prices: dict[str, float],
    constructor_points: dict[str, float],
    constructor_prices: dict[str, float],
    *,
    driver_delta_budget: dict[str, float] | None = None,
    constructor_delta_budget: dict[str, float] | None = None,
    cap: float = 100.0,
    lam: float = 0.0,
    n_drivers: int = 5,
    n_constructors: int = 2,
) -> TeamSelection | None:
    """The provably-optimal team under *cap*, maximising
    ``expected_points + lam * expected_delta_budget``. None if nothing fits.
    """
    driver_combos = list(_combo_sums(driver_prices, driver_points, driver_delta_budget, n_drivers))
    constructor_combos = list(_combo_sums(constructor_prices, constructor_points, constructor_delta_budget, n_constructors))

    best: TeamSelection | None = None
    for d_items, d_price, d_points, d_delta in driver_combos:
        if d_price > cap:
            continue
        for c_items, c_price, c_points, c_delta in constructor_combos:
            total_price = d_price + c_price
            if total_price > cap:
                continue
            points = d_points + c_points
            delta = d_delta + c_delta
            objective = points + lam * delta
            if best is None or objective > best.objective:
                best = TeamSelection(
                    drivers=d_items,
                    constructors=c_items,
                    total_price=total_price,
                    expected_points=points,
                    expected_delta_budget=delta,
                    objective=objective,
                )
    return best


def naive_baseline_team(
    driver_prices: dict[str, float],
    constructor_prices: dict[str, float],
    *,
    driver_points: dict[str, float] | None = None,
    constructor_points: dict[str, float] | None = None,
    cap: float = 100.0,
    n_drivers: int = 5,
    n_constructors: int = 2,
) -> TeamSelection | None:
    """Baseline #1 (the plan's own spec): the single most expensive team
    that fits under the cap. Uses the same exact-enumeration machinery as
    optimise_team, maximising total price instead of points -- a true
    "most expensive affordable" answer, not a greedy approximation that
    could miss it."""
    driver_combos = list(_combo_sums(driver_prices, driver_points or {}, None, n_drivers))
    constructor_combos = list(_combo_sums(constructor_prices, constructor_points or {}, None, n_constructors))

    best: TeamSelection | None = None
    for d_items, d_price, d_points, _ in driver_combos:
        if d_price > cap:
            continue
        for c_items, c_price, c_points, _ in constructor_combos:
            total_price = d_price + c_price
            if total_price > cap:
                continue
            if best is None or total_price > best.total_price:
                best = TeamSelection(
                    drivers=d_items,
                    constructors=c_items,
                    total_price=total_price,
                    expected_points=d_points + c_points,
                    expected_delta_budget=0.0,
                    objective=total_price,
                )
    return best


def season_points_baseline_team(
    driver_season_points: dict[str, float],
    constructor_season_points: dict[str, float],
    driver_prices: dict[str, float],
    constructor_prices: dict[str, float],
    *,
    cap: float = 100.0,
    n_drivers: int = 5,
    n_constructors: int = 2,
) -> TeamSelection | None:
    """Baseline #2, a harder target than 'most expensive affordable' added
    on top of the plan's own spec so a pass isn't just beating an easy
    strawman (a human decision, not the plan's original requirement): the
    highest-cumulative-season-points team affordable under the cap."""
    return optimise_team(
        driver_season_points,
        driver_prices,
        constructor_season_points,
        constructor_prices,
        cap=cap,
        lam=0.0,
        n_drivers=n_drivers,
        n_constructors=n_constructors,
    )


def marginal_points_per_million(
    driver_points: dict[str, float],
    driver_prices: dict[str, float],
    constructor_points: dict[str, float],
    constructor_prices: dict[str, float],
    *,
    cap: float = 100.0,
    n_drivers: int = 5,
    n_constructors: int = 2,
) -> float:
    """lambda: the points a $1M larger cap would have bought this round,
    found by solving optimise_team at cap and cap+1 and differencing --
    the two-solve recipe the plan specifies, not an iterative solve."""
    at_cap = optimise_team(
        driver_points, driver_prices, constructor_points, constructor_prices,
        cap=cap, n_drivers=n_drivers, n_constructors=n_constructors,
    )
    at_cap_plus_one = optimise_team(
        driver_points, driver_prices, constructor_points, constructor_prices,
        cap=cap + 1.0, n_drivers=n_drivers, n_constructors=n_constructors,
    )
    if at_cap is None or at_cap_plus_one is None:
        return 0.0
    return at_cap_plus_one.expected_points - at_cap.expected_points


def _realised_outcome(
    selection: TeamSelection | None,
    realised_driver_points: dict[str, float],
    realised_constructor_points: dict[str, float],
    realised_driver_delta: dict[str, float],
    realised_constructor_delta: dict[str, float],
) -> tuple[float, float] | None:
    if selection is None:
        return None
    points = sum(realised_driver_points.get(d, 0.0) for d in selection.drivers) + sum(
        realised_constructor_points.get(c, 0.0) for c in selection.constructors
    )
    delta = sum(realised_driver_delta.get(d, 0.0) for d in selection.drivers) + sum(
        realised_constructor_delta.get(c, 0.0) for c in selection.constructors
    )
    return points, delta


def backtest_optimiser(
    season: int,
    rounds: list[int],
    *,
    cache_dir: Path | str | None = None,
    n_drivers: int = 5,
    n_constructors: int = 2,
) -> dict:
    """Walk-forward: the optimiser's predicted team vs. both baselines,
    scored against realised outcomes for 2026 -- actual GamedayPoints (via
    reconcile.fetch_driver_feed/fetch_constructor_feed_rows) for points, and
    actual recorded price deltas for budget growth. This is 2026-only: the
    public fantasy feed (and therefore realised budget growth) does not
    exist for 2024/2025, the same limitation prices.py already documents
    for its own gate -- never faked here either.

    Constructor points (predicted and realised) are each constructor's two
    drivers' points summed; the real game also awards a pit-stop bonus this
    project does not model, a known simplification.
    """
    from f1_fantasy.predict import points as points_module
    from f1_fantasy.predict.reconcile import fetch_constructor_feed_rows, fetch_driver_feed

    per_round = []
    for index, target_round in enumerate(rounds):
        train_rounds = rounds[:index]
        if not train_rounds:
            continue
        try:
            driver_feed = fetch_driver_feed(target_round, cache_dir=cache_dir)
            constructor_feed = fetch_constructor_feed_rows(target_round, cache_dir=cache_dir)
        except Exception:  # noqa: BLE001 -- a round the public feed doesn't have yet shouldn't abort the sweep
            continue
        if not driver_feed or not constructor_feed:
            continue

        predicted = points_module.build_round_distributions(season, train_rounds, target_round, n_samples=500)
        if not predicted:
            continue

        driver_prices = {code: float(row.get("OldPlayerValue") or 0) for code, row in driver_feed.items()}
        constructor_prices = {name: float(row.get("OldPlayerValue") or 0) for name, row in constructor_feed.items()}

        driver_points = {d: dist.mean for d, dist in predicted.items()}
        constructor_points: dict[str, float] = {}
        for d, dist in predicted.items():
            constructor_points[dist.constructor] = constructor_points.get(dist.constructor, 0.0) + dist.mean

        driver_season_points = {code: float(row.get("OverallPpints") or 0) for code, row in driver_feed.items()}
        constructor_season_points = {name: float(row.get("OverallPpints") or 0) for name, row in constructor_feed.items()}

        optimised = optimise_team(
            driver_points, driver_prices, constructor_points, constructor_prices,
            n_drivers=n_drivers, n_constructors=n_constructors,
        )
        naive = naive_baseline_team(
            driver_prices, constructor_prices, driver_points=driver_points, constructor_points=constructor_points,
            n_drivers=n_drivers, n_constructors=n_constructors,
        )
        season_baseline = season_points_baseline_team(
            driver_season_points, constructor_season_points, driver_prices, constructor_prices,
            n_drivers=n_drivers, n_constructors=n_constructors,
        )

        realised_driver_points = {code: float(row.get("GamedayPoints") or 0) for code, row in driver_feed.items()}
        realised_constructor_points = {name: float(row.get("GamedayPoints") or 0) for name, row in constructor_feed.items()}
        realised_driver_delta = {
            code: float(row.get("Value") or 0) - float(row.get("OldPlayerValue") or 0) for code, row in driver_feed.items()
        }
        realised_constructor_delta = {
            name: float(row.get("Value") or 0) - float(row.get("OldPlayerValue") or 0)
            for name, row in constructor_feed.items()
        }

        per_round.append(
            {
                "round": target_round,
                "optimised": _realised_outcome(optimised, realised_driver_points, realised_constructor_points, realised_driver_delta, realised_constructor_delta),
                "naive_baseline": _realised_outcome(naive, realised_driver_points, realised_constructor_points, realised_driver_delta, realised_constructor_delta),
                "season_points_baseline": _realised_outcome(season_baseline, realised_driver_points, realised_constructor_points, realised_driver_delta, realised_constructor_delta),
            }
        )

    def _mean(key: str, index: int) -> float | None:
        values = [r[key][index] for r in per_round if r[key] is not None]
        return float(np.mean(values)) if values else None

    return {
        "rounds_evaluated": len(per_round),
        "per_round": per_round,
        "summary": {
            "optimised_mean_points": _mean("optimised", 0),
            "optimised_mean_delta_budget": _mean("optimised", 1),
            "naive_baseline_mean_points": _mean("naive_baseline", 0),
            "naive_baseline_mean_delta_budget": _mean("naive_baseline", 1),
            "season_points_baseline_mean_points": _mean("season_points_baseline", 0),
            "season_points_baseline_mean_delta_budget": _mean("season_points_baseline", 1),
        },
    }
