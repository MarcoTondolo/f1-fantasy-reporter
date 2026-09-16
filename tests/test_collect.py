"""collect_league's team-fetch cap.

No coverage existed for this module before the cap was added -- the gap that
let an uncapped loop reach a "10k+ member" league on the very first live run
and stall a CI job. These tests pin the cap's behaviour directly, rather than
trusting a manual re-run to prove it.
"""

from __future__ import annotations

from f1_fantasy.api.models import LeagueRef, Member, Phase, team_key
from f1_fantasy.collect import _pick_team, collect_league
from tests.conftest import make_players, make_team


class FakeApi:
    """Duck-typed stand-in for FantasyApi: only what collect_league calls.

    Tracks which of the two team-fetch paths each guid went through, since
    that routing -- own guid via try_teams, everyone else via
    try_opponent_teams -- is itself the fix for a real bug: getteam silently
    echoes the caller's own team for any other guid rather than erroring.
    """

    def __init__(
        self,
        members: list[Member],
        teams_by_guid: dict[str, list],
        *,
        guid: str = "self-guid",
        opponent_teams_by_guid: dict[str, list] | None = None,
    ) -> None:
        self._members = members
        self._teams_by_guid = teams_by_guid
        self._opponent_teams_by_guid = opponent_teams_by_guid or {}
        self.guid = guid
        self.fetched_guids: list[str] = []
        self.own_path_guids: list[str] = []
        self.opponent_path_guids: list[str] = []

    def leaderboard(self, league_id: int):
        return LeagueRef(league_id=league_id, league_name="Test League"), self._members

    def players(self, race_id: int):
        return make_players()

    def try_teams(self, race_id: int, *, guid: str):
        self.fetched_guids.append(guid)
        self.own_path_guids.append(guid)
        return self._teams_by_guid.get(guid)

    def try_opponent_teams(self, guid: str, race_id: int, team_no: int):
        self.fetched_guids.append(guid)
        self.opponent_path_guids.append(guid)
        return self._opponent_teams_by_guid.get(guid) or self._teams_by_guid.get(guid)


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
    assert set(snapshot.teams) == {team_key(f"g{i}", 1) for i in range(15)}
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


def test_own_team_uses_try_teams_but_other_members_use_try_opponent_teams():
    """getteam silently echoes the caller's own team for any other guid --
    confirmed live -- so only the authenticated account's own guid may ever
    go through try_teams; everyone else must route through the dedicated
    opponent endpoint.
    """
    members = [_member("self-guid", 1), _member("other-guid", 2)]
    api = FakeApi(
        members,
        teams_by_guid={"self-guid": [make_team("self-guid", 11, ["1"])]},
        guid="self-guid",
        opponent_teams_by_guid={"other-guid": [make_team("other-guid", 11, ["2"])]},
    )

    snapshot, access = collect_league(
        api, league_id=1, race_id=11, phase=Phase.LOCKED, season=2026, spacing=0
    )

    assert api.own_path_guids == ["self-guid"]
    assert api.opponent_path_guids == ["other-guid"]
    assert access.readable == 2
    assert team_key("self-guid", 1) in snapshot.teams
    assert team_key("other-guid", 1) in snapshot.teams


def test_own_accounts_second_team_comes_from_the_same_getteam_call():
    """Regression test for the round-13/14 bug: getteam returns this
    account's *entire* userTeam list in one call, discriminated by team_no --
    confirmed live 2026-09-16, where a two-team account's getteam response
    carried both team_no 1 and 2 with genuinely independent bank/value/picks.
    It authenticates by session token, not by the guid string in the URL, so
    it answers identically regardless of exactly which of this account's own
    guids is passed.

    An earlier version instead retried a team_no mismatch via the opponent
    endpoint, on the (then-untested) assumption that endpoint discriminates
    correctly by team_no for this account too. Also confirmed live the same
    day: it does not -- asked for team_no 2 it answers with team_no 1 again,
    which is exactly how "cadillac thrillz" shipped as a silent duplicate of
    "Chucky Layclercks" for two real rounds. _pick_team must get everything
    it needs from the one getteam call; the opponent endpoint must not be
    consulted for this account's own rows at all.
    """
    primary = _member("self-guid-0-999", 1)
    second = _member("self-guid-0-999", 2)
    second.team_no = 2
    primary_team = make_team("self-guid-0-999", 11, ["1"], team_no=1)
    real_second_team = make_team("self-guid-0-999", 11, ["2"], team_no=2)
    api = FakeApi(
        [primary, second],
        # One guid maps to the account's full team list, both entries.
        teams_by_guid={"self-guid-0-999": [primary_team, real_second_team]},
        # api.guid is the bare secret; leaderboard rows carry the compound
        # "<uuid>-0-<n>" form -- the two never match by exact equality.
        guid="self-guid",
        # If the opponent endpoint were ever consulted for this account's
        # own rows, it would answer with the known-wrong echo -- present
        # here so the test would fail loudly if that path were taken again.
        opponent_teams_by_guid={"self-guid-0-999": [primary_team]},
    )

    snapshot, access = collect_league(
        api, league_id=1, race_id=11, phase=Phase.LOCKED, season=2026, spacing=0
    )

    assert access.readable == 2
    assert snapshot.teams[team_key("self-guid-0-999", 1)] is primary_team
    assert snapshot.teams[team_key("self-guid-0-999", 2)] is real_second_team
    # Both rows go through try_teams; the opponent endpoint is never touched
    # for this account's own guid.
    assert api.own_path_guids == ["self-guid-0-999", "self-guid-0-999"]
    assert api.opponent_path_guids == []


def test_is_own_account_matches_by_prefix_not_exact_equality():
    """api.guid (the bare F1_USER_GUID secret) and a leaderboard row's guid
    (compound "<uuid>-0-<n>") are two different strings for the same
    account -- confirmed live 2026-09-16 -- so exact equality would reject
    every one of this account's own rows, including its primary team."""
    from f1_fantasy.collect import _is_own_account

    assert _is_own_account("46d0040c-141d-11f1-b1e2-2dda4e54308a-0-115473702", "46d0040c-141d-11f1-b1e2-2dda4e54308a")
    assert _is_own_account("same-guid", "same-guid")
    assert not _is_own_account("c9697cfc-1807-11f1-8f34-51ef40b99e66-0-198616229", "46d0040c-141d-11f1-b1e2-2dda4e54308a")
