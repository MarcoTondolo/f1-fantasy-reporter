"""Per-driver time in each track segment, and the strength profile it builds.

A TrackProfile fixes one set of distance boundaries (corner zones, by speed
band, plus straights) from a single reference lap. Every driver's own lap is
then measured against those *same* boundaries -- not each driver's own
braking points -- so "time in the slow corners" means the same patch of
tarmac for everyone, the way a broadcast minisector split does.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from f1_fantasy.pace.track_profile import TrackProfile

#: The four segment classes every lap gets split into.
SEGMENT_CLASSES = ("slow", "medium", "fast", "straight")


def _elapsed_time(tel: pd.DataFrame, start_distance: float, end_distance: float) -> float | None:
    """Seconds between two distances, by linearly interpolating the lap's
    own Time-vs-Distance trace. None if the lap doesn't cover that range
    (e.g. a lap that ended in the pits partway round)."""
    if end_distance <= start_distance:
        return None
    distance = tel["Distance"].to_numpy(dtype=float)
    if distance.min() > start_distance or distance.max() < end_distance:
        return None
    time_s = tel["Time"].dt.total_seconds().to_numpy(dtype=float)
    start_t = float(np.interp(start_distance, distance, time_s))
    end_t = float(np.interp(end_distance, distance, time_s))
    return end_t - start_t


def segment_times(telemetry: pd.DataFrame, profile: TrackProfile) -> dict[str, float]:
    """Total time (seconds) this lap spent in each segment class."""
    tel = telemetry.dropna(subset=["Distance", "Time"]).sort_values("Distance")
    totals = {cls: 0.0 for cls in SEGMENT_CLASSES}
    for corner in profile.corners:
        if corner.length <= 0:
            continue
        elapsed = _elapsed_time(tel, corner.entry_distance, corner.exit_distance)
        if elapsed is not None:
            totals[corner.speed_band] += elapsed
    for straight in profile.straights:
        end = min(straight.end_distance, profile.lap_distance)
        elapsed = _elapsed_time(tel, straight.start_distance, end)
        if elapsed is not None:
            totals["straight"] += elapsed
    return totals


@dataclass(frozen=True)
class DriverSegmentProfile:
    driver: str
    round_number: int
    times: dict[str, float]  # seconds per class, this driver's reference lap
    gap_pct: dict[str, float | None]  # % slower than the fastest driver in that class, this round
    gap_s: dict[str, float | None]  # seconds slower -- gap_pct on a short segment reads huge for a
    # modest absolute loss, so this is the number worth quoting alongside it.


def round_segment_profiles(
    profile: TrackProfile, driver_telemetry: dict[str, pd.DataFrame]
) -> list[DriverSegmentProfile]:
    """One profile per driver, each driver's *fastest clean lap*'s telemetry.

    Comparing everyone's single best lap (rather than averaging across laps)
    matches how one-lap pace is measured elsewhere in this dataset, and
    avoids mixing a driver's qualifying-sim lap with a scruffier long-run lap
    under the same "segment time" umbrella.
    """
    raw_times = {driver: segment_times(tel, profile) for driver, tel in driver_telemetry.items()}

    best = {}
    for cls in SEGMENT_CLASSES:
        values = [t[cls] for t in raw_times.values() if t.get(cls, 0.0) > 0]
        best[cls] = min(values) if values else None

    results = []
    for driver, times in raw_times.items():
        gaps_pct: dict[str, float | None] = {}
        gaps_s: dict[str, float | None] = {}
        for cls in SEGMENT_CLASSES:
            value, best_value = times.get(cls, 0.0), best[cls]
            if not value or not best_value:
                gaps_pct[cls] = None
                gaps_s[cls] = None
            else:
                gaps_pct[cls] = (value - best_value) / best_value * 100.0
                gaps_s[cls] = value - best_value
        results.append(
            DriverSegmentProfile(
                driver=driver,
                round_number=profile.round_number,
                times=times,
                gap_pct=gaps_pct,
                gap_s=gaps_s,
            )
        )
    return results
