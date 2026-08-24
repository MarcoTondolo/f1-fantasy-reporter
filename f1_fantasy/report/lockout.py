"""The post-lockout card: who changed what, before anyone knows if it worked.

Deliberately says nothing about outcomes -- at lockout the race has not run, so
the catalogue's points are all zero. Anything evaluative here would be invented.
The recap card is where moves get judged.
"""

from __future__ import annotations

from collections import Counter

from f1_fantasy.api.models import CHIP_LABELS, LeagueSnapshot
from f1_fantasy.render.teams import team_color
from f1_fantasy.store.diff import TeamChange, chip_activations, diff_teams


def _swap_lines(change: TeamChange) -> list[dict]:
    """Pair outgoing with incoming for display only, longest list driving rows.

    This is presentation, not analysis: the rows read as "out -> in" but no claim
    is made that a given incoming player replaced that specific outgoing one.
    """
    outs, ins = change.players_out, change.players_in
    rows = []
    for index in range(max(len(outs), len(ins))):
        out = outs[index] if index < len(outs) else None
        incoming = ins[index] if index < len(ins) else None
        rows.append(
            {
                "out": out.name if out else None,
                "in": incoming.name if incoming else None,
            }
        )
    return rows


def build_lockout(
    current: LeagueSnapshot,
    previous: LeagueSnapshot | None,
    *,
    race_label: str = "",
    you_guid: str | None = None,
) -> dict:
    changes = diff_teams(previous, current)
    by_guid = {c.guid: c for c in changes}

    movers, held = [], []
    for member in current.members:
        change = by_guid.get(member.guid)
        if change is None:
            continue
        if change.unchanged:
            held.append(change.member_name)
            continue
        movers.append(
            {
                "name": change.member_name,
                "team_name": change.team_name,
                "swaps": _swap_lines(change),
                "captain": change.captain_to.name if change.captain_to else None,
                "captain_changed": change.captain_changed,
                "captain_color": _color(current, change),
                "chips": [CHIP_LABELS[chip] for chip in change.chips_activated],
                "value_change": change.value_change,
                "is_you": you_guid is not None and change.guid == you_guid,
            }
        )

    # What the league collectively backed this week -- the interesting signal at
    # lockout, since it shows where everyone is converging.
    incoming = Counter()
    outgoing = Counter()
    for change in changes:
        for player in change.players_in:
            incoming[player.name] += 1
        for player in change.players_out:
            outgoing[player.name] += 1

    total_moves = sum(incoming.values())

    chips_played = [
        {"label": CHIP_LABELS[chip], "who": ", ".join(names)}
        for chip, names in sorted(chip_activations(current).items(), key=lambda kv: kv[0].value)
    ]

    return {
        "eyebrow": "Teams locked",
        "title": race_label or f"Round {current.race_id}",
        "subtitle": f"{total_moves} transfers across {len(current.members)} teams",
        "league_name": current.league_name,
        "footer_note": current.captured_at.strftime("%d %b %Y %H:%M UTC"),
        "movers": movers,
        "held": held,
        "most_backed": [{"name": n, "count": c} for n, c in incoming.most_common(4) if c > 1],
        "most_dropped": [{"name": n, "count": c} for n, c in outgoing.most_common(4) if c > 1],
        "chips_played": chips_played,
        "caveat": _caveat(current, previous),
    }


def _color(snapshot: LeagueSnapshot, change: TeamChange) -> str:
    if not change.captain_to:
        return "#898781"
    player = snapshot.players.get(change.captain_to.player_id)
    return team_color(player.constructor if player else None)


def _caveat(current: LeagueSnapshot, previous: LeagueSnapshot | None) -> str:
    if previous is None:
        return (
            "First capture for this league -- there is no earlier lineup to compare "
            "against, so no changes can be shown this week."
        )
    covered = len(current.teams)
    total = len(current.members)
    if covered < total:
        return f"Team details unavailable for {total - covered} of {total} members."
    return ""


def caption(context: dict) -> str:
    lines = [f"\U0001f512 *Teams are locked* -- {context['title']}", ""]
    lines.append(f"{context['subtitle']}.")

    if context["chips_played"]:
        played = "; ".join(f"{c['label']} ({c['who']})" for c in context["chips_played"])
        lines.append(f"\U0001f3b4 Chips: {played}")

    if context["most_backed"]:
        backed = ", ".join(f"{p['name']} (×{p['count']})" for p in context["most_backed"])
        lines.append(f"\U0001f4c8 Most backed: {backed}")

    if context["held"]:
        lines.append(f"\U0001f9ca Held firm: {', '.join(context['held'])}")

    lines.append("")
    lines.append("Good luck \U0001f3ce️")
    return "\n".join(lines)
