"""DNF/reliability hazard: the biggest lever left in race prediction.

Actual grid position correlates with race order at 0.652 (2026 rounds 1-12).
Restricted to classified finishers only, that rises to 0.846 -- the entire
0.19-point gap is DNFs, at a season rate of 21.6% that ranges from 4.2%
(Alpine) to 45.8% (Aston Martin) by constructor and persists across the
season (first-half vs second-half DNF rate correlates at r=0.42). Modelled at
constructor level, not driver level: reliability is overwhelmingly a car
property, and pooling both cars gives twice the sample per team per round.
"""

from __future__ import annotations

from dataclasses import dataclass

from f1_fantasy.predict.scoring import is_classified
from f1_fantasy.results import fetch_race_results


@dataclass(frozen=True)
class ReliabilityRecord:
    constructor: str
    races: int
    dnfs: int

    @property
    def raw_rate(self) -> float:
        return self.dnfs / self.races if self.races else 0.0


def constructor_history(season: int, rounds: list[int]) -> dict[str, ReliabilityRecord]:
    """Classified/DNF tally per constructor across *rounds*.

    Uses ``is_classified`` with lap counts, not raw status -- a driver tagged
    "Lapped" who in fact retired early (confirmed live: Stroll 43/58 laps,
    Albon 55/66, both scored as DNFs by the game) must count as a DNF here or
    every reliability estimate downstream inherits the same undercounting.
    """
    tally: dict[str, list[int]] = {}
    for round_number in rounds:
        results = fetch_race_results(season, round_number)
        winner_laps = max((r.laps for r in results), default=0)
        for result in results:
            races, dnfs = tally.get(result.constructor, [0, 0])
            races += 1
            if not is_classified(result.status, result.laps, winner_laps):
                dnfs += 1
            tally[result.constructor] = [races, dnfs]
    return {
        constructor: ReliabilityRecord(constructor, races, dnfs)
        for constructor, (races, dnfs) in tally.items()
    }


#: Shrinkage prior expressed as pseudo-races at the season-wide 2026 DNF rate
#: (21.6%), so a constructor with little history is pulled toward the field
#: average rather than toward an unearned 0%.
PRIOR_RACES = 5.0
PRIOR_RATE = 0.216


def dnf_probability(
    record: ReliabilityRecord | None,
    *,
    prior_races: float = PRIOR_RACES,
    prior_rate: float = PRIOR_RATE,
) -> float:
    """Empirical-Bayes shrinkage estimate of a constructor's DNF rate.

    Equivalent to a Beta(prior_races * prior_rate, prior_races * (1 -
    prior_rate)) prior updated with the observed record: a constructor with
    few races is dominated by the prior, one with a long consistent record by
    its own rate.
    """
    races = record.races if record else 0
    dnfs = record.dnfs if record else 0
    return (dnfs + prior_races * prior_rate) / (races + prior_races)
