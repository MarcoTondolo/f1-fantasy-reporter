"""Snapshot store round-tripping and history lookup."""

from __future__ import annotations

from f1_fantasy.api.models import Chip, Phase
from f1_fantasy.store.snapshots import SnapshotStore
from tests.conftest import make_snapshot, make_team


def test_snapshot_survives_a_write_read_round_trip(tmp_path):
    """Everything the diff engine needs must survive serialisation."""
    store = SnapshotStore(tmp_path)
    original = make_snapshot(
        11,
        {"a": make_team("a", 11, ["1", "2", "101"], captain="2", chips={Chip.WILDCARD: 11})},
        points={"1": 25.0},
    )

    store.write(original)
    restored = store.read(2026, 555, 11, Phase.LOCKED)

    assert restored is not None
    team = restored.teams["a"]
    assert team.captain_id == "2"
    assert team.chips_used_on(11) == [Chip.WILDCARD]
    assert restored.players["1"].race_points == 25.0
    assert restored.captured_at == original.captured_at


def test_rewriting_a_phase_corrects_it_rather_than_duplicating(tmp_path):
    store = SnapshotStore(tmp_path)
    store.write(make_snapshot(11, {"a": make_team("a", 11, ["1"])}))
    store.write(make_snapshot(11, {"a": make_team("a", 11, ["1", "2"])}))

    restored = store.read(2026, 555, 11, Phase.LOCKED)

    assert restored.teams["a"].player_ids == ["1", "2"]
    assert store.race_ids(2026, 555) == [11]


def test_previous_race_skips_gaps_in_capture_history(tmp_path):
    """A missed weekend must not silently produce an empty diff."""
    store = SnapshotStore(tmp_path)
    for race_id in (9, 12):
        store.write(make_snapshot(race_id, {"a": make_team("a", race_id, ["1"])}))

    assert store.previous_race_id(2026, 555, 15) == 12
    assert store.previous_race_id(2026, 555, 12) == 9
    assert store.previous_race_id(2026, 555, 9) is None


def test_latest_prefers_the_most_settled_phase(tmp_path):
    store = SnapshotStore(tmp_path)
    store.write(make_snapshot(11, {"a": make_team("a", 11, ["1"])}, phase=Phase.PRE_LOCK))
    store.write(make_snapshot(11, {"a": make_team("a", 11, ["1", "2"])}, phase=Phase.LOCKED))

    assert store.latest(2026, 555, 11).phase is Phase.LOCKED

    store.write(make_snapshot(11, {"a": make_team("a", 11, ["1", "3"])}, phase=Phase.FINAL))
    assert store.latest(2026, 555, 11).phase is Phase.FINAL


def test_missing_snapshot_reads_as_none(tmp_path):
    store = SnapshotStore(tmp_path)

    assert store.read(2026, 555, 11, Phase.FINAL) is None
    assert store.race_ids(2026, 555) == []


def test_corrupt_snapshot_is_ignored_not_fatal(tmp_path):
    """One bad file must not take down a whole weekend's reporting."""
    store = SnapshotStore(tmp_path)
    path = store.path_for(2026, 555, 11, Phase.FINAL)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    assert store.read(2026, 555, 11, Phase.FINAL) is None
