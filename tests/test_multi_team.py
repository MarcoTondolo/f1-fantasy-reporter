"""Regression coverage: an account running more than one team in one league.

Found on this tool's first live run, against its own test account: two of the
three real leagues have members (including the account's own guid) holding a
second team (team_no 2, 3, ...) as a separate leaderboard entry with its own
rank. Before team_key existed, snapshot.teams was keyed by guid alone, so the
second team silently overwrote the first -- no error, no warning, just
quietly wrong data for exactly the account whose reports this tool exists to
produce.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from f1_fantasy.api.models import Chip, LeagueSnapshot, Member, Phase, team_key
from f1_fantasy.store.diff import chip_status, diff_standings, diff_teams, ownership, score_captains
from tests.conftest import make_players, make_team


def _snapshot(team1_kwargs: dict, team2_kwargs: dict) -> LeagueSnapshot:
    """Two teams under one guid, as two separate leaderboard rows -- the
    real shape confirmed live, which make_snapshot's plain-guid convenience
    can't represent (it needs two different ranks for the same guid).
    """
    guid = "shared-guid"
    team1 = make_team(guid, 12, team_no=1, **team1_kwargs)
    team2 = make_team(guid, 12, team_no=2, **team2_kwargs)

    members = [
        Member(guid=guid, user_name="Chris", team_no=1, team_name="Squad A", rank=1, points=100.0),
        Member(guid=guid, user_name="Chris", team_no=2, team_name="Squad B", rank=3, points=80.0),
    ]

    return LeagueSnapshot(
        league_id=1,
        league_name="Multi-team league",
        season=2026,
        race_id=12,
        phase=Phase.LOCKED,
        captured_at=datetime(2026, 8, 25, tzinfo=timezone.utc),
        members=members,
        teams={team_key(guid, 1): team1, team_key(guid, 2): team2},
        players=make_players(),
    )


def test_both_teams_for_one_guid_are_stored_without_collision():
    snapshot = _snapshot(
        {"player_ids": ["1", "101"], "team_name": "Squad A"},
        {"player_ids": ["2", "102"], "team_name": "Squad B"},
    )

    assert len(snapshot.teams) == 2


def test_team_for_returns_the_matching_team_per_member_row():
    snapshot = _snapshot(
        {"player_ids": ["1", "101"], "team_name": "Squad A", "captain": "1"},
        {"player_ids": ["2", "102"], "team_name": "Squad B", "captain": "2"},
    )
    member_1, member_2 = snapshot.members

    team_1 = snapshot.team_for(member_1)
    team_2 = snapshot.team_for(member_2)

    assert team_1.team_name == "Squad A"
    assert team_2.team_name == "Squad B"
    assert team_1.player_ids == ["1", "101"]
    assert team_2.player_ids == ["2", "102"]


def test_ownership_counts_both_of_the_same_persons_teams_separately():
    """The two teams are separate competitive entries -- both should count,
    including when they happen to pick the same driver.
    """
    snapshot = _snapshot(
        {"player_ids": ["1", "101"], "team_name": "Squad A"},
        {"player_ids": ["1", "102"], "team_name": "Squad B"},
    )

    rows = {player.player_id: owners for player, owners in ownership(snapshot)}

    assert rows["1"] == ["Chris", "Chris"]  # both of Chris's teams own driver 1
    assert rows["101"] == ["Chris"]
    assert rows["102"] == ["Chris"]


def test_captain_scoring_covers_both_teams_independently():
    snapshot = _snapshot(
        {"player_ids": ["1", "101"], "team_name": "Squad A", "captain": "1"},
        {"player_ids": ["2", "102"], "team_name": "Squad B", "captain": "2"},
    )

    calls = score_captains(snapshot)

    assert {c.captain.player_id for c in calls} == {"1", "2"}


def test_chip_status_does_not_conflate_the_two_teams_chip_usage():
    """Squad A plays Wildcard; Squad B doesn't -- both facts must survive."""
    snapshot = _snapshot(
        {"player_ids": ["1"], "team_name": "Squad A", "chips": {Chip.WILDCARD: 12}},
        {"player_ids": ["2"], "team_name": "Squad B"},
    )

    wildcard = next(row for row in chip_status(snapshot) if row.chip == Chip.WILDCARD)

    used_teams = {u.member_name for u in wildcard.used}
    assert "Chris" in used_teams
    # Squad B's row (same member_name "Chris") should still show as available
    # from its own team's perspective -- both entries are named "Chris" here,
    # so the real assertion is on count: one used, one available.
    assert len(wildcard.used) == 1
    assert len(wildcard.available) == 1


def test_diff_teams_produces_one_change_entry_per_team_not_per_guid():
    """Two teams for one guid must yield two TeamChange entries, not one
    (or one overwriting the other).
    """
    current = _snapshot(
        {"player_ids": ["1", "101"], "team_name": "Squad A"},
        {"player_ids": ["2", "102"], "team_name": "Squad B"},
    )

    changes = diff_teams(None, current)

    assert len(changes) == 2
    assert {c.team_name for c in changes} == {"Squad A", "Squad B"}


def test_standings_movement_matches_each_team_to_its_own_previous_points():
    """Confirmed live in Ciao Squadra 2026: one account's two teams
    ("sydkav1" team_no 1, "sydkav2" team_no 2) both keyed by the same guid.
    diff_standings used to build its previous-round lookup by bare guid,
    so team_no 2's current points got diffed against team_no 1's previous
    points -- 3010.0 - 1289.0 = a bogus 1721.0 "round winner", printed
    straight onto the recap/winners_losers/hindsight cards. The real
    per-team deltas are 3010.0 - 2491.0 = 519.0 and 1671.0 - 1289.0 = 382.0.
    """
    previous = _snapshot(
        {"player_ids": ["1"], "team_name": "sydkav1", "points": 1289.0},
        {"player_ids": ["2"], "team_name": "sydkav2", "points": 2491.0},
    )
    current = _snapshot(
        {"player_ids": ["1"], "team_name": "sydkav1", "points": 1671.0},
        {"player_ids": ["2"], "team_name": "sydkav2", "points": 3010.0},
    )
    # _snapshot's Member rows are hardcoded to "Squad A"/"Squad B" with fixed
    # points, ignoring team1_kwargs/team2_kwargs -- override both here to match
    # the real leaderboard shape (team_name + points) this bug came from.
    previous.members[0].team_name, previous.members[1].team_name = "sydkav1", "sydkav2"
    current.members[0].team_name, current.members[1].team_name = "sydkav1", "sydkav2"
    previous.members[0].points, previous.members[1].points = 1289.0, 2491.0
    current.members[0].points, current.members[1].points = 1671.0, 3010.0

    moves = {m.team_name: m for m in diff_standings(previous, current)}

    assert moves["sydkav1"].points_gained == pytest.approx(382.0)
    assert moves["sydkav2"].points_gained == pytest.approx(519.0)
