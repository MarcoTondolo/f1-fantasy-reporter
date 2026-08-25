"""Qualifying and race results from Jolpica, and grid-penalty detection.

Sibling to calendar.py -- same API, same urllib-based fetch, same tolerant
parsing philosophy. Grid penalties are detected deterministically by comparing
a driver's qualifying classification against their actual starting grid
position, rather than scraped from prose: free, accurate, and it needs no
guesswork about wording.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any, Mapping

from pydantic import Field

from f1_fantasy.api.models import Model
from f1_fantasy.calendar import BASE_URL

log = logging.getLogger(__name__)


class QualifyingResult(Model):
    driver_code: str
    driver_name: str
    constructor: str
    position: int


class RaceResult(Model):
    driver_code: str
    driver_name: str
    constructor: str
    grid: int
    position: int | None = None
    status: str = ""
    points: float = 0.0

    @property
    def finished(self) -> bool:
        return self.position is not None


class GridPenalty(Model):
    """A driver who started further back than they qualified."""

    driver_code: str
    driver_name: str
    quali_position: int
    grid_position: int

    @property
    def places_lost(self) -> int:
        return self.grid_position - self.quali_position


#: A drop of this many places or more between quali and the actual grid counts
#: as a penalty rather than ordinary reshuffling (a car ahead not starting,
#: pushed everyone up one, and that isn't a penalty).
PENALTY_THRESHOLD = 3


def _fetch(path: str, *, timeout: float = 20.0) -> Mapping[str, Any]:
    url = f"{BASE_URL}/{path}"
    log.debug("fetching %s", url)
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_qualifying(payload: Mapping[str, Any]) -> list[QualifyingResult]:
    races = payload.get("MRData", {}).get("RaceTable", {}).get("Races", [])
    if not races:
        return []
    results = []
    for row in races[0].get("QualifyingResults", []):
        driver = row.get("Driver", {})
        constructor = row.get("Constructor", {})
        results.append(
            QualifyingResult(
                driver_code=driver.get("code", ""),
                driver_name=f"{driver.get('givenName', '')} {driver.get('familyName', '')}".strip(),
                constructor=constructor.get("name", ""),
                position=int(row.get("position", 0)),
            )
        )
    return results


def parse_race_results(payload: Mapping[str, Any]) -> list[RaceResult]:
    races = payload.get("MRData", {}).get("RaceTable", {}).get("Races", [])
    if not races:
        return []
    results = []
    for row in races[0].get("Results", []):
        driver = row.get("Driver", {})
        constructor = row.get("Constructor", {})
        status = row.get("status", "")
        # A classified finish has a numeric "position"; a DNF/DSQ still has a
        # "positionText" but it's non-numeric ("R", "D", "W") and not a rank.
        position_text = row.get("position")
        position = int(position_text) if str(position_text).isdigit() else None
        results.append(
            RaceResult(
                driver_code=driver.get("code", ""),
                driver_name=f"{driver.get('givenName', '')} {driver.get('familyName', '')}".strip(),
                constructor=constructor.get("name", ""),
                grid=int(row.get("grid", 0) or 0),
                position=position,
                status=status,
                points=float(row.get("points", 0) or 0),
            )
        )
    return results


def fetch_qualifying(season: int, round_number: int) -> list[QualifyingResult]:
    return parse_qualifying(_fetch(f"{season}/{round_number}/qualifying.json"))


def fetch_race_results(season: int, round_number: int) -> list[RaceResult]:
    return parse_race_results(_fetch(f"{season}/{round_number}/results.json"))


def detect_grid_penalties(
    qualifying: list[QualifyingResult],
    race_results: list[RaceResult],
    *,
    threshold: int = PENALTY_THRESHOLD,
) -> list[GridPenalty]:
    """Drivers whose actual grid slot fell threshold+ places behind their quali.

    A grid position of 0 means "started from the pit lane" -- always a
    penalty regardless of quali position, so it's treated as maximally last.
    """
    quali_by_code = {q.driver_code: q.position for q in qualifying if q.driver_code}
    penalties = []
    for result in race_results:
        quali_position = quali_by_code.get(result.driver_code)
        if quali_position is None:
            continue
        grid_position = result.grid if result.grid > 0 else len(race_results)
        if grid_position - quali_position >= threshold:
            penalties.append(
                GridPenalty(
                    driver_code=result.driver_code,
                    driver_name=result.driver_name,
                    quali_position=quali_position,
                    grid_position=grid_position,
                )
            )
    penalties.sort(key=lambda p: p.places_lost, reverse=True)
    return penalties
