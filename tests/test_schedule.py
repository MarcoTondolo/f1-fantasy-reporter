"""Calendar parsing and tick scheduling."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from f1_fantasy.calendar import (
    POST_RACE_GRACE,
    current_event,
    parse_calendar,
    previous_event,
)
from f1_fantasy.schedule import Action, RunState, due_actions, points_are_settled

UTC = timezone.utc


def _payload(*races: dict) -> dict:
    return {"MRData": {"RaceTable": {"Races": list(races)}}}


STANDARD = {
    "season": "2026",
    "round": "15",
    "raceName": "Dutch Grand Prix",
    "date": "2026-08-30",
    "time": "13:00:00Z",
    "Circuit": {
        "circuitName": "Circuit Zandvoort",
        "Location": {"locality": "Zandvoort", "country": "Netherlands"},
    },
    "FirstPractice": {"date": "2026-08-28", "time": "10:30:00Z"},
    "SecondPractice": {"date": "2026-08-28", "time": "14:00:00Z"},
    "ThirdPractice": {"date": "2026-08-29", "time": "09:30:00Z"},
    "Qualifying": {"date": "2026-08-29", "time": "13:00:00Z"},
}

SPRINT_ROUND = 16

SPRINT = {
    "season": "2026",
    "round": str(SPRINT_ROUND),
    "raceName": "Sprint Land Grand Prix",
    "date": "2026-09-06",
    "time": "13:00:00Z",
    "Circuit": {"circuitName": "Somewhere", "Location": {}},
    "FirstPractice": {"date": "2026-09-04", "time": "10:30:00Z"},
    "SprintQualifying": {"date": "2026-09-04", "time": "14:30:00Z"},
    "Sprint": {"date": "2026-09-05", "time": "10:00:00Z"},
    "Qualifying": {"date": "2026-09-05", "time": "14:00:00Z"},
}


# -- calendar ---------------------------------------------------------------


def test_calendar_parses_sessions_and_location():
    (event,) = parse_calendar(_payload(STANDARD))

    assert event.round == 15
    assert event.name == "Dutch Grand Prix"
    assert event.locality == "Zandvoort"
    assert event.starts_at == datetime(2026, 8, 30, 13, 0, tzinfo=UTC)
    assert event.session("ThirdPractice").starts_at == datetime(2026, 8, 29, 9, 30, tzinfo=UTC)


def test_standard_weekend_locks_at_qualifying():
    (event,) = parse_calendar(_payload(STANDARD))

    assert not event.is_sprint_weekend
    assert event.lockout_at == datetime(2026, 8, 29, 13, 0, tzinfo=UTC)


def test_sprint_weekend_locks_earlier_at_the_sprint():
    """The sprint runs before qualifying, so lineups lock a day sooner."""
    (event,) = parse_calendar(_payload(SPRINT))

    assert event.is_sprint_weekend
    assert event.lockout_at == datetime(2026, 9, 5, 10, 0, tzinfo=UTC)
    assert event.lockout_at < event.session("Qualifying").starts_at


def test_final_practice_falls_back_when_fp3_does_not_exist():
    """Sprint weekends run a single practice session."""
    (event,) = parse_calendar(_payload(SPRINT))

    # FP1 starts 10:30, so running is done about an hour later.
    assert event.final_practice_at == datetime(2026, 9, 4, 11, 30, tzinfo=UTC)


def test_missing_session_times_fall_back_to_the_race_start():
    bare = {"season": "2026", "round": "1", "raceName": "X", "date": "2026-03-08"}
    (event,) = parse_calendar(_payload(bare))

    assert event.lockout_at == event.starts_at
    assert event.final_practice_at is None


def test_calendar_selects_the_current_then_next_event():
    events = parse_calendar(_payload(STANDARD, SPRINT))

    friday = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
    assert current_event(events, friday).round == 15

    # Two days after the Dutch race, the next weekend is the sprint.
    after = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    assert current_event(events, after).round == 16
    assert previous_event(events, after).round == 15

    assert current_event(events, datetime(2027, 1, 1, tzinfo=UTC)) is None


def test_post_race_grace_keeps_a_race_current_while_its_recap_can_be_due():
    """Regression test for round 14 of 2026, whose recap was never produced.

    At DEFAULT_GRACE a race stops being current one day after the flag, while
    ``due_actions`` keeps the recap window open for three. In that two-day
    overlap the round was unreachable: the tick resolved to the *next* race and
    reported nothing due for it, and a forced recap rendered one round's card
    while marking a different round's action done -- which is how round 14's
    slot was consumed. Any moment where the recap is still due must resolve to
    the race it belongs to under POST_RACE_GRACE.
    """
    events = parse_calendar(_payload(STANDARD, SPRINT))
    dutch = events[0]

    two_days_after = dutch.starts_at + timedelta(days=2)
    assert Action.RECAP in due_actions(dutch, two_days_after, set())
    assert current_event(events, two_days_after, grace=POST_RACE_GRACE).round == dutch.round

    # The default stays short, so the forward-looking cards (picks, preview)
    # still point at the race being prepared for rather than the one just run.
    assert current_event(events, two_days_after).round == SPRINT_ROUND


def test_post_race_grace_never_reaches_into_the_next_weekend():
    """Three days is safe only because the next race is at least seven away."""
    events = parse_calendar(_payload(STANDARD, SPRINT))
    dutch, sprint = events

    assert sprint.starts_at - dutch.starts_at >= timedelta(days=7)
    # Once the recap window has closed, the next race is current again.
    after_window = dutch.starts_at + POST_RACE_GRACE + timedelta(hours=1)
    assert Action.RECAP not in due_actions(dutch, after_window, set())
    assert current_event(events, after_window, grace=POST_RACE_GRACE).round == sprint.round


# -- scheduling -------------------------------------------------------------


@pytest.fixture
def event():
    return parse_calendar(_payload(STANDARD))[0]


def test_pace_is_due_after_final_practice_and_before_lockout(event):
    after_fp3 = datetime(2026, 8, 29, 11, 0, tzinfo=UTC)

    assert Action.PACE in due_actions(event, after_fp3, set())


def test_pace_is_not_due_before_practice_has_run(event):
    thursday = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)

    assert Action.PACE not in due_actions(event, thursday, set())


def test_preview_goes_out_in_the_day_before_lockout(event):
    day_before = event.lockout_at - timedelta(hours=6)

    assert Action.PREVIEW in due_actions(event, day_before, set())


def test_preview_does_not_fire_after_lockout(event):
    assert Action.PREVIEW not in due_actions(event, event.lockout_at, set())


def test_lockout_waits_for_late_edits_to_settle(event):
    """Firing exactly at the lock would race the last-second team changes."""
    assert Action.LOCKOUT not in due_actions(event, event.lockout_at, set())
    assert Action.LOCKOUT in due_actions(
        event, event.lockout_at + timedelta(minutes=20), set()
    )


def test_recap_waits_for_the_race_to_be_scored(event):
    just_finished = event.starts_at + timedelta(hours=2)
    later = event.starts_at + timedelta(hours=6)

    assert Action.RECAP not in due_actions(event, just_finished, set())
    assert Action.RECAP in due_actions(event, later, set())


def test_completed_actions_are_not_repeated(event):
    moment = event.lockout_at + timedelta(minutes=20)

    assert Action.LOCKOUT in due_actions(event, moment, set())
    assert Action.LOCKOUT not in due_actions(event, moment, {Action.LOCKOUT})


def test_stale_windows_close_so_an_offline_week_does_not_fire_old_reports(event):
    """Coming back online days later must not post a preview for a finished race."""
    a_week_later = event.starts_at + timedelta(days=7)

    assert due_actions(event, a_week_later, set()) == []


# -- state ------------------------------------------------------------------


def test_run_state_round_trips(tmp_path):
    path = tmp_path / "state.json"
    state = RunState(path)
    state.mark(2026, 15, Action.LOCKOUT)
    state.save()

    assert RunState(path).done(2026, 15) == {Action.LOCKOUT}
    assert RunState(path).done(2026, 16) == set()


def test_run_state_survives_a_corrupt_file(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{oops", encoding="utf-8")

    assert RunState(path).done(2026, 15) == set()


# -- settling ---------------------------------------------------------------


def test_points_are_settled_only_when_two_reads_agree():
    first = {"a": 100.0, "b": 90.0}

    assert points_are_settled(first, dict(first))
    assert not points_are_settled(first, {"a": 104.0, "b": 90.0})


def test_points_are_never_settled_on_a_first_or_empty_read():
    """A single read proves nothing -- penalties move points hours after the flag."""
    assert not points_are_settled({}, {"a": 100.0})
    assert not points_are_settled({"a": 100.0}, {})
