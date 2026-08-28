"""The upgrade tracker card: recent upgrade mentions and their measured effect.

No league/team data dependency -- same class of card as ``report/preview.py``
and ``report/picks.py``, built from ``news/upgrades.py``'s detected mentions
and ``predict/upgrades.py``'s field-relative before/after measurements
rather than a league snapshot diff.
"""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse

from f1_fantasy.news.upgrades import UpgradeMention
from f1_fantasy.predict.upgrades import UpgradeEffect

#: Sort key fallback for a mention with no parsed publish date -- sorts last.
_EPOCH = datetime.fromtimestamp(0, tz=timezone.utc)

#: How many recent mentions to show -- this is a digest, not a full archive.
TOP_MENTIONS = 10

#: Shown on every card so a reader doesn't mistake a small-sample effect
#: size for a statistically confident claim -- this project's established
#: transparency convention (see picks.py's own model caveat) applied here.
EFFECT_CAVEAT = (
    "relative_delta is a field-relative qualifying-pace change (negative = improved "
    "more than the field), not a p-value-backed claim -- always read alongside n_before/n_after."
)


def _source_name(url: str) -> str:
    host = urlparse(url).netloc
    return host[4:] if host.startswith("www.") else host


def _mention_row(mention: UpgradeMention) -> dict:
    return {
        "title": mention.title,
        "source": _source_name(mention.source),
        "constructors": list(mention.constructors),
        "link": mention.link,
        "published_at": mention.published_at.strftime("%d %b") if mention.published_at else "",
    }


def _effect_row(effect: UpgradeEffect) -> dict:
    verdict = "no measurable edge vs field"
    if effect.relative_delta is not None:
        verdict = "improved vs field" if effect.relative_delta < 0 else "behind the field's own gain"
    return {
        "constructor": effect.constructor,
        "round": effect.upgrade_round,
        "relative_delta": round(effect.relative_delta, 3) if effect.relative_delta is not None else None,
        "n_before": effect.n_before,
        "n_after": effect.n_after,
        "verdict": verdict,
    }


def build_upgrades(
    mentions: list[UpgradeMention],
    effects: list[UpgradeEffect],
    *,
    season: int,
    league_name: str = "",
) -> dict:
    recent = sorted(mentions, key=lambda m: m.published_at or _EPOCH, reverse=True)[:TOP_MENTIONS]
    ranked_effects = sorted(
        effects, key=lambda e: abs(e.relative_delta) if e.relative_delta is not None else -1, reverse=True
    )
    unattributed_count = sum(1 for m in mentions if not m.constructors)

    return {
        "eyebrow": "Upgrade tracker",
        "title": f"{season} upgrade tracker",
        "subtitle": f"{len(mentions)} mentions checked · {len(effects)} measured effects",
        "league_name": league_name,
        "footer_note": "",
        "recent_mentions": [_mention_row(m) for m in recent],
        "measured_effects": [_effect_row(e) for e in ranked_effects],
        "unattributed_count": unattributed_count,
        "caveat": EFFECT_CAVEAT if effects else "No upgrade effects measured yet -- check back after more rounds are raced.",
    }


def caption(context: dict) -> str:
    lines = [f"\U0001f527 *{context['title']}*", context["subtitle"], ""]

    if context["measured_effects"]:
        lines.append("\U0001f4c8 Measured effects:")
        for row in context["measured_effects"][:5]:
            delta = f"{row['relative_delta']:+.3f}pp" if row["relative_delta"] is not None else "n/a"
            lines.append(
                f"  {row['constructor']} R{row['round']}: {delta} "
                f"(n={row['n_before']}/{row['n_after']}) -- {row['verdict']}"
            )
        lines.append("")

    if context["recent_mentions"]:
        lines.append("\U0001f4f0 Recent mentions:")
        for row in context["recent_mentions"][:5]:
            teams = ", ".join(row["constructors"]) or "unattributed"
            lines.append(f"  [{row['source']}] {row['title']} ({teams})")

    return "\n".join(lines)
