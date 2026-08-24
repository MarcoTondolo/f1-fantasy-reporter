"""Diff engine behaviour, case by case.

Each test names a real situation from a race weekend rather than a code path.
"""

from __future__ import annotations

import pytest

from f1_fantasy.api.models import Chip, Phase
from f1_fantasy.store.diff import (
    chip_activations,
    chips_remaining,
    diff_standings,
    diff_teams,
    ownership,
    score_captains,
    score_changes,
)
from tests.conftest import make_snapshot, make_team

BASE = ["1", "2", "3", "5", "101", "103"]


def test_single_swap_is_reported_as_a_head_to_head():
    """One in, one out -- the case worth framing as a duel in the report."""
    previous = make_snapshot(10, {"a": make_team("a", 10, BASE, captain="1")})
    current = make_snapshot(
        11,
        {"a": make_team("a", 11, ["1", "2", "4", "5", "101", "103"], captain="1", transfers_made=1)},
    )

    (change,) = diff_teams(previous, current)

    assert [p.name for p in change.drivers_out] == ["Leclerc"]
    assert [p.name for p in change.drivers_in] == ["Hamilton"]
    assert not change.constructors_in and not change.constructors_out
    assert not change.unchanged
    assert not change.captain_changed


def test_swap_is_scored_from_post_race_points():
    """The delta must come from the scored snapshot, not the lockout one."""
    previous = make_snapshot(10, {"a": make_team("a", 10, BASE, captain="1")})
    current = make_snapshot(
        11,
        {"a": make_team("a", 11, ["1", "2", "4", "5", "101", "103"], captain="1")},
        phase=Phase.FINAL,
        points={"3": 6.0, "4": 20.0},
    )

    changes = diff_teams(previous, current)
    (score,) = score_changes(changes, current)

    assert score.is_clean_swap
    assert score.delta == pytest.approx(14.0)


def test_wildcard_week_scores_in_aggregate_not_as_pairs():
    """Five changes have no principled pairing, so the delta is a total."""
    previous = make_snapshot(10, {"a": make_team("a", 10, BASE, captain="1")})
    current = make_snapshot(
        11,
        {
            "a": make_team(
                "a",
                11,
                ["4", "6", "2", "5", "102", "103"],
                captain="2",
                chips={Chip.WILDCARD: 11},
            )
        },
        phase=Phase.FINAL,
        points={"1": 30.0, "3": 10.0, "101": 25.0, "4": 22.0, "6": 8.0, "102": 19.0},
    )

    (change,) = diff_teams(previous, current)
    (score,) = score_changes(change and [change], current)

    assert Chip.WILDCARD in change.chips_activated
    assert not score.is_clean_swap
    # in: Hamilton 22 + Ocon 8 + Ferrari 19 = 49; out: Verstappen 30 + Leclerc 10 + McLaren 25 = 65
    assert score.delta == pytest.approx(-16.0)


def test_captain_change_is_detected_and_valued():
    previous = make_snapshot(10, {"a": make_team("a", 10, BASE, captain="1")})
    current = make_snapshot(
        11,
        {"a": make_team("a", 11, BASE, captain="2")},
        phase=Phase.FINAL,
        points={"2": 25.0},
    )

    (change,) = diff_teams(previous, current)
    assert change.captain_changed
    assert change.captain_from.name == "Verstappen"
    assert change.captain_to.name == "Norris"

    (call,) = score_captains(current)
    assert call.multiplier == 2
    assert call.bonus == pytest.approx(25.0)


def test_mega_captain_triples_rather_than_doubles():
    current = make_snapshot(
        11,
        {"a": make_team("a", 11, BASE, mega_captain="2")},
        phase=Phase.FINAL,
        points={"2": 25.0},
    )

    (call,) = score_captains(current)

    assert call.multiplier == 3
    assert call.bonus == pytest.approx(50.0)


@pytest.mark.parametrize("chip", list(Chip))
def test_every_chip_is_detected_on_the_race_it_was_played(chip):
    current = make_snapshot(11, {"a": make_team("a", 11, BASE, chips={chip: 11})})

    (change,) = diff_teams(None, current)

    assert change.chips_activated == [chip]
    assert chip_activations(current)[chip] == ["member-a"]
    assert chip not in chips_remaining(current)["member-a"]


def test_chip_played_at_an_earlier_race_is_not_reported_as_new():
    """A chip burned three races ago must not resurface in this week's report."""
    current = make_snapshot(11, {"a": make_team("a", 11, BASE, chips={Chip.LIMITLESS: 8})})

    (change,) = diff_teams(None, current)

    assert change.chips_activated == []
    assert chip_activations(current) == {}
    # ...but it is still gone from their remaining chips.
    assert Chip.LIMITLESS not in chips_remaining(current)["member-a"]


def test_member_who_changed_nothing_is_flagged_unchanged():
    previous = make_snapshot(10, {"a": make_team("a", 10, BASE, captain="1")})
    current = make_snapshot(11, {"a": make_team("a", 11, BASE, captain="1")})

    (change,) = diff_teams(previous, current)

    assert change.unchanged
    assert change.value_change == pytest.approx(0.0)


def test_new_member_is_not_reported_as_transferring_in_a_whole_team():
    """Joining mid-season is not eleven transfers."""
    previous = make_snapshot(10, {})
    current = make_snapshot(11, {"a": make_team("a", 11, BASE, captain="1")})

    (change,) = diff_teams(previous, current)

    assert change.players_in == []
    assert change.players_out == []
    assert change.unchanged


def test_members_without_team_data_are_skipped_not_reported_as_static():
    """If a team could not be read, saying "no changes" would be a lie."""
    current = make_snapshot(
        11,
        {"a": make_team("a", 11, BASE)},
        standings={"a": (1, 100.0), "b": (2, 90.0)},
    )

    changes = diff_teams(None, current)

    assert [c.guid for c in changes] == ["a"]


def test_value_change_tracks_team_price_movement():
    previous = make_snapshot(10, {"a": make_team("a", 10, BASE, value=100.0)})
    current = make_snapshot(11, {"a": make_team("a", 11, BASE, value=101.4)})

    (change,) = diff_teams(previous, current)

    assert change.value_change == pytest.approx(1.4)


def test_standings_movement_uses_rank_and_points_gained():
    previous = make_snapshot(10, {}, standings={"a": (3, 200.0), "b": (1, 260.0)})
    current = make_snapshot(11, {}, standings={"a": (1, 265.0), "b": (2, 262.0)})

    moves = {m.guid: m for m in diff_standings(previous, current)}

    assert moves["a"].rank_change == 2  # 3rd -> 1st, positive means up
    assert moves["a"].points_gained == pytest.approx(65.0)
    assert moves["b"].rank_change == -1


def test_standings_movement_is_zero_for_a_first_ever_snapshot():
    current = make_snapshot(11, {}, standings={"a": (1, 265.0)})

    (move,) = diff_standings(None, current)

    assert move.previous_rank is None
    assert move.rank_change == 0
    assert move.points_gained == pytest.approx(0.0)


def test_ownership_ranks_common_picks_first_and_exposes_differentials():
    current = make_snapshot(
        11,
        {
            "a": make_team("a", 11, ["1", "2", "101"]),
            "b": make_team("b", 11, ["1", "3", "101"]),
            "c": make_team("c", 11, ["1", "6", "102"]),
        },
        standings={"a": (1, 10.0), "b": (2, 9.0), "c": (3, 8.0)},
    )

    rows = ownership(current)
    by_name = {player.name: owners for player, owners in rows}

    assert rows[0][0].name == "Verstappen"
    assert len(by_name["Verstappen"]) == 3
    assert by_name["Ocon"] == ["member-c"]  # a differential


def test_transfer_scores_are_ranked_best_first():
    previous = make_snapshot(
        10,
        {
            "a": make_team("a", 10, ["1", "101"]),
            "b": make_team("b", 10, ["1", "101"]),
        },
        standings={"a": (1, 10.0), "b": (2, 9.0)},
    )
    current = make_snapshot(
        11,
        {
            "a": make_team("a", 11, ["4", "101"]),  # Verstappen -> Hamilton
            "b": make_team("b", 11, ["6", "101"]),  # Verstappen -> Ocon
        },
        standings={"a": (1, 10.0), "b": (2, 9.0)},
        phase=Phase.FINAL,
        points={"1": 10.0, "4": 25.0, "6": 2.0},
    )

    scores = score_changes(diff_teams(previous, current), current)

    assert [s.guid for s in scores] == ["a", "b"]
    assert scores[0].delta == pytest.approx(15.0)
    assert scores[-1].delta == pytest.approx(-8.0)


def test_unknown_player_id_is_surfaced_rather_than_dropped():
    """A catalogue miss must be visible in the report, not silently swallowed."""
    previous = make_snapshot(10, {"a": make_team("a", 10, ["1", "101"])})
    current = make_snapshot(11, {"a": make_team("a", 11, ["999", "101"])})

    (change,) = diff_teams(previous, current)

    assert [p.name for p in change.players_in] == ["999"]
