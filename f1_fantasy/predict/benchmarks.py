"""External benchmark snapshots: comparison points for our own expected-points model.

The user asked to track f1fantasytools.com's driver-projection table, which
that site updates a few times through a race weekend as practice sessions
progress toward qualifying. Live investigation established that table
**cannot be reached programmatically**: a plain fetch returns only an empty
Next.js shell (the real table loads client-side post-hydration and is absent
from both the server-rendered HTML and its RSC streaming payload), six
guessed REST paths all 404 on a real custom 404 route, and headless-browser
automation is hard-blocked with a connection reset at the network level --
the same pattern already hit on ``fantasy.formula1.com``'s own JS app
elsewhere in this project. So f1fantasytools.com is tracked via **manual
capture** (``parse_manual_entries`` + the CLI's ``record-benchmark``
command): the user pastes numbers copied off the site a few times per
weekend, rather than this project scraping them.

Three further signals are automatable and free, and are tracked here
alongside the manual capture:

- ``official_projected_snapshot`` -- the public feed's own
  ``ProjectedGamedayPoints``, already proven useless *post-race*
  (``backtest_points.py`` documents it being silently overwritten to equal
  the real ``GamedayPoints``). Its status *pre-race* is an open question --
  checked live for the next unraced round and found flat at 0 for every
  driver, which is equally consistent with "no session data exists yet" as
  with "the field is broken pre-race too." This module's snapshot history,
  collected across a real race weekend, is what resolves that question --
  it makes no claim either way about whether the field is meaningful.
- ``crowd_consensus_snapshot`` -- the feed's ``SelectedPercentage``
  (ownership %). Deliberately *not* a points projection -- kept as a
  distinct, clearly-labelled signal ("what informed players are picking"),
  never blended into a projection it isn't.
- ``persistence_forecast_snapshot`` -- each driver's mean actual points over
  prior rounds, the trivial floor every other benchmark (including our own
  model) should be expected to clear.

All snapshots share one shape (``ExternalBenchmarkSnapshot``) and one
append-only store per season, so the comparison card can show every source
side by side without caring which were manual and which were automated.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.stats import spearmanr

from f1_fantasy.api.models import Model
from f1_fantasy.predict.reconcile import fetch_driver_feed

log = logging.getLogger(__name__)

#: Matches every other predict/ backtest artifact's committed-JSON home.
DEFAULT_STORE_DIR = Path("data/pace")


class ExternalBenchmarkSnapshot(Model):
    source: str  # "f1fantasytools" (manual) | "f1fantasytools_<page>" (auto -- see
    # f1fantasytools_capture.F1FT_PAGES) | "official_projected" | "crowd_consensus" | "persistence" | "ours"
    season: int
    round_number: int
    session_label: str = ""  # "FP1" | "FP2" | "FP3" | "pre_quali" | "" -- whatever the caller passes
    captured_at: datetime
    entries: dict[str, float]  # driver_code -> whatever value this source reports
    note: str = ""


def _store_path(season: int, *, store_dir: Path | str = DEFAULT_STORE_DIR) -> Path:
    return Path(store_dir) / f"external_benchmarks_{season}.json"


def load_snapshots(season: int, *, store_dir: Path | str = DEFAULT_STORE_DIR) -> list[ExternalBenchmarkSnapshot]:
    path = _store_path(season, store_dir=store_dir)
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [ExternalBenchmarkSnapshot.model_validate(item) for item in raw]


def append_snapshot(
    snapshot: ExternalBenchmarkSnapshot, *, store_dir: Path | str = DEFAULT_STORE_DIR
) -> Path:
    """Read-modify-write the season's JSON list -- mirrors ``store/snapshots.py``'s
    own append idiom. Re-run through the season as the weekend progresses,
    same convention as ``news/upgrades.py``'s store, never a frozen
    historical-range artifact like the backtest files."""
    path = _store_path(snapshot.season, store_dir=store_dir)
    existing = load_snapshots(snapshot.season, store_dir=store_dir)
    existing.append(snapshot)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [json.loads(s.model_dump_json()) for s in existing]
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    log.info("appended %s benchmark snapshot (%s) to %s", snapshot.source, snapshot.session_label, path)
    return path


def parse_manual_entries(raw: str, *, known_codes: frozenset[str] | None = None) -> dict[str, float]:
    """Parses ``"VER:185,NOR:172.5"``-style CLI input into
    ``{driver_code: value}``. A malformed chunk (no ``:``, an unparsable
    number) is logged and skipped -- never raises, since this is meant to
    be typed by hand mid-race-weekend. An unrecognised driver code (checked
    against ``known_codes`` when given) is kept but logged as a warning,
    not silently dropped -- a typo'd code is the most likely real failure
    mode here, and a visible-but-maybe-wrong entry is easier to catch than
    one quietly missing.
    """
    entries: dict[str, float] = {}
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            log.warning("ignoring malformed benchmark entry (no ':'): %r", chunk)
            continue
        code, _, value = chunk.partition(":")
        code = code.strip().upper()
        try:
            entries[code] = float(value.strip())
        except ValueError:
            log.warning("ignoring malformed benchmark entry (bad value): %r", chunk)
            continue
        if known_codes is not None and code not in known_codes:
            log.warning("benchmark entry uses an unrecognised driver code: %r", code)
    return entries


def crowd_consensus_snapshot(
    season: int, round_number: int, *, cache_dir: Path | str | None = None
) -> ExternalBenchmarkSnapshot:
    """Each driver's ``SelectedPercentage`` from the public feed -- an
    ownership rank, not a points projection. Documented explicitly as a
    *complementary* signal, never conflated with a projection."""
    feed = fetch_driver_feed(round_number, cache_dir=cache_dir)
    entries = {code: float(row.get("SelectedPercentage") or 0.0) for code, row in feed.items()}
    return ExternalBenchmarkSnapshot(
        source="crowd_consensus",
        season=season,
        round_number=round_number,
        session_label="ownership",
        captured_at=datetime.now(timezone.utc),
        entries=entries,
        note="SelectedPercentage (ownership %), not a points projection",
    )


def official_projected_snapshot(
    season: int, round_number: int, *, session_label: str = "", cache_dir: Path | str | None = None
) -> ExternalBenchmarkSnapshot:
    """``ProjectedGamedayPoints`` captured as-is at call time. Makes no
    claim about whether the field is meaningful pre-race -- that's exactly
    the open question this snapshot history exists to answer. Already
    known useless *post-race* (see ``backtest_points.py``'s module
    docstring); never call this after a round has been scored expecting a
    real number."""
    feed = fetch_driver_feed(round_number, cache_dir=cache_dir)
    entries = {code: float(row.get("ProjectedGamedayPoints") or 0.0) for code, row in feed.items()}
    return ExternalBenchmarkSnapshot(
        source="official_projected",
        season=season,
        round_number=round_number,
        session_label=session_label,
        captured_at=datetime.now(timezone.utc),
        entries=entries,
    )


def persistence_forecast_snapshot(
    season: int,
    round_number: int,
    available_rounds: list[int],
    *,
    cache_dir: Path | str | None = None,
) -> ExternalBenchmarkSnapshot:
    """Each driver's mean actual ``GamedayPoints`` over the rounds in
    ``available_rounds`` strictly before ``round_number`` -- the trivial
    floor baseline every other benchmark, including our own model, should
    be expected to clear."""
    prior_rounds = [r for r in available_rounds if r < round_number]
    totals: dict[str, list[float]] = {}
    for r in prior_rounds:
        try:
            feed = fetch_driver_feed(r, cache_dir=cache_dir)
        except Exception as exc:  # noqa: BLE001 -- one missing prior round shouldn't abort the whole baseline
            log.warning("persistence_forecast_snapshot: could not fetch round %s: %s", r, exc)
            continue
        for code, row in feed.items():
            totals.setdefault(code, []).append(float(row.get("GamedayPoints") or 0.0))
    entries = {code: sum(values) / len(values) for code, values in totals.items() if values}
    return ExternalBenchmarkSnapshot(
        source="persistence",
        season=season,
        round_number=round_number,
        session_label="",
        captured_at=datetime.now(timezone.utc),
        entries=entries,
        note=f"mean actual points over {len(prior_rounds)} prior round(s)",
    )


def compare_snapshots_to_outcome(
    snapshots: list[ExternalBenchmarkSnapshot], actual: dict[str, float]
) -> dict[str, dict]:
    """Spearman rank correlation of each snapshot's entries against actual
    points, keyed by ``"{source}:{session_label}"``. Only snapshots with at
    least 3 drivers overlapping ``actual`` are included -- gracefully
    returns just the sources/sessions with a real snapshot on file, never
    fabricates a missing comparison point."""
    out: dict[str, dict] = {}
    for snap in snapshots:
        drivers = [d for d in snap.entries if d in actual]
        if len(drivers) < 3:
            continue
        predicted_values = np.array([snap.entries[d] for d in drivers])
        actual_values = np.array([actual[d] for d in drivers])
        correlation, _ = spearmanr(predicted_values, actual_values)
        key = f"{snap.source}:{snap.session_label}" if snap.session_label else snap.source
        out[key] = {
            "source": snap.source,
            "session_label": snap.session_label,
            "captured_at": snap.captured_at.isoformat(),
            "n_drivers": len(drivers),
            "spearman": float(correlation) if not np.isnan(correlation) else None,
        }
    return out


def load_backtest_track_record(
    season: int, *, path: Path | str = "data/pace/points_backtest.json"
) -> dict | None:
    """The already-committed ``backtest_points.py`` artifact's summary for
    one season, if it exists -- never recomputed here, and simply omitted
    (not fabricated) when the file or the season's entry is absent."""
    file_path = Path(path)
    if not file_path.exists():
        return None
    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        log.warning("could not parse %s: %s", file_path, exc)
        return None
    by_season = raw.get(str(season))
    if by_season is None:
        return None
    return by_season.get("summary")
