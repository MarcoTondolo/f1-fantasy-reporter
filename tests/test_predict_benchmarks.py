"""External benchmark snapshot store and comparison signals, against synthetic feed rows.

Mirrors test_predict_upgrades.py's monkeypatching style and
test_news_upgrades.py's no-network discipline -- nothing here touches a
real feed or filesystem path outside a pytest tmp_path.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from f1_fantasy.predict import benchmarks as benchmarks_module
from f1_fantasy.predict.benchmarks import (
    ExternalBenchmarkSnapshot,
    append_snapshot,
    compare_snapshots_to_outcome,
    crowd_consensus_snapshot,
    load_backtest_track_record,
    load_snapshots,
    official_projected_snapshot,
    parse_manual_entries,
    persistence_forecast_snapshot,
)


def _snapshot(source="f1fantasytools", round_number=13, entries=None, session_label="FP1") -> ExternalBenchmarkSnapshot:
    return ExternalBenchmarkSnapshot(
        source=source,
        season=2026,
        round_number=round_number,
        session_label=session_label,
        captured_at=datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc),
        entries=entries or {"VER": 20.0, "NOR": 18.0, "PIA": 16.0},
    )


def test_parse_manual_entries_builds_a_dict_from_a_comma_separated_string():
    assert parse_manual_entries("VER:185,NOR:172.5") == {"VER": 185.0, "NOR": 172.5}


def test_parse_manual_entries_skips_a_chunk_with_no_colon():
    assert parse_manual_entries("VER:185,garbage,NOR:172") == {"VER": 185.0, "NOR": 172.0}


def test_parse_manual_entries_skips_a_chunk_with_an_unparsable_value():
    assert parse_manual_entries("VER:185,NOR:not-a-number") == {"VER": 185.0}


def test_parse_manual_entries_uppercases_driver_codes():
    assert parse_manual_entries("ver:185") == {"VER": 185.0}


def test_parse_manual_entries_keeps_an_unrecognised_code_rather_than_dropping_it():
    result = parse_manual_entries("XYZ:100", known_codes=frozenset({"VER", "NOR"}))

    assert result == {"XYZ": 100.0}


def test_append_and_load_snapshots_round_trip_through_a_temp_store(tmp_path):
    snap = _snapshot()

    append_snapshot(snap, store_dir=tmp_path)
    loaded = load_snapshots(2026, store_dir=tmp_path)

    assert len(loaded) == 1
    assert loaded[0].source == "f1fantasytools"
    assert loaded[0].entries == {"VER": 20.0, "NOR": 18.0, "PIA": 16.0}


def test_append_snapshot_accumulates_rather_than_overwriting(tmp_path):
    append_snapshot(_snapshot(session_label="FP1"), store_dir=tmp_path)
    append_snapshot(_snapshot(session_label="FP2"), store_dir=tmp_path)

    loaded = load_snapshots(2026, store_dir=tmp_path)

    assert [s.session_label for s in loaded] == ["FP1", "FP2"]


def test_load_snapshots_returns_empty_list_when_no_store_file_exists(tmp_path):
    assert load_snapshots(2026, store_dir=tmp_path) == []


def test_crowd_consensus_snapshot_reads_selected_percentage_not_points(monkeypatch):
    monkeypatch.setattr(
        benchmarks_module,
        "fetch_driver_feed",
        lambda round_number, cache_dir=None: {
            "VER": {"SelectedPercentage": 45.2, "GamedayPoints": 99},
            "NOR": {"SelectedPercentage": 60.1, "GamedayPoints": 10},
        },
    )

    snap = crowd_consensus_snapshot(2026, 13)

    assert snap.source == "crowd_consensus"
    assert snap.entries == {"VER": 45.2, "NOR": 60.1}
    assert "not a points projection" in snap.note


def test_official_projected_snapshot_reads_projected_gameday_points(monkeypatch):
    monkeypatch.setattr(
        benchmarks_module,
        "fetch_driver_feed",
        lambda round_number, cache_dir=None: {
            "VER": {"ProjectedGamedayPoints": 0},
            "NOR": {"ProjectedGamedayPoints": 0},
        },
    )

    snap = official_projected_snapshot(2026, 13, session_label="pre_quali")

    assert snap.source == "official_projected"
    assert snap.session_label == "pre_quali"
    assert snap.entries == {"VER": 0.0, "NOR": 0.0}


def test_persistence_forecast_snapshot_averages_prior_rounds_only(monkeypatch):
    feeds = {
        10: {"VER": {"GamedayPoints": 20}, "NOR": {"GamedayPoints": 10}},
        11: {"VER": {"GamedayPoints": 30}, "NOR": {"GamedayPoints": 20}},
        12: {"VER": {"GamedayPoints": 999}, "NOR": {"GamedayPoints": 999}},  # the target round itself -- must be excluded
    }
    monkeypatch.setattr(benchmarks_module, "fetch_driver_feed", lambda r, cache_dir=None: feeds[r])

    snap = persistence_forecast_snapshot(2026, round_number=12, available_rounds=[10, 11, 12])

    assert snap.entries == {"VER": 25.0, "NOR": 15.0}
    assert "2 prior round" in snap.note


def test_persistence_forecast_snapshot_skips_a_round_whose_fetch_fails(monkeypatch):
    def _fetch(r, cache_dir=None):
        if r == 10:
            raise RuntimeError("feed down")
        return {"VER": {"GamedayPoints": 30}}

    monkeypatch.setattr(benchmarks_module, "fetch_driver_feed", _fetch)

    snap = persistence_forecast_snapshot(2026, round_number=12, available_rounds=[10, 11])

    assert snap.entries == {"VER": 30.0}


def test_compare_snapshots_to_outcome_computes_spearman_per_snapshot():
    snapshots = [
        _snapshot(source="ours", entries={"VER": 30.0, "NOR": 20.0, "PIA": 10.0}, session_label="pre_quali"),
        _snapshot(source="crowd_consensus", entries={"VER": 10.0, "NOR": 20.0, "PIA": 30.0}, session_label="ownership"),
    ]
    actual = {"VER": 25.0, "NOR": 15.0, "PIA": 5.0}

    result = compare_snapshots_to_outcome(snapshots, actual)

    assert result["ours:pre_quali"]["spearman"] == pytest.approx(1.0)
    assert result["crowd_consensus:ownership"]["spearman"] == pytest.approx(-1.0)


def test_compare_snapshots_to_outcome_omits_a_snapshot_with_too_few_overlapping_drivers():
    snapshots = [_snapshot(entries={"VER": 20.0}, session_label="FP1")]

    result = compare_snapshots_to_outcome(snapshots, {"VER": 25.0, "NOR": 15.0, "PIA": 5.0})

    assert result == {}


def test_load_backtest_track_record_returns_none_when_file_is_absent(tmp_path):
    assert load_backtest_track_record(2026, path=tmp_path / "missing.json") is None


def test_load_backtest_track_record_returns_the_seasons_summary(tmp_path):
    import json

    path = tmp_path / "points_backtest.json"
    path.write_text(json.dumps({"2026": {"summary": {"mean_mae": 12.1, "mean_spearman": 0.45}}}))

    summary = load_backtest_track_record(2026, path=path)

    assert summary == {"mean_mae": 12.1, "mean_spearman": 0.45}


def test_load_backtest_track_record_returns_none_when_season_is_missing(tmp_path):
    import json

    path = tmp_path / "points_backtest.json"
    path.write_text(json.dumps({"2024": {"summary": {}}}))

    assert load_backtest_track_record(2026, path=path) is None
