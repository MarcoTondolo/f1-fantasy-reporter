"""The daily digest email body: plain text, no card image needed.

Follows every other report module's build_context/caption split even
though there's no HTML card here -- the digest is read as an email, not
pasted into a league chat, so the "caption" *is* the whole deliverable.
"""

from __future__ import annotations

from f1_fantasy.news.digest import DailyDigest, DigestMention

TOP_PER_CATEGORY = 8


def _mention_line(m: DigestMention) -> str:
    teams = f" [{', '.join(m.constructors)}]" if m.constructors else ""
    when = f" ({m.published_at.strftime('%d %b')})" if m.published_at else ""
    return f"  - {m.title}{teams}{when} -- {m.source}"


def build_digest_report(digest: DailyDigest) -> dict:
    return {
        "title": f"F1 Fantasy daily digest -- {digest.season}"
        + (f" round {digest.round_number}" if digest.round_number else ""),
        "generated_at": digest.generated_at.strftime("%Y-%m-%d %H:%M UTC"),
        "lineup_mentions": digest.lineup_mentions[:TOP_PER_CATEGORY],
        "upgrade_mentions": digest.upgrade_mentions[:TOP_PER_CATEGORY],
        "penalty_mentions": digest.penalty_mentions[:TOP_PER_CATEGORY],
        "confirmed_lineup_changes": digest.confirmed_lineup_changes,
        "lineup_watch_note": digest.lineup_watch_note,
    }


def caption(context: dict) -> str:
    lines = [context["title"], f"Generated {context['generated_at']}", ""]

    if context["confirmed_lineup_changes"]:
        lines.append("CONFIRMED LINEUP CHANGES (from session data, not rumour):")
        for headline in context["confirmed_lineup_changes"]:
            lines.append(f"  - {headline}")
        lines.append("")
    elif context["lineup_watch_note"]:
        lines.append(f"Confirmed lineup changes: {context['lineup_watch_note']}")
        lines.append("")

    if context["lineup_mentions"]:
        lines.append("LINEUP NEWS (rumoured/reported, not yet confirmed in session data):")
        for m in context["lineup_mentions"]:
            lines.append(_mention_line(m))
        lines.append("")

    if context["upgrade_mentions"]:
        lines.append("UPGRADE PACKAGES:")
        for m in context["upgrade_mentions"]:
            lines.append(_mention_line(m))
        lines.append("")

    if context["penalty_mentions"]:
        lines.append("PENALTIES / RELIABILITY:")
        for m in context["penalty_mentions"]:
            lines.append(_mention_line(m))
        lines.append("")

    if not any(
        (
            context["confirmed_lineup_changes"],
            context["lineup_mentions"],
            context["upgrade_mentions"],
            context["penalty_mentions"],
        )
    ):
        lines.append("Nothing matched any category today -- a quiet news day, not a fetch failure.")

    return "\n".join(lines).strip()
