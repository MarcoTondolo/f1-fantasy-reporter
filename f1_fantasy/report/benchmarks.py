"""The benchmark comparison card: our own expected-points model next to every
external/automated signal this project tracks, side by side.

No league dependency -- same class of card as ``picks.py``/``upgrades.py``.
Built from ``predict/benchmarks.py``'s snapshot store (manual
f1fantasytools.com captures, the official feed's own
``ProjectedGamedayPoints``, crowd-consensus ownership %, and our own current
estimate) and ``backtest_points.py``'s already-committed walk-forward
summary, rather than a league snapshot diff.
"""

from __future__ import annotations

from f1_fantasy.predict.benchmarks import ExternalBenchmarkSnapshot
from f1_fantasy.predict.simulate import SimulationSummary

#: How many entries to show per source/our-own-picks list.
TOP_N = 8

#: Shown on every card so none of these sources is mistaken for more than it
#: is -- this project's established transparency convention (see picks.py's
#: own MODEL_CAVEAT) extended to cover every benchmark source at once.
CAVEAT = (
    "f1fantasytools.com's projection table can't be fetched automatically "
    "(client-rendered, headless-browser blocked) -- its numbers here are "
    "manually pasted, not scraped. crowd_consensus is ownership %, not a "
    "points projection. official_projected is already proven stale "
    "post-race; its pre-race meaningfulness is still being verified live. "
    "persistence is a trivial floor, not a real forecast. Read every source "
    "next to its own captured_at and n_drivers, never as a bare number."
)


def _label(snapshot: ExternalBenchmarkSnapshot) -> str:
    return f"{snapshot.source} ({snapshot.session_label})" if snapshot.session_label else snapshot.source


def _our_top_picks(summaries: dict[str, SimulationSummary], *, top_n: int) -> list[dict]:
    ranked = sorted(summaries.values(), key=lambda s: -s.mean)[:top_n]
    return [{"driver": s.driver, "mean": round(s.mean, 1), "p10": round(s.p10, 1), "p90": round(s.p90, 1)} for s in ranked]


def _snapshot_top_entries(snapshot: ExternalBenchmarkSnapshot, *, top_n: int) -> list[dict]:
    ranked = sorted(snapshot.entries.items(), key=lambda kv: -kv[1])[:top_n]
    return [{"driver": driver, "value": round(value, 1)} for driver, value in ranked]


def build_benchmarks(
    snapshots: list[ExternalBenchmarkSnapshot],
    our_summaries: dict[str, SimulationSummary],
    backtest_summary: dict | None,
    *,
    season: int,
    round_number: int,
    league_name: str = "",
    top_n: int = TOP_N,
) -> dict:
    """``backtest_summary`` is ``backtest_points.py``'s already-computed
    per-season summary dict (``predict/benchmarks.load_backtest_track_record``'s
    return value) -- never recomputed here. ``None`` when no backtest
    artifact exists yet; the card simply omits that section rather than
    fabricating a number."""
    ordered = sorted(snapshots, key=lambda s: s.captured_at)

    # Oldest-first iteration means the last write for a given label is the
    # most recent snapshot -- exactly what "latest by source" should show.
    latest_by_label: dict[str, ExternalBenchmarkSnapshot] = {}
    for snap in ordered:
        latest_by_label[_label(snap)] = snap

    latest_by_source = [
        {
            "label": label,
            "source": snap.source,
            "captured_at": snap.captured_at.isoformat(),
            "n_drivers": len(snap.entries),
            "note": snap.note,
            "top_entries": _snapshot_top_entries(snap, top_n=top_n),
        }
        for label, snap in latest_by_label.items()
    ]

    snapshot_history = [
        {"label": _label(snap), "captured_at": snap.captured_at.isoformat(), "n_drivers": len(snap.entries)}
        for snap in ordered
    ]

    our_track_record = None
    if backtest_summary is not None:
        mae = backtest_summary.get("mean_mae")
        spearman = backtest_summary.get("mean_spearman")
        if mae is not None and spearman is not None:
            our_track_record = {"mean_mae": round(mae, 1), "mean_spearman": round(spearman, 3)}

    return {
        "eyebrow": "Benchmark tracker",
        "title": f"{season} round {round_number} benchmarks",
        "subtitle": f"{len(snapshots)} snapshot(s) captured" if snapshots else "No external snapshots captured yet",
        "league_name": league_name,
        "footer_note": "",
        "our_top_picks": _our_top_picks(our_summaries, top_n=top_n),
        "latest_by_source": latest_by_source,
        "snapshot_history": snapshot_history,
        "our_track_record": our_track_record,
        "caveat": CAVEAT,
    }


def caption(context: dict) -> str:
    lines = [f"\U0001f4ca *{context['title']}*", context["subtitle"], ""]

    if context["our_top_picks"]:
        lines.append("Our top picks:")
        for row in context["our_top_picks"][:5]:
            lines.append(f"  {row['driver']}: {row['mean']:.1f} (p10 {row['p10']:.1f} / p90 {row['p90']:.1f})")
        lines.append("")

    for source in context["latest_by_source"]:
        lines.append(f"{source['label']} (n={source['n_drivers']}):")
        for row in source["top_entries"][:5]:
            lines.append(f"  {row['driver']}: {row['value']}")
        lines.append("")

    if context["our_track_record"]:
        record = context["our_track_record"]
        lines.append(f"Our backtested track record: MAE {record['mean_mae']:.1f}, Spearman {record['mean_spearman']:.3f}")

    return "\n".join(lines).strip()
