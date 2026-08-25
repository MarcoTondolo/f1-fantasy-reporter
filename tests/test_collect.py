"""collect_league's team-fetch cap.

No coverage existed for this module before the cap was added -- the gap that
let an uncapped loop reach a "10k+ member" league on the very first live run
and stall a CI job. These tests pin the cap's behaviour directly, rather than
trusting a manual re-run to prove it.
"""

from __future__ import annotations

from f1_fantasy.api.models import LeagueRef, Member, Phase
from f1_fantasy.collect import _pick_team, collect_league
from tests.conftest import make_players, make_team


class FakeApi:
    """Duck-typed stand-in for FantasyApi: only what collect_league calls."""

    def __init__(self, members: list[Member], teams_by_guid: dict[str, list]) -> None:
        self._members = members
        self._teams_by_guid = teams_by_guid
        self.fetched_guids: list[str] = []

    def leaderboard(self, league_id: int):
        return LeagueRef(league_id=league_id, league_name="Test League"), self._members

    def players(self, race_id: int):
        return make_players()

    def try_teams(self, race_id: int, *, guid: str):
        self.fetched_guids.append(guid)
        return self._teams_by_guid.get(guid)


def _member(guid: str, rank: int) -> Member:
    return Member(guid=guid, user_name=f"member-{guid}", rank=rank, team_no=1, points=0.0)


def _api(count: int) -> FakeApi:
    members = [_member(f"g{i}", rank=i + 1) for i in range(count)]
    teams = {m.guid: [make_team(m.guid, 11, ["1", "101"])] for m in members}
    return FakeApi(members, teams)


def test_team_fetches_are_capped_to_the_top_n_by_rank():
    api = _api(count=50)

    snapshot, access = collect_league(
        api, league_id=1, race_id=11, phase=Phase.LOCKED, season=2026,
        spacing=0, max_team_fetches=15,
    )

    assert access.total == 50
    assert access.readable == 15
    assert api.fetched_guids == [f"g{i}" for i in range(15)]
    assert set(snapshot.teams) == {f"g{i}" for i in range(15)}
    # Every member still appears in standings -- only team detail is capped.
    assert len(snapshot.members) == 50


def test_no_cap_fetches_every_member_when_max_team_fetches_is_none():
    api = _api(count=30)

    snapshot, access = collect_league(
        api, league_id=1, race_id=11, phase=Phase.LOCKED, season=2026,
        spacing=0, max_team_fetches=None,
    )

    assert access.readable == 30
    assert len(snapshot.teams) == 30


def test_cap_larger_than_the_league_fetches_everyone():
    api = _api(count=5)

    snapshot, access = collect_league(
        api, league_id=1, race_id=11, phase=Phase.LOCKED, season=2026,
        spacing=0, max_team_fetches=15,
    )

    assert access.total == 5
    assert access.readable == 5


def test_a_member_with_no_readable_team_does_not_count_as_readable():
    members = [_member("a", 1), _member("b", 2)]
    api = FakeApi(members, teams_by_guid={"a": [make_team("a", 11, ["1"])]})  # "b" unreadable

    snapshot, access = collect_league(
        api, league_id=1, race_id=11, phase=Phase.LOCKED, season=2026, spacing=0
    )

    assert access.readable == 1
    assert access.unreadable == 1
    assert "b" not in snapshot.teams


def test_pick_team_matches_the_members_team_number():
    """A user can hold several teams; the wrong one would diff against the
    wrong lineup and every report would show phantom transfers."""
    member = _member("a", 1)
    member.team_no = 2
    teams = [make_team("a", 11, ["1"]), make_team("a", 11, ["2"])]
    teams[0].team_no, teams[1].team_no = 1, 2

    chosen = _pick_team(teams, member)

    assert chosen is teams[1]


def test_pick_team_falls_back_to_the_first_when_no_number_matches():
    member = _member("a", 1)
    member.team_no = 9
    teams = [make_team("a", 11, ["1"])]

    assert _pick_team(teams, member) is teams[0]


def test_pick_team_handles_no_teams_at_all():
    assert _pick_team([], _member("a", 1)) is None
