"""The benchmark tracker card's context assembly, against synthetic snapshots/summaries."""

from __future__ import annotations

from datetime import datetime, timezone

from f1_fantasy.predict.benchmarks import ExternalBenchmarkSnapshot
from f1_fantasy.predict.simulate import SimulationSummary
from f1_fantasy.render.shot import render_html
from f1_fantasy.report.benchmarks import build_benchmarks, caption


def _snapshot(source, entries, session_label="", captured_at=None) -> ExternalBenchmarkSnapshot:
    return ExternalBenchmarkSnapshot(
        source=source,
        season=2026,
        round_number=13,
        session_label=session_label,
        captured_at=captured_at or datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc),
        entries=entries,
    )


def _summary(driver, mean, p10=0.0, p90=0.0) -> SimulationSummary:
    return SimulationSummary(driver, 10.0, mean, p10, p90, 0.0, 0.0, n_samples=100)


def test_build_benchmarks_ranks_our_top_picks_by_mean_descending():
    summaries = {"A": _summary("A", 20.0), "B": _summary("B", 10.0)}

    context = build_benchmarks([], summaries, None, season=2026, round_number=13)

    assert [row["driver"] for row in context["our_top_picks"]] == ["A", "B"]


def test_build_benchmarks_keeps_only_the_most_recent_snapshot_per_label():
    older = _snapshot("f1fantasytools", {"VER": 20.0}, session_label="FP1", captured_at=datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc))
    newer = _snapshot("f1fantasytools", {"VER": 25.0}, session_label="FP1", captured_at=datetime(2026, 9, 4, 15, 0, tzinfo=timezone.utc))

    context = build_benchmarks([older, newer], {}, None, season=2026, round_number=13)

    assert len(context["latest_by_source"]) == 1
    assert context["latest_by_source"][0]["top_entries"] == [{"driver": "VER", "value": 25.0}]


def test_build_benchmarks_keeps_distinct_sessions_of_the_same_source_separate():
    fp1 = _snapshot("f1fantasytools", {"VER": 20.0}, session_label="FP1")
    fp2 = _snapshot("f1fantasytools", {"VER": 25.0}, session_label="FP2")

    context = build_benchmarks([fp1, fp2], {}, None, season=2026, round_number=13)

    labels = {row["label"] for row in context["latest_by_source"]}
    assert labels == {"f1fantasytools (FP1)", "f1fantasytools (FP2)"}


def test_build_benchmarks_snapshot_history_is_chronological():
    first = _snapshot("official_projected", {"VER": 0.0}, captured_at=datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc))
    second = _snapshot("crowd_consensus", {"VER": 40.0}, captured_at=datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc))

    context = build_benchmarks([second, first], {}, None, season=2026, round_number=13)

    assert [row["label"] for row in context["snapshot_history"]] == ["official_projected", "crowd_consensus"]


def test_build_benchmarks_omits_track_record_when_no_backtest_summary_given():
    context = build_benchmarks([], {}, None, season=2026, round_number=13)

    assert context["our_track_record"] is None


def test_build_benchmarks_includes_track_record_when_given_a_backtest_summary():
    context = build_benchmarks(
        [], {}, {"mean_mae": 12.345, "mean_spearman": 0.456}, season=2026, round_number=13
    )

    assert context["our_track_record"] == {"mean_mae": 12.3, "mean_spearman": 0.456}


def test_build_benchmarks_subtitle_notes_when_no_snapshots_exist_yet():
    context = build_benchmarks([], {}, None, season=2026, round_number=13)

    assert "No external snapshots" in context["subtitle"]


def test_caption_includes_title_and_top_pick():
    summaries = {"A": _summary("A", 20.0)}

    context = build_benchmarks([], summaries, None, season=2026, round_number=13)
    text = caption(context)

    assert "2026 round 13 benchmarks" in text
    assert "A" in text


def test_benchmarks_template_renders_without_error():
    snapshots = [_snapshot("f1fantasytools", {"VER": 25.0, "NOR": 20.0}, session_label="FP2")]
    summaries = {"A": _summary("A", 20.0, 10.0, 30.0)}

    context = build_benchmarks(
        snapshots, summaries, {"mean_mae": 12.0, "mean_spearman": 0.45}, season=2026, round_number=13
    )
    html = render_html("benchmarks.html.j2", context)

    assert "f1fantasytools (FP2)" in html
    assert "2026 round 13 benchmarks" in html
