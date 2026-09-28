"""The post-lockout card: who changed what, before anyone knows if it worked.

Deliberately says nothing about outcomes at real lockout time -- the race has
not run yet, so the catalogue's points are all zero and anything evaluative
would be invented. The recap card is where moves get judged.

**`final`, however, lets a *later* viewing of this same card show the real
point impact of each move, honestly.** The site is browsed after the fact --
often once the race (and its FINAL snapshot) already exists -- so passing
that snapshot in as `final` scores each mover's swap with
`store.diff.score_changes`, the same real-points lookup `recap.py` uses, and
attaches it to their row. This never touches the live post right after
lockout (which has no FINAL snapshot to pass and so gets exactly the old,
zero-information card); it only enriches the static site's rendering of the
same card once the outcome is knowable.
"""

from __future__ import annotations

from collections import Counter

from f1_fantasy.api.models import CHIP_LABELS, LeagueSnapshot, team_key
from f1_fantasy.render.teams import team_color
from f1_fantasy.store.diff import TeamChange, chip_activations, diff_teams, score_changes


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
    final: LeagueSnapshot | None = None,
) -> dict:
    changes = diff_teams(previous, current)
    # Keyed by (guid, team_no), not guid alone -- an account can run more than
    # one team in this league (see team_key), and a bare-guid key collapses
    # both teams' TeamChange onto each other: whichever team's diff is built
    # last wins the dict slot, so the other team's real transfer is silently
    # dropped and its member row falsely reports the survivor's status twice.
    by_member = {team_key(c.guid, c.team_no): c for c in changes}

    # Real point impact per move, only when *final* (the post-race snapshot)
    # was supplied -- see module docstring. score_changes reads points off
    # whichever snapshot it's given, so this is the exact recap.py convention,
    # just optionally reused here.
    points_by_member = {}
    if final is not None:
        points_by_member = {team_key(s.guid, s.team_no): s.delta for s in score_changes(changes, final)}

    movers, held = [], []
    for member in current.members:
        key = team_key(member.guid, member.team_no)
        change = by_member.get(key)
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
                "points_impact": points_by_member.get(key) if final is not None else None,
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
        "has_points_impact": final is not None,
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

    scored_movers = [m for m in context["movers"] if m.get("points_impact") is not None]
    if scored_movers:
        best = max(scored_movers, key=lambda m: m["points_impact"])
        worst = min(scored_movers, key=lambda m: m["points_impact"])
        lines.append(f"\U0001f4c8 Best call: {best['name']} ({best['points_impact']:+g})")
        if worst["name"] != best["name"]:
            lines.append(f"\U0001f4c9 Worst call: {worst['name']} ({worst['points_impact']:+g})")

    lines.append("")
    lines.append("Good luck \U0001f3ce️")
    return "\n".join(lines)
