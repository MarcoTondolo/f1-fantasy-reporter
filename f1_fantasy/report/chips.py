"""The chip watch card: who's played what, and who's still holding what.

Unlike the recap and lockout cards, this needs only one snapshot -- the API's
chip flags are cumulative for the season, so "who's used their wildcard" never
requires a comparison against a previous race.
"""

from __future__ import annotations

from f1_fantasy.api.models import CHIP_LABELS, LeagueSnapshot
from f1_fantasy.store.diff import chip_activations, chip_status


def build_chips(
    snapshot: LeagueSnapshot,
    _previous: LeagueSnapshot | None = None,
    *,
    race_label: str = "",
) -> dict:
    """``_previous`` is accepted, unused, only so this matches every other
    builder's call signature -- the runner dispatches every card the same way,
    and chip status needs no diff (the API's flags are already cumulative).
    """
    statuses = chip_status(snapshot)
    played_this_race = chip_activations(snapshot)
    total_teams = len(snapshot.teams) or len(snapshot.members)

    rows = []
    for status in statuses:
        rows.append(
            {
                "chip": status.chip,
                "label": CHIP_LABELS[status.chip],
                "used": [{"name": u.member_name, "race": u.race_id} for u in status.used],
                "available": status.available,
                "used_count": len(status.used),
                "available_count": len(status.available),
            }
        )
    # Chips nobody's touched yet are the most actionable to see first.
    rows.sort(key=lambda r: r["used_count"])

    played = [
        {"label": CHIP_LABELS[chip], "who": ", ".join(names)}
        for chip, names in sorted(played_this_race.items(), key=lambda kv: kv[0].value)
    ]

    total_used = sum(r["used_count"] for r in rows)

    return {
        "eyebrow": "Chip watch",
        "title": race_label or f"Round {snapshot.race_id}",
        "subtitle": f"{total_used} of {total_teams * len(rows)} chips played league-wide",
        "league_name": snapshot.league_name,
        "footer_note": snapshot.captured_at.strftime("%d %b %Y"),
        "played_this_race": played,
        "chips": rows,
        "caveat": _caveat(snapshot),
    }


def _caveat(snapshot: LeagueSnapshot) -> str:
    covered = len(snapshot.teams)
    total = len(snapshot.members)
    if covered < total:
        return f"Chip data unavailable for {total - covered} of {total} members."
    return ""


def caption(context: dict) -> str:
    lines = [f"\U0001f3b4 *Chip Watch* -- {context['title']}", ""]

    if context["played_this_race"]:
        lines.append("*Played this race*")
        for chip in context["played_this_race"]:
            lines.append(f"  {chip['label']}: {chip['who']}")
        lines.append("")

    untouched = [row["label"] for row in context["chips"] if row["used_count"] == 0]
    if untouched:
        lines.append(f"\U0001f9ca Nobody's played: {', '.join(untouched)}")

    fully_played = [row["label"] for row in context["chips"] if row["available_count"] == 0]
    if fully_played:
        lines.append(f"✅ Everyone's used: {', '.join(fully_played)}")

    return "\n".join(lines)
