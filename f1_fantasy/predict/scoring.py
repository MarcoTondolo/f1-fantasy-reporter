"""The F1 Fantasy points table, derived from data rather than documentation.

The official rules page is a JavaScript application that is unreachable from
here (blocked to both WebFetch and headless Chromium), and secondary
write-ups contradict each other -- one claims fastest-lap points were
abolished, while the live feed plainly carries ``fastest_lap_pts``. So every
value below was instead *solved from the game's own published numbers* and is
pinned by a reconciliation test against them.

How each value was established (2026 rounds 1-12, driver feed vs Jolpica
results):

- Qualifying 10..1: exact across all 12 rounds, no exceptions.
- Qualifying -5: observed for drivers with empty Q1/Q2/Q3 (Hadjar, round 4)
  and for drivers absent from the qualifying classification entirely.
- Race positions 25..1: matches ``top10_race_position_pts`` exactly.
- Positions gained/lost, 1 per place: the ratio of points to places gained is
  exactly 1.0 wherever it is non-zero. Note it is measured against the
  *starting grid*, not the qualifying classification -- those differ whenever
  a penalty is applied, which is what made an earlier reading of "2 per
  place" look plausible.
- Fastest lap +10, driver of the day +10, DNF -20: single observed values
  across every occurrence in the fresh-data rounds.

Two data hazards this module's callers must respect, both confirmed live:

1. ``AdditionalStats`` is season-cumulative *and sometimes stale* -- the
   round 5 and 7 feeds repeat round 4's and 6's totals verbatim. Differencing
   consecutive feeds to recover per-race components is therefore unreliable.
   The per-race ``QualifyingPoints`` / ``RacePoints`` / ``SprintPoints``
   fields are trustworthy: they sum exactly to ``OverallPpints`` for 22 of 23
   drivers across the season.
2. The exception is Lawson, whose cumulative totals were restated mid-season
   (a mid-season seat change). Any per-driver reconciliation should expect
   him to fail and treat it as a known data artifact, not a rule error.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Points for finishing position in a grand prix. The real F1 points table.
RACE_POSITION_POINTS: dict[int, int] = {
    1: 25, 2: 18, 3: 15, 4: 12, 5: 10, 6: 8, 7: 6, 8: 4, 9: 2, 10: 1
}

#: Points for finishing position in a sprint.
SPRINT_POSITION_POINTS: dict[int, int] = {1: 8, 2: 7, 3: 6, 4: 5, 5: 4, 6: 3, 7: 2, 8: 1}

#: Points for qualifying classification: P1 scores 10 down to P10 scoring 1.
QUALIFYING_POINTS: dict[int, int] = {pos: 11 - pos for pos in range(1, 11)}

#: Charged when a driver sets no qualifying time at all.
NO_QUALIFYING_TIME_POINTS = -5

#: Per place gained or lost between the starting grid and the finish.
POINTS_PER_PLACE_GAINED = 1

#: Per on-track overtake. The one value not independently confirmable from
#: Jolpica, which carries no lap-by-lap positions -- see ``reconcile`` and the
#: residual test, which is what establishes it.
POINTS_PER_OVERTAKE = 1

FASTEST_LAP_POINTS = 10
DRIVER_OF_THE_DAY_POINTS = 10

#: Charged for a retirement or disqualification. The sprint charge is half.
DNF_POINTS = -20
SPRINT_DNF_POINTS = -10

#: Statuses that count as a classified finish. This feed says "Lapped" where
#: Ergast said "+1 Lap"; a lapped runner is classified, a retirement is not.
CLASSIFIED_STATUSES = frozenset({"Finished", "Lapped"})

#: Fraction of the winner's distance a driver must cover to be classified,
#: per the FIA sporting regulations.
#:
#: Needed because the status alone is not sufficient: the results feed labels
#: some drivers "Lapped" who in fact retired. Confirmed live -- Stroll
#: completed 43 of 58 laps at round 1 and Albon 55 of 66 at round 7, both
#: tagged "Lapped", and both were scored -20 by the game. Every genuinely
#: classified "Lapped" driver in the season covered at least 95.5%, so the
#: regulation's 90% cleanly separates the two groups.
CLASSIFICATION_DISTANCE_FRACTION = 0.9


def is_classified(status: str, laps: int = 0, winner_laps: int = 0) -> bool:
    """Whether a driver counts as having finished, for scoring purposes.

    Falls back to the status alone when lap counts are unavailable, which is
    right for every case except the mislabelled retirements described above.
    """
    if status not in CLASSIFIED_STATUSES:
        return False
    if laps and winner_laps:
        return laps >= CLASSIFICATION_DISTANCE_FRACTION * winner_laps
    return True


@dataclass
class PointsBreakdown:
    """Fantasy points for one driver in one session, by source."""

    position: float = 0.0
    positions_gained: float = 0.0
    overtakes: float = 0.0
    fastest_lap: float = 0.0
    driver_of_the_day: float = 0.0
    dnf: float = 0.0
    qualifying: float = 0.0
    components: dict[str, float] = field(default_factory=dict)

    @property
    def total(self) -> float:
        return (
            self.position
            + self.positions_gained
            + self.overtakes
            + self.fastest_lap
            + self.driver_of_the_day
            + self.dnf
            + self.qualifying
        )


def qualifying_points(position: int | None, *, set_a_time: bool = True) -> float:
    """Points for a qualifying classification.

    ``position`` is the qualifying classification; None means the driver did
    not appear in it at all, which is charged the same as setting no time.
    """
    if not set_a_time or position is None:
        return float(NO_QUALIFYING_TIME_POINTS)
    return float(QUALIFYING_POINTS.get(position, 0))


def race_points(
    *,
    grid: int,
    position: int | None,
    status: str,
    overtakes: int = 0,
    fastest_lap: bool = False,
    driver_of_the_day: bool = False,
    sprint: bool = False,
    laps: int = 0,
    winner_laps: int = 0,
) -> PointsBreakdown:
    """Points for a grand prix or sprint.

    A retirement takes the flat DNF charge and is *not* additionally charged
    for the places it nominally lost -- confirmed live, where drivers
    classified 17th-22nd after retiring from midfield grid slots scored zero
    position-change points rather than a large negative.
    """
    table = SPRINT_POSITION_POINTS if sprint else RACE_POSITION_POINTS
    classified = is_classified(status, laps, winner_laps)
    breakdown = PointsBreakdown()

    if classified and position is not None:
        breakdown.position = float(table.get(position, 0))
        if grid > 0:
            breakdown.positions_gained = float((grid - position) * POINTS_PER_PLACE_GAINED)
    else:
        breakdown.dnf = float(SPRINT_DNF_POINTS if sprint else DNF_POINTS)

    breakdown.overtakes = float(overtakes * POINTS_PER_OVERTAKE)
    if fastest_lap:
        breakdown.fastest_lap = float(FASTEST_LAP_POINTS)
    if driver_of_the_day:
        breakdown.driver_of_the_day = float(DRIVER_OF_THE_DAY_POINTS)
    return breakdown
