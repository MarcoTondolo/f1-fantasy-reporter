"""The budget cap card: who's carrying the most (and least) total spend.

Total budget cap is the site's own figure: the value of a team's picked
drivers and constructors plus its unspent Cost Cap balance. Both halves are
already parsed onto Team (``value`` and ``bank``); this card just ranks
teams by their sum, the same way ``recap`` ranks teams by points.
"""

from __future__ import annotations

from f1_fantasy.api.models import LeagueSnapshot, Team


def _money(value: float | None) -> str:
    return f"${value:.1f}M" if value is not None else "–"


def _row(team: Team, member_name: str, peak: float) -> dict:
    return {
        "name": member_name,
        "team_name": team.team_name,
        "budget_cap": team.budget_cap,
        "budget_cap_label": _money(team.budget_cap),
        "value_label": _money(team.value),
        "bank_label": _money(team.bank),
        "bar_pct": round((team.budget_cap or 0.0) / peak * 100, 1) if peak else 0.0,
    }


def build_budget(
    snapshot: LeagueSnapshot,
    _previous: LeagueSnapshot | None = None,
    *,
    race_label: str = "",
) -> dict:
    """``_previous`` is accepted, unused, only for call-signature parity --
    budget cap is read straight off the snapshot, like chips and ownership.
    """
    ranked: list[tuple[Team, str]] = []
    for member in snapshot.members:
        team = snapshot.team_for(member)
        if team is not None and team.budget_cap is not None:
            ranked.append((team, member.user_name))
    ranked.sort(key=lambda pair: pair[0].budget_cap, reverse=True)

    peak = ranked[0][0].budget_cap if ranked else 0.0
    rows = [_row(team, name, peak) for team, name in ranked]

    highest = rows[0] if rows else None
    lowest = rows[-1] if len(rows) > 1 else None

    return {
        "eyebrow": "Budget cap",
        "title": race_label or f"Round {snapshot.race_id}",
        "subtitle": f"{len(rows)} teams",
        "league_name": snapshot.league_name,
        "footer_note": snapshot.captured_at.strftime("%d %b %Y"),
        "rows": rows,
        "highest": highest,
        "lowest": lowest,
        "caveat": _caveat(snapshot),
    }


def _caveat(snapshot: LeagueSnapshot) -> str:
    covered = len(snapshot.teams)
    total = len(snapshot.members)
    if covered < total:
        return f"Budget cap reflects {covered} of {total} members whose teams were readable."
    return ""


def caption(context: dict) -> str:
    lines = [f"\U0001f4b0 *Budget Cap* -- {context['title']}", ""]

    if context["highest"]:
        top = context["highest"]
        lines.append(f"Biggest budget: *{top['name']}* ({top['budget_cap_label']})")

    if context["lowest"]:
        bottom = context["lowest"]
        lines.append(f"Tightest squeeze: *{bottom['name']}* ({bottom['budget_cap_label']})")

    return "\n".join(lines)
