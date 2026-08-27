"""The odds collector: must never raise, regardless of what sources do."""

from __future__ import annotations

from datetime import datetime, timezone

from f1_fantasy.calendar import RaceEvent
from f1_fantasy.predict.odds import fetch_odds_snapshot

EVENT = RaceEvent(season=2026, round=1, name="Test Grand Prix", starts_at=datetime(2026, 1, 1, tzinfo=timezone.utc))


def test_fetch_odds_snapshot_with_no_sources_reports_failure_not_an_exception():
    snapshot = fetch_odds_snapshot(EVENT, sources=[])

    assert snapshot.fetch_succeeded is False
    assert snapshot.entries == {}
    assert snapshot.note


def test_fetch_odds_snapshot_uses_the_first_source_that_returns_entries():
    def fails():
        raise RuntimeError("blocked")

    def succeeds():
        return {"VER": 2.5, "NOR": 3.0}

    snapshot = fetch_odds_snapshot(EVENT, sources=[("bad", fails), ("good", succeeds)])

    assert snapshot.fetch_succeeded is True
    assert snapshot.source == "good"
    assert snapshot.entries == {"VER": 2.5, "NOR": 3.0}


def test_fetch_odds_snapshot_never_raises_even_if_every_source_fails():
    def fails():
        raise RuntimeError("blocked")

    snapshot = fetch_odds_snapshot(EVENT, sources=[("bad1", fails), ("bad2", fails)])

    assert snapshot.fetch_succeeded is False


def test_fetch_odds_snapshot_skips_a_source_that_returns_nothing_useful():
    def empty():
        return {}

    def succeeds():
        return {"VER": 2.5}

    snapshot = fetch_odds_snapshot(EVENT, sources=[("empty", empty), ("real", succeeds)])

    assert snapshot.source == "real"
