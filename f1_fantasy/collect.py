"""Assembling a LeagueSnapshot from live API calls.

One snapshot needs one leaderboard call, one catalogue call, and one team call
per member -- so a ten-person league is a dozen requests. They are issued
serially with a small pause: this runs a handful of times per race weekend, so
there is nothing to gain from hammering an undocumented endpoint.
"""

from __future__ import annotations

import logging
import time

from f1_fantasy.api.endpoints import FantasyApi
from f1_fantasy.api.models import LeagueSnapshot, Member, Phase, Team, utcnow

log = logging.getLogger(__name__)

#: Pause between per-member team fetches.
REQUEST_SPACING_SECONDS = 0.4


class TeamAccess:
    """Summary of how much of a league we could actually read.

    Recorded because partial access changes what the reports can honestly claim.
    """

    def __init__(self, total: int = 0, readable: int = 0) -> None:
        self.total = total
        self.readable = readable

    @property
    def unreadable(self) -> int:
        return self.total - self.readable

    @property
    def is_complete(self) -> bool:
        return self.total > 0 and self.readable == self.total

    @property
    def is_empty(self) -> bool:
        return self.readable == 0

    def __str__(self) -> str:
        return f"{self.readable}/{self.total} member teams readable"


def _pick_team(teams: list[Team], member: Member) -> Team | None:
    """Choose the team matching the member's leaderboard entry.

    A user may hold several teams while a league entry refers to exactly one, so
    matching on team number matters -- otherwise a member's second team gets
    diffed against their first and every report shows phantom transfers.
    """
    if not teams:
        return None
    for team in teams:
        if team.team_no == member.team_no:
            return team
    return teams[0]


def collect_league(
    api: FantasyApi,
    *,
    league_id: int,
    race_id: int,
    phase: Phase,
    season: int,
    spacing: float = REQUEST_SPACING_SECONDS,
) -> tuple[LeagueSnapshot, TeamAccess]:
    """Capture one league at one moment."""
    league, members = api.leaderboard(league_id)
    log.info("league %s (%s): %d members", league_id, league.league_name, len(members))

    players = api.players(race_id)
    log.info("catalogue for race %d: %d players", race_id, len(players))

    teams: dict[str, Team] = {}
    access = TeamAccess(total=len(members))

    for index, member in enumerate(members):
        if index and spacing:
            time.sleep(spacing)
        found = api.try_teams(race_id, guid=member.guid)
        if not found:
            log.debug("no team data for %s (%s)", member.user_name, member.guid)
            continue
        team = _pick_team(found, member)
        if team is not None:
            teams[member.guid] = team
            access.readable += 1

    log.info("%s", access)

    snapshot = LeagueSnapshot(
        league_id=league_id,
        league_name=league.league_name,
        season=season,
        race_id=race_id,
        phase=phase,
        captured_at=utcnow(),
        members=members,
        teams=teams,
        players=players,
    )
    return snapshot, access
