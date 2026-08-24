"""Append-only snapshot store.

The API only ever returns *current* state, so any question of the form "what
changed since last race" can only be answered from history we captured
ourselves. Snapshots are plain JSON committed to the repo: no database to run,
history readable in a diff, and a bad parse is inspectable after the fact.

Layout::

    snapshots/{season}/{league_id}/{race_id}/{phase}.json
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from f1_fantasy.api.models import LeagueSnapshot, Phase

log = logging.getLogger(__name__)


class SnapshotStore:
    def __init__(self, root: Path | str = "snapshots") -> None:
        self.root = Path(root)

    # -- paths -------------------------------------------------------------

    def league_dir(self, season: int, league_id: int) -> Path:
        return self.root / str(season) / str(league_id)

    def path_for(self, season: int, league_id: int, race_id: int, phase: Phase) -> Path:
        return self.league_dir(season, league_id) / str(race_id) / f"{phase.value}.json"

    # -- writing -----------------------------------------------------------

    def write(self, snapshot: LeagueSnapshot) -> Path:
        """Persist a snapshot, overwriting any existing file for that phase.

        Overwrite is deliberate: re-running a phase should correct a bad capture
        rather than accumulate duplicates.
        """
        path = self.path_for(
            snapshot.season, snapshot.league_id, snapshot.race_id, snapshot.phase
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        # sort_keys keeps git diffs meaningful between runs.
        payload = json.loads(snapshot.model_dump_json())
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        log.info("wrote snapshot %s", path)
        return path

    # -- reading -----------------------------------------------------------

    def read(
        self, season: int, league_id: int, race_id: int, phase: Phase
    ) -> LeagueSnapshot | None:
        path = self.path_for(season, league_id, race_id, phase)
        if not path.exists():
            return None
        try:
            return LeagueSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            log.warning("ignoring unreadable snapshot %s: %s", path, exc)
            return None

    def exists(self, season: int, league_id: int, race_id: int, phase: Phase) -> bool:
        return self.path_for(season, league_id, race_id, phase).exists()

    def race_ids(self, season: int, league_id: int) -> list[int]:
        """Every race id with at least one snapshot, ascending."""
        base = self.league_dir(season, league_id)
        if not base.exists():
            return []
        ids = []
        for child in base.iterdir():
            if child.is_dir() and child.name.isdigit():
                ids.append(int(child.name))
        return sorted(ids)

    def previous_race_id(self, season: int, league_id: int, race_id: int) -> int | None:
        """The most recent race before *race_id* that we have snapshots for.

        Not simply ``race_id - 1``: the tool may have been added mid-season, or a
        run may have been missed, and the diff must still compare against the
        last real capture rather than silently reporting nothing.
        """
        earlier = [r for r in self.race_ids(season, league_id) if r < race_id]
        return max(earlier) if earlier else None

    def latest(
        self,
        season: int,
        league_id: int,
        race_id: int,
        phases: tuple[Phase, ...] = (Phase.FINAL, Phase.LOCKED, Phase.PRE_LOCK),
    ) -> LeagueSnapshot | None:
        """Best available snapshot for a race, preferring the most settled phase."""
        for phase in phases:
            snapshot = self.read(season, league_id, race_id, phase)
            if snapshot is not None:
                return snapshot
        return None
