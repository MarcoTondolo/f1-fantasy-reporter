"""Race calendar and lockout times, from the Jolpica (Ergast successor) API.

Used to decide when each report is due. Fetched rather than hard-coded because
sessions get rescheduled, and a hard-coded calendar silently reports at the
wrong time when they do.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from pydantic import Field

from f1_fantasy.api.models import Model

log = logging.getLogger(__name__)

BASE_URL = "https://api.jolpi.ca/ergast/f1"


class Session(Model):
    name: str
    starts_at: datetime


class RaceEvent(Model):
    season: int
    round: int
    name: str
    circuit: str = ""
    locality: str = ""
    country: str = ""
    starts_at: datetime
    sessions: list[Session] = Field(default_factory=list)

    def session(self, name: str) -> Session | None:
        return next((s for s in self.sessions if s.name == name), None)

    @property
    def is_sprint_weekend(self) -> bool:
        return self.session("Sprint") is not None

    @property
    def lockout_at(self) -> datetime:
        """When lineups lock.

        Normally the start of Qualifying; on sprint weekends the Sprint comes
        first and locks them earlier. Falls back to the race start if the feed
        is missing session times, which is better than reporting at a guess.
        """
        if self.is_sprint_weekend:
            sprint = self.session("Sprint")
            if sprint:
                return sprint.starts_at
        qualifying = self.session("Qualifying")
        if qualifying:
            return qualifying.starts_at
        return self.starts_at

    @property
    def final_practice_at(self) -> datetime | None:
        """End of meaningful practice running -- when pace data becomes available.

        Sprint weekends only run one practice session, so FP3 does not exist.
        """
        for name in ("ThirdPractice", "SecondPractice", "FirstPractice"):
            session = self.session(name)
            if session:
                return session.starts_at + timedelta(hours=1)
        return None


def _parse_session(name: str, raw: Mapping[str, Any] | None) -> Session | None:
    if not isinstance(raw, Mapping):
        return None
    date, time = raw.get("date"), raw.get("time")
    if not date:
        return None
    return Session(name=name, starts_at=_combine(date, time))


def _combine(date: str, time: str | None) -> datetime:
    """Ergast splits date and time, and marks UTC with a trailing Z."""
    stamp = f"{date}T{(time or '00:00:00Z').replace('Z', '+00:00')}"
    parsed = datetime.fromisoformat(stamp)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_calendar(payload: Mapping[str, Any]) -> list[RaceEvent]:
    """Parse a Jolpica ``{season}.json`` response into race events."""
    races = (
        payload.get("MRData", {})
        .get("RaceTable", {})
        .get("Races", [])
    )

    events: list[RaceEvent] = []
    for race in races:
        if not isinstance(race, Mapping):
            continue
        circuit = race.get("Circuit") or {}
        location = circuit.get("Location") or {}

        sessions = []
        for key in (
            "FirstPractice",
            "SecondPractice",
            "ThirdPractice",
            "SprintQualifying",
            "SprintShootout",
            "Sprint",
            "Qualifying",
        ):
            session = _parse_session(key, race.get(key))
            if session:
                sessions.append(session)

        events.append(
            RaceEvent(
                season=int(race.get("season", 0)),
                round=int(race.get("round", 0)),
                name=race.get("raceName", ""),
                circuit=circuit.get("circuitName", ""),
                locality=location.get("locality", ""),
                country=location.get("country", ""),
                starts_at=_combine(race["date"], race.get("time")),
                sessions=sessions,
            )
        )

    events.sort(key=lambda event: event.round)
    return events


def fetch_calendar(season: int, *, timeout: float = 20.0) -> list[RaceEvent]:
    url = f"{BASE_URL}/{season}.json?limit=100"
    log.debug("fetching calendar %s", url)
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return parse_calendar(json.loads(response.read().decode("utf-8")))


def current_event(events: list[RaceEvent], now: datetime) -> RaceEvent | None:
    """The race weekend *now* falls in, or the next one.

    A weekend is treated as running from three days before the race until a day
    after, so a Friday tick during a race week resolves to that race rather than
    the previous one.
    """
    for event in events:
        if now <= event.starts_at + timedelta(days=1):
            return event
    return None


def previous_event(events: list[RaceEvent], now: datetime) -> RaceEvent | None:
    finished = [e for e in events if e.starts_at < now]
    return finished[-1] if finished else None
