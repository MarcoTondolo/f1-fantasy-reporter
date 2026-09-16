"""Assembling a LeagueSnapshot from live API calls.

One snapshot needs one leaderboard call, one catalogue call, and one team call
per member -- so a ten-person league is a dozen requests. They are issued
serially with a small pause: this runs a handful of times per race weekend, so
there is nothing to gain from hammering an undocumented endpoint.

Team-fetch count is capped (see ``max_team_fetches``). Private leagues are
usually a small friend group, but "private" only means invite-only -- a
heavily promoted community league can carry thousands of members, and without
a cap this loop would fetch every one of them, one request at a time, at
``REQUEST_SPACING_SECONDS`` apart. That isn't hypothetical: it happened on the
first live run against this tool's own account, against a "10k+ member"
league, and stalled a CI job for minutes before being caught and cancelled.
"""

from __future__ import annotations

import logging
import time

from f1_fantasy.api.endpoints import FantasyApi
from f1_fantasy.api.models import LeagueSnapshot, Member, Phase, Team, team_key, utcnow

log = logging.getLogger(__name__)

#: Pause between per-member team fetches.
REQUEST_SPACING_SECONDS = 0.4

#: Default cap on how many members (by rank) get their team fetched. Keeps a
#: surprisingly large "private" league from turning one capture into
#: thousands of sequential requests. Override via config for a league you
#: know is small enough to want full coverage of.
DEFAULT_MAX_TEAM_FETCHES = 15


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


def _is_own_account(member_guid: str, api_guid: str) -> bool:
    """Whether a leaderboard row's guid names the authenticated account.

    ``api_guid`` (``Credentials.guid``, i.e. ``F1_USER_GUID``) is the bare
    UUID copied from the login response. A leaderboard row's guid is a
    compound ``"<uuid>-0-<n>"`` form -- confirmed live 2026-09-16 across
    every member in a real league, not just this account's own rows -- so
    the two never match by exact equality even for this account's own
    entries. Matched by prefix instead; a UUID is high-entropy enough that
    an accidental collision with another member's guid is not a real risk.
    """
    return member_guid == api_guid or member_guid.startswith(api_guid)


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
    max_team_fetches: int | None = DEFAULT_MAX_TEAM_FETCHES,
) -> tuple[LeagueSnapshot, TeamAccess]:
    """Capture one league at one moment.

    ``max_team_fetches`` caps how many members (by rank, since the leaderboard
    is already sorted) get their team pulled. Pass None for no cap -- only
    worth doing for a league you've confirmed is small. Standings still cover
    every member regardless, since that's one cheap call rather than one per
    member; only the per-member team detail (and so the lockout/chips/
    ownership content) is capped.
    """
    league, members = api.leaderboard(league_id)
    log.info("league %s (%s): %d members", league_id, league.league_name, len(members))

    players = api.players(race_id)
    log.info("catalogue for race %d: %d players", race_id, len(players))

    fetchable = members if max_team_fetches is None else members[:max_team_fetches]
    if len(fetchable) < len(members):
        log.warning(
            "league %s has %d members; fetching team details for the top %d by rank only",
            league_id, len(members), len(fetchable),
        )

    teams: dict[str, Team] = {}
    access = TeamAccess(total=len(members))

    for index, member in enumerate(fetchable):
        if index and spacing:
            time.sleep(spacing)
        # getteam silently echoes the caller's own team(s) for any other
        # guid rather than erroring (confirmed live) -- it must only ever be
        # used for the authenticated account itself. Every other member goes
        # through the dedicated opponent endpoint instead.
        if _is_own_account(member.guid, api.guid):
            # One call recovers every team this account holds: getteam
            # authenticates by session token, not by the guid string in its
            # URL, and returns the account's *full* userTeam list regardless
            # -- confirmed live 2026-09-16, a two-team account's response
            # carried both team_no 1 and 2, each with genuinely independent
            # bank/value/points/picks. _pick_team already discriminates by
            # team_no from that one list, so no retry is needed here. (An
            # earlier version of this fetch retried a team_no mismatch via
            # the opponent endpoint instead -- confirmed live, also on
            # 2026-09-16, that the opponent endpoint is the one that *can't*
            # reach this account's own non-primary team: asked for team_no 2
            # it always answers with team_no 1 again. That silent duplicate
            # shipped for two rounds before this was caught.)
            found = api.try_teams(race_id, guid=member.guid)
            team = _pick_team(found, member) if found else None
        else:
            found = api.try_opponent_teams(member.guid, race_id, member.team_no)
            team = _pick_team(found, member) if found else None
        if team is None:
            log.debug("no team data for %s (%s)", member.user_name, member.guid)
            continue
        teams[team_key(member.guid, member.team_no)] = team
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
