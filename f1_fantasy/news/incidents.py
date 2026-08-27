"""Structured incidents from FastF1's race control feed.

A separate loader local to this module, following ``pace/hers.py``'s own
precedent (``_load_quali_laps`` duplicates ``pace/sessions.py``'s
``load_laps`` pattern rather than modifying it, because it needs different
``session.load(...)`` flags and a different return shape): messages are
session-level (``session.race_control_messages``), not lap-level, and
``load_laps``'s cache key has no ``messages`` flag. ``pace/sessions.py``
stays untouched.

**One finding from the real feed that reshaped this module's design**: the
``RacingNumber`` column is populated for blue-flag messages but is almost
always blank for the penalty/investigation messages that actually matter
here -- every car involved is instead named inline in the message text,
e.g. ``"TURN 6 INCIDENT INVOLVING CARS 44 (HAM) AND 63 (RUS) NOTED -
CAUSING A COLLISION"``. So driver codes are extracted from the message
text itself (``\\(([A-Z]{3})\\)``), not from ``RacingNumber`` -- checked
live against 2026 rounds 1-12, where this pattern holds for every
penalty/investigation message found. There is also no explicit
"CAR X RETIRED" message type anywhere in the 2026 feed at all -- retirement
cause has to be inferred from whatever incident messages exist *near* a
driver's actual last lap (from ``RaceResult.laps``), which will often be
nothing, and "no matching message" is a legitimate, expected outcome, not
a bug.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass

import pandas as pd

from f1_fantasy.results import RaceResult

PENALTY_PATTERN = re.compile(r"(DRIVE[- ]THROUGH PENALTY|TIME PENALTY|GRID PENALTY|STOP[- ]AND[- ]GO|PENALTY SERVED)", re.I)
INVESTIGATION_PATTERN = re.compile(r"INVESTIGAT", re.I)
NO_FURTHER_ACTION_PATTERN = re.compile(r"NO FURTHER (ACTION|INVESTIGATION)", re.I)
DRIVER_CODE_PATTERN = re.compile(r"\(([A-Z]{3})\)")

MECHANICAL_KEYWORDS = ("ENGINE", "GEARBOX", "HYDRAULIC", "POWER UNIT", "BRAKE", "SUSPENSION", "ELECTRICAL", "MECHANICAL")
COLLISION_KEYWORDS = ("COLLISION", "CONTACT", "CRASH", "SPUN", "SPIN", "ACCIDENT")

#: How many laps either side of a driver's retirement lap to search for a
#: relevant message. Message timestamps lag the on-track moment slightly
#: (stewards note an incident, then investigate a lap or two later).
RETIREMENT_LAP_WINDOW = 3


def _load_race_control_messages(season: int, round_number: int) -> pd.DataFrame:
    import fastf1

    from f1_fantasy.pace.sessions import _ensure_cache

    _ensure_cache()
    session = fastf1.get_session(season, round_number, "R")
    session.load(laps=False, telemetry=False, weather=False, messages=True)
    return session.race_control_messages


@dataclass(frozen=True)
class IncidentMessage:
    round_number: int
    lap: int | None
    category: str
    driver_codes: tuple[str, ...]
    is_penalty: bool
    is_investigation: bool
    is_no_further_action: bool
    is_safety_car: bool
    is_red_flag: bool
    raw_message: str


def parse_messages(raw: pd.DataFrame, round_number: int) -> list[IncidentMessage]:
    messages = []
    for _, row in raw.iterrows():
        text = str(row.get("Message") or "")
        category = str(row.get("Category") or "")
        flag = str(row.get("Flag") or "")
        lap = row.get("Lap")
        messages.append(
            IncidentMessage(
                round_number=round_number,
                lap=int(lap) if pd.notna(lap) else None,
                category=category,
                driver_codes=tuple(DRIVER_CODE_PATTERN.findall(text)),
                is_penalty=bool(PENALTY_PATTERN.search(text)),
                is_investigation=bool(INVESTIGATION_PATTERN.search(text)),
                is_no_further_action=bool(NO_FURTHER_ACTION_PATTERN.search(text)),
                is_safety_car=(category == "SafetyCar"),
                is_red_flag=(flag.upper() == "RED"),
                raw_message=text,
            )
        )
    return messages


def _classify_retirement_cause(messages: list[IncidentMessage]) -> tuple[str | None, float]:
    """Vote mechanical vs. collision from nearby messages' raw text.

    No matching message at all -> (None, 0.0): there is nothing to classify,
    which the real 2026 feed shows is the common case (no retirement is ever
    announced explicitly). Messages found but genuinely mixed signals ->
    ("ambiguous", low confidence), rather than forcing a guess -- an explicit
    design decision (confidence-scored, not a hard boolean) so a downstream
    consumer like adjusted.py can choose its own threshold rather than
    inheriting a silent one.
    """
    if not messages:
        return None, 0.0

    mechanical_votes = sum(1 for m in messages if any(k in m.raw_message.upper() for k in MECHANICAL_KEYWORDS))
    collision_votes = sum(1 for m in messages if any(k in m.raw_message.upper() for k in COLLISION_KEYWORDS))

    if mechanical_votes and collision_votes:
        return "ambiguous", 0.3
    if collision_votes:
        return "collision", min(1.0, 0.5 + 0.25 * collision_votes)
    if mechanical_votes:
        return "mechanical", min(1.0, 0.5 + 0.25 * mechanical_votes)
    return "ambiguous", 0.1  # messages exist near the retirement but none carry a cause keyword


@dataclass(frozen=True)
class DriverIncidentSummary:
    driver_code: str
    round_number: int
    penalties: int
    investigations: int
    no_further_action: int
    safety_car_laps: int
    red_flag_laps: int
    retirement_cause: str | None  # "mechanical" | "collision" | "ambiguous" | None
    retirement_confidence: float


def summarise_driver_incidents(
    messages: list[IncidentMessage], race_results: list[RaceResult]
) -> dict[str, DriverIncidentSummary]:
    summaries = {}
    for result in race_results:
        code = result.driver_code
        driver_messages = [m for m in messages if code in m.driver_codes]

        penalties = sum(1 for m in driver_messages if m.is_penalty)
        investigations = sum(1 for m in driver_messages if m.is_investigation)
        no_further_action = sum(1 for m in driver_messages if m.is_no_further_action)

        # A driver's own exposure to SC/red-flag laps is capped by how long
        # they were actually still running -- a lap 30 safety car cannot have
        # affected a driver who retired on lap 5.
        last_lap = result.laps or 0
        safety_car_laps = len({m.lap for m in messages if m.is_safety_car and m.lap is not None and m.lap <= last_lap})
        red_flag_laps = len({m.lap for m in messages if m.is_red_flag and m.lap is not None and m.lap <= last_lap})

        retirement_cause, retirement_confidence = None, 0.0
        if not result.finished:
            nearby = [
                m
                for m in driver_messages
                if m.lap is not None and abs(m.lap - last_lap) <= RETIREMENT_LAP_WINDOW
            ]
            retirement_cause, retirement_confidence = _classify_retirement_cause(nearby)

        summaries[code] = DriverIncidentSummary(
            driver_code=code,
            round_number=messages[0].round_number if messages else 0,
            penalties=penalties,
            investigations=investigations,
            no_further_action=no_further_action,
            safety_car_laps=safety_car_laps,
            red_flag_laps=red_flag_laps,
            retirement_cause=retirement_cause,
            retirement_confidence=retirement_confidence,
        )
    return summaries


@functools.lru_cache(maxsize=None)
def round_incidents(season: int, round_number: int) -> dict[str, DriverIncidentSummary]:
    """Convenience entry point: load, parse and summarise one round's race
    control messages against its own race results.

    Cached: a scored round's messages never change, and adjusted.py's
    walk-forward backtest refetches the same early rounds on every later
    round's prediction (the same rationale results.py's fetchers are cached
    for).
    """
    from f1_fantasy.results import fetch_race_results

    raw = _load_race_control_messages(season, round_number)
    messages = parse_messages(raw, round_number)
    race_results = fetch_race_results(season, round_number)
    return summarise_driver_incidents(messages, race_results)
