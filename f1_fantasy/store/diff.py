"""Deriving "what changed" and "did it work" from consecutive snapshots.

This is where the reports get their content. Two questions are answered:

* **What did each member change?** -- transfers, captain, chips, team value.
  Answered by comparing two snapshots.
* **Was the change any good?** -- scored once the race points land, by valuing
  every player moved in against every player moved out.

On pairing transfers: when a member makes one swap it is natural to say "X out,
Y in, +14". When they make five (a wildcard week) there is no principled way to
say which incoming player replaced which outgoing one. Rather than invent a
pairing, the delta is always computed in aggregate -- total points in minus total
points out -- and the head-to-head framing is only used when it is genuinely a
single swap.
"""

from __future__ import annotations

from f1_fantasy.api.models import Chip, LeagueSnapshot, Model, Player, Team


class ScoredPlayer(Model):
    player_id: str
    name: str
    is_constructor: bool = False
    points: float | None = None
    price: float | None = None


class ChipUse(Model):
    member_name: str
    race_id: int


class ChipStatus(Model):
    """One chip's season-wide status across the whole league."""

    chip: Chip
    used: list[ChipUse] = []
    available: list[str] = []


class TeamChange(Model):
    """What one member altered between two races."""

    guid: str
    member_name: str
    team_name: str = ""
    drivers_in: list[ScoredPlayer] = []
    drivers_out: list[ScoredPlayer] = []
    constructors_in: list[ScoredPlayer] = []
    constructors_out: list[ScoredPlayer] = []
    captain_from: ScoredPlayer | None = None
    captain_to: ScoredPlayer | None = None
    chips_activated: list[Chip] = []
    value_change: float | None = None
    transfers_made: int = 0
    #: False when there is no earlier snapshot to compare against, e.g. a member
    #: who joined mid-season or the first race the tool ever captured. Their
    #: current team is still reported; it is only *change* that is unknowable.
    has_previous: bool = True

    @property
    def players_in(self) -> list[ScoredPlayer]:
        return [*self.drivers_in, *self.constructors_in]

    @property
    def players_out(self) -> list[ScoredPlayer]:
        return [*self.drivers_out, *self.constructors_out]

    @property
    def captain_changed(self) -> bool:
        if not self.has_previous:
            return False
        left = self.captain_from.player_id if self.captain_from else None
        right = self.captain_to.player_id if self.captain_to else None
        return left != right

    @property
    def unchanged(self) -> bool:
        """True when the member did nothing at all -- worth calling out."""
        return (
            not self.players_in
            and not self.players_out
            and not self.captain_changed
            and not self.chips_activated
        )


class TransferScore(Model):
    """Whether a member's changes paid off, once the race has been scored."""

    guid: str
    member_name: str
    team_name: str = ""
    players_in: list[ScoredPlayer] = []
    players_out: list[ScoredPlayer] = []
    delta: float = 0.0
    chips_activated: list[Chip] = []

    @property
    def is_clean_swap(self) -> bool:
        """A single player for a single player -- the head-to-head case."""
        return len(self.players_in) == 1 and len(self.players_out) == 1

    @property
    def made_changes(self) -> bool:
        return bool(self.players_in or self.players_out)


class CaptainCall(Model):
    """One member's captain pick and what the multiplier earned them."""

    guid: str
    member_name: str
    captain: ScoredPlayer | None = None
    multiplier: int = 2
    #: Points the multiplier added on top of the driver's base score.
    bonus: float = 0.0


class StandingsMove(Model):
    guid: str
    member_name: str
    team_name: str = ""
    rank: int = 0
    previous_rank: int | None = None
    points: float = 0.0
    points_gained: float = 0.0

    @property
    def rank_change(self) -> int:
        """Positive means moved up the table."""
        if self.previous_rank is None:
            return 0
        return self.previous_rank - self.rank


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _scored(player_id: str, snapshot: LeagueSnapshot) -> ScoredPlayer:
    """Build a ScoredPlayer, degrading gracefully when the catalogue lacks an id.

    An unknown id is shown rather than dropped -- a missing driver in a report is
    a silent bug, whereas a bare id is visibly odd and gets investigated.
    """
    player: Player | None = snapshot.players.get(player_id)
    if player is None:
        return ScoredPlayer(player_id=player_id, name=player_id)
    return ScoredPlayer(
        player_id=player_id,
        name=player.display_name or player.full_name or player_id,
        is_constructor=player.is_constructor,
        points=player.race_points,
        price=player.price,
    )


def _split(players: list[ScoredPlayer]) -> tuple[list[ScoredPlayer], list[ScoredPlayer]]:
    drivers = [p for p in players if not p.is_constructor]
    constructors = [p for p in players if p.is_constructor]
    return drivers, constructors


def _multiplier(team: Team) -> int:
    """3x for a mega captain, otherwise the standard 2x."""
    return 3 if team.mega_captain_id else 2


def _effective_captain(team: Team) -> str | None:
    return team.mega_captain_id or team.captain_id


# --------------------------------------------------------------------------
# Diffing
# --------------------------------------------------------------------------


def diff_team(
    previous: Team | None,
    current: Team,
    *,
    member_name: str,
    snapshot: LeagueSnapshot,
) -> TeamChange:
    """Compare one member's team against their previous one."""
    previous_ids = set(previous.player_ids) if previous else set()
    current_ids = set(current.player_ids)

    # With no previous team there is nothing to diff against -- a member who
    # joined mid-season is not "transferring in" eleven players.
    if previous is None:
        incoming: list[ScoredPlayer] = []
        outgoing: list[ScoredPlayer] = []
    else:
        incoming = [_scored(pid, snapshot) for pid in sorted(current_ids - previous_ids)]
        outgoing = [_scored(pid, snapshot) for pid in sorted(previous_ids - current_ids)]

    drivers_in, constructors_in = _split(incoming)
    drivers_out, constructors_out = _split(outgoing)

    previous_captain = _effective_captain(previous) if previous else None
    current_captain = _effective_captain(current)

    value_change = None
    if previous and previous.value is not None and current.value is not None:
        value_change = round(current.value - previous.value, 2)

    return TeamChange(
        guid=current.guid,
        member_name=member_name,
        team_name=current.team_name,
        drivers_in=drivers_in,
        drivers_out=drivers_out,
        constructors_in=constructors_in,
        constructors_out=constructors_out,
        captain_from=_scored(previous_captain, snapshot) if previous_captain else None,
        captain_to=_scored(current_captain, snapshot) if current_captain else None,
        chips_activated=current.chips_used_on(current.race_id),
        value_change=value_change,
        transfers_made=current.transfers_made,
        has_previous=previous is not None,
    )


def diff_teams(
    previous: LeagueSnapshot | None,
    current: LeagueSnapshot,
) -> list[TeamChange]:
    """Diff every member's team, in league standing order."""
    changes: list[TeamChange] = []
    for member in current.members:
        current_team = current.teams.get(member.guid)
        if current_team is None:
            # No team data for this member -- either not shared, or a failed
            # fetch. Skipped rather than reported as "no changes", which would
            # be a lie.
            continue
        previous_team = previous.teams.get(member.guid) if previous else None
        changes.append(
            diff_team(
                previous_team,
                current_team,
                member_name=member.user_name or member.team_name,
                snapshot=current,
            )
        )
    return changes


def diff_standings(
    previous: LeagueSnapshot | None,
    current: LeagueSnapshot,
) -> list[StandingsMove]:
    """Rank and points movement between two races."""
    previous_by_guid = {m.guid: m for m in previous.members} if previous else {}

    moves: list[StandingsMove] = []
    for member in current.members:
        before = previous_by_guid.get(member.guid)
        moves.append(
            StandingsMove(
                guid=member.guid,
                member_name=member.user_name or member.team_name,
                team_name=member.team_name,
                rank=member.rank,
                previous_rank=before.rank if before else None,
                points=member.points,
                points_gained=round(member.points - before.points, 2) if before else 0.0,
            )
        )
    moves.sort(key=lambda m: (m.rank or 10**6, -m.points))
    return moves


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


def score_changes(
    changes: list[TeamChange],
    snapshot: LeagueSnapshot,
) -> list[TransferScore]:
    """Value each member's transfers using the race points now on the catalogue.

    Delta is aggregate (points in minus points out) so it stays meaningful when
    more than one swap was made. Members who changed nothing score 0 and are kept
    in the list -- "held firm and it worked" is its own story.
    """
    scores: list[TransferScore] = []
    for change in changes:
        # Re-read points from the post-race snapshot: the catalogue attached to
        # the lockout snapshot has zeros, since the race had not run.
        players_in = [_scored(p.player_id, snapshot) for p in change.players_in]
        players_out = [_scored(p.player_id, snapshot) for p in change.players_out]

        gained = sum(p.points or 0.0 for p in players_in)
        lost = sum(p.points or 0.0 for p in players_out)

        scores.append(
            TransferScore(
                guid=change.guid,
                member_name=change.member_name,
                team_name=change.team_name,
                players_in=players_in,
                players_out=players_out,
                delta=round(gained - lost, 2),
                chips_activated=change.chips_activated,
            )
        )

    scores.sort(key=lambda s: s.delta, reverse=True)
    return scores


def score_captains(snapshot: LeagueSnapshot) -> list[CaptainCall]:
    """What each member's captain armband actually earned them."""
    calls: list[CaptainCall] = []
    for member in snapshot.members:
        team = snapshot.teams.get(member.guid)
        if team is None:
            continue
        captain_id = _effective_captain(team)
        if not captain_id:
            continue

        captain = _scored(captain_id, snapshot)
        multiplier = _multiplier(team)
        calls.append(
            CaptainCall(
                guid=member.guid,
                member_name=member.user_name or member.team_name,
                captain=captain,
                multiplier=multiplier,
                bonus=round((captain.points or 0.0) * (multiplier - 1), 2),
            )
        )

    calls.sort(key=lambda c: c.bonus, reverse=True)
    return calls


def chip_activations(snapshot: LeagueSnapshot) -> dict[Chip, list[str]]:
    """Who played which chip for this race: ``chip -> member names``."""
    activations: dict[Chip, list[str]] = {}
    for member in snapshot.members:
        team = snapshot.teams.get(member.guid)
        if team is None:
            continue
        for chip in team.chips_used_on(snapshot.race_id):
            activations.setdefault(chip, []).append(member.user_name or member.team_name)
    return activations


def chips_remaining(snapshot: LeagueSnapshot) -> dict[str, list[Chip]]:
    """Chips each member still holds: ``member name -> chips``."""
    remaining: dict[str, list[Chip]] = {}
    for member in snapshot.members:
        team = snapshot.teams.get(member.guid)
        if team is None:
            continue
        remaining[member.user_name or member.team_name] = team.chips_remaining()
    return remaining


def chip_status(snapshot: LeagueSnapshot) -> list[ChipStatus]:
    """Season-wide status of every chip across the league.

    A single snapshot is enough for this, unlike the diff-based reports: the
    API's ``isXtaken`` flags are cumulative for the season, not per-race, so
    "who's used what, and who still has it" doesn't need a comparison at all.
    """
    rows: dict[Chip, ChipStatus] = {chip: ChipStatus(chip=chip) for chip in Chip}
    for member in snapshot.members:
        team = snapshot.teams.get(member.guid)
        if team is None:
            continue
        name = member.user_name or member.team_name
        for usage in team.chips:
            row = rows[usage.chip]
            if usage.used:
                row.used.append(ChipUse(member_name=name, race_id=usage.race_id or 0))
            else:
                row.available.append(name)

    for row in rows.values():
        row.used.sort(key=lambda u: u.race_id)
        row.available.sort()

    return list(rows.values())


def ownership(snapshot: LeagueSnapshot) -> list[tuple[ScoredPlayer, list[str]]]:
    """Who owns whom across the league, most-owned first.

    The tail is the interesting half: a player owned by exactly one member is a
    differential, and that is what decides private leagues.
    """
    owners: dict[str, list[str]] = {}
    for member in snapshot.members:
        team = snapshot.teams.get(member.guid)
        if team is None:
            continue
        name = member.user_name or member.team_name
        for player_id in team.player_ids:
            owners.setdefault(player_id, []).append(name)

    rows = [(_scored(pid, snapshot), names) for pid, names in owners.items()]
    rows.sort(key=lambda row: (-len(row[1]), row[0].name))
    return rows
