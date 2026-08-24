"""Domain models, and parsers from raw API payloads into them.

Two layers live here on purpose. The API's own field names are misspelled
(``OverallPpints``), inconsistent (``playerpostion``), and unstable between
seasons; everything downstream -- diffing, reports, templates -- speaks the clean
names below instead. When the API shifts, only the ``parse_*`` functions move.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, ConfigDict

from f1_fantasy.util import as_bool, as_float, as_int, decode, first, positive_int_or_none


class Model(BaseModel):
    """Base for every domain model: ignore unknown keys, allow round-tripping."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class Chip(str, Enum):
    WILDCARD = "wildcard"
    LIMITLESS = "limitless"
    AUTOPILOT = "autopilot"
    EXTRA_DRS = "extra_drs"
    FINAL_FIX = "final_fix"
    NO_NEGATIVE = "no_negative"


CHIP_LABELS: dict[Chip, str] = {
    Chip.WILDCARD: "Wildcard",
    Chip.LIMITLESS: "Limitless",
    Chip.AUTOPILOT: "Autopilot",
    Chip.EXTRA_DRS: "Extra DRS",
    Chip.FINAL_FIX: "Final Fix",
    Chip.NO_NEGATIVE: "No Negative",
}

# (flag keys, race-id keys) per chip. Multiple spellings because the team and
# game-days endpoints disagree -- notably autopilot's race id.
CHIP_KEYS: dict[Chip, tuple[tuple[str, ...], tuple[str, ...]]] = {
    Chip.WILDCARD: (
        ("iswildcardtaken",),
        ("wildcardtakengd", "is_wildcard_taken_gd_id"),
    ),
    Chip.LIMITLESS: (("islimitlesstaken",), ("limitlesstakengd",)),
    Chip.AUTOPILOT: (
        ("isautopilottaken",),
        ("autopilottakengd", "isautopilottakengd"),
    ),
    Chip.EXTRA_DRS: (("isextradrstaken",), ("extradrstakengd",)),
    Chip.FINAL_FIX: (("isfinalfixtaken",), ("finalfixtakengd",)),
    Chip.NO_NEGATIVE: (("isnonigativetaken",), ("nonigativetakengd",)),
}


class Phase(str, Enum):
    """When during a race weekend a snapshot was taken."""

    PRE_LOCK = "pre_lock"
    LOCKED = "locked"
    FINAL = "final"


class ChipUsage(Model):
    chip: Chip
    used: bool = False
    race_id: int | None = None

    @property
    def label(self) -> str:
        return CHIP_LABELS[self.chip]


class Pick(Model):
    player_id: str
    is_captain: bool = False
    is_mega_captain: bool = False
    slot: int = 0


class Team(Model):
    """One fantasy team as it stood for a given race."""

    guid: str
    race_id: int
    team_no: int = 1
    team_name: str = ""
    picks: list[Pick] = []
    value: float | None = None
    bank: float | None = None
    transfers_made: int = 0
    transfers_left: int = 0
    points: float = 0.0
    chips: list[ChipUsage] = []

    @property
    def player_ids(self) -> list[str]:
        return [pick.player_id for pick in self.picks]

    @property
    def captain_id(self) -> str | None:
        return next((p.player_id for p in self.picks if p.is_captain), None)

    @property
    def mega_captain_id(self) -> str | None:
        return next((p.player_id for p in self.picks if p.is_mega_captain), None)

    def chips_used_on(self, race_id: int) -> list[Chip]:
        """Chips activated for *this* race, not the whole season."""
        return [c.chip for c in self.chips if c.used and c.race_id == race_id]

    def chips_remaining(self) -> list[Chip]:
        used = {c.chip for c in self.chips if c.used}
        return [chip for chip in Chip if chip not in used]


class Player(Model):
    """A driver or constructor, priced and scored for one race."""

    player_id: str
    display_name: str
    full_name: str = ""
    tla: str = ""
    constructor: str = ""
    is_constructor: bool = False
    price: float = 0.0
    race_points: float = 0.0
    season_points: float = 0.0
    selected_pct: float = 0.0
    captain_pct: float = 0.0

    @property
    def short_name(self) -> str:
        return self.tla or self.display_name


class Member(Model):
    """A league member's standing, from the leaderboard endpoint."""

    guid: str
    user_name: str
    team_id: int = 0
    team_no: int = 1
    team_name: str = ""
    rank: int = 0
    points: float = 0.0
    trend: int = 0
    is_admin: bool = False


class LeagueRef(Model):
    league_id: int
    league_name: str = ""
    league_code: str = ""
    member_count: int = 0


class LeagueSnapshot(Model):
    """Everything captured about one league at one moment.

    This is the unit written to ``snapshots/`` and the sole input to the diff
    engine -- so it must be self-contained, including the player catalogue, since
    prices and points at capture time cannot be recovered later.
    """

    league_id: int
    league_name: str
    season: int
    race_id: int
    phase: Phase
    captured_at: datetime
    members: list[Member] = []
    teams: dict[str, Team] = {}
    players: dict[str, Player] = {}

    def member(self, guid: str) -> Member | None:
        return next((m for m in self.members if m.guid == guid), None)

    def player_name(self, player_id: str) -> str:
        player = self.players.get(player_id)
        return player.display_name if player else player_id


# --------------------------------------------------------------------------
# Parsers: raw payload -> domain model
# --------------------------------------------------------------------------


def parse_chips(raw: Mapping[str, Any]) -> list[ChipUsage]:
    """Read all six chip flags off a team or game-days payload."""
    chips: list[ChipUsage] = []
    for chip, (flag_keys, race_keys) in CHIP_KEYS.items():
        used = as_bool(first(raw, *flag_keys))
        race_id = positive_int_or_none(first(raw, *race_keys))
        # A chip with a race id but no flag is still a used chip; the flags are
        # occasionally absent on historical payloads.
        chips.append(ChipUsage(chip=chip, used=used or race_id is not None, race_id=race_id))
    return chips


def parse_picks(raw: Mapping[str, Any]) -> list[Pick]:
    entries = raw.get("playerid") or []
    picks: list[Pick] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        picks.append(
            Pick(
                player_id=str(first(entry, "id", "playerid", default="")).strip(),
                is_captain=as_bool(entry.get("iscaptain")),
                is_mega_captain=as_bool(entry.get("ismgcaptain")),
                slot=as_int(first(entry, "playerpostion", "playerposition")),
            )
        )
    return [p for p in picks if p.player_id]


def parse_team(raw: Mapping[str, Any], *, guid: str, race_id: int) -> Team:
    """Parse one entry of ``getteam``'s ``userTeam`` list."""
    info = raw.get("team_info") or {}
    picks = parse_picks(raw)

    # Captaincy is duplicated: per-pick flags and top-level ids. Trust the flags,
    # fall back to the ids when the flags are missing.
    if not any(p.is_captain for p in picks):
        captain_id = str(first(raw, "capplayerid", default="") or "").strip()
        for pick in picks:
            if pick.player_id == captain_id:
                pick.is_captain = True
    if not any(p.is_mega_captain for p in picks):
        mega_id = str(first(raw, "mgcapplayerid", default="") or "").strip()
        for pick in picks:
            if pick.player_id == mega_id:
                pick.is_mega_captain = True

    return Team(
        guid=guid,
        race_id=race_id,
        team_no=as_int(first(raw, "teamno"), default=1),
        team_name=decode(first(raw, "teamname")),
        picks=picks,
        value=as_float(first(raw, "teamval", default=info.get("teamVal")), default=0.0) or None,
        bank=as_float(first(raw, "teambal", default=info.get("teamBal")), default=0.0) or None,
        transfers_made=as_int(first(raw, "usersubs")),
        transfers_left=as_int(first(raw, "usersubsleft", default=info.get("userSubsleft"))),
        points=as_float(first(raw, "ovpoints")),
        chips=parse_chips(raw),
    )


def parse_teams(payload: Mapping[str, Any], *, guid: str, race_id: int | None = None) -> list[Team]:
    """Parse a full ``getteam`` response (a user may hold several teams)."""
    resolved_race = race_id if race_id is not None else as_int(payload.get("mdid"))
    entries = payload.get("userTeam") or []
    return [
        parse_team(entry, guid=guid, race_id=resolved_race)
        for entry in entries
        if isinstance(entry, Mapping)
    ]


def parse_member(raw: Mapping[str, Any]) -> Member:
    return Member(
        guid=str(first(raw, "guid", default="")).strip(),
        user_name=decode(first(raw, "userName", "username")),
        team_id=as_int(first(raw, "teamId", "teamid")),
        team_no=as_int(first(raw, "teamNo", "teamno"), default=1),
        team_name=decode(first(raw, "teamName", "teamname")),
        rank=as_int(first(raw, "rank")),
        points=as_float(first(raw, "ovPoints", "ovpoints")),
        trend=as_int(first(raw, "trend")),
        is_admin=as_bool(first(raw, "isAdmin", "isadmin")),
    )


def parse_leaderboard(payload: Mapping[str, Any]) -> tuple[LeagueRef, list[Member]]:
    """Parse ``pvtleagueuserrankget`` into a league ref plus its members."""
    info = payload.get("leagueInfo") or {}
    league = LeagueRef(
        league_id=as_int(first(info, "leagueid", "leagueId")),
        league_name=decode(first(info, "leagueName", "leaguename")),
        league_code=str(first(info, "leagueCode", default="") or ""),
        member_count=as_int(first(info, "memCount")),
    )
    rows = payload.get("memRank") or payload.get("userRank") or []
    members = [parse_member(row) for row in rows if isinstance(row, Mapping)]
    members = [m for m in members if m.guid]
    members.sort(key=lambda m: (m.rank or 10**6, -m.points))
    return league, members


def parse_league_list(payload: Any) -> list[LeagueRef]:
    """Parse any of the league-list endpoints, which vary in envelope shape."""
    rows: Sequence[Any]
    if isinstance(payload, Mapping):
        for key in ("leagues", "Leagues", "value", "Value"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                rows = candidate
                break
        else:
            rows = []
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []

    leagues: list[LeagueRef] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        league_id = as_int(first(row, "leagueid", "leagueId", "id"))
        if not league_id:
            continue
        leagues.append(
            LeagueRef(
                league_id=league_id,
                league_name=decode(first(row, "leagueName", "leaguename", "name")),
                league_code=str(first(row, "leagueCode", "leaguecode", default="") or ""),
                member_count=as_int(first(row, "memCount", "memcount", "membercount")),
            )
        )
    return leagues


def parse_player(raw: Mapping[str, Any]) -> Player:
    """Parse one entry of the public ``/feeds/drivers/{race}_en.json`` catalogue."""
    position_name = str(first(raw, "PositionName", default="") or "")
    is_constructor = position_name.strip().lower().startswith("constructor")

    return Player(
        player_id=str(first(raw, "PlayerId", "playerid", default="")).strip(),
        display_name=decode(first(raw, "DisplayName", "displayname")),
        # "FUllName" is the API's spelling, not a typo on our side.
        full_name=decode(first(raw, "FUllName", "FullName", "fullname")),
        tla=str(first(raw, "DriverTLA", default="") or "").strip(),
        constructor=decode(first(raw, "TeamName", "teamname")),
        is_constructor=is_constructor,
        price=as_float(first(raw, "Value", "value")),
        race_points=as_float(first(raw, "GamedayPoints", "gamedaypoints")),
        # "OverallPpints" is likewise the API's spelling.
        season_points=as_float(first(raw, "OverallPpints", "OverallPoints", "overallpoints")),
        selected_pct=as_float(first(raw, "SelectedPercentage")),
        captain_pct=as_float(first(raw, "CaptainSelectedPercentage")),
    )


def parse_players(payload: Any) -> dict[str, Player]:
    """Parse the driver/constructor feed into a ``player_id -> Player`` map."""
    rows: Sequence[Any]
    if isinstance(payload, Mapping):
        rows = payload.get("Value") or payload.get("value") or []
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []

    players: dict[str, Player] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        player = parse_player(row)
        if player.player_id:
            players[player.player_id] = player
    return players


def parse_game_days(payload: Mapping[str, Any]) -> tuple[dict[int, float], list[ChipUsage]]:
    """Parse ``getusergamedaysv1`` into per-race points and season chip state.

    This is the one endpoint that backfills a member's scoring history, since
    ``mddetails`` is keyed by race id for every race played so far.
    """
    entries = payload.get("data") or payload.get("Value") or []
    if isinstance(entries, Mapping):
        entries = [entries]
    if not entries:
        return {}, []

    head = entries[0] if isinstance(entries[0], Mapping) else {}
    details = head.get("mddetails") or {}

    points: dict[int, float] = {}
    for race_key, detail in details.items():
        race_id = as_int(race_key)
        if not race_id:
            continue
        if isinstance(detail, Mapping):
            value = first(detail, "pts", "points")
        else:
            value = detail
        if value is None:
            continue
        points[race_id] = as_float(value)

    return points, parse_chips(head)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
