"""Energy-depletion detection ("clipping") under 2026's power-unit regs.

A clean internal-combustion car keeps accelerating whenever the driver is at
full throttle, until drag balances power at the circuit's natural top speed.
2026's smaller, more energy-limited hybrid power units can run *out* of
deployable electrical power mid-straight: the driver is still flat to the
floor, but the car decelerates anyway because the engine alone can't hold
that speed. Bartolozzi (Formula Data Analysis, see the Phase-7 research)
calls this "clipping" and reports it varying enormously by circuit -- long
full-throttle circuits like Spa force it on every team, Monaco's short
straights and constant harvesting opportunities produce none at all.

Detected directly from telemetry: full throttle (>=99%) together with a
negative speed derivative, sustained long enough to rule out sensor noise
riding what is really a constant-speed plateau.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

#: Throttle reading counted as "full" -- FastF1's channel occasionally reports
#: 99.x rather than a clean 100 for a genuinely flat-to-the-floor sample.
FULL_THROTTLE = 99.0

#: Deceleration (km/h per second) below which a full-throttle sample counts
#: as clipping rather than noise on a constant-speed plateau.
CLIPPING_DECEL_THRESHOLD = -0.5

#: Minimum sustained duration (seconds) for a clipping *event* -- a single
#: noisy sample is not "the engine ran out of energy".
MIN_CLIPPING_DURATION = 0.3


def _clean(telemetry: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    return telemetry.dropna(subset=columns).sort_values("Time")


def clipping_seconds(telemetry: pd.DataFrame) -> float:
    """Total time this lap spent decelerating while at full throttle."""
    tel = _clean(telemetry, ["Speed", "Throttle", "Time"])
    if len(tel) < 2:
        return 0.0
    time_s = tel["Time"].dt.total_seconds().to_numpy(dtype=float)
    speed = tel["Speed"].to_numpy(dtype=float)
    throttle = tel["Throttle"].to_numpy(dtype=float)

    dt = np.diff(time_s)
    with np.errstate(invalid="ignore", divide="ignore"):
        accel = np.diff(speed) / dt  # km/h per second; one shorter than the source arrays
    accel = np.where(dt > 0, accel, 0.0)

    clipping_sample = (throttle[:-1] >= FULL_THROTTLE) & (accel < CLIPPING_DECEL_THRESHOLD)

    total = 0.0
    run_start: int | None = None
    for i, is_clip in enumerate(clipping_sample):
        if is_clip and run_start is None:
            run_start = i
        elif not is_clip and run_start is not None:
            duration = time_s[i] - time_s[run_start]
            if duration >= MIN_CLIPPING_DURATION:
                total += duration
            run_start = None
    if run_start is not None:
        duration = time_s[len(clipping_sample)] - time_s[run_start]
        if duration >= MIN_CLIPPING_DURATION:
            total += duration
    return float(total)


def time_at_max_throttle(telemetry: pd.DataFrame) -> float:
    """Total time this lap spent at (near-)full throttle, clipping or not."""
    tel = _clean(telemetry, ["Throttle", "Time"])
    if len(tel) < 2:
        return 0.0
    time_s = tel["Time"].dt.total_seconds().to_numpy(dtype=float)
    throttle = tel["Throttle"].to_numpy(dtype=float)
    dt = np.diff(time_s)
    full = throttle[:-1] >= FULL_THROTTLE
    return float(np.sum(dt[full]))


def lap_duration(telemetry: pd.DataFrame) -> float:
    tel = telemetry.dropna(subset=["Time"])
    if tel.empty:
        return 0.0
    time_s = tel["Time"].dt.total_seconds()
    return float(time_s.max() - time_s.min())


def clipping_fraction(telemetry: pd.DataFrame) -> float:
    """Clipping time as a fraction of the lap -- comparable across circuits
    of different length and lap time, unlike the raw-seconds figure."""
    duration = lap_duration(telemetry)
    return clipping_seconds(telemetry) / duration if duration else 0.0
