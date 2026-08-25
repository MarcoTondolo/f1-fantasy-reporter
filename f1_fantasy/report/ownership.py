"""The ownership card: who's on whose team, and who nobody else has.

The tail of this list is the interesting half. A driver every team owns tells
you nothing about the table; a driver exactly one team owns is a differential,
and differentials are what actually separate a private league.
"""

from __future__ import annotations

from f1_fantasy.api.models import LeagueSnapshot
from f1_fantasy.render.teams import team_color
from f1_fantasy.store.diff import ScoredPlayer, ownership

#: How many of the most-owned drivers to show; constructors are few enough to
#: show in full.
TOP_DRIVERS = 8


def _row(snapshot: LeagueSnapshot, player: ScoredPlayer, owners: list[str], total_teams: int) -> dict:
    catalogue_entry = snapshot.players.get(player.player_id)
    return {
        "name": player.name,
        "owners": len(owners),
        "owner_names": owners,
        "pct": round(len(owners) / total_teams * 100) if total_teams else 0,
        "color": team_color(catalogue_entry.constructor if catalogue_entry else None),
    }


def build_ownership(snapshot: LeagueSnapshot, *, race_label: str = "") -> dict:
    rows = ownership(snapshot)
    total_teams = len(snapshot.teams) or len(snapshot.members)

    drivers = [(p, o) for p, o in rows if not p.is_constructor]
    constructors = [(p, o) for p, o in rows if p.is_constructor]

    top_drivers = [_row(snapshot, p, o, total_teams) for p, o in drivers[:TOP_DRIVERS]]
    all_constructors = [_row(snapshot, p, o, total_teams) for p, o in constructors]

    differentials = [_row(snapshot, p, o, total_teams) for p, o in rows if len(o) == 1]
    differentials.sort(key=lambda r: r["name"])

    return {
        "eyebrow": "Ownership",
        "title": race_label or f"Round {snapshot.race_id}",
        "subtitle": f"{len(rows)} players across {total_teams} teams",
        "league_name": snapshot.league_name,
        "footer_note": snapshot.captured_at.strftime("%d %b %Y"),
        "total_teams": total_teams,
        "top_drivers": top_drivers,
        "constructors": all_constructors,
        "differentials": differentials,
        "caveat": _caveat(snapshot),
    }


def _caveat(snapshot: LeagueSnapshot) -> str:
    covered = len(snapshot.teams)
    total = len(snapshot.members)
    if covered < total:
        return f"Ownership reflects {covered} of {total} members whose teams were readable."
    return ""


def caption(context: dict) -> str:
    lines = [f"\U0001f465 *Ownership* -- {context['title']}", ""]

    if context["top_drivers"]:
        top = context["top_drivers"][0]
        lines.append(f"Most owned: *{top['name']}* ({top['owners']}/{context['total_teams']} teams)")

    if context["differentials"]:
        names = ", ".join(row["name"] for row in context["differentials"][:6])
        more = len(context["differentials"]) - 6
        suffix = f" +{more} more" if more > 0 else ""
        lines.append(f"\U0001f48e Differentials: {names}{suffix}")

    return "\n".join(lines)
