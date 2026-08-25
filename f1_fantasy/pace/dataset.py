"""Per-round pace dataset: every practice session for a weekend, joined.

A single practice session under-samples a driver's pace -- a short run, a
traffic-affected lap, a program built around setup items rather than pace.
Combining every session run before lockout (FP1-3, or just FP1 on a sprint
weekend) gives each driver's best lap and best stint the widest evidence base
available before that race locks.
"""

from __future__ import annotations

import logging

import pandas as pd

from f1_fantasy.pace.features import DriverPace, session_pace
from f1_fantasy.pace.sessions import SessionUnavailable, load_clean_laps

log = logging.getLogger(__name__)

#: Session names in running order. A sprint weekend only runs FP1 before
#: qualifying (FP2/FP3 don't exist on the calendar at all -- see
#: RaceEvent.is_sprint_weekend), so those legitimately come back unavailable
#: rather than being an error worth surfacing.
PRACTICE_SESSIONS = ("FP1", "FP2", "FP3")


def round_pace(
    season: int, round_number: int, *, sprint_weekend: bool = False
) -> tuple[list[DriverPace], list[str]]:
    """Combined pace features for a round, and which sessions fed them.

    Stint numbers reset to 1 at the start of every session, so a naive
    concat would conflate FP1's stint 1 with FP2's stint 1 under the same
    groupby key; each session's Stint column is namespaced before combining
    so ``features.session_pace`` never merges laps across sessions.
    """
    session_names = ["FP1"] if sprint_weekend else list(PRACTICE_SESSIONS)
    frames = []
    used = []
    for name in session_names:
        try:
            laps = load_clean_laps(season, round_number, name)
        except SessionUnavailable as exc:
            log.info("skipping %s R%d %d: %s", name, round_number, season, exc)
            continue
        if laps.empty:
            continue
        laps = laps.copy()
        laps["Stint"] = name + "-" + laps["Stint"].astype(str)
        frames.append(laps)
        used.append(name)

    if not frames:
        return [], []
    combined = pd.concat(frames, ignore_index=True)
    return session_pace(combined), used
