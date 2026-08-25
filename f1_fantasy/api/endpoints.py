"""Typed wrappers over the Fantasy service paths.

Path shapes are copied from the site's own XHR calls. Several segments are
constants whose meaning is not documented anywhere (the ``1/1/.../1`` in
``getteam``); they are reproduced verbatim rather than guessed at.
"""

from __future__ import annotations

import logging

from f1_fantasy.api.client import FantasyClient, FantasyError, NotShared
from f1_fantasy.api.models import (
    ChipUsage,
    LeagueRef,
    Member,
    Player,
    Team,
    parse_game_days,
    parse_leaderboard,
    parse_league_list,
    parse_players,
    parse_teams,
)

log = logging.getLogger(__name__)


class FantasyApi:
    """Every endpoint the reporter needs, returning domain models."""

    def __init__(self, client: FantasyClient) -> None:
        self.client = client

    @property
    def guid(self) -> str:
        """The authenticated user's guid."""
        return self.client.guid

    # -- leagues -----------------------------------------------------------

    def private_leagues(self) -> list[LeagueRef]:
        payload = self.client.get(f"/services/user/league/{self.guid}/1/0/0/privateleague")
        return parse_league_list(payload)

    def all_leagues(self) -> list[LeagueRef]:
        payload = self.client.get(f"/services/user/league/{self.guid}/getuserleague/1")
        return parse_league_list(payload)

    def leaderboard(self, league_id: int) -> tuple[LeagueRef, list[Member]]:
        """League standings: every member with guid, rank, points and trend.

        The trailing 1000 is the page size; leagues larger than that would need
        paging, which no private league realistically reaches.
        """
        path = (
            f"/services/user/leaderboard/{self.guid}"
            f"/pvtleagueuserrankget/1/{league_id}/0/1/1/1000/"
        )
        return parse_leaderboard(self.client.get(path))

    # -- teams -------------------------------------------------------------

    def teams(self, race_id: int, *, guid: str | None = None) -> list[Team]:
        """Teams held by *guid* (default: the authenticated user) for a race.

        Whether this works for another member's guid is the open question the
        ``probe`` command answers -- every cross-league report depends on it.
        """
        target = guid or self.guid
        path = f"/services/user/gameplay/{target}/getteam/1/1/{race_id}/1"
        payload = self.client.get(path)
        return parse_teams(payload, guid=target, race_id=race_id)

    def team(self, race_id: int, *, guid: str | None = None, team_no: int = 1) -> Team | None:
        for team in self.teams(race_id, guid=guid):
            if team.team_no == team_no:
                return team
        return None

    def try_teams(self, race_id: int, *, guid: str) -> list[Team] | None:
        """Like :meth:`teams` but returns None when the data is not shared.

        Used when sweeping a whole league, where one unreadable member must not
        abort the run.
        """
        try:
            return self.teams(race_id, guid=guid)
        except NotShared:
            return None
        except FantasyError as exc:
            log.warning("could not read team for %s: %s", guid, exc)
            return None

    # -- scoring history ---------------------------------------------------

    def game_days(self, *, guid: str | None = None) -> tuple[dict[int, float], list[ChipUsage]]:
        """Per-race points and season chip state for *guid*.

        This is the only endpoint that backfills history: ``mddetails`` is keyed
        by race id across the whole season.
        """
        target = guid or self.guid
        payload = self.client.get(f"/services/user/gameplay/{target}/getusergamedaysv1/1")
        return parse_game_days(payload)

    def opponent_game_days(
        self, guid: str, team_no: int
    ) -> tuple[dict[int, float], list[ChipUsage]]:
        """Per-race points and season chip state for another league member.

        Confirmed live 2026-08-25, via a DevTools capture on the site itself --
        ``getteam`` cannot read another member (it silently echoes the caller's
        own team regardless of guid), but this dedicated opponent endpoint
        genuinely does. It does not carry picks/roster, only points and chips;
        a further endpoint for the opponent's actual driver/constructor
        selections has not yet been found. Race id is not part of this URL --
        unlike ``getteam``, it always reflects the account's current gameday.
        """
        payload = self.client.get(
            f"/services/user/opponentteam/opponentgamedayget/1/{guid}/{team_no}"
        )
        return parse_game_days(payload)

    def current_race_id(self) -> int:
        """Highest race id the authenticated user has a scoring record for."""
        points, _ = self.game_days()
        if not points:
            raise FantasyError(
                "Cannot determine the current race: no game-day records for this account."
            )
        return max(points)

    # -- catalogue ---------------------------------------------------------

    def players(self, race_id: int) -> dict[str, Player]:
        """Driver and constructor catalogue for a race: price, points, ownership.

        Public feed -- no auth needed, and historical race ids still resolve,
        which is what makes price/points backfill possible.
        """
        payload = self.client.get(f"/feeds/drivers/{race_id}_en.json")
        return parse_players(payload)
