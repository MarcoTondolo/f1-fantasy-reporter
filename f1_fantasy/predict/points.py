"""Expected fantasy points: a joint Monte Carlo draw over the whole field.

Race and qualifying order are a permutation of the *whole* field, and
positions-gained needs a consistent ``(grid, finish)`` pair -- so the core
primitive here is a joint draw over every driver at once, not independent
per-driver distributions. ``sample_field`` draws one full scenario (a
qualifying order, a set of DNFs, a race order among the survivors, one
fastest-lap winner, one driver-of-the-day winner) and scores every driver
within that one consistent scenario via ``scoring.qualifying_points`` /
``scoring.race_points``. A point estimate and a full distribution are the
same mechanism at different sample counts, not two separate models --
``build_round_distributions`` just runs many draws and averages.

This keeps "classified" and "DNF" as genuinely separate terms in every
single draw, which is exactly what ``predict.race.predict_race_order`` got
wrong by collapsing both into one discounted expected rank (task #15's
documented null result). Here, a DNF draw takes the flat -20 in that
scenario; a classified draw takes its position/gained points in that
scenario; averaging the *draws* is not the same as averaging the *inputs*
first, and that difference is the whole reason this module exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.stats import spearmanr

from f1_fantasy.predict.form import rolling_form
from f1_fantasy.predict.reliability import PRIOR_RATE as DEFAULT_DNF_PROBABILITY
from f1_fantasy.predict.reliability import constructor_history, dnf_probability
from f1_fantasy.predict.scoring import PointsBreakdown, qualifying_points, race_points
from f1_fantasy.results import fetch_qualifying

#: Calibrated via calibrate_noise_scale so a simulated field's own Spearman
#: against its true strength ordering matches this project's already-gated
#: walk-forward numbers: 0.898 for quali (form.py, 2026) and 0.846 for race
#: order restricted to classified finishers (reliability.py's documented
#: grid-ceiling). **Calibrated against real strengths, not a synthetic
#: ladder** -- see calibrate_noise_scale's docstring for why that distinction
#: matters: an earlier version of this file calibrated against a synthetic
#: uniformly-spaced field (field_size=20, giving 2.355 / 3.063) and applied
#: the result to the real, tightly-clustered 2026 top group. That silently
#: passed this module's own field-mean parity check (predicted 10.35 vs
#: actual 11.09 for round 10) while badly compressing predicted spread
#: *within* the top group -- confirmed live against a real per-driver
#: points-per-race screenshot the user supplied (2026 rounds 1-12): actual
#: GamedayPoints for ANT/HAM/LEC/RUS/NOR/VER/PIA span 38.8 down to 15.1
#: points/race, while the ladder-calibrated model predicted them within
#: 14.8-9.2 of each other -- roughly half the real spread, and in the
#: wrong order (NOR/PIA, McLaren's actual points leaders, predicted lowest
#: of the seven). Root cause: Spearman across a 20+ driver field is
#: dominated by getting the widely-separated majority right and is nearly
#: insensitive to shuffling within an already-adjacent cluster, so a
#: noise_scale fit to hit a target Spearman on a *uniform* ladder ends up
#: far too large for the real field's tightly-bunched leaders. Frozen
#: constants, not recomputed at import time -- rerun
#: calibrate_noise_scale(0.898, strengths=snapshot) /
#: calibrate_noise_scale(0.846, strengths=snapshot) across several real
#: form.rolling_form snapshots (trains ending rounds 2-12, trials=1200,
#: seed=round index) to reproduce them: per-round quali values ranged
#: 0.418-0.512 (mean 0.4733), race 0.572-0.687 (mean 0.6403).
QUALI_NOISE_SCALE_2026 = 0.4733
RACE_NOISE_SCALE = 0.6403

N_POINT_ESTIMATE_SAMPLES = 1000

#: Fit from 2026's reconciliation residual across 252 driver-rounds where the
#: residual looked like a plausible overtake count (reconcile.py). Two
#: hypotheses were tested and both failed: overtake points scaling with net
#: position change |grid - finish| (slope ~ -0.05, no real relationship) and
#: with how many cars retired in that race (correlation -0.03). Modelled
#: instead as a flat, overdispersed count: mean 5.31, variance 21.0 --
#: variance is ~4x the mean, which a Poisson fit (variance = mean) cannot
#: represent, so this samples from a negative binomial fit to those two
#: real moments instead. 2026-specific like form.py's 0.898 -- overtakes
#: cannot be recovered from Jolpica for 2024/2025 to check whether this
#: holds outside the new-regulation season's unusually high overtake rate.
#:
#: **A third hypothesis was tested and also mostly failed: circuit
#: identity.** Real per-circuit mean overtake-residual across rounds 2-12
#: ranges from 2.38 (Monaco) to 7.43 (Miami) -- a genuine ~3x spread, and
#: Monaco's own outlier status is real and large. But a continuous proxy
#: (full-throttle-time fraction from race-control telemetry, computed the
#: same way pace/energy.py's clipping metrics are) correlates only weakly
#: and not significantly across those 11 circuits (Pearson r=0.36, p=0.28,
#: n=11) -- Spa's long straights (0.686 full-throttle fraction, the
#: highest of the 11) don't bring correspondingly high overtaking, likely
#: because modern-car dirty air suppresses passing there despite the
#: straight-line opportunity. Monza's own full-throttle fraction, measured
#: from a real 2025 reference session since 2026's race hasn't run yet, is
#: 0.736 -- higher than every 2026 circuit measured so far -- but with no
#: reliable general relationship established, this is reported as
#: descriptive context only, not used to adjust OVERTAKE_MEAN for Monza or
#: any other single circuit. Revisit if a full season's worth of circuits
#: (n=20+) shows a cleaner signal, or if Monaco is worth special-casing on
#: its own (a real, large outlier) independent of a general model.
OVERTAKE_MEAN = 5.31
OVERTAKE_VARIANCE = 21.0

#: Fastest-lap rate by starting-grid bucket, fit from real fetch_race_results
#: data across all of 2024, 2025 and 2026 (1221 driver-races). Cleanly
#: monotonic and the bucket rates already sum close to 1 across a typical
#: field (0.172*3 + 0.072*3 + 0.021*4 + 0.018*10 ~= 1.0), consistent with
#: "exactly one fastest lap per race" -- a real, well-evidenced calibration,
#: not a guess.
GRID_BUCKET_FASTEST_LAP_RATE: dict[tuple[int, int], float] = {
    (1, 3): 0.1722,
    (4, 6): 0.0722,
    (7, 10): 0.0208,
    (11, 99): 0.0177,
}

@dataclass
class DriverPointsDistribution:
    """A Monte Carlo summary of one driver's expected fantasy points for a round."""

    driver: str
    constructor: str
    mean: float
    components: dict[str, float] = field(default_factory=dict)
    p_dnf: float = 0.0


def _negative_binomial_params(mean: float, variance: float) -> tuple[float, float]:
    """n, p for numpy's negative_binomial(n, p) matching a target mean/variance."""
    p = mean / variance
    n = mean * p / (1 - p)
    return n, p


_OVERTAKE_N, _OVERTAKE_P = _negative_binomial_params(OVERTAKE_MEAN, OVERTAKE_VARIANCE)


def _sample_overtake_points(rng: np.random.Generator) -> int:
    return int(rng.negative_binomial(_OVERTAKE_N, _OVERTAKE_P))


def _grid_bucket_rate(grid_position: int) -> float:
    for (lo, hi), rate in GRID_BUCKET_FASTEST_LAP_RATE.items():
        if lo <= grid_position <= hi:
            return rate
    return GRID_BUCKET_FASTEST_LAP_RATE[(11, 99)]


def _sample_fastest_lap_winner(survivors: list[str], grid: dict[str, int], rng: np.random.Generator) -> str | None:
    if not survivors:
        return None
    weights = np.array([_grid_bucket_rate(grid[d]) for d in survivors])
    weights = weights / weights.sum()
    return str(rng.choice(survivors, p=weights))


def _sample_dotd_winner(
    survivors: list[str], grid: dict[str, int], race_position: dict[str, int], rng: np.random.Generator
) -> str | None:
    """Weighted toward drivers who gained places, the pattern in the only 4
    confirmed real Driver of the Day cases (reconcile.py) -- all 4 gained
    places (+1, +3, +2, +2). n=4 is too small to fit confidently; this is a
    documented heuristic, not a calibrated rate, same honesty standard as
    form.py's "0.898 is 2026-specific" caveat."""
    if not survivors:
        return None
    gains = np.array([max(0.0, grid[d] - race_position[d]) + 0.1 for d in survivors])
    weights = gains / gains.sum()
    return str(rng.choice(survivors, p=weights))


def plackett_luce_order(strengths: dict[str, float], noise_scale: float, rng: np.random.Generator) -> list[str]:
    """Sample a finishing order (best first) via the Gumbel-max trick.

    ``strengths`` maps driver -> a score where LOWER is better (e.g.
    form.py's gap-to-best %, where 0 is fastest). ``noise_scale`` controls
    how far the sampled order can deviate from the deterministic strength
    ranking: 0 reproduces it exactly every time; larger values randomize it
    more. This is an exact sample from the Plackett-Luce distribution
    implied by the strengths when the noise is standard Gumbel.
    """
    drivers = list(strengths)
    if not drivers:
        return []
    if noise_scale <= 0:
        return sorted(drivers, key=lambda d: strengths[d])
    gumbel = rng.gumbel(0.0, 1.0, size=len(drivers))
    keys = {d: -strengths[d] / noise_scale + g for d, g in zip(drivers, gumbel)}
    return sorted(drivers, key=lambda d: -keys[d])


def calibrate_noise_scale(
    target_spearman: float,
    field_size: int | None = None,
    *,
    strengths: dict[str, float] | None = None,
    trials: int = 3000,
    seed: int = 0,
) -> float:
    """Binary-search the noise_scale whose sampled order, compared against
    its own true strength ranking, averages ``target_spearman`` Spearman
    correlation over ``trials`` draws. Kept callable (not just baked in) so
    the derivation is inspectable and reproducible, matching how
    reliability.py documents PRIOR_RATE's own derivation.

    Pass ``strengths`` -- a real snapshot from ``form.rolling_form`` -- to
    calibrate against real, non-uniformly-spaced strength gaps. Real
    competitors are not evenly spaced: this project's own 2026 top group
    (Mercedes/Ferrari/McLaren/Red Bull) sits within ~0.5 percentage points
    of each other in qualifying-gap terms, while the gap down to the
    midfield is several times that. A noise_scale fit against the
    synthetic ``field_size`` ladder below (uniform integer spacing) and
    then applied to that real, clustered field over-randomizes the tightly
    bunched leaders while barely touching the widely-separated rest --
    Spearman across a 20+ driver field is dominated by getting the
    separated majority right and is nearly insensitive to shuffling within
    an already-adjacent cluster, so a ladder-calibrated noise_scale can hit
    the same aggregate target while still erasing real, persistent
    differentiation between the leaders. This was confirmed live: a
    ladder-calibrated noise_scale reproduced the correct field-mean
    expected points (predicted 10.35 vs actual 11.09 for round 10) while
    compressing the top group's predicted spread to roughly half of the
    real one (predicted ANT/RUS/HAM/LEC/NOR/VER/PIA within ~14.8-9.2
    points/race of each other; actual GamedayPoints spans 38.8-15.1 over
    the same seven drivers, 2026 rounds 1-12) -- the ``field_size`` path is
    kept only for cheap synthetic unit tests, never for deriving the real
    constants below.
    """
    rng = np.random.default_rng(seed)
    if strengths is None:
        if field_size is None:
            raise ValueError("calibrate_noise_scale needs either field_size or strengths")
        strengths = {str(i): float(i) for i in range(field_size)}

    drivers = list(strengths)
    true_order = sorted(drivers, key=lambda d: strengths[d])
    true_rank = [true_order.index(d) + 1 for d in drivers]

    def mean_spearman(noise_scale: float) -> float:
        correlations = []
        for _ in range(trials):
            order = plackett_luce_order(strengths, noise_scale, rng)
            predicted_rank = [order.index(d) + 1 for d in drivers]
            correlation, _ = spearmanr(predicted_rank, true_rank)
            if not np.isnan(correlation):
                correlations.append(correlation)
        return float(np.mean(correlations)) if correlations else 0.0

    lo, hi = 0.0, 1.0
    while mean_spearman(hi) > target_spearman and hi < 1e5:
        hi *= 2
    for _ in range(25):
        mid = (lo + hi) / 2
        if mean_spearman(mid) > target_spearman:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def sample_field(
    strengths: dict[str, float],
    constructor_of: dict[str, str],
    dnf_probabilities: dict[str, float],
    *,
    sprint: bool = False,
    rng: np.random.Generator,
) -> dict[str, PointsBreakdown]:
    """One joint Monte Carlo draw for the whole field.

    A Plackett-Luce qualifying permutation, independent per-constructor DNF
    draws, a Plackett-Luce race permutation restricted to survivors (DNFs
    are appended after every survivor, ordered by grid -- they are never
    scored on that placement, ``scoring.race_points`` gives every DNF the
    flat charge regardless), one fastest-lap winner and one driver-of-the-day
    winner drawn field-wide so their probabilities sum to 1 and nothing
    double-counts, and an overtake-points draw per driver. Every driver is
    then scored via ``scoring.qualifying_points``/``scoring.race_points``
    within this one consistent scenario.

    **Known gap on a sprint weekend:** ``sprint`` selects which position
    table ``scoring.race_points`` uses (the smaller sprint table or the
    full race table) for the *one* session this draw scores -- it does not
    score both a sprint and a full race in the same draw. A real sprint
    weekend's ``GamedayPoints`` sums qualifying + sprint + race; comparing
    this function's single-session output against that real total
    understates it substantially (confirmed live: round 12, a sprint
    weekend, field-mean actual GamedayPoints was 10.41 against a
    single-session predicted mean of 9.59 -- close in isolation, but the
    real total also includes the sprint session's own points on top,
    unaccounted for here). Every non-sprint round checked (10, 11) shows
    close field-mean parity between predicted and actual (10.17 vs 11.09,
    10.23 vs 10.64).

    **Field-mean parity alone is not sufficient evidence of correct
    calibration** -- an earlier version of this note claimed it was. Points
    scoring is close to zero-sum across the field (position points are a
    fixed pool being sliced up), so a model can get the *average* exactly
    right while badly misallocating it between drivers -- which is exactly
    what happened here until QUALI_NOISE_SCALE_2026/RACE_NOISE_SCALE were
    recalibrated against real strength spacing instead of a synthetic
    ladder (see their docstring). Field-mean parity is a necessary check,
    not a sufficient one; the per-driver comparison against real
    GamedayPoints is what actually caught the bug.
    """
    drivers = list(strengths)
    if not drivers:
        return {}

    quali_order = plackett_luce_order(strengths, QUALI_NOISE_SCALE_2026, rng)
    quali_position = {d: i + 1 for i, d in enumerate(quali_order)}
    grid = dict(quali_position)

    dnf = {
        d: bool(rng.random() < dnf_probabilities.get(constructor_of.get(d, ""), DEFAULT_DNF_PROBABILITY))
        for d in drivers
    }
    survivors = [d for d in drivers if not dnf[d]]
    retirees = sorted((d for d in drivers if dnf[d]), key=lambda d: grid[d])

    race_strengths = {d: strengths[d] for d in survivors}
    race_order = plackett_luce_order(race_strengths, RACE_NOISE_SCALE, rng)
    full_order = race_order + retirees
    race_position = {d: i + 1 for i, d in enumerate(full_order)}

    fastest_lap_winner = _sample_fastest_lap_winner(survivors, grid, rng)
    dotd_winner = _sample_dotd_winner(survivors, grid, race_position, rng)

    breakdowns = {}
    for d in drivers:
        status = "Retired" if dnf[d] else "Finished"
        overtakes = _sample_overtake_points(rng)
        breakdown = race_points(
            grid=grid[d],
            position=race_position[d],
            status=status,
            overtakes=overtakes,
            fastest_lap=(d == fastest_lap_winner),
            driver_of_the_day=(d == dotd_winner),
            sprint=sprint,
        )
        breakdown.qualifying = qualifying_points(quali_position[d])
        breakdowns[d] = breakdown
    return breakdowns


def build_round_distributions(
    season: int,
    train_rounds: list[int],
    target_round: int,
    *,
    sprint: bool = False,
    n_samples: int = N_POINT_ESTIMATE_SAMPLES,
    seed: int | None = None,
) -> dict[str, DriverPointsDistribution]:
    """Wire form.rolling_form (strength) and reliability's constructor
    history/DNF probability (hazard) into ``n_samples`` draws of
    ``sample_field``, reduced to a DriverPointsDistribution per driver.
    ``target_round`` is accepted for signature symmetry with the rest of
    this project's walk-forward predictors (form.py, race.py) but the
    strength/hazard inputs already come only from ``train_rounds`` -- it is
    never peeked at.
    """
    strengths = rolling_form(season, train_rounds)
    if not strengths:
        return {}

    constructor_of = {q.driver_code: q.constructor for q in fetch_qualifying(season, train_rounds[-1])}
    history = constructor_history(season, train_rounds)
    dnf_probabilities = {constructor: dnf_probability(record) for constructor, record in history.items()}

    rng = np.random.default_rng(seed)
    totals: dict[str, list[float]] = {d: [] for d in strengths}
    component_totals: dict[str, dict[str, list[float]]] = {d: {} for d in strengths}
    dnf_counts: dict[str, int] = dict.fromkeys(strengths, 0)

    component_fields = ("position", "positions_gained", "overtakes", "fastest_lap", "driver_of_the_day", "dnf", "qualifying")

    for _ in range(n_samples):
        draw = sample_field(strengths, constructor_of, dnf_probabilities, sprint=sprint, rng=rng)
        for d, breakdown in draw.items():
            totals[d].append(breakdown.total)
            if breakdown.dnf:
                dnf_counts[d] += 1
            for name in component_fields:
                component_totals[d].setdefault(name, []).append(getattr(breakdown, name))

    return {
        d: DriverPointsDistribution(
            driver=d,
            constructor=constructor_of.get(d, ""),
            mean=float(np.mean(totals[d])) if totals[d] else 0.0,
            components={name: float(np.mean(values)) for name, values in component_totals[d].items()},
            p_dnf=dnf_counts[d] / n_samples if n_samples else 0.0,
        )
        for d in strengths
    }
