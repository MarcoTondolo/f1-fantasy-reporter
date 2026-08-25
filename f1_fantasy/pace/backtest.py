"""Wiring the pace pipeline to real rounds: dataset assembly and the backfill run.

Ties pace/dataset.py (practice pace) to results.py (actual quali/race
positions) into one row-per-driver-per-round frame, then runs the
leave-one-round-out evaluation in model.py across a season's rounds.
"""

from __future__ import annotations

import logging

import pandas as pd

from f1_fantasy.calendar import RaceEvent, fetch_calendar
from f1_fantasy.pace.dataset import round_pace
from f1_fantasy.pace.model import RoundEvaluation, leave_one_round_out, summarize
from f1_fantasy.results import fetch_qualifying, fetch_race_results

log = logging.getLogger(__name__)


def build_round_frame(season: int, round_number: int, event: RaceEvent) -> pd.DataFrame | None:
    """One row per driver with pace features and actual quali/race positions.

    A driver is dropped from this round's frame if practice gave no
    representative long-run stint, or if quali/race results don't carry
    their code (a DNQ, or a code mismatch) -- silently imputing any of that
    would let the model score against numbers it invented, not numbers F1
    actually produced.
    """
    pace, sessions_used = round_pace(season, round_number, sprint_weekend=event.is_sprint_weekend)
    if not pace:
        return None

    quali_by_code = {q.driver_code: q.position for q in fetch_qualifying(season, round_number)}
    race_by_code = {r.driver_code: r.position for r in fetch_race_results(season, round_number)}

    rows = []
    for p in pace:
        if p.one_lap_gap_pct is None or p.long_run_gap_pct is None:
            continue
        quali_position = quali_by_code.get(p.driver)
        if quali_position is None:
            continue
        rows.append(
            {
                "driver": p.driver,
                "one_lap_gap_pct": p.one_lap_gap_pct,
                "long_run_gap_pct": p.long_run_gap_pct,
                "quali_position": quali_position,
                "race_position": race_by_code.get(p.driver),  # None for a DNF
            }
        )
    if not rows:
        return None
    frame = pd.DataFrame(rows)
    frame.attrs["sessions_used"] = sessions_used
    return frame


def run_backfill(season: int, rounds: list[int]) -> dict:
    events = {e.round: e for e in fetch_calendar(season)}

    frames: dict[int, pd.DataFrame] = {}
    event_names: dict[int, str] = {}
    sessions_by_round: dict[int, list[str]] = {}
    skipped: list[dict] = []

    for round_number in rounds:
        event = events.get(round_number)
        if event is None:
            skipped.append({"round": round_number, "reason": "not in calendar"})
            continue
        try:
            frame = build_round_frame(season, round_number, event)
        except Exception as exc:  # noqa: BLE001 -- one bad round shouldn't kill the backfill
            log.warning("round %d failed: %s", round_number, exc)
            skipped.append({"round": round_number, "reason": str(exc)})
            continue
        if frame is None or len(frame) < 3:
            skipped.append({"round": round_number, "reason": "no usable pace/results data"})
            continue
        frames[round_number] = frame
        event_names[round_number] = event.name
        sessions_by_round[round_number] = frame.attrs.get("sessions_used", [])
        log.info("round %d (%s): %d drivers", round_number, event.name, len(frame))

    quali_rounds = {r: f for r, f in frames.items()}
    race_rounds = {r: f.dropna(subset=["race_position"]) for r, f in frames.items()}
    race_rounds = {r: f for r, f in race_rounds.items() if len(f) >= 3}

    quali_eval = leave_one_round_out(quali_rounds, "quali_position", event_names)
    race_eval = leave_one_round_out(race_rounds, "race_position", event_names)

    return {
        "season": season,
        "rounds_requested": rounds,
        "rounds_used": sorted(frames),
        "skipped": skipped,
        "sessions_by_round": sessions_by_round,
        "quali": {
            "per_round": [vars(e) for e in quali_eval],
            "summary": summarize(quali_eval),
        },
        "race": {
            "per_round": [vars(e) for e in race_eval],
            "summary": summarize(race_eval),
        },
    }
