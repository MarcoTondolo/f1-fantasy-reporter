"""The pre-race preview card: when things run, and what's happened since.

Unlike every other card, this one isn't built from a LeagueSnapshot diff --
there is no team data to show before lockout. It's calendar plus news: session
times in the reader's own timezone, a retrospective on grid penalties confirmed
from the last race (jolpica only knows the actual starting grid once a race has
been run, so this can never be about the upcoming race's own grid), filtered
headlines, and -- once the pace dataset exists -- a Friday long-run picture.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from f1_fantasy.calendar import RaceEvent
from f1_fantasy.results import GridPenalty

#: Display order and label for each session type the calendar might carry.
SESSION_ORDER = [
    ("FirstPractice", "FP1"),
    ("SecondPractice", "FP2"),
    ("ThirdPractice", "FP3"),
    ("SprintQualifying", "Sprint Quali"),
    ("Sprint", "Sprint"),
    ("Qualifying", "Qualifying"),
]


def _session_rows(event: RaceEvent, tz: ZoneInfo) -> list[dict]:
    rows = []
    for key, label in SESSION_ORDER:
        session = event.session(key)
        if session is None:
            continue
        local = session.starts_at.astimezone(tz)
        rows.append(
            {
                "label": label,
                "day": local.strftime("%a"),
                "time": local.strftime("%H:%M"),
                "is_lockout": session.starts_at == event.lockout_at,
            }
        )
    return rows


def _penalty_row(penalty: GridPenalty) -> dict:
    return {
        "driver": penalty.driver_name,
        "from": penalty.quali_position,
        "to": penalty.grid_position,
        "places_lost": penalty.places_lost,
    }


def build_preview(
    event: RaceEvent,
    *,
    last_race_name: str = "",
    last_race_penalties: list[GridPenalty] | None = None,
    headlines: list[dict] | None = None,
    pace: list[dict] | None = None,
    timezone: str = "Europe/London",
    league_name: str = "",
    now: datetime | None = None,
) -> dict:
    tz = ZoneInfo(timezone)
    lockout_local = event.lockout_at.astimezone(tz)

    return {
        "eyebrow": "Race preview",
        "title": event.name,
        "subtitle": f"{event.circuit}, {event.locality}" if event.circuit else "",
        "league_name": league_name,
        "footer_note": (now or event.starts_at).strftime("%d %b %Y"),
        "is_sprint_weekend": event.is_sprint_weekend,
        "sessions": _session_rows(event, tz),
        "lockout_label": lockout_local.strftime("%A %H:%M %Z"),
        "last_race_name": last_race_name,
        "penalties": [_penalty_row(p) for p in (last_race_penalties or [])],
        "headlines": headlines or [],
        "pace": pace or [],
        "caveat": _caveat(last_race_penalties, headlines),
    }


def _caveat(penalties: list[GridPenalty] | None, headlines: list[dict] | None) -> str:
    if not penalties and not headlines:
        return "No grid penalties or flagged headlines since the last race."
    return ""


def caption(context: dict) -> str:
    lines = [f"\U0001f4c5 *Preview* -- {context['title']}"]
    if context["subtitle"]:
        lines.append(context["subtitle"])
    lines.append("")

    lines.append(f"⏰ Lockout: {context['lockout_label']}")
    for session in context["sessions"]:
        marker = " (lockout)" if session["is_lockout"] else ""
        lines.append(f"  {session['label']}: {session['day']} {session['time']}{marker}")

    if context["penalties"]:
        lines.append("")
        lines.append(f"\U0001f6a8 Grid penalties from {context['last_race_name']}:")
        for row in context["penalties"]:
            lines.append(f"  {row['driver']}: P{row['from']} -> P{row['to']} (-{row['places_lost']})")

    if context["headlines"]:
        lines.append("")
        lines.append("\U0001f4f0 Worth knowing:")
        for headline in context["headlines"][:4]:
            lines.append(f"  {headline['title']}")

    return "\n".join(lines)
