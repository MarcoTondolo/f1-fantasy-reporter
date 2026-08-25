"""Wiring track_profile + segments to real sessions, and the cross-track model.

A driver's segment-class gaps from rounds they've already run become a
per-class "strength rating"; a target track's own segment composition (how
much of its lap-time is spent in slow/medium/fast corners vs straights)
turns those ratings into a predicted order for a track via a weighted sum --
the same leave-one-round-out discipline as the lap-level pace model, so a
round is only ever scored by ratings that never saw it.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from f1_fantasy.pace.segments import SEGMENT_CLASSES, DriverSegmentProfile, round_segment_profiles
from f1_fantasy.pace.sessions import SessionUnavailable, _ensure_cache
from f1_fantasy.pace.track_profile import TrackProfile, build_track_profile
from f1_fantasy.results import fetch_qualifying, fetch_race_results

log = logging.getLogger(__name__)

#: Reference session for corner geometry and driver comparison: qualifying is
#: everyone's single fastest, most representative lap of the whole track at
#: its limit -- practice laps are compromised by fuel loads and programs that
#: vary lap to lap in ways that would corrupt a "same patch of tarmac" split.
REFERENCE_SESSION = "Q"


def round_track_and_segments(season: int, round_number: int) -> tuple[TrackProfile, list[DriverSegmentProfile]] | None:
    """Build this round's track geometry and every driver's segment profile
    from qualifying telemetry. None if quali telemetry isn't available."""
    import fastf1

    _ensure_cache()
    try:
        session = fastf1.get_session(season, round_number, REFERENCE_SESSION)
        session.load(laps=True, telemetry=True, weather=False, messages=False)
    except Exception as exc:
        raise SessionUnavailable(f"{REFERENCE_SESSION} R{round_number} {season}: {exc}") from exc

    laps = session.laps
    if laps is None or laps.empty:
        return None

    fastest = laps.pick_fastest()
    if fastest is None or (hasattr(fastest, "empty") and fastest.empty):
        return None
    circuit_corners = session.get_circuit_info().corners
    reference_telemetry = fastest.get_telemetry()
    profile = build_track_profile(season, round_number, session.event["EventName"], circuit_corners, reference_telemetry)

    driver_telemetry = {}
    for driver in laps["Driver"].unique():
        driver_fastest = laps.pick_drivers(driver).pick_fastest()
        if driver_fastest is None or (hasattr(driver_fastest, "empty") and driver_fastest.empty):
            continue
        try:
            driver_telemetry[driver] = driver_fastest.get_telemetry()
        except Exception as exc:  # noqa: BLE001 -- one driver's bad telemetry shouldn't drop the round
            log.warning("R%d %s: no telemetry for %s: %s", round_number, season, driver, exc)

    segment_profiles = round_segment_profiles(profile, driver_telemetry)
    return profile, segment_profiles


def strength_ratings(profiles: list[DriverSegmentProfile]) -> dict[str, dict[str, float]]:
    """Mean gap % per class per driver, across every round in *profiles*.

    Lower is better (closer to that class's best on the day). A driver
    missing a class in a given round (no representative lap data) just
    doesn't contribute to that round's average for that class.
    """
    by_driver: dict[str, dict[str, list[float]]] = {}
    for profile in profiles:
        bucket = by_driver.setdefault(profile.driver, {cls: [] for cls in SEGMENT_CLASSES})
        for cls in SEGMENT_CLASSES:
            gap = profile.gap_pct.get(cls)
            if gap is not None:
                bucket[cls].append(gap)
    return {
        driver: {cls: float(np.mean(values)) if values else None for cls, values in classes.items()}
        for driver, classes in by_driver.items()
    }


def track_class_weights(profile: TrackProfile, reference_telemetry: pd.DataFrame) -> dict[str, float]:
    """Each class's share of the *reference lap's own time* -- corners take
    proportionally more time per metre than straights, so this is a fairer
    weight than plain track distance."""
    from f1_fantasy.pace.segments import segment_times

    times = segment_times(reference_telemetry, profile)
    total = sum(times.values())
    if not total:
        return {cls: 0.0 for cls in SEGMENT_CLASSES}
    return {cls: value / total for cls, value in times.items()}


def predict_ranking(ratings: dict[str, dict[str, float]], weights: dict[str, float]) -> dict[str, float]:
    """A predicted score per driver: the class-weighted sum of their average
    gaps. Lower predicted score = predicted faster. A driver missing every
    class (never raced in the training rounds) gets no score."""
    scores = {}
    for driver, classes in ratings.items():
        total_weight = sum(weights[cls] for cls in SEGMENT_CLASSES if classes.get(cls) is not None)
        if not total_weight:
            continue
        weighted = sum(
            classes[cls] * weights[cls] for cls in SEGMENT_CLASSES if classes.get(cls) is not None
        )
        scores[driver] = weighted / total_weight
    return scores


def backtest_track_model(season: int, rounds: list[int]) -> dict:
    """Leave-one-round-out: predict each round's order from every *other*
    round's segment-class ratings, weighted by the held-out round's own
    track composition, scored against real quali and race positions.
    """
    round_data: dict[int, tuple[TrackProfile, list[DriverSegmentProfile], pd.DataFrame]] = {}
    skipped = []
    for round_number in rounds:
        try:
            result = round_track_and_segments(season, round_number)
        except SessionUnavailable as exc:
            skipped.append({"round": round_number, "reason": str(exc)})
            continue
        if result is None:
            skipped.append({"round": round_number, "reason": "no usable session data"})
            continue
        profile, profiles = result
        if not profiles:
            skipped.append({"round": round_number, "reason": "no driver segment profiles"})
            continue
        import fastf1

        session = fastf1.get_session(season, round_number, REFERENCE_SESSION)
        session.load(laps=True, telemetry=True, weather=False, messages=False)
        reference_telemetry = session.laps.pick_fastest().get_telemetry()
        weights = track_class_weights(profile, reference_telemetry)
        round_data[round_number] = (profile, profiles, weights)
        log.info("round %d (%s): %d driver segment profiles", round_number, profile.event_name, len(profiles))

    per_round_eval = []
    for held_out, (profile, _profiles, weights) in round_data.items():
        train_profiles = [p for r, (_, profiles, _) in round_data.items() if r != held_out for p in profiles]
        if len(train_profiles) < 20:
            continue
        ratings = strength_ratings(train_profiles)
        predicted = predict_ranking(ratings, weights)
        if len(predicted) < 3:
            continue

        quali = {q.driver_code: q.position for q in fetch_qualifying(season, held_out)}
        race = {r.driver_code: r.position for r in fetch_race_results(season, held_out)}

        for label, actual_by_code in (("quali", quali), ("race", race)):
            drivers = [d for d in predicted if actual_by_code.get(d) is not None]
            if len(drivers) < 3:
                continue
            predicted_scores = [predicted[d] for d in drivers]
            actual_positions = [actual_by_code[d] for d in drivers]
            correlation, _ = spearmanr(predicted_scores, actual_positions)
            per_round_eval.append(
                {
                    "round": held_out,
                    "event_name": profile.event_name,
                    "target": label,
                    "n_drivers": len(drivers),
                    "spearman": float(correlation) if not np.isnan(correlation) else None,
                }
            )

    def _mean(label: str) -> float | None:
        values = [e["spearman"] for e in per_round_eval if e["target"] == label and e["spearman"] is not None]
        return float(np.mean(values)) if values else None

    return {
        "season": season,
        "rounds_requested": rounds,
        "rounds_used": sorted(round_data),
        "skipped": skipped,
        "per_round": per_round_eval,
        "summary": {"mean_quali_spearman": _mean("quali"), "mean_race_spearman": _mean("race")},
    }
