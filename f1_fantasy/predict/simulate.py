"""Monte Carlo summaries: captaincy EV, floor/ceiling picks, price-rise probability.

Runs ``points.sample_field`` many times and reduces the draws to per-driver
summary statistics. The price-rise probability is not a separate, simplified
restatement of the PPM mechanism -- each draw's sampled points are fed
straight through ``prices.average_ppm`` / ``prices.predict_price_delta``
alongside that driver's *real* prior-round history, so ``P(price rise) =
P(points > tier threshold)`` falls out of the simulation exactly as the
approved plan describes, using the same 91.8%-validated mechanism prices.py
already is.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from f1_fantasy.predict import prices
from f1_fantasy.predict.points import sample_field
from f1_fantasy.predict.form import rolling_form
from f1_fantasy.predict.reliability import constructor_history, dnf_probability
from f1_fantasy.results import fetch_qualifying

#: To estimate a price-rise probability to within +-1 percentage point at
#: 95% confidence in the worst case (p near 0.5), the binomial margin-of-
#: error formula gives n >= (1.96/0.01)^2 * 0.25 ~= 9604. Rounded up. This is
#: overkill for the mean/captaincy-EV estimates, which converge much faster;
#: pass a smaller n_samples (e.g. 2000) for a fast/dev mode.
N_SAMPLES = 10_000


@dataclass
class SimulationSummary:
    driver: str
    price: float
    mean: float
    p10: float
    p90: float
    p_price_rise: float
    mean_delta_budget: float
    n_samples: int
    #: Probability of qualifying in the top 10, and expected Driver of the
    #: Day points -- both needed to assemble constructor points, which carry
    #: a Q3 bonus their drivers don't and exclude DOTD their drivers do.
    p_q3: float = 0.0
    mean_dotd_points: float = 0.0


def _recent_price_window(
    history: dict[int, tuple[float, float, float]], target_round: int
) -> list[tuple[float, float]]:
    """(points, price_before) for the real prior rounds prices.py's own
    rolling window would use -- up to ROLLING_WINDOW - 1 rounds immediately
    before target_round, leaving exactly one slot for this draw's own
    sampled points."""
    window_rounds = [
        r for r in range(target_round - prices.ROLLING_WINDOW, target_round) if r in history and r >= 1
    ]
    return [(history[r][0], history[r][1]) for r in window_rounds]


def simulate_round(
    season: int,
    train_rounds: list[int],
    target_round: int,
    *,
    price_before: dict[str, float],
    recent_price_history: prices.DriverPriceHistory,
    sprint: bool = False,
    n_samples: int = N_SAMPLES,
    seed: int | None = None,
) -> dict[str, SimulationSummary]:
    """Monte Carlo summary per driver for *target_round*.

    ``price_before`` is each driver's real price going into the round (known
    pre-race, the same input prices.predict_round would use).
    ``recent_price_history`` is prices.round_history's real output for prior
    rounds -- this round's own points are never in it, only sampled.
    """
    strengths = rolling_form(season, train_rounds)
    if not strengths:
        return {}

    constructor_of = {q.driver_code: q.constructor for q in fetch_qualifying(season, train_rounds[-1])}
    history = constructor_history(season, train_rounds)
    dnf_probabilities = {constructor: dnf_probability(record) for constructor, record in history.items()}

    recent_windows = {
        d: _recent_price_window(recent_price_history.get(d, {}), target_round) for d in strengths
    }

    rng = np.random.default_rng(seed)
    totals: dict[str, list[float]] = {d: [] for d in strengths}
    price_rises: dict[str, list[bool]] = {d: [] for d in strengths}
    delta_budgets: dict[str, list[float]] = {d: [] for d in strengths}
    q3_counts: dict[str, int] = dict.fromkeys(strengths, 0)
    dotd_totals: dict[str, list[float]] = {d: [] for d in strengths}

    for _ in range(n_samples):
        draw = sample_field(strengths, constructor_of, dnf_probabilities, sprint=sprint, rng=rng)
        for d, breakdown in draw.items():
            total = breakdown.total
            totals[d].append(total)
            # Only a top-10 classification scores, so a positive qualifying
            # component is exactly "reached Q3".
            if breakdown.qualifying > 0:
                q3_counts[d] += 1
            dotd_totals[d].append(breakdown.driver_of_the_day)
            price = price_before.get(d)
            if price is not None:
                avg_ppm = prices.average_ppm(recent_windows[d] + [(total, price)])
                delta = prices.predict_price_delta(avg_ppm, price)
                price_rises[d].append(delta > 0)
                delta_budgets[d].append(delta)

    summaries = {}
    for d in strengths:
        values = np.array(totals[d]) if totals[d] else np.array([0.0])
        summaries[d] = SimulationSummary(
            driver=d,
            price=price_before.get(d, 0.0),
            mean=float(values.mean()),
            p10=float(np.percentile(values, 10)),
            p90=float(np.percentile(values, 90)),
            p_price_rise=float(np.mean(price_rises[d])) if price_rises[d] else 0.0,
            mean_delta_budget=float(np.mean(delta_budgets[d])) if delta_budgets[d] else 0.0,
            n_samples=n_samples,
            p_q3=q3_counts[d] / n_samples if n_samples else 0.0,
            mean_dotd_points=float(np.mean(dotd_totals[d])) if dotd_totals[d] else 0.0,
        )
    return summaries


def captaincy_ev(summaries: dict[str, SimulationSummary]) -> list[tuple[str, float]]:
    """2x expected points per driver (the standard captain multiplier), ranked best first."""
    return sorted(((d, s.mean * 2.0) for d, s in summaries.items()), key=lambda kv: -kv[1])


def floor_ceiling_picks(summaries: dict[str, SimulationSummary], *, n: int = 5) -> dict[str, list[str]]:
    """Highest-p10 ('floor', safest downside) and highest-p90 ('ceiling', best upside) picks."""
    floor = sorted(summaries, key=lambda d: -summaries[d].p10)[:n]
    ceiling = sorted(summaries, key=lambda d: -summaries[d].p90)[:n]
    return {"floor": floor, "ceiling": ceiling}
