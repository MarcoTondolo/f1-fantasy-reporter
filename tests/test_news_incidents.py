"""Race-control message parsing, pinned against real and synthetic cases.

The regex test below is the one confirmed real string from the plan's own
research; the DataFrame fixtures are built directly (matching hers.py's
test-construction style) from the real 2026 shape confirmed live: Category
in {"Flag", "Other", "SafetyCar"}, RacingNumber usually blank for
penalty/investigation messages (driver codes live in the message text
instead, e.g. "CAR 41 (LIN)").
"""

from __future__ import annotations

import pandas as pd

from f1_fantasy.news.incidents import (
    PENALTY_PATTERN,
    parse_messages,
    summarise_driver_incidents,
)
from f1_fantasy.results import RaceResult


def test_penalty_pattern_matches_the_one_confirmed_real_message():
    assert PENALTY_PATTERN.search("FIA STEWARDS: DRIVE THROUGH PENALTY FOR CAR 41 (LIN)")


def _messages_df(rows: list[dict]) -> pd.DataFrame:
    base = {"Time": None, "Category": "Other", "Message": "", "Status": None, "Flag": None, "Scope": None, "Sector": None, "RacingNumber": None, "Lap": 1}
    return pd.DataFrame([{**base, **row} for row in rows])


def test_parse_messages_extracts_driver_codes_from_the_message_text_not_racing_number():
    raw = _messages_df(
        [{"Message": "TURN 6 INCIDENT INVOLVING CARS 44 (HAM) AND 63 (RUS) NOTED - CAUSING A COLLISION", "Lap": 2}]
    )

    messages = parse_messages(raw, round_number=10)

    assert messages[0].driver_codes == ("HAM", "RUS")
    assert messages[0].is_penalty is False


def test_parse_messages_flags_penalties_investigations_and_no_further_action():
    raw = _messages_df(
        [
            {"Message": "FIA STEWARDS: 5 SECOND TIME PENALTY FOR CAR 44 (HAM) - CAUSING A COLLISION", "Lap": 9},
            {"Message": "FIA STEWARDS: TURN 5 INCIDENT INVOLVING CARS 16 (LEC) UNDER INVESTIGATION", "Lap": 11},
            {"Message": "FIA STEWARDS: TURN 5 INCIDENT INVOLVING CARS 16 (LEC) NO FURTHER ACTION", "Lap": 16},
        ]
    )

    messages = parse_messages(raw, round_number=10)

    assert messages[0].is_penalty is True
    assert messages[1].is_investigation is True
    assert messages[2].is_no_further_action is True


def test_parse_messages_flags_safety_car_by_category_and_red_flag_by_flag_column():
    raw = _messages_df(
        [
            {"Category": "SafetyCar", "Message": "SAFETY CAR DEPLOYED", "Lap": 1},
            {"Category": "Flag", "Flag": "RED", "Message": "RED FLAG", "Lap": 5},
        ]
    )

    messages = parse_messages(raw, round_number=1)

    assert messages[0].is_safety_car is True
    assert messages[1].is_red_flag is True
    assert messages[0].is_red_flag is False
    assert messages[1].is_safety_car is False


WINNER = RaceResult(driver_code="WINNER", driver_name="Winner", constructor="Team", grid=1, position=1, status="Finished", laps=58)


def test_summarise_classifies_a_clear_collision_retirement_with_high_confidence():
    raw = _messages_df(
        [{"Message": "TURN 6 INCIDENT INVOLVING CARS 44 (HAM) AND 63 (RUS) NOTED - CAUSING A COLLISION", "Lap": 2}]
    )
    messages = parse_messages(raw, round_number=10)
    rus = RaceResult(driver_code="RUS", driver_name="Russell", constructor="Mercedes", grid=5, position=20, status="Retired", laps=0)

    summaries = summarise_driver_incidents(messages, [WINNER, rus])

    assert summaries["RUS"].retirement_cause == "collision"
    assert summaries["RUS"].retirement_confidence > 0.5


def test_summarise_classifies_a_clear_mechanical_retirement():
    raw = _messages_df([{"Message": "CAR 10 (GAS) INFORMED HAS AN ENGINE ISSUE", "Lap": 30}])
    messages = parse_messages(raw, round_number=1)
    gas = RaceResult(driver_code="GAS", driver_name="Gasly", constructor="Alpine", grid=10, position=18, status="Retired", laps=30)

    summaries = summarise_driver_incidents(messages, [WINNER, gas])

    assert summaries["GAS"].retirement_cause == "mechanical"
    assert summaries["GAS"].retirement_confidence > 0.5


def test_summarise_reports_no_cause_when_no_message_exists_near_the_retirement():
    """The real, common case in the 2026 feed: most retirements have no
    race-control message at all. None, not a forced guess."""
    messages: list = []
    stroll = RaceResult(driver_code="STR", driver_name="Stroll", constructor="Aston Martin", grid=18, position=20, status="Retired", laps=25)

    summaries = summarise_driver_incidents(messages, [WINNER, stroll])

    assert summaries["STR"].retirement_cause is None
    assert summaries["STR"].retirement_confidence == 0.0


def test_summarise_reports_ambiguous_when_both_mechanical_and_collision_keywords_appear_nearby():
    raw = _messages_df(
        [
            {"Message": "CAR 14 (ALO) SPUN", "Lap": 9},
            {"Message": "CAR 14 (ALO) REPORTED A BRAKE ISSUE BEFOREHAND", "Lap": 10},
        ]
    )
    messages = parse_messages(raw, round_number=1)
    alonso = RaceResult(driver_code="ALO", driver_name="Alonso", constructor="Aston Martin", grid=9, position=19, status="Retired", laps=10)

    summaries = summarise_driver_incidents(messages, [WINNER, alonso])

    assert summaries["ALO"].retirement_cause == "ambiguous"
    assert summaries["ALO"].retirement_confidence < 0.5


def test_summarise_caps_safety_car_exposure_at_the_drivers_own_last_lap():
    raw = _messages_df(
        [
            {"Category": "SafetyCar", "Message": "SAFETY CAR DEPLOYED", "Lap": 2},
            {"Category": "SafetyCar", "Message": "SAFETY CAR DEPLOYED", "Lap": 40},
        ]
    )
    messages = parse_messages(raw, round_number=1)
    early_retiree = RaceResult(driver_code="EARLY", driver_name="Early", constructor="Team", grid=20, position=20, status="Retired", laps=5)

    summaries = summarise_driver_incidents(messages, [WINNER, early_retiree])

    # Only the lap-2 safety car happened before this driver's lap 5 retirement.
    assert summaries["EARLY"].safety_car_laps == 1
    # The winner ran the whole race, so both safety car laps count for them.
    assert summaries["WINNER"].safety_car_laps == 2


def test_summarise_never_classifies_a_driver_who_finished():
    raw = _messages_df([{"Message": "CAR 1 (WINNER) SPUN", "Lap": 2}])
    messages = parse_messages(raw, round_number=1)

    summaries = summarise_driver_incidents(messages, [WINNER])

    assert summaries["WINNER"].retirement_cause is None
    assert summaries["WINNER"].retirement_confidence == 0.0
