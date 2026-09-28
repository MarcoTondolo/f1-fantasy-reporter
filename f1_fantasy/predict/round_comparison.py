"""Per-driver predicted-vs-actual fantasy points, broken down by the same
components on both sides -- the data behind the site's drill-down "Model vs.
actuals" visual.

Predicted: ``points.build_grid_conditioned_distributions``, which already
conditions on the real grid once qualifying has happened. Actual:
``reconcile.reconstruct_points`` (Jolpica: position, positions gained, DNF,
qualifying, fastest lap -- all confirmed exact, see scoring.py) plus the
reconciliation residual for everything Jolpica can't see.

Overtakes and Driver of the Day are **not separated** on the actual side.
Both would need an independent per-round source to split apart, and none
exists: Jolpica carries no lap-by-lap positions (no overtake count), and the
public feed's ``AdditionalStats.overtaking_pts``/``dotd_pts`` are
season-cumulative and confirmed stale (rounds 12 and 13's payloads are
byte-identical for every driver checked, despite real points movement that
round -- the same staleness class already documented for other
``AdditionalStats`` fields). So the residual -- actual race points minus
everything reconstructed without it -- is reported as one combined
"overtakes + driver of the day" component, on both sides, rather than
guessing a split. This is a merge for honesty, not a limitation hidden from
the reader: the site labels it as combined.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from f1_fantasy.predict.points import build_grid_conditioned_distributions
from f1_fantasy.predict.reconcile import reconcile_round, reconstruct_points

#: Components shown on both sides of the comparison, in display order.
#: "overtakes_and_dotd" merges points.py's overtakes + driver_of_the_day
#: components (predicted side) with reconcile.py's residual (actual side) --
#: see the module docstring for why these can't be split further.
COMPONENT_FIELDS = ("position", "positions_gained", "overtakes_and_dotd", "fastest_lap", "dnf", "qualifying")
COMPONENT_LABELS = {
    "position": "Race position",
    "positions_gained": "Positions gained",
    "overtakes_and_dotd": "Overtakes + DOTD",
    "fastest_lap": "Fastest lap",
    "dnf": "DNF",
    "qualifying": "Qualifying",
}


@dataclass
class DriverComparison:
    driver: str
    constructor: str
    predicted_total: float
    predicted_p10: float
    predicted_p90: float
    actual_total: float
    #: True unless the residual behind actual's overtakes_and_dotd didn't
    #: look like a plausible non-negative value -- see
    #: reconcile.residual_looks_like_overtakes. False means "shown as 0, but
    #: don't trust it" rather than a dropped driver, matching this project's
    #: rule against reporting a lie by omission.
    residual_confident: bool
    predicted_components: dict[str, float] = field(default_factory=dict)
    actual_components: dict[str, float] = field(default_factory=dict)


def _actual_breakdowns(season: int, round_number: int) -> tuple[dict[str, dict[str, float]], dict[str, bool]]:
    base = reconstruct_points(season, round_number)
    rows = {r.driver: r for r in reconcile_round(season, round_number)}

    breakdowns: dict[str, dict[str, float]] = {}
    confident: dict[str, bool] = {}
    for driver, breakdown in base.items():
        row = rows.get(driver)
        residual = row.residual if row else 0.0
        plausible = row.residual_looks_like_overtakes if row else False
        breakdowns[driver] = {
            "position": breakdown.position,
            "positions_gained": breakdown.positions_gained,
            "overtakes_and_dotd": residual if plausible else 0.0,
            "fastest_lap": breakdown.fastest_lap,
            "dnf": breakdown.dnf,
            "qualifying": breakdown.qualifying,
        }
        confident[driver] = plausible
    return breakdowns, confident


def build_round_comparison(
    season: int,
    train_rounds: list[int],
    target_round: int,
    *,
    sprint: bool = False,
    n_samples: int = 1000,
    seed: int | None = None,
) -> list[DriverComparison]:
    """One DriverComparison per driver with both a predicted distribution
    (from ``train_rounds``, conditioned on the real grid) and a real result
    for ``target_round``. A driver missing either side is silently dropped --
    same convention as the rest of this project's backtests.
    """
    predicted = build_grid_conditioned_distributions(
        season, train_rounds, target_round, sprint=sprint, n_samples=n_samples, seed=seed
    )
    actual_breakdowns, confident = _actual_breakdowns(season, target_round)

    out = []
    for driver, dist in predicted.items():
        actual_components = actual_breakdowns.get(driver)
        if actual_components is None:
            continue
        predicted_components = {
            "position": dist.components.get("position", 0.0),
            "positions_gained": dist.components.get("positions_gained", 0.0),
            "overtakes_and_dotd": dist.components.get("overtakes", 0.0) + dist.components.get("driver_of_the_day", 0.0),
            "fastest_lap": dist.components.get("fastest_lap", 0.0),
            "dnf": dist.components.get("dnf", 0.0),
            "qualifying": dist.components.get("qualifying", 0.0),
        }
        out.append(
            DriverComparison(
                driver=driver,
                constructor=dist.constructor,
                predicted_total=dist.mean,
                predicted_p10=dist.p10,
                predicted_p90=dist.p90,
                actual_total=sum(actual_components.values()),
                residual_confident=confident.get(driver, False),
                predicted_components=predicted_components,
                actual_components=actual_components,
            )
        )
    out.sort(key=lambda c: -c.actual_total)
    return out
