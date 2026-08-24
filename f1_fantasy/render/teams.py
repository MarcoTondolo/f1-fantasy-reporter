"""Constructor identity colours.

Matched by substring because the API's ``TeamName`` is inconsistent across
seasons and endpoints ("Red Bull Racing", "Oracle Red Bull Racing", "RB").
A miss is harmless -- it falls back to muted ink.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path

ASSET_PATH = Path(__file__).resolve().parents[2] / "assets" / "teams.json"


@functools.lru_cache(maxsize=1)
def _table() -> tuple[dict[str, str], str]:
    if not ASSET_PATH.exists():
        return {}, "#898781"
    data = json.loads(ASSET_PATH.read_text(encoding="utf-8"))
    return data.get("colors", {}), data.get("fallback", "#898781")


def team_color(name: str | None) -> str:
    """Identity colour for a constructor name, or muted ink if unrecognised."""
    colors, fallback = _table()
    if not name:
        return fallback
    lowered = name.strip().lower()
    # Longest key first so "racing bulls" wins over a bare "rb".
    for key in sorted(colors, key=len, reverse=True):
        if key in lowered:
            return colors[key]
    return fallback
