"""Content correctness for the preview card.

Unlike every other report, preview has no LeagueSnapshot to diff -- it's
calendar plus news, so these tests build a RaceEvent directly rather than
going through make_snapshot.
"""

from __future__ import annotations

from datetime import datetime, timezone

from f1_fantasy.calendar import RaceEvent, Session
from f1_fantasy.report.preview import build_preview, caption
from f1_fantasy.results import GridPenalty


def _event(*, sprint: bool = False) -> RaceEvent:
    sessions = [
        Session(name="FirstPractice", starts_at=datetime(2026, 9, 4, 10, 30, tzinfo=timezone.utc)),
        Session(name="Qualifying", starts_at=datetime(2026, 9, 5, 14, 0, tzinfo=timezone.utc)),
    ]
    if sprint:
        sessions.append(Session(name="Sprint", starts_at=datetime(2026, 9, 5, 10, 0, tzinfo=timezone.utc)))
    return RaceEvent(
        season=2026,
        round=13,
        name="Italian Grand Prix",
        circuit="Monza",
        locality="Monza",
        country="Italy",
        starts_at=datetime(2026, 9, 6, 13, 0, tzinfo=timezone.utc),
        sessions=sessions,
    )


def test_sessions_are_converted_to_the_configured_local_timezone():
    context = build_preview(_event(), timezone="Europe/Rome")

    quali = next(s for s in context["sessions"] if s["label"] == "Qualifying")
    # 14:00 UTC in early September is UTC+2 in Rome.
    assert quali["time"] == "16:00"


def test_the_lockout_session_is_flagged():
    context = build_preview(_event())

    quali = next(s for s in context["sessions"] if s["label"] == "Qualifying")
    assert quali["is_lockout"] is True
    fp1 = next(s for s in context["sessions"] if s["label"] == "FP1")
    assert fp1["is_lockout"] is False


def test_sprint_weekend_locks_out_at_sprint_not_qualifying():
    context = build_preview(_event(sprint=True))

    sprint = next(s for s in context["sessions"] if s["label"] == "Sprint")
    quali = next(s for s in context["sessions"] if s["label"] == "Qualifying")
    assert sprint["is_lockout"] is True
    assert quali["is_lockout"] is False
    assert context["is_sprint_weekend"] is True


def test_penalties_are_shaped_for_the_template():
    penalty = GridPenalty(driver_code="HAD", driver_name="Isack Hadjar", quali_position=3, grid_position=13)

    context = build_preview(_event(), last_race_name="Dutch Grand Prix", last_race_penalties=[penalty])

    assert context["penalties"] == [{"driver": "Isack Hadjar", "from": 3, "to": 13, "places_lost": 10}]


def test_caveat_is_empty_once_there_is_something_to_show():
    penalty = GridPenalty(driver_code="HAD", driver_name="Isack Hadjar", quali_position=3, grid_position=13)

    context = build_preview(_event(), last_race_penalties=[penalty])

    assert context["caveat"] == ""


def test_caveat_says_so_when_thereas_nothing_to_report():
    context = build_preview(_event())

    assert "No grid penalties" in context["caveat"]


def test_caption_lists_session_times_and_penalties():
    penalty = GridPenalty(driver_code="HAD", driver_name="Isack Hadjar", quali_position=3, grid_position=13)
    context = build_preview(
        _event(), last_race_name="Dutch Grand Prix", last_race_penalties=[penalty], timezone="UTC"
    )

    text = caption(context)

    assert "Italian Grand Prix" in text
    assert "Isack Hadjar" in text
    assert "Qualifying" in text
