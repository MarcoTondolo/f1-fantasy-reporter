"""Tolerant coercion helpers.

The F1 Fantasy API is undocumented and inconsistent: numbers arrive as strings,
booleans as 0/1, names URL-encoded, and the same concept is spelled differently
between endpoints. Everything that reads a raw payload goes through here so the
messiness stays in one place.
"""

from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import unquote_plus

_MISSING = object()


def first(payload: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    """Return the first key present and non-null, else *default*.

    Endpoints disagree on spelling for the same field -- e.g. autopilot's race id
    is ``autopilottakengd`` on the team payload but ``isautopilottakengd`` on the
    game-days payload -- so callers pass every spelling they have seen.
    """
    for key in keys:
        value = payload.get(key, _MISSING)
        if value is not _MISSING and value is not None:
            return value
    return default


def as_float(value: Any, default: float = 0.0) -> float:
    """Coerce to float, tolerating strings, blanks, and None."""
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int = 0) -> int:
    """Coerce to int, tolerating floats-as-strings like ``"12.0"``."""
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        pass
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def as_bool(value: Any) -> bool:
    """Coerce to bool. The API uses 0/1, "0"/"1", and occasionally "true"."""
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(as_int(value)) if value is not None else False


def decode(value: Any, default: str = "") -> str:
    """URL-decode a display string.

    Team and league names come back percent-encoded with ``+`` for spaces, so a
    team called "Ferrari's Finest" arrives as ``Ferrari%27s+Finest``.
    """
    if value is None:
        return default
    if not isinstance(value, str):
        return str(value)
    return unquote_plus(value).strip()


def positive_int_or_none(value: Any) -> int | None:
    """Race-id fields use 0 (not null) to mean "not used". Normalise to None."""
    parsed = as_int(value, default=0)
    return parsed if parsed > 0 else None
