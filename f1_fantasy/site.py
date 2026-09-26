"""A static site over this project's own committed output: one page per
race weekend (pre-race analysis, post-race actuals, and how the model's
predictions compared to what actually happened), plus an index.

Deliberately reuses what `out/`, `snapshots/`, and `data/pace/` already
carry rather than recomputing anything -- this is an assembly step, not a
new analysis pipeline. Run it after `tick`/`recap` capture new data (the
GitHub Actions workflow does this on every push to the cards), or by hand
with `f1-fantasy build-site`.

Designed to be published as a public GitHub Pages site. That is a real
choice, made explicitly by the project's owner: the league snapshots this
draws from name every member of the tracked leagues, not just the owner's
own teams. `README.md`'s "Going public" section records that decision and
what it means before this is ever pointed at Pages.
"""

from __future__ import annotations

import html
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from f1_fantasy.calendar import fetch_calendar

CARD_TITLES = {
    "preview": "Preview",
    "lockout": "Teams locked",
    "picks": "Picks",
    "recap": "Recap",
    "winners_losers": "Winners & losers",
    "hindsight": "Hindsight",
    "chips": "Chip watch",
    "ownership": "Ownership",
    "budget": "Budget cap",
    "benchmarks": "Model benchmarks",
}
#: Display order within a round page; anything else found falls in after these.
CARD_ORDER = ["preview", "lockout", "picks", "chips", "ownership", "budget", "benchmarks", "recap", "winners_losers", "hindsight"]
PRE_RACE_CARDS = {"preview", "lockout", "picks", "chips", "ownership", "budget", "benchmarks"}
POST_RACE_CARDS = {"recap", "winners_losers", "hindsight"}


@dataclass
class CardFile:
    key: str
    title: str
    caption_html: str | None
    image_rel: str | None  # path relative to the round's output dir, once copied


@dataclass
class RoundPage:
    round_number: int
    event_name: str
    circuit: str
    status: str  # "completed" | "upcoming" | "no data"
    cards: list[CardFile]
    model_vs_actual: dict | None
    extras: list[tuple[str, str]]  # (label, relative href) for hand-built deep dives


def _caption_to_html(text: str) -> str:
    """This project's captions are chat messages (emoji, `*bold*`, line
    breaks) -- not markdown meant for a full parser. Just the two
    transforms that matter for on-page legibility."""
    escaped = html.escape(text)
    bolded = re.sub(r"\*(.+?)\*", r"<strong>\1</strong>", escaped)
    return bolded.replace("\n", "<br>")


def _load_round_cards(season: int, round_number: int, out_dir: Path, dest_assets: Path) -> list[CardFile]:
    round_dir = out_dir / str(season) / str(round_number)
    cards: list[CardFile] = []
    seen = set()

    if round_dir.is_dir():
        for txt_path in sorted(round_dir.glob("*.txt")):
            key = txt_path.stem
            seen.add(key)
            png_path = txt_path.with_suffix(".png")
            image_rel = None
            if png_path.exists():
                dest_assets.mkdir(parents=True, exist_ok=True)
                shutil.copy2(png_path, dest_assets / png_path.name)
                image_rel = f"assets/{season}/{round_number}/{png_path.name}"
            cards.append(
                CardFile(
                    key=key,
                    title=CARD_TITLES.get(key, key.replace("_", " ").title()),
                    caption_html=_caption_to_html(txt_path.read_text(encoding="utf-8")),
                    image_rel=image_rel,
                )
            )

    # Legacy rounds (captured before the out/{season}/{round}/<action>.{txt,png}
    # convention settled) kept their cards under a per-league subdirectory with
    # no caption file. Still worth showing -- just without caption text.
    for league_dir in sorted(round_dir.glob("league-*")) if round_dir.is_dir() else []:
        for png_path in sorted(league_dir.glob("*.png")):
            key = f"{png_path.stem}-{league_dir.name}"
            if key in seen:
                continue
            seen.add(key)
            dest_assets.mkdir(parents=True, exist_ok=True)
            shutil.copy2(png_path, dest_assets / f"{png_path.stem}_{league_dir.name}.png")
            cards.append(
                CardFile(
                    key=key,
                    title=f"{CARD_TITLES.get(png_path.stem, png_path.stem.title())} ({league_dir.name})",
                    caption_html=None,
                    image_rel=f"assets/{season}/{round_number}/{png_path.stem}_{league_dir.name}.png",
                )
            )

    def sort_key(card: CardFile) -> tuple[int, str]:
        base = card.key.split("-")[0]
        try:
            return (CARD_ORDER.index(base), card.key)
        except ValueError:
            return (len(CARD_ORDER), card.key)

    return sorted(cards, key=sort_key)


def _model_vs_actual_for_round(season: int, round_number: int, backtest_path: Path) -> dict | None:
    if not backtest_path.exists():
        return None
    data = json.loads(backtest_path.read_text(encoding="utf-8"))
    season_data = data.get(str(season))
    if not season_data:
        return None
    for row in season_data.get("per_round", []):
        if row.get("round") == round_number:
            return row
    return None


def _extras_for_round(round_number: int, extras_dir: Path, dest_root: Path) -> list[tuple[str, str]]:
    """Hand-built deep dives (a pace-progression chart, a written commentary
    page) live in ``extras_dir/round-{n}/*.html`` when they exist for that
    round -- this project doesn't auto-generate those, it only links them in.
    """
    src = extras_dir / f"round-{round_number}"
    if not src.is_dir():
        return []
    dest = dest_root / "extras" / f"round-{round_number}"
    dest.mkdir(parents=True, exist_ok=True)
    out = []
    for path in sorted(src.glob("*.html")):
        shutil.copy2(path, dest / path.name)
        label = path.stem.replace("-", " ").replace("_", " ").title()
        out.append((label, f"extras/round-{round_number}/{path.name}"))
    return out


def build_round_pages(
    season: int,
    *,
    out_dir: Path = Path("out"),
    snapshot_dir: Path = Path("snapshots"),
    backtest_path: Path = Path("data/pace/points_backtest.json"),
    extras_dir: Path = Path("site_extras"),
    dest_root: Path = Path("docs"),
) -> list[RoundPage]:
    """One RoundPage per round that has *any* committed output, in calendar
    order. A round with no captured data at all is left out entirely rather
    than shown as an empty placeholder -- there is nothing yet to show.
    """
    events = {e.round: e for e in fetch_calendar(season)}
    candidate_rounds = sorted(
        {int(p.name) for p in (out_dir / str(season)).glob("*") if p.name.isdigit()}
        if (out_dir / str(season)).is_dir()
        else set()
    )

    pages = []
    for round_number in candidate_rounds:
        event = events.get(round_number)
        dest_assets = dest_root / "assets" / str(season) / str(round_number)
        cards = _load_round_cards(season, round_number, out_dir, dest_assets)
        if not cards:
            continue
        keys = {c.key.split("-")[0] for c in cards}
        # Card-key matching alone misses legacy rounds (captured before the
        # out/{season}/{round}/<action>.{txt,png} convention settled): their
        # post-race cards live under a per-league subdirectory and get keys
        # like "budget-league-4512504", whose base "budget" never lands in
        # POST_RACE_CARDS. Falling back to the calendar date catches those --
        # a round whose race has already happened is "completed" regardless
        # of which card-naming convention captured it.
        race_happened = event is not None and event.starts_at <= datetime.now(timezone.utc)
        if keys & POST_RACE_CARDS or race_happened:
            status = "completed"
        elif keys & PRE_RACE_CARDS:
            status = "upcoming"
        else:
            status = "no data"
        pages.append(
            RoundPage(
                round_number=round_number,
                event_name=event.name if event else f"Round {round_number}",
                circuit=event.circuit if event else "",
                status=status,
                cards=cards,
                model_vs_actual=_model_vs_actual_for_round(season, round_number, backtest_path),
                extras=_extras_for_round(round_number, extras_dir, dest_root),
            )
        )
    return pages


# --------------------------------------------------------------------------
# rendering -- one dark, broadcast-graphic design system shared with every
# card this project renders (f1_fantasy/render/templates/theme.css) and with
# the artifacts built alongside it, so the site, the emailed cards, and the
# chat artifacts all read as one product.
# --------------------------------------------------------------------------

_BASE_CSS = """
:root {
  --page: #0d0d0d; --surface: #1a1a19; --surface-raised: #232322;
  --ink: #ffffff; --ink-secondary: #c3c2b7; --ink-muted: #898781;
  --grid: #2c2c2a; --baseline: #383835; --hairline: rgba(255,255,255,0.10);
  --pos: #0ca30c; --neg: #d03b3b; --accent: #e10600;
  --status-upcoming: #fab219; --status-completed: #0ca30c;
  --font-display: 'Barlow Condensed', 'Liberation Sans', system-ui, sans-serif;
  --font-body: 'Inter', 'Liberation Sans', system-ui, sans-serif;
  color-scheme: dark;
}
* { box-sizing: border-box; }
body {
  background: var(--page); color: var(--ink); font-family: var(--font-body);
  -webkit-font-smoothing: antialiased; margin: 0; padding: 20px 16px 48px;
  display: flex; justify-content: center;
}
.page { width: 100%; max-width: 1080px; display: flex; flex-direction: column; gap: 22px; }
a { color: inherit; }
.eyebrow {
  font-family: var(--font-display); font-weight: 700; font-size: 13px;
  letter-spacing: 0.16em; text-transform: uppercase; color: var(--accent);
}
header.site-header { border-bottom: 2px solid var(--accent); padding-bottom: 16px; display: flex; flex-direction: column; gap: 8px; }
h1 {
  font-family: var(--font-display); font-weight: 700; font-size: clamp(30px, 5.5vw, 44px);
  line-height: 0.98; letter-spacing: -0.01em; text-transform: uppercase; margin: 0; text-wrap: balance;
}
.subtitle { color: var(--ink-secondary); font-size: 14px; max-width: 700px; }
.nav-back { font-size: 13px; color: var(--ink-secondary); text-decoration: none; display: inline-flex; align-items: center; gap: 6px; }
.nav-back:hover { color: var(--ink); }

.round-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 14px; }
.round-card {
  background: var(--surface); border-radius: 4px; padding: 18px; text-decoration: none;
  display: flex; flex-direction: column; gap: 8px; border: 1px solid var(--hairline);
  transition: border-color 0.15s, transform 0.15s;
}
.round-card:hover { border-color: var(--baseline); transform: translateY(-1px); }
.round-num { font-family: var(--font-display); font-weight: 700; font-size: 13px; color: var(--ink-muted); letter-spacing: 0.08em; text-transform: uppercase; }
.round-name { font-family: var(--font-display); font-weight: 700; font-size: 22px; text-transform: uppercase; line-height: 1.05; }
.round-circuit { font-size: 12px; color: var(--ink-muted); }
.status-pill {
  align-self: flex-start; font-size: 10px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase;
  padding: 3px 9px; border-radius: 999px; margin-top: 4px;
}
.status-pill.completed { background: rgba(12,163,12,0.16); color: var(--status-completed); }
.status-pill.upcoming { background: rgba(250,178,25,0.16); color: var(--status-upcoming); }

.section-title {
  font-family: var(--font-display); font-weight: 700; font-size: 15px; letter-spacing: 0.1em;
  text-transform: uppercase; color: var(--ink-muted); border-bottom: 1px solid var(--hairline); padding-bottom: 8px; margin: 6px 0 -4px;
}
.card-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 14px; }
.card {
  background: var(--surface); border-radius: 4px; padding: 18px; display: flex; flex-direction: column; gap: 10px;
}
.card-title { font-family: var(--font-display); font-weight: 700; font-size: 16px; text-transform: uppercase; letter-spacing: 0.02em; }
.card-caption { font-size: 13px; line-height: 1.6; color: var(--ink-secondary); }
.card img { max-width: 100%; border-radius: 3px; display: block; }

.metric-row { display: flex; flex-wrap: wrap; gap: 10px; }
.metric {
  background: var(--surface-raised); border-radius: 4px; padding: 12px 16px; min-width: 130px; flex: 1;
}
.metric-label { font-size: 10px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--ink-muted); margin-bottom: 4px; }
.metric-value { font-family: var(--font-display); font-weight: 700; font-size: 24px; font-variant-numeric: tabular-nums; }
.metric-value.good { color: var(--status-completed); }
.metric-value.warn { color: var(--status-upcoming); }

.extras-list { display: flex; flex-wrap: wrap; gap: 10px; }
.extras-list a {
  background: var(--accent); color: var(--ink); font-family: var(--font-body); font-weight: 600; font-size: 13px;
  text-decoration: none; padding: 9px 16px; border-radius: 999px;
}
.empty-note { color: var(--ink-muted); font-size: 13px; padding: 8px 0; }

footer.site-footer { font-size: 11px; color: var(--ink-muted); line-height: 1.6; padding-top: 8px; border-top: 1px solid var(--hairline); }

@media (max-width: 560px) { .card, .round-card { padding: 14px; } }
"""

_HEAD = """<title>{title}</title>
<meta name="description" content="{description}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>{css}</style>
"""


def _status_label(status: str) -> str:
    return {"completed": "Race weekend complete", "upcoming": "Upcoming"}.get(status, status)


def render_index_html(pages: list[RoundPage], *, season: int, league_name: str) -> str:
    cards = []
    for p in sorted(pages, key=lambda p: -p.round_number):
        cards.append(
            f'<a class="round-card" href="rounds/round-{p.round_number}.html">'
            f'<span class="round-num">Round {p.round_number}</span>'
            f'<span class="round-name">{html.escape(p.event_name)}</span>'
            f'<span class="round-circuit">{html.escape(p.circuit)}</span>'
            f'<span class="status-pill {p.status}">{_status_label(p.status)}</span>'
            f"</a>"
        )
    body = f"""
<div class="page">
  <header class="site-header">
    <div class="eyebrow">{season} Season</div>
    <h1>{html.escape(league_name)}</h1>
    <div class="subtitle">Every race weekend this project has captured: pre-race analysis, post-race results, and how the expected-points model compared to what actually happened.</div>
  </header>
  <div class="round-grid">
    {''.join(cards)}
  </div>
  <footer class="site-footer">Generated from this repository's own committed snapshots and cards -- see <a href="https://github.com/tiptoptopher/f1-fantasy-reporter">the source</a> for how.</footer>
</div>
"""
    return _HEAD.format(title=f"{league_name} — {season}", description=f"Race-by-race fantasy analysis and results for the {season} season.", css=_BASE_CSS) + body


def _model_vs_actual_html(row: dict | None) -> str:
    if row is None:
        return '<div class="empty-note">Not in the walk-forward backtest yet -- run <code>f1-fantasy points-backtest</code> to add this round once it has raced.</div>'
    mae = row.get("mae")
    spearman = row.get("spearman")
    rank = row.get("top_pick_actual_rank")
    hit = row.get("top_pick_in_top3")
    hit_class = "good" if hit else "warn"
    return f"""
<div class="metric-row">
  <div class="metric"><div class="metric-label">Mean absolute error</div><div class="metric-value">{mae:.1f} pts</div></div>
  <div class="metric"><div class="metric-label">Rank correlation</div><div class="metric-value">{spearman:.2f}</div></div>
  <div class="metric"><div class="metric-label">Top pick actually finished</div><div class="metric-value {hit_class}">P{rank}</div></div>
</div>
<div class="empty-note">MAE is how far the model's mean points prediction was from each driver's real score, averaged across {row.get('n_drivers', '?')} drivers. Rank correlation (Spearman) is whether it got the *order* right even when the numbers were off. "Top pick actually finished" checks whether the model's #1 projected driver landed in the top 3 on the day.</div>
"""


def render_round_html(page: RoundPage, *, league_name: str, season: int) -> str:
    pre_cards = [c for c in page.cards if c.key.split("-")[0] in PRE_RACE_CARDS]
    post_cards = [c for c in page.cards if c.key.split("-")[0] in POST_RACE_CARDS]
    other_cards = [c for c in page.cards if c not in pre_cards and c not in post_cards]

    def card_html(c: CardFile) -> str:
        img = f'<img src="../{c.image_rel}" alt="{html.escape(c.title)}" loading="lazy">' if c.image_rel else ""
        caption = f'<div class="card-caption">{c.caption_html}</div>' if c.caption_html else ""
        return f'<div class="card"><div class="card-title">{html.escape(c.title)}</div>{img}{caption}</div>'

    extras_html = ""
    if page.extras:
        links = "".join(f'<a href="../{href}">{html.escape(label)}</a>' for label, href in page.extras)
        extras_html = f'<div class="section-title">Deep dives</div><div class="extras-list">{links}</div>'

    sections = [extras_html] if extras_html else []
    if pre_cards:
        sections.append('<div class="section-title">Pre-race</div><div class="card-grid">' + "".join(card_html(c) for c in pre_cards) + "</div>")
    if post_cards:
        sections.append('<div class="section-title">Post-race</div><div class="card-grid">' + "".join(card_html(c) for c in post_cards) + "</div>")
        sections.append('<div class="section-title">Model vs. actuals</div>' + _model_vs_actual_html(page.model_vs_actual))
    if other_cards:
        sections.append('<div class="section-title">Other</div><div class="card-grid">' + "".join(card_html(c) for c in other_cards) + "</div>")
    if not post_cards and page.status == "upcoming":
        sections.append('<div class="section-title">Post-race</div><div class="empty-note">Not raced yet -- this section fills in once the recap is captured.</div>')

    body = f"""
<div class="page">
  <a class="nav-back" href="../index.html">&larr; All rounds</a>
  <header class="site-header">
    <div class="eyebrow">Round {page.round_number} &middot; {season}</div>
    <h1>{html.escape(page.event_name)}</h1>
    <div class="subtitle">{html.escape(page.circuit)}</div>
  </header>
  {''.join(sections)}
  <footer class="site-footer">{html.escape(league_name)} &middot; generated from this repository's committed cards and snapshots.</footer>
</div>
"""
    return _HEAD.format(title=f"{page.event_name} — Round {page.round_number}", description=f"Pre-race analysis and post-race results for the {season} {page.event_name}.", css=_BASE_CSS) + body


def build_site(
    season: int,
    league_name: str,
    *,
    out_dir: Path = Path("out"),
    snapshot_dir: Path = Path("snapshots"),
    backtest_path: Path = Path("data/pace/points_backtest.json"),
    extras_dir: Path = Path("site_extras"),
    dest_root: Path = Path("docs"),
) -> list[RoundPage]:
    dest_root.mkdir(parents=True, exist_ok=True)
    (dest_root / "rounds").mkdir(exist_ok=True)

    pages = build_round_pages(
        season, out_dir=out_dir, snapshot_dir=snapshot_dir, backtest_path=backtest_path, extras_dir=extras_dir, dest_root=dest_root
    )
    (dest_root / "index.html").write_text(render_index_html(pages, season=season, league_name=league_name), encoding="utf-8")
    for page in pages:
        (dest_root / "rounds" / f"round-{page.round_number}.html").write_text(
            render_round_html(page, league_name=league_name, season=season), encoding="utf-8"
        )
    return pages
