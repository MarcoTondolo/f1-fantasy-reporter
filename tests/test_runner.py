"""Which cards actually fire for a tick action, and whether toggles are honoured.

These render real PNGs via the same Playwright pipeline as `demo` -- slower than
the rest of the suite, but the thing worth proving is that COMPANION_BUILDERS
wiring and config.reports toggles actually reach the publisher, not just that
the report-building functions work in isolation (already covered elsewhere).
"""

from __future__ import annotations

from datetime import datetime, timezone

from f1_fantasy.calendar import RaceEvent
from f1_fantasy.config import Config
from f1_fantasy.publish.base import NullPublisher
from f1_fantasy.runner import COMPANION_BUILDERS, _render_and_publish
from f1_fantasy.schedule import Action
from f1_fantasy.store.snapshots import SnapshotStore
from tests.conftest import make_snapshot, make_team


def _event() -> RaceEvent:
    return RaceEvent(
        season=2026,
        round=15,
        name="Dutch Grand Prix",
        starts_at=datetime(2026, 8, 30, 13, 0, tzinfo=timezone.utc),
    )


def test_lockout_renders_its_companion_cards_when_enabled(tmp_path):
    store = SnapshotStore(tmp_path / "snapshots")
    config = Config(output_dir=tmp_path / "out")
    publisher = NullPublisher()
    snapshot = make_snapshot(11, {"a": make_team("a", 11, ["1", "2", "101"])})

    written = _render_and_publish(
        Action.LOCKOUT,
        snapshot=snapshot,
        store=store,
        config=config,
        event=_event(),
        publisher=publisher,
    )

    assert {report.kind for report in publisher.published} == {"lockout", "chips", "ownership", "budget"}
    assert written and all(path.exists() for path in written)


def test_companion_cards_are_skipped_when_toggled_off(tmp_path):
    store = SnapshotStore(tmp_path / "snapshots")
    config = Config(
        output_dir=tmp_path / "out",
        reports={"lockout": True, "chips": False, "ownership": False, "budget": False},
    )
    publisher = NullPublisher()
    snapshot = make_snapshot(11, {"a": make_team("a", 11, ["1", "2", "101"])})

    _render_and_publish(
        Action.LOCKOUT,
        snapshot=snapshot,
        store=store,
        config=config,
        event=_event(),
        publisher=publisher,
    )

    assert {report.kind for report in publisher.published} == {"lockout"}


def test_recap_renders_the_winners_losers_and_hindsight_companions(tmp_path):
    store = SnapshotStore(tmp_path / "snapshots")
    config = Config(output_dir=tmp_path / "out")
    publisher = NullPublisher()
    snapshot = make_snapshot(
        11, {"a": make_team("a", 11, ["1", "2", "101"])}, standings={"a": (1, 40.0)}
    )

    _render_and_publish(
        Action.RECAP,
        snapshot=snapshot,
        store=store,
        config=config,
        event=_event(),
        publisher=publisher,
    )

    assert {report.kind for report in publisher.published} == {"recap", "winners_losers", "hindsight"}


def test_lockout_and_recap_are_the_only_actions_with_companions():
    assert set(COMPANION_BUILDERS) == {Action.LOCKOUT, Action.RECAP}
