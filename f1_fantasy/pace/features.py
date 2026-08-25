"""Per-driver pace features from one session's cleaned laps.

Two measures, because they answer different fantasy questions:
- One-lap pace: a single fast lap, the way qualifying is decided.
- Long-run pace: sustained pace over a stint, the way a race is run. A car
  that's quick for one lap on a low-fuel, soft-tyre qualifying simulation can
  still be a handful over a 20-lap stint -- conflating the two would treat a
  qualifying-sim lap as evidence of race pace, which it isn't.

Both are expressed as a percentage gap to the best in the session, so they
compare across circuits where absolute lap times mean nothing to each other.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

#: A stint shorter than this has too few laps for a representative pace
#: estimate -- a single push lap surrounded by traffic isn't a race stint.
MIN_STINT_LAPS = 5


@dataclass(frozen=True)
class DriverPace:
    driver: str
    one_lap_time: float | None  # seconds
    one_lap_gap_pct: float | None
    long_run_pace: float | None  # seconds, fuel/degradation-corrected
    long_run_gap_pct: float | None
    degradation: float | None  # seconds per lap of tyre life, within the best stint
    stint_laps: int


def _seconds(series: pd.Series) -> pd.Series:
    return series.dt.total_seconds()


def _one_lap_pace(driver_laps: pd.DataFrame) -> float | None:
    if driver_laps.empty:
        return None
    return float(_seconds(driver_laps["LapTime"]).min())


def _stint_pace(stint: pd.DataFrame) -> tuple[float, float] | None:
    """Representative pace and degradation slope for one stint.

    Lap time drifts within a stint from two confounded causes -- fuel burning
    off (makes it faster) and tyres degrading (makes it slower) -- and
    practice running gives no clean way to separate them without a fuel
    reading this data doesn't have. Rather than assume a fuel-effect number,
    this fits lap time against tyre life directly and reports the fitted
    pace at the stint's *median* tyre life: robust to a stint's first lap
    (still finding grip) or last lap (backing off) being noisy, without
    pretending to know the fuel/degradation split.
    """
    valid = stint["TyreLife"].notna() & stint["LapTime"].notna()
    stint = stint[valid]
    if len(stint) < MIN_STINT_LAPS:
        return None
    tyre_life = stint["TyreLife"].to_numpy(dtype=float)
    lap_time = _seconds(stint["LapTime"]).to_numpy(dtype=float)
    if not np.isfinite(tyre_life).all() or not np.isfinite(lap_time).all():
        return None
    if np.ptp(tyre_life) == 0:
        return float(np.median(lap_time)), 0.0
    slope, intercept = np.polyfit(tyre_life, lap_time, 1)
    representative = slope * float(np.median(tyre_life)) + intercept
    return float(representative), float(slope)


def _long_run_pace(driver_laps: pd.DataFrame) -> tuple[float | None, float | None, int]:
    """The fastest of a driver's sufficiently-long stints.

    A driver may run several stints in a session; the quickest one is taken
    as their race-pace potential, the same way a driver's best qualifying
    lap (not their average) is taken as their one-lap potential.
    """
    best_pace, best_slope, best_len = None, None, 0
    for _, stint in driver_laps.groupby("Stint"):
        result = _stint_pace(stint)
        if result is None:
            continue
        pace, slope = result
        if best_pace is None or pace < best_pace:
            best_pace, best_slope, best_len = pace, slope, len(stint)
    return best_pace, best_slope, best_len


def session_pace(laps: pd.DataFrame) -> list[DriverPace]:
    """Pace features for every driver with at least one clean lap in *laps*.

    *laps* is expected pre-cleaned (see ``sessions.clean_laps``) -- this
    function does feature derivation only, no filtering, so a caller who
    wants a different cleaning policy can swap it without touching this.
    """
    if laps.empty:
        return []

    one_lap_times = {}
    long_run_paces = {}
    degradations = {}
    stint_lens = {}
    for driver, driver_laps in laps.groupby("Driver"):
        one_lap_times[driver] = _one_lap_pace(driver_laps)
        pace, slope, stint_len = _long_run_pace(driver_laps)
        long_run_paces[driver] = pace
        degradations[driver] = slope
        stint_lens[driver] = stint_len

    best_one_lap = min((v for v in one_lap_times.values() if v is not None), default=None)
    best_long_run = min((v for v in long_run_paces.values() if v is not None), default=None)

    results = []
    for driver in one_lap_times:
        one_lap = one_lap_times[driver]
        long_run = long_run_paces[driver]
        results.append(
            DriverPace(
                driver=driver,
                one_lap_time=one_lap,
                one_lap_gap_pct=_gap_pct(one_lap, best_one_lap),
                long_run_pace=long_run,
                long_run_gap_pct=_gap_pct(long_run, best_long_run),
                degradation=degradations[driver],
                stint_laps=stint_lens[driver],
            )
        )
    return results


def _gap_pct(value: float | None, best: float | None) -> float | None:
    if value is None or best is None or best <= 0:
        return None
    return (value - best) / best * 100.0
