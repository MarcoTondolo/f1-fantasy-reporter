"""Did an upgrade actually work? A field-relative before/after measurement.

The question isn't "did this constructor get faster" -- every team drifts
round to round as tyres, fuel loads, and setups mature, regardless of
upgrades. It's "did they get faster *more than the field did* over the same
window" -- the same "compare a delta against a control, not a raw value"
spirit as ``pace/hers.py``'s year-on-year comparison and
``predict/adjusted.py``'s before/after reliability comparison, just
controlling for field-wide drift within one season instead of holding
circuit identity fixed across seasons.

No p-value anywhere: with at most a few rounds either side of an upgrade
and two drivers per team, this is a small-n comparison by construction.
``UpgradeEffect`` always carries ``n_before``/``n_after`` alongside
``relative_delta`` so a reader judges confidence themselves, the same
practice ``pace/tyre_asymmetry.py`` uses (``n < 4 -> None`` there; here, an
empty window -> None, never a single noisy round dressed up as a result).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from f1_fantasy.predict.form import qualifying_gap_pct
from f1_fantasy.results import fetch_qualifying

#: Rounds either side of an upgrade round to average, by default.
DEFAULT_WINDOW = 3

#: Below this many rounds on a side, there's nothing to average -- the
#: comparison for that side is reported as unavailable (None), not computed
#: on an empty or single noisy sample dressed up as a measurement.
MIN_WINDOW = 1


def constructor_gap_pct(season: int, round_number: int) -> dict[str, float]:
    """Each constructor's mean qualifying gap % for one round, averaged
    across its two drivers. A driver absent that round (DNS, reserve swap)
    just means a single-driver mean instead of two -- still real signal
    about the car, not dropped.
    """
    gaps = qualifying_gap_pct(season, round_number)
    constructor_of = {q.driver_code: q.constructor for q in fetch_qualifying(season, round_number)}

    by_constructor: dict[str, list[float]] = {}
    for driver, gap in gaps.items():
        constructor = constructor_of.get(driver)
        if constructor:
            by_constructor.setdefault(constructor, []).append(gap)
    return {constructor: statistics.mean(values) for constructor, values in by_constructor.items()}


def _window(upgrade_round: int, available_rounds: list[int], *, before: bool, window: int = DEFAULT_WINDOW) -> list[int]:
    """Up to *window* rounds strictly before/after *upgrade_round*, closest
    first, from whatever exists in *available_rounds*. Shrinks near a
    season boundary rather than refusing outright (a settled decision: the
    case this feature is checked for most -- an upgrade at the very latest
    race -- would otherwise never show a number at all); empty once fewer
    than MIN_WINDOW rounds exist on that side.
    """
    if before:
        candidates = sorted((r for r in available_rounds if r < upgrade_round), reverse=True)
    else:
        candidates = sorted(r for r in available_rounds if r > upgrade_round)
    selected = candidates[:window]
    return selected if len(selected) >= MIN_WINDOW else []


@dataclass(frozen=True)
class UpgradeEffect:
    constructor: str
    upgrade_round: int
    before_rounds: tuple[int, ...]
    after_rounds: tuple[int, ...]
    constructor_before_mean: float | None
    constructor_after_mean: float | None
    field_before_mean: float | None
    field_after_mean: float | None
    constructor_delta: float | None  # after - before; negative = improved (lower gap% is better)
    field_delta: float | None
    relative_delta: float | None  # constructor_delta - field_delta; negative = beat the field
    n_before: int
    n_after: int


def measure_upgrade_effect(
    season: int,
    constructor: str,
    upgrade_round: int,
    available_rounds: list[int],
    *,
    window: int = DEFAULT_WINDOW,
) -> UpgradeEffect:
    before_rounds = tuple(_window(upgrade_round, available_rounds, before=True, window=window))
    after_rounds = tuple(_window(upgrade_round, available_rounds, before=False, window=window))

    per_round = {r: constructor_gap_pct(season, r) for r in set(before_rounds) | set(after_rounds)}

    def _constructor_mean(rounds: tuple[int, ...]) -> float | None:
        values = [per_round[r][constructor] for r in rounds if constructor in per_round[r]]
        return statistics.mean(values) if values else None

    def _field_mean(rounds: tuple[int, ...]) -> float | None:
        values = [v for r in rounds for v in per_round[r].values()]
        return statistics.mean(values) if values else None

    constructor_before, constructor_after = _constructor_mean(before_rounds), _constructor_mean(after_rounds)
    field_before, field_after = _field_mean(before_rounds), _field_mean(after_rounds)

    constructor_delta = (
        constructor_after - constructor_before if constructor_before is not None and constructor_after is not None else None
    )
    field_delta = field_after - field_before if field_before is not None and field_after is not None else None
    relative_delta = constructor_delta - field_delta if constructor_delta is not None and field_delta is not None else None

    return UpgradeEffect(
        constructor=constructor,
        upgrade_round=upgrade_round,
        before_rounds=before_rounds,
        after_rounds=after_rounds,
        constructor_before_mean=constructor_before,
        constructor_after_mean=constructor_after,
        field_before_mean=field_before,
        field_after_mean=field_after,
        constructor_delta=constructor_delta,
        field_delta=field_delta,
        relative_delta=relative_delta,
        n_before=len(before_rounds),
        n_after=len(after_rounds),
    )


def evaluate_attributed_upgrades(
    season: int,
    groups: dict[tuple[str, int], list],
    available_rounds: list[int],
    *,
    window: int = DEFAULT_WINDOW,
) -> list[UpgradeEffect]:
    """One UpgradeEffect per (constructor, round) news/upgrades.py attributed
    a mention to -- skips any pair whose window can't be filled on either
    side at all, rather than computing a meaningless n=0 entry."""
    effects = []
    for constructor, upgrade_round in groups:
        effect = measure_upgrade_effect(season, constructor, upgrade_round, available_rounds, window=window)
        if effect.n_before and effect.n_after:
            effects.append(effect)
    return effects
