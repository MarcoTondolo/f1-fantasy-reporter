"""Parser tests against the API's real payload shapes.

The fixtures below reproduce the field names, spellings and types observed on
the live service -- including its misspellings (``FUllName``, ``OverallPpints``,
``playerpostion``, ``isnonigativetaken``) and its habit of sending numbers as
strings. If the API changes, these are the tests that should break first.
"""

from __future__ import annotations

import pytest

from f1_fantasy.api.client import AuthExpired, FantasyError, NotShared, unwrap
from f1_fantasy.api.models import (
    Chip,
    parse_game_days,
    parse_leaderboard,
    parse_league_list,
    parse_players,
    parse_teams,
)

RAW_TEAM = {
    "mdid": 15,
    "retval": 1,
    "userTeam": [
        {
            "teamno": 1,
            "teamname": "Box+Box+Baby",
            "teamval": 102.4,
            "teambal": 1.6,
            "ovpoints": 1284.0,
            "usersubs": 2,
            "usersubsleft": 0,
            "capplayerid": "2",
            "mgcapplayerid": None,
            "playerid": [
                {"id": "1", "isfinal": 1, "iscaptain": 0, "ismgcaptain": 0, "playerpostion": 1},
                {"id": "2", "isfinal": 1, "iscaptain": 1, "ismgcaptain": 0, "playerpostion": 2},
                {"id": "101", "isfinal": 1, "iscaptain": 0, "ismgcaptain": 0, "playerpostion": 6},
            ],
            "team_info": {
                "teamBal": 1.6,
                "teamVal": 102.4,
                "maxTeambal": 100.0,
                "subsallowed": 2,
                "userSubsleft": 0,
            },
            "iswildcardtaken": 1,
            "wildcardtakengd": 15,
            "islimitlesstaken": 0,
            "limitlesstakengd": 0,
            "isautopilottaken": 1,
            "autopilottakengd": 9,
            "isextradrstaken": 0,
            "extradrstakengd": None,
            "isfinalfixtaken": 0,
            "finalfixtakengd": None,
            "isnonigativetaken": 0,
            "nonigativetakengd": None,
        }
    ],
}

RAW_LEADERBOARD = {
    "leagueInfo": {
        "leagueid": 4321,
        "memCount": "8",
        "leagueCode": "ABC123",
        "leagueName": "Sunday+Drivers",
    },
    "memRank": [
        {
            "teamId": 11,
            "teamNo": 1,
            "teamName": "Box+Box+Baby",
            "userName": "chris",
            "guid": "guid-a",
            "isAdmin": 1,
            "rno": 1,
            "ovPoints": 1284,
            "rank": 2,
            "trend": 1,
            "lpgdid": 15,
        },
        {
            "teamId": 12,
            "teamNo": 1,
            "teamName": "Hammer+Time",
            "userName": "sam",
            "guid": "guid-b",
            "isAdmin": 0,
            "rno": 2,
            "ovPoints": 1301,
            "rank": 1,
            "trend": -1,
            "lpgdid": 15,
        },
    ],
    "userRank": [],
}

RAW_PLAYERS = {
    "Value": [
        {
            "PlayerId": "1",
            "Skill": 1,
            "PositionName": "Driver",
            "Value": 28.5,
            "TeamId": "1",
            "FUllName": "Max Verstappen",
            "DisplayName": "M+Verstappen",
            "TeamName": "Red+Bull+Racing",
            "DriverTLA": "VER",
            "OverallPpints": "312",
            "GamedayPoints": "28",
            "SelectedPercentage": "64.2",
            "CaptainSelectedPercentage": "31.5",
        },
        {
            "PlayerId": "101",
            "Skill": 2,
            "PositionName": "Constructor",
            "Value": 24.0,
            "TeamId": "2",
            "FUllName": "McLaren",
            "DisplayName": "McLaren",
            "TeamName": "McLaren",
            "DriverTLA": "",
            "OverallPpints": "402",
            "GamedayPoints": "41",
            "SelectedPercentage": "70.1",
            "CaptainSelectedPercentage": "0",
        },
    ]
}

RAW_GAME_DAYS = {
    "data": [
        {
            "teamno": 1,
            "teamname": "Box+Box+Baby",
            "iswildcardtaken": 1,
            "wildcardtakengd": 15,
            "islimitlesstaken": 0,
            "limitlesstakengd": 0,
            # Note the differing spelling from the team payload.
            "isautopilottaken": 1,
            "isautopilottakengd": 9,
            "isextradrstaken": 0,
            "extradrstakengd": 0,
            "isfinalfixtaken": 0,
            "finalfixtakengd": 0,
            "isnonigativetaken": 0,
            "nonigativetakengd": 0,
            "mddetails": {
                "13": {"mds": 13, "phId": 1, "pts": 88.0},
                "14": {"mds": 14, "phId": 1, "pts": 102.5},
                "15": {"mds": 15, "phId": 1, "pts": None},
            },
        }
    ]
}


# -- teams -----------------------------------------------------------------


def test_team_parses_lineup_captain_and_value():
    (team,) = parse_teams(RAW_TEAM, guid="guid-a")

    assert team.race_id == 15  # taken from mdid when not passed explicitly
    assert team.team_name == "Box Box Baby"  # percent/plus decoded
    assert team.player_ids == ["1", "2", "101"]
    assert team.captain_id == "2"
    assert team.mega_captain_id is None
    assert team.value == pytest.approx(102.4)
    assert team.transfers_made == 2


def test_team_chips_record_the_race_they_were_played_on():
    (team,) = parse_teams(RAW_TEAM, guid="guid-a")
    by_chip = {chip.chip: chip for chip in team.chips}

    assert by_chip[Chip.WILDCARD].used and by_chip[Chip.WILDCARD].race_id == 15
    assert by_chip[Chip.AUTOPILOT].used and by_chip[Chip.AUTOPILOT].race_id == 9
    assert not by_chip[Chip.LIMITLESS].used
    # Zero means "not used", and must not be read as race 0.
    assert by_chip[Chip.LIMITLESS].race_id is None
    assert team.chips_used_on(15) == [Chip.WILDCARD]


def test_autopilot_race_id_is_read_under_either_spelling():
    """The team and game-days endpoints disagree on this key."""
    _, chips = parse_game_days(RAW_GAME_DAYS)
    by_chip = {chip.chip: chip for chip in chips}

    assert by_chip[Chip.AUTOPILOT].race_id == 9


def test_captaincy_falls_back_to_top_level_id_when_flags_are_absent():
    raw = {
        "mdid": 15,
        "userTeam": [
            {
                "teamno": 1,
                "capplayerid": "3",
                "mgcapplayerid": "1",
                "playerid": [
                    {"id": "1", "playerpostion": 1},
                    {"id": "3", "playerpostion": 2},
                ],
            }
        ],
    }

    (team,) = parse_teams(raw, guid="guid-a")

    assert team.captain_id == "3"
    assert team.mega_captain_id == "1"


# -- leaderboard -----------------------------------------------------------


def test_leaderboard_decodes_names_and_sorts_by_rank():
    league, members = parse_leaderboard(RAW_LEADERBOARD)

    assert league.league_id == 4321
    assert league.league_name == "Sunday Drivers"
    assert league.member_count == 8  # arrived as a string
    assert [m.user_name for m in members] == ["sam", "chris"]  # rank 1 first
    assert members[1].is_admin is True
    assert members[0].points == pytest.approx(1301)


# -- players ---------------------------------------------------------------


def test_player_catalogue_handles_api_misspellings_and_string_numbers():
    players = parse_players(RAW_PLAYERS)

    verstappen = players["1"]
    assert verstappen.full_name == "Max Verstappen"  # from "FUllName"
    assert verstappen.display_name == "M Verstappen"
    assert verstappen.season_points == pytest.approx(312)  # from "OverallPpints"
    assert verstappen.race_points == pytest.approx(28)
    assert verstappen.selected_pct == pytest.approx(64.2)
    assert verstappen.is_constructor is False

    assert players["101"].is_constructor is True
    assert players["101"].constructor == "McLaren"


# -- game days -------------------------------------------------------------


def test_game_days_backfills_per_race_points_and_skips_unscored_races():
    points, _ = parse_game_days(RAW_GAME_DAYS)

    assert points == {13: pytest.approx(88.0), 14: pytest.approx(102.5)}
    # Race 15 has a null score -- not yet settled -- so it is absent rather than 0.
    assert 15 not in points


# -- envelope handling -----------------------------------------------------


def test_unwrap_returns_the_inner_value():
    assert unwrap({"Data": {"Value": [1, 2]}, "Meta": {}}) == [1, 2]


@pytest.mark.parametrize(
    "message, expected",
    [
        ("Your session has expired", AuthExpired),
        ("Invalid token", AuthExpired),
        ("You do not have permission to view this", NotShared),
        ("Something went wrong", FantasyError),
    ],
)
def test_unwrap_classifies_failure_messages(message, expected):
    """A 200 with Data=null is a failure, and the reason decides the exception."""
    with pytest.raises(expected):
        unwrap({"Data": None, "Meta": {"Message": message}}, path="/x")


def test_unwrap_passes_through_unenveloped_payloads():
    """Some feeds are returned bare."""
    assert unwrap([{"PlayerId": "1"}]) == [{"PlayerId": "1"}]


# -- league lists ----------------------------------------------------------


def test_league_list_tolerates_differing_envelope_shapes():
    keyed = {"leagues": [{"leagueid": 1, "leagueName": "A+League", "memCount": "4"}]}
    bare = [{"leagueId": 2, "leaguename": "B", "memcount": 6}]

    assert parse_league_list(keyed)[0].league_name == "A League"
    assert parse_league_list(bare)[0].league_id == 2
    assert parse_league_list({"unexpected": True}) == []
