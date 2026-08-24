"""Deciding what is due on any given tick.

The workflow runs hourly and asks this module what to do. Self-gating on the
calendar beats a hand-written cron per report: sessions move, sprint weekends
lock earlier, and time zones shift twice a year, none of which a fixed schedule
survives.

Every action is recorded once completed, so a tick that fires twice in the same
window does not re-send a report.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path

from f1_fantasy.calendar import RaceEvent

log = logging.getLogger(__name__)


class Action(str, Enum):
    PACE = "pace"
    PREVIEW = "preview"
    LOCKOUT = "lockout"
    RECAP = "recap"


#: How long before lockout the preview goes out.
PREVIEW_LEAD = timedelta(hours=24)
#: How long after lockout to capture, letting late edits settle.
LOCKOUT_DELAY = timedelta(minutes=15)
#: Earliest a race can be scored. Points keep moving for a while after the
#: flag, so the recap also verifies stability before publishing.
RECAP_EARLIEST = timedelta(hours=4)
#: After this long, stop waiting for a window to be serviced.
WINDOW_GRACE = timedelta(hours=18)


class RunState:
    """Which actions have already run, per race.

    Committed alongside the snapshots so the schedule survives the runner being
    ephemeral -- otherwise every tick would look like the first one.
    """

    def __init__(self, path: Path | str = "state.json") -> None:
        self.path = Path(path)
        self._data: dict[str, list[str]] = {}
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except ValueError:
                log.warning("ignoring unreadable state file %s", self.path)

    @staticmethod
    def _key(season: int, round_number: int) -> str:
        return f"{season}:{round_number}"

    def done(self, season: int, round_number: int) -> set[Action]:
        raw = self._data.get(self._key(season, round_number), [])
        return {Action(value) for value in raw if value in Action._value2member_map_}

    def mark(self, season: int, round_number: int, action: Action) -> None:
        key = self._key(season, round_number)
        actions = set(self._data.get(key, []))
        actions.add(action.value)
        self._data[key] = sorted(actions)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


def due_actions(event: RaceEvent, now: datetime, done: set[Action]) -> list[Action]:
    """Actions whose window is open and which have not already run.

    Windows close after a grace period so a tool that was offline for a week
    does not wake up and fire a stale preview for a race that has already run.
    """
    lockout = event.lockout_at
    due: list[Action] = []

    def window(start: datetime, end: datetime) -> bool:
        return start <= now < end

    practice_end = event.final_practice_at
    if (
        Action.PACE not in done
        and practice_end is not None
        and window(practice_end, lockout)
    ):
        due.append(Action.PACE)

    if Action.PREVIEW not in done and window(lockout - PREVIEW_LEAD, lockout):
        due.append(Action.PREVIEW)

    if Action.LOCKOUT not in done and window(
        lockout + LOCKOUT_DELAY, lockout + WINDOW_GRACE
    ):
        due.append(Action.LOCKOUT)

    if Action.RECAP not in done and window(
        event.starts_at + RECAP_EARLIEST, event.starts_at + timedelta(days=3)
    ):
        due.append(Action.RECAP)

    return due


def points_are_settled(previous: dict[str, float], current: dict[str, float]) -> bool:
    """Whether scoring has stopped moving between two consecutive reads.

    The recap waits for this rather than assuming a fixed delay after the flag:
    penalties and classification changes can move fantasy points hours later, and
    a recap published from provisional numbers has to be corrected in the chat.
    """
    if not current:
        return False
    if not previous:
        return False
    return previous == current
