"""Loading and cleaning FastF1 practice laps.

Raw practice laps are mostly noise for a pace estimate: in/out laps, traffic,
red-flag and safety-car laps, and lap times FastF1 itself has flagged as
inaccurate (usually a missed timing loop, not a slow lap). This is the one
place that filtering happens, so every feature downstream sees the same
cleaned data regardless of which session it came from.
"""

from __future__ import annotations

import functools
import logging
from pathlib import Path

import fastf1
import pandas as pd

log = logging.getLogger(__name__)

#: FastF1's own on-disk cache of raw session data -- large (tens of MB per
#: session) and fully re-derivable from the timing API, so it is never
#: committed (see .gitignore); only the cleaned features that come out of it
#: are.
DEFAULT_CACHE_DIR = Path(".fastf1-cache")

_CACHE_ENABLED = False


def _ensure_cache(cache_dir: Path | str = DEFAULT_CACHE_DIR) -> None:
    global _CACHE_ENABLED
    if _CACHE_ENABLED:
        return
    path = Path(cache_dir)
    path.mkdir(parents=True, exist_ok=True)
    fastf1.Cache.enable_cache(str(path))
    _CACHE_ENABLED = True


class SessionUnavailable(Exception):
    """A session doesn't exist for this weekend (e.g. FP2/FP3 on a sprint
    weekend, which only runs FP1) or hasn't happened yet."""


@functools.lru_cache(maxsize=None)
def load_laps(
    season: int, round_number: int, session_name: str, *, cache_dir: Path | str = DEFAULT_CACHE_DIR
) -> pd.DataFrame:
    """Raw laps for one practice session. Cached in-process per (season, round, session)."""
    _ensure_cache(cache_dir)
    try:
        session = fastf1.get_session(season, round_number, session_name)
        session.load(laps=True, telemetry=False, weather=False, messages=False)
    except Exception as exc:  # FastF1 raises several distinct types for "no such session"
        raise SessionUnavailable(f"{session_name} R{round_number} {season}: {exc}") from exc
    laps = session.laps
    if laps is None or laps.empty:
        raise SessionUnavailable(f"{session_name} R{round_number} {season}: no lap data")
    return laps


def clean_laps(laps: pd.DataFrame) -> pd.DataFrame:
    """Green-flag, non-pit, accurate, non-deleted laps with a real lap time.

    Each filter removes a specific source of noise a raw pace comparison
    would otherwise be corrupted by:
    - pick_wo_box: drops in/out laps, which are slow for reasons unrelated to
      pace (pit lane speed limit, cold tyres).
    - pick_track_status('1'): drops laps run under yellow/safety
      car/red flag, where lap time reflects the flag, not the car.
    - pick_accurate: FastF1's own check that the lap's timing data is
      self-consistent (sector times sum correctly, etc).
    - pick_not_deleted: drops laps the stewards deleted (track limits), which
      are often the fastest lap on the sheet for the wrong reason.
    """
    clean = laps.pick_wo_box().pick_track_status("1").pick_accurate()
    # pick_not_deleted() assumes a proper bool column; practice sessions have
    # been observed serving "Deleted" as an all-None object column instead
    # (confirmed live, 2026 season) -- coerce rather than let it crash.
    deleted = clean["Deleted"].fillna(False).infer_objects(copy=False).astype(bool)
    clean = clean[~deleted]
    clean = clean[clean["LapTime"].notna()]
    return clean


def load_clean_laps(
    season: int, round_number: int, session_name: str, *, cache_dir: Path | str = DEFAULT_CACHE_DIR
) -> pd.DataFrame:
    return clean_laps(load_laps(season, round_number, session_name, cache_dir=cache_dir))
