"""Synthetic league data for previewing cards without live API access.

Exists because report layout has to be judged by eye, and waiting for a real
race weekend to see whether a card looks right is a bad feedback loop. The
numbers are plausible rather than real.
"""

from __future__ import annotations

import random
from datetime import datetime, timezone

from f1_fantasy.api.models import (
    Chip,
    ChipUsage,
    LeagueSnapshot,
    Member,
    Phase,
    Pick,
    Player,
    Team,
)

DRIVERS = [
    ("1", "M Verstappen", "Red Bull Racing", 29.1),
    ("2", "L Norris", "McLaren", 27.4),
    ("3", "O Piastri", "McLaren", 25.8),
    ("4", "C Leclerc", "Ferrari", 23.2),
    ("5", "L Hamilton", "Ferrari", 21.9),
    ("6", "G Russell", "Mercedes", 21.1),
    ("7", "K Antonelli", "Mercedes", 17.6),
    ("8", "F Alonso", "Aston Martin", 12.4),
    ("9", "A Albon", "Williams", 11.8),
    ("10", "P Gasly", "Alpine", 9.2),
    ("11", "N Hulkenberg", "Audi", 7.9),
    ("12", "E Ocon", "Haas", 6.8),
]

CONSTRUCTORS = [
    ("101", "McLaren", "McLaren", 30.2),
    ("102", "Ferrari", "Ferrari", 24.6),
    ("103", "Red Bull", "Red Bull Racing", 22.8),
    ("104", "Mercedes", "Mercedes", 21.4),
    ("105", "Williams", "Williams", 12.1),
    ("106", "Audi", "Audi", 8.4),
]

MEMBERS = [
    ("guid-01", "Chris", "Box Box Baby"),
    ("guid-02", "Sam", "Hammer Time"),
    ("guid-03", "Priya", "Full Send Racing"),
    ("guid-04", "Tom", "Undercut Kings"),
    ("guid-05", "Aoife", "Purple Sector"),
    ("guid-06", "Marcus", "Lights Out"),
    ("guid-07", "Jen", "DRS Enjoyers"),
    ("guid-08", "Dev", "Sunday Drivers"),
    ("guid-09", "Kaz", "Tyre Whisperers"),
    ("guid-10", "Ellie", "Podium Bound"),
]


def _catalogue(seed: int, *, scored: bool) -> dict[str, Player]:
    """Build the player catalogue. Unscored mirrors a pre-race capture."""
    rng = random.Random(seed)
    players: dict[str, Player] = {}

    for pid, name, constructor, price in DRIVERS:
        points = 0.0
        if scored:
            # Roughly price-correlated with real scatter, so upsets happen.
            points = max(-5.0, round(rng.gauss(price * 0.9, 9), 1))
        players[pid] = Player(
            player_id=pid,
            display_name=name,
            full_name=name,
            tla=name.split()[-1][:3].upper(),
            constructor=constructor,
            is_constructor=False,
            price=price,
            race_points=points,
            season_points=round(price * 11, 1),
            selected_pct=round(rng.uniform(3, 70), 1),
            captain_pct=round(rng.uniform(0, 35), 1),
        )

    for pid, name, constructor, price in CONSTRUCTORS:
        points = 0.0
        if scored:
            points = max(-4.0, round(rng.gauss(price * 0.85, 8), 1))
        players[pid] = Player(
            player_id=pid,
            display_name=name,
            full_name=name,
            tla="",
            constructor=constructor,
            is_constructor=True,
            price=price,
            race_points=points,
            season_points=round(price * 11, 1),
            selected_pct=round(rng.uniform(5, 75), 1),
            captain_pct=0.0,
        )

    return players


def _build(
    rng: random.Random,
    guid: str,
    race_id: int,
    team_name: str,
    chips: dict[Chip, int],
    *,
    driver_ids: list[str],
    constructor_ids: list[str],
    captain: str,
) -> Team:
    picks = [
        Pick(player_id=pid, is_captain=pid == captain, slot=index)
        for index, pid in enumerate([*driver_ids, *constructor_ids])
    ]
    return Team(
        guid=guid,
        race_id=race_id,
        team_no=1,
        team_name=team_name,
        picks=picks,
        value=round(rng.uniform(96, 108), 1),
        bank=round(rng.uniform(0, 4), 1),
        transfers_made=rng.choice([0, 0, 1, 1, 2]),
        transfers_left=2,
        points=0.0,
        chips=[
            ChipUsage(chip=chip, used=chip in chips, race_id=chips.get(chip))
            for chip in Chip
        ],
    )


def _random_team(rng: random.Random, guid: str, race_id: int, team_name: str) -> Team:
    driver_ids = rng.sample([d[0] for d in DRIVERS], 5)
    constructor_ids = rng.sample([c[0] for c in CONSTRUCTORS], 2)
    return _build(
        rng,
        guid,
        race_id,
        team_name,
        {},
        driver_ids=driver_ids,
        constructor_ids=constructor_ids,
        captain=rng.choice(driver_ids),
    )


def _evolve(
    rng: random.Random,
    previous: Team,
    race_id: int,
    chips: dict[Chip, int],
    *,
    swaps: int,
) -> Team:
    """Carry a team forward with a limited number of changes.

    Free transfers cap most weeks at one or two swaps; only a wildcard or
    limitless week reshuffles a whole squad. Generating fully random teams each
    race made every member look like they had wildcarded, which both misrepresents
    the data and inflates the card's height.
    """
    drivers = [p.player_id for p in previous.picks if p.player_id in {d[0] for d in DRIVERS}]
    constructors = [p.player_id for p in previous.picks if p.player_id not in drivers]

    driver_pool = [d[0] for d in DRIVERS if d[0] not in drivers]
    constructor_pool = [c[0] for c in CONSTRUCTORS if c[0] not in constructors]

    for _ in range(swaps):
        # Constructors change far less often than drivers.
        if constructor_pool and rng.random() < 0.25:
            constructors[rng.randrange(len(constructors))] = constructor_pool.pop(
                rng.randrange(len(constructor_pool))
            )
        elif driver_pool:
            drivers[rng.randrange(len(drivers))] = driver_pool.pop(
                rng.randrange(len(driver_pool))
            )

    previous_captain = previous.captain_id
    keep_captain = previous_captain in drivers and rng.random() < 0.7
    captain = previous_captain if keep_captain else rng.choice(drivers)

    team = _build(
        rng,
        previous.guid,
        race_id,
        previous.team_name,
        chips,
        driver_ids=drivers,
        constructor_ids=constructors,
        captain=captain,
    )
    # Team value drifts with driver prices rather than being redrawn, so the
    # value-change figure on the card is consistent with the transfers shown.
    base = previous.value or 100.0
    team.value = round(base + rng.gauss(0.1, 0.6) + 0.4 * swaps * rng.choice([-1, 1]), 1)
    return team


def demo_snapshots(seed: int = 7) -> tuple[LeagueSnapshot, LeagueSnapshot]:
    """A previous and a current snapshot, with realistic churn between them."""
    rng = random.Random(seed)
    previous_race, current_race = 14, 15

    previous_teams: dict[str, Team] = {}
    current_teams: dict[str, Team] = {}

    for index, (guid, _, team_name) in enumerate(MEMBERS):
        previous_teams[guid] = _random_team(rng, guid, previous_race, team_name)

        # A realistic weekend: most members make one or two transfers, a couple
        # stand pat, one wildcards and one goes limitless.
        chips: dict[Chip, int] = {}
        if index == 2:
            chips[Chip.WILDCARD] = current_race
            swaps = 5
        elif index == 6:
            chips[Chip.LIMITLESS] = current_race
            swaps = 4
        elif index in (5, 8):
            swaps = 0  # held firm
            if index == 5:
                chips[Chip.AUTOPILOT] = 11  # played earlier in the season
        else:
            swaps = rng.choice([1, 1, 2])

        current_teams[guid] = _evolve(
            rng, previous_teams[guid], current_race, chips, swaps=swaps
        )

    previous_points = {guid: round(rng.uniform(880, 1180), 1) for guid, _, _ in MEMBERS}
    round_points = {guid: round(rng.uniform(28, 96), 1) for guid, _, _ in MEMBERS}
    current_points = {g: round(previous_points[g] + round_points[g], 1) for g in previous_points}

    def _members(totals: dict[str, float]) -> list[Member]:
        ordered = sorted(MEMBERS, key=lambda m: -totals[m[0]])
        return [
            Member(
                guid=guid,
                user_name=name,
                team_id=1000 + index,
                team_no=1,
                team_name=team_name,
                rank=index + 1,
                points=totals[guid],
                trend=0,
            )
            for index, (guid, name, team_name) in enumerate(ordered)
        ]

    captured = datetime(2026, 8, 23, 18, 30, tzinfo=timezone.utc)

    previous = LeagueSnapshot(
        league_id=4321,
        league_name="Sunday Drivers",
        season=2026,
        race_id=previous_race,
        phase=Phase.FINAL,
        captured_at=captured,
        members=_members(previous_points),
        teams=previous_teams,
        players=_catalogue(seed, scored=True),
    )
    current = LeagueSnapshot(
        league_id=4321,
        league_name="Sunday Drivers",
        season=2026,
        race_id=current_race,
        phase=Phase.FINAL,
        captured_at=captured,
        members=_members(current_points),
        teams=current_teams,
        players=_catalogue(seed + 1, scored=True),
    )
    return previous, current
