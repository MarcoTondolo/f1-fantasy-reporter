"""Fixture builders.

Snapshots are constructed from compact specs rather than recorded JSON so each
test states exactly the situation it is about. Real recorded payloads are used
separately, in test_models, to pin the parsers to the API's actual shapes.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from f1_fantasy.api.models import (
    Chip,
    ChipUsage,
    LeagueSnapshot,
    Member,
    Phase,
    Pick,
    Player,
    Team,
    team_key,
)

# A small, stable catalogue. Points are per-race and set by each test.
CATALOGUE = {
    "1": ("Verstappen", False, "Red Bull", 28.5),
    "2": ("Norris", False, "McLaren", 26.0),
    "3": ("Leclerc", False, "Ferrari", 22.4),
    "4": ("Hamilton", False, "Ferrari", 21.8),
    "5": ("Albon", False, "Williams", 12.1),
    "6": ("Ocon", False, "Haas", 7.4),
    "101": ("McLaren", True, "McLaren", 24.0),
    "102": ("Ferrari", True, "Ferrari", 20.5),
    "103": ("Williams", True, "Williams", 11.0),
}


def make_players(points: dict[str, float] | None = None) -> dict[str, Player]:
    """Catalogue with per-race points; anything unlisted scores zero."""
    points = points or {}
    return {
        pid: Player(
            player_id=pid,
            display_name=name,
            full_name=name,
            tla=name[:3].upper(),
            constructor=constructor,
            is_constructor=is_constructor,
            price=price,
            race_points=points.get(pid, 0.0),
            season_points=points.get(pid, 0.0) * 5,
            selected_pct=25.0,
            captain_pct=10.0,
        )
        for pid, (name, is_constructor, constructor, price) in CATALOGUE.items()
    }


def make_team(
    guid: str,
    race_id: int,
    player_ids: list[str],
    *,
    captain: str | None = None,
    mega_captain: str | None = None,
    chips: dict[Chip, int] | None = None,
    value: float | None = 100.0,
    transfers_made: int = 0,
    team_name: str = "",
    points: float = 0.0,
    team_no: int = 1,
) -> Team:
    """Build a team. *chips* maps a chip to the race id it was used on.

    *team_no* defaults to 1; pass 2+ to model an account running more than one
    team in the same league -- confirmed live, and the reason snapshot.teams
    is keyed by (guid, team_no) rather than guid alone. See team_key.
    """
    chips = chips or {}
    picks = [
        Pick(
            player_id=pid,
            is_captain=pid == captain,
            is_mega_captain=pid == mega_captain,
            slot=index,
        )
        for index, pid in enumerate(player_ids)
    ]
    return Team(
        guid=guid,
        race_id=race_id,
        team_no=team_no,
        team_name=team_name or f"Team {guid}",
        picks=picks,
        value=value,
        bank=2.0,
        transfers_made=transfers_made,
        transfers_left=max(0, 2 - transfers_made),
        points=points,
        chips=[
            ChipUsage(chip=chip, used=chip in chips, race_id=chips.get(chip))
            for chip in Chip
        ],
    )


def make_snapshot(
    race_id: int,
    teams: dict[str, Team],
    *,
    phase: Phase = Phase.LOCKED,
    points: dict[str, float] | None = None,
    standings: dict[str, tuple[int, float]] | None = None,
    league_id: int = 555,
    season: int = 2026,
) -> LeagueSnapshot:
    """Build a snapshot. *standings* maps guid to (rank, overall points).

    *teams* is keyed by plain guid here, for every test's convenience -- one
    team per guid covers everything except the multi-team-per-account
    scenario, which needs two different ranks for the same guid and so
    doesn't fit this helper's shape; those tests build a LeagueSnapshot
    directly. Internally re-keyed to match the real (guid, team_no) scheme
    LeagueSnapshot.teams actually uses -- see team_key.
    """
    standings = standings or {guid: (i + 1, 0.0) for i, guid in enumerate(teams)}
    members = [
        Member(
            guid=guid,
            user_name=f"member-{guid}",
            team_id=int(guid) if guid.isdigit() else 0,
            team_no=teams[guid].team_no if guid in teams else 1,
            team_name=teams[guid].team_name if guid in teams else "",
            rank=standings.get(guid, (0, 0.0))[0],
            points=standings.get(guid, (0, 0.0))[1],
        )
        for guid in standings
    ]
    keyed_teams = {team_key(guid, team.team_no): team for guid, team in teams.items()}
    return LeagueSnapshot(
        league_id=league_id,
        league_name="Test League",
        season=season,
        race_id=race_id,
        phase=phase,
        captured_at=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
        members=members,
        teams=keyed_teams,
        players=make_players(points),
    )


@pytest.fixture
def players() -> dict[str, Player]:
    return make_players()
