"""A track's own geometry, derived from telemetry rather than assumed.

FastF1's ``get_circuit_info()`` gives each corner's apex distance along the
lap, but not its speed or direction -- its "Angle" field is a label-rotation
hint for plotting corner numbers, not corner severity, so it is deliberately
not used for classification here. Speed band and turn direction are instead
read off a reference lap's own telemetry: the minimum speed near each apex,
and the curvature of the car's own X/Y path through it.

Corner *zones* (not just apex points) come from Throttle: the corner starts
where the driver last lifted off full throttle before the apex and ends
where they're back to full throttle after it, found by searching within the
midpoint to each neighbouring corner so a chicane's back-to-back corners
don't bleed into each other's search window. Everything between one corner's
exit and the next corner's entry is a straight.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

#: Minimum apex speed (km/h) for each band. A corner's band is decided by
#: where its minimum speed falls, using thresholds inspired by common
#: broadcast low/medium/high-speed corner commentary -- not an official FIA
#: classification, which doesn't publicly exist in a standardised form.
SPEED_BAND_THRESHOLDS = (
    ("slow", 0, 145),
    ("medium", 145, 215),
    ("fast", 215, float("inf")),
)


def classify_speed_band(min_speed: float) -> str:
    for label, low, high in SPEED_BAND_THRESHOLDS:
        if low <= min_speed < high:
            return label
    return "fast"


@dataclass(frozen=True)
class Corner:
    number: int
    apex_distance: float
    min_speed: float
    speed_band: str
    direction: str  # "left" | "right"
    entry_distance: float
    exit_distance: float

    @property
    def length(self) -> float:
        return self.exit_distance - self.entry_distance


@dataclass(frozen=True)
class Straight:
    start_distance: float
    end_distance: float

    @property
    def length(self) -> float:
        return self.end_distance - self.start_distance


@dataclass
class TrackProfile:
    season: int
    round_number: int
    event_name: str
    lap_distance: float
    corners: list[Corner] = field(default_factory=list)
    straights: list[Straight] = field(default_factory=list)

    def corners_in_band(self, band: str) -> list[Corner]:
        return [c for c in self.corners if c.speed_band == band]

    def band_distance_pct(self, band: str) -> float:
        if band == "straight":
            total = sum(s.length for s in self.straights)
        else:
            total = sum(c.length for c in self.corners_in_band(band))
        return total / self.lap_distance * 100.0 if self.lap_distance else 0.0

    @property
    def corner_direction_balance(self) -> float:
        """(left corner distance - right corner distance) / total cornering
        distance, in [-1, 1]. Positive means more time turning left, which
        loads the right-side tyres more; negative loads the left side more.
        A directional proxy for asymmetric tyre load, not a literal per-wheel
        wear measurement -- FastF1 carries no tyre sensor data.
        """
        left = sum(c.length for c in self.corners if c.direction == "left")
        right = sum(c.length for c in self.corners if c.direction == "right")
        total = left + right
        return (left - right) / total if total else 0.0


def _find_apex_window(tel: pd.DataFrame, target_distance: float, half_width: float = 120.0) -> pd.DataFrame:
    return tel[(tel["Distance"] >= target_distance - half_width) & (tel["Distance"] <= target_distance + half_width)]


def _corner_direction(tel: pd.DataFrame, apex_distance: float, window: float = 60.0) -> str:
    """Sign of the path curvature through the apex: which way the car turns.

    Uses the car's own X/Y telemetry rather than the circuit info "Angle"
    field, whose sign convention is for label placement, not turn direction.
    """
    nearby = tel[(tel["Distance"] >= apex_distance - window) & (tel["Distance"] <= apex_distance + window)]
    nearby = nearby.dropna(subset=["X", "Y"])
    if len(nearby) < 5:
        return "right"
    x = nearby["X"].to_numpy(dtype=float)
    y = nearby["Y"].to_numpy(dtype=float)
    # Cross product of consecutive direction vectors, summed: positive means
    # the heading rotates counter-clockwise (a left-hand corner in a
    # standard-orientation track map) over the window.
    dx, dy = np.diff(x), np.diff(y)
    cross = dx[:-1] * dy[1:] - dy[:-1] * dx[1:]
    return "left" if np.sum(cross) > 0 else "right"


def _corner_zone(
    tel: pd.DataFrame, apex_distance: float, prev_bound: float, next_bound: float
) -> tuple[float, float]:
    """Entry (last full-throttle point before the apex) and exit (first
    full-throttle point after) within [prev_bound, next_bound]. Collapses to
    the midpoint boundary itself if the driver never reaches full throttle in
    that window -- back-to-back corners in a chicane merge into one zone,
    which matches how they're actually driven.
    """
    before = tel[(tel["Distance"] >= prev_bound) & (tel["Distance"] <= apex_distance)]
    full_before = before[before["Throttle"] >= 99]
    entry = float(full_before["Distance"].max()) if not full_before.empty else prev_bound

    after = tel[(tel["Distance"] >= apex_distance) & (tel["Distance"] <= next_bound)]
    full_after = after[after["Throttle"] >= 99]
    exit_ = float(full_after["Distance"].min()) if not full_after.empty else next_bound

    return entry, exit_


def build_track_profile(
    season: int, round_number: int, event_name: str, circuit_corners: pd.DataFrame, reference_telemetry: pd.DataFrame
) -> TrackProfile:
    """Build a track's corner/straight geometry from one reference lap.

    *circuit_corners* is ``session.get_circuit_info().corners`` (apex
    distances). *reference_telemetry* is a fast, clean lap's
    ``get_telemetry()`` output (needs Distance, Speed, Throttle, X, Y) --
    typically the session's fastest lap, since a representative geometry
    needs a lap that actually explores full speed and full throttle, not one
    compromised by traffic.
    """
    tel = reference_telemetry.dropna(subset=["Distance", "Speed"]).sort_values("Distance").reset_index(drop=True)
    lap_distance = float(tel["Distance"].max())

    apex_rows = circuit_corners.sort_values("Distance").reset_index(drop=True)
    apex_distances = apex_rows["Distance"].to_numpy(dtype=float)

    corners: list[Corner] = []
    for i, row in apex_rows.iterrows():
        apex_distance = float(row["Distance"])
        window = _find_apex_window(tel, apex_distance)
        if window.empty:
            continue
        min_speed = float(window["Speed"].min())

        prev_bound = (apex_distances[i - 1] + apex_distance) / 2 if i > 0 else 0.0
        next_bound = (
            (apex_distance + apex_distances[i + 1]) / 2 if i < len(apex_distances) - 1 else lap_distance
        )
        entry, exit_ = _corner_zone(tel, apex_distance, prev_bound, next_bound)

        corners.append(
            Corner(
                number=int(row["Number"]),
                apex_distance=apex_distance,
                min_speed=min_speed,
                speed_band=classify_speed_band(min_speed),
                direction=_corner_direction(tel, apex_distance),
                entry_distance=entry,
                exit_distance=exit_,
            )
        )

    corners.sort(key=lambda c: c.apex_distance)
    straights: list[Straight] = []
    for a, b in zip(corners, corners[1:]):
        if b.entry_distance > a.exit_distance:
            straights.append(Straight(a.exit_distance, b.entry_distance))
    # Wrap-around straight from the last corner's exit to the first corner's
    # entry (through the start/finish line), when there's room for one.
    if corners:
        last, first = corners[-1], corners[0]
        if last.exit_distance < lap_distance or first.entry_distance > 0:
            straights.append(Straight(last.exit_distance, lap_distance + first.entry_distance))

    return TrackProfile(
        season=season,
        round_number=round_number,
        event_name=event_name,
        lap_distance=lap_distance,
        corners=corners,
        straights=straights,
    )
