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

import math
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

#: Softmax temperatures over driver strength (form.py's gap-to-best %, 0 =
#: fastest) for the two single-winner-per-race bonus events, each worth 10
#: points. Lower temperature = more concentrated on the fastest cars.
#:
#: **Fit by maximum likelihood against the real 2026 winners**, recovered by
#: differencing the public driver feed's cumulative ``fastest_lap_pts`` /
#: ``dotd_pts`` across rounds 2-13 -- 12 fastest laps (ANT 7, NOR 2, LEC 2,
#: HAM 1) and 12 driver-of-the-day awards (ANT 3, VER 3, HAM 2, LEC 2, PIA 1,
#: NOR 1). Grid search over T against the summed log-likelihood of each
#: observed winner under that round's own walk-forward
#: ``rolling_form`` strengths: T=0.350 for fastest lap (loglik -21.41),
#: T=0.625 for driver of the day (loglik -27.42).
#:
#: This replaces two weaker earlier models, both of which badly flattened
#: these bonuses across the field:
#:
#: - Fastest lap was keyed on *starting-grid bucket* (grid 1-3 at 0.172),
#:   which discards the car-pace information the strength score already
#:   carries. Confirmed wrong against real data: Antonelli's modelled
#:   fastest-lap expectation was 1.23 points/race against 6.2 actual (he
#:   took 7 of the season's 12). Only four drivers took any fastest lap all
#:   season -- it is concentrated on the quickest car, not on whoever
#:   happens to start near the front.
#: - Driver of the day was weighted by *places gained*, a documented
#:   heuristic fit to only 4 confirmed cases, which pushed probability onto
#:   backmarkers climbing the order. The recovered 12-case record is instead
#:   entirely front-runners, so a strength fit on 12 observations supersedes
#:   a places-gained guess on 4.
#:
#: **Known limit, reported rather than fitted away:** a strength softmax
#: cannot reproduce the full concentration. Form separates Antonelli only
#: modestly from his own team-mate Russell (implied shares 0.284 vs 0.200 at
#: round 13), yet Antonelli took 7 fastest laps and Russell none. Closing
#: that would need a per-driver fastest-lap skill term, which on n=12 would
#: be fitting noise. So this recovers most of the missing concentration
#: (Antonelli 1.23 -> 2.84 points/race against 6.2 actual) and is honest
#: about the rest.
FASTEST_LAP_TEMPERATURE = 0.350
DOTD_TEMPERATURE = 0.625

#: How much a constructor scores *above* the sum of its two drivers, in
#: points per race. Measured across all 143 constructor-rounds of 2026
#: (public constructor feed's GamedayPoints minus the sum of its drivers'):
#: mean +7.57, median +6.0, stdev 6.91, range -5 to +30. The constructor-only
#: sources the driver totals cannot carry (the pit-stop award chief among
#: them) are real and consistently positive -- only 2 of 143 came in
#: negative.
#:
#: Modelling this as a flat constant rather than a per-constructor rate is
#: deliberate: the spread does not track team strength in any usable way
#: (round 13 alone had Audi +13 and Mercedes +2, the slowest and fastest cars
#: on the grid), so a fitted per-team term would be fitting noise on ~13
#: observations each.
CONSTRUCTOR_BONUS_MEAN = 7.57


def constructor_points_from_drivers(
    driver_points: dict[str, float],
    constructor_of: dict[str, str],
    *,
    bonus: float = CONSTRUCTOR_BONUS_MEAN,
) -> dict[str, float]:
    """Expected constructor points: its drivers' sum, plus the measured bonus.

    ``constructor_of`` maps driver code to the constructor key the caller
    wants in the result -- translate feed/Jolpica naming *before* calling,
    since optimise_team matches these keys against its price dict.

    Callers predicting from a model want this. Anything working from
    *realised* points (report/hindsight.py reading a captured snapshot)
    must not add the bonus -- the real recorded value already includes it.
    """
    totals: dict[str, float] = {}
    for driver, points in driver_points.items():
        constructor = constructor_of.get(driver)
        if constructor:
            totals[constructor] = totals.get(constructor, 0.0) + points
    return {constructor: total + bonus for constructor, total in totals.items()}

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

#: Between-race spread of the *field-wide* overtake-points total, as a
#: coefficient of variation, drawn once per scenario and shared by every
#: driver in it.
#:
#: Overtaking is the single largest element in this game -- 30.0% of all
#: points flowing through the 2026 season field-wide, ahead of race position
#: at 22.5% -- and it is also by far the most race-dependent. Real 2026
#: per-race field totals, recovered by differencing the feed's cumulative
#: ``overtaking_pts``: Zandvoort 41, Red Bull Ring ~44, Monaco/Canada ~52
#: each, Hungaroring 101, Spa 106, Suzuka 106, Round 1 120, Silverstone 192,
#: Monza 258, Shanghai 280, Miami 300 -- a 7x spread, mean 130.5 with
#: standard deviation 91.4 (CV 0.70) across all 13 rounds.
#:
#: Independent per-driver draws cannot produce that. Summing 23 independent
#: NB(5.31, 21) draws gives a field total with CV of only ~0.18 -- a
#: quarter of the real race-to-race variation -- so every simulated race
#: looked like an average-overtaking race. This multiplies each driver's
#: overtake mean by one shared Gamma factor per scenario, sized so the
#: field total's CV comes out at the observed 0.70 once the independent
#: per-driver noise already contributing ~0.18 is accounted for:
#: sqrt(0.70^2 - 0.18^2) = 0.676. Mean is left unchanged (OVERTAKE_MEAN
#: 5.31/driver against 130.5/23 = 5.67 real -- within 7%, already fine).
#:
#: **This fixes the dispersion, not the predictability.** A shared factor
#: makes the simulation produce realistic low- and high-overtaking races in
#: the right proportion, which is what the p10/p90 and captaincy numbers
#: depend on. It does *not* know which kind of race is coming. Two routes
#: to that were tried and both failed: a continuous track proxy
#: (full-throttle fraction, Pearson r=0.36, p=0.28, n=11 -- see
#: OVERTAKE_MEAN's note) and a per-circuit history from prior seasons, which
#: needs per-race overtake counts for 2024/2025 that no available source
#: provides. A lap-by-lap position-change proxy from FastF1 was built and
#: rejected on validation: it ranked Zandvoort (176) above Monza (130) when
#: the real feed has Monza at 258 and Zandvoort at 41, because position
#: changes caused by *other* cars pitting swamp genuine on-track passes.
#: For a brand-new circuit with no history at all -- Madrid, round 14 --
#: neither route could work even in principle.
OVERTAKE_RACE_LEVEL_CV = 0.676


def _sample_race_overtake_factor(rng: np.random.Generator) -> float:
    """One shared multiplier on every driver's overtake mean, per scenario.

    Gamma with mean 1 and the observed between-race CV, so a scenario is a
    low-, average- or high-overtaking race in the proportions 2026 actually
    produced.
    """
    shape = 1.0 / (OVERTAKE_RACE_LEVEL_CV**2)
    return float(rng.gamma(shape, 1.0 / shape))


#: **Tested and rejected: redistributing overtake points by places gained.**
#: Recorded because the conditional evidence for it is genuinely good and it
#: would otherwise be an obvious thing to try again.
#:
#: Fit on 137 classified driver-rounds from the eight 2026 rounds whose feed
#: delta covers exactly one race (excluding LAW, whose entries are known to
#: be restated -- see reconcile.KNOWN_INCONSISTENT_DRIVERS -- and the
#: negative deltas those restatements produce):
#: ``overtake_pts ~ 8.17 + 0.410 * places_gained``, r=0.264, p=0.0018. That
#: even *reverses* the earlier null recorded against OVERTAKE_MEAN, which
#: tested the same idea on the reconciliation residual (slope ~-0.05) and was
#: measuring a noisier quantity.
#:
#: Applying it anyway made the model worse, for a reason worth keeping:
#: a relationship that holds *conditional on a race outcome* can still
#: produce the wrong *marginal* once integrated over the distribution of
#: outcomes. Back-markers have the higher expected places-gained, because
#: attrition promotes them, so a places-gained term hands them the most
#: overtake points -- implemented, it gave Stroll 6.00 points/race against
#: Antonelli's 4.72. The real relationship between car speed and overtake
#: points is flat: across all 23 drivers of 2026, Spearman(strength,
#: overtake_pts/race) = -0.022, p=0.445, with everyone inside a 5.2-8.5
#: band. A flat per-driver mean matches that; a places-gained gradient does
#: not. Any future attempt needs to condition on something that predicts
#: *gross* passes rather than net position change -- the two come apart
#: badly, as Hulkenberg's round 13 shows: zero net places, 14 overtake
#: points.
#:
#:
#: Variance-to-mean ratio of the per-driver count, held fixed as the mean
#: moves so the overdispersion fit from real data survives every rescaling.
_OVERTAKE_VARIANCE_RATIO = OVERTAKE_VARIANCE / OVERTAKE_MEAN

#: A driver is never modelled as certain to make no pass at all: the lowest
#: real classified value across those 137 driver-rounds was 1 point.
_OVERTAKE_MIN_MEAN = 0.5


def _sample_overtake_points(rng: np.random.Generator, mean: float = OVERTAKE_MEAN) -> int:
    """Overtake points for one driver, at the mean this scenario implies.

    Holding variance/mean fixed keeps ``p`` constant and scales ``n``
    linearly, so the per-driver overdispersion fit from real data (mean 5.31,
    variance 21.0) is preserved at every race level and for every
    places-gained adjustment.
    """
    mean = max(mean, _OVERTAKE_MIN_MEAN)
    p = 1.0 / _OVERTAKE_VARIANCE_RATIO
    n = mean * p / (1.0 - p)
    return int(rng.negative_binomial(n, p))


def _strength_softmax(candidates: list[str], strengths: dict[str, float], temperature: float) -> np.ndarray:
    """Probability over *candidates* falling off with strength gap.

    Strengths are form.py's gap-to-best % (0 = fastest), so the weight is
    ``exp(-gap / temperature)`` -- the fastest car carries the most mass and
    a lower temperature concentrates it harder.
    """
    gaps = np.array([strengths.get(d, 0.0) for d in candidates], dtype=float)
    # Subtract the minimum first: mathematically identical after
    # normalisation, but keeps exp() away from underflow on a wide field.
    weights = np.exp(-(gaps - gaps.min()) / temperature)
    return weights / weights.sum()


def _sample_fastest_lap_winner(
    survivors: list[str], strengths: dict[str, float], rng: np.random.Generator
) -> str | None:
    if not survivors:
        return None
    return str(rng.choice(survivors, p=_strength_softmax(survivors, strengths, FASTEST_LAP_TEMPERATURE)))


def _sample_dotd_winner(
    survivors: list[str], strengths: dict[str, float], rng: np.random.Generator
) -> str | None:
    if not survivors:
        return None
    return str(rng.choice(survivors, p=_strength_softmax(survivors, strengths, DOTD_TEMPERATURE)))


def fit_event_temperature(
    observed_winners: dict[int, list[str]],
    strengths_by_round: dict[int, dict[str, float]],
    *,
    grid: np.ndarray | None = None,
) -> tuple[float, float]:
    """Maximum-likelihood softmax temperature for a single-winner-per-race event.

    Kept in-module rather than as a one-off script so the derivation behind
    FASTEST_LAP_TEMPERATURE/DOTD_TEMPERATURE stays inspectable, the same way
    calibrate_noise_scale documents the noise constants. Returns
    ``(temperature, log_likelihood)``.
    """
    if grid is None:
        grid = np.arange(0.05, 6.0, 0.025)

    def log_likelihood(temperature: float) -> float:
        total = 0.0
        for round_number, winners in observed_winners.items():
            strengths = strengths_by_round.get(round_number)
            if not strengths:
                continue
            candidates = list(strengths)
            probabilities = dict(zip(candidates, _strength_softmax(candidates, strengths, temperature)))
            for winner in winners:
                total += math.log(max(probabilities.get(winner, 1e-9), 1e-9))
        return total

    best_temperature = float(grid[0])
    best_score = -math.inf
    for temperature in grid:
        score = log_likelihood(float(temperature))
        if score > best_score:
            best_score, best_temperature = score, float(temperature)
    return best_temperature, best_score


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

    fastest_lap_winner = _sample_fastest_lap_winner(survivors, strengths, rng)
    dotd_winner = _sample_dotd_winner(survivors, strengths, rng)
    # One overtaking level for the whole scenario -- see
    # OVERTAKE_RACE_LEVEL_CV for why this must be shared, not per driver --
    # then redistributed between drivers by places gained, centred so the
    # redistribution is points-neutral field-wide.
    race_overtake_factor = _sample_race_overtake_factor(rng)

    breakdowns = {}
    for d in drivers:
        status = "Retired" if dnf[d] else "Finished"
        overtakes = _sample_overtake_points(rng, OVERTAKE_MEAN * race_overtake_factor)
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
