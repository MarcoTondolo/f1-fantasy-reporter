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
from f1_fantasy.site_comparison import load_round_comparison, render_comparison_page
from f1_fantasy.site_leagues import (
    SECONDARY_VISUAL_LEAGUES,
    backfill_primary_league_cards,
    render_secondary_league_cards,
)

#: Where the feedback widget (see `_FEEDBACK_WIDGET_HTML`) files new issues.
GITHUB_REPO = "tiptoptopher/f1-fantasy-reporter"

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
#: Cards that are about one specific league's teams/standings, as opposed to
#: the model's own predictions (preview, picks, benchmarks) -- these get
#: pulled out of the generic Pre-race/Post-race grids and grouped per league
#: instead, see `LeagueCardGroup`.
LEAGUE_CARD_KEYS = {"lockout", "chips", "ownership", "budget", "recap", "winners_losers", "hindsight"}


@dataclass
class CardFile:
    key: str
    title: str
    caption_html: str | None
    image_rel: str | None  # path relative to the round's output dir, once copied


@dataclass
class LeagueCardGroup:
    league_id: int
    league_name: str
    cards: list[CardFile]


@dataclass
class RoundPage:
    round_number: int
    event_name: str
    circuit: str
    status: str  # "completed" | "upcoming" | "no data"
    cards: list[CardFile]
    league_groups: list[LeagueCardGroup]
    model_vs_actual: dict | None
    #: Relative href (from docs/rounds/) to the driver-by-driver drill-down
    #: page, when data/pace/round_comparison/ has this round cached.
    comparison_href: str | None
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

    if round_dir.is_dir():
        for txt_path in sorted(round_dir.glob("*.txt")):
            key = txt_path.stem
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

    def sort_key(card: CardFile) -> tuple[int, str]:
        try:
            return (CARD_ORDER.index(card.key), card.key)
        except ValueError:
            return (len(CARD_ORDER), card.key)

    return sorted(cards, key=sort_key)


def _league_names(season: int, snapshot_dir: Path) -> dict[int, str]:
    """``league_id -> league_name``, read straight off whatever snapshot is
    cheapest to find -- just enough to label a section heading, so this
    doesn't validate through the full LeagueSnapshot model.
    """
    names: dict[int, str] = {}
    season_dir = snapshot_dir / str(season)
    if not season_dir.is_dir():
        return names
    for league_dir in sorted(season_dir.iterdir()):
        if not league_dir.is_dir() or not league_dir.name.isdigit():
            continue
        league_id = int(league_dir.name)
        for snapshot_path in sorted(league_dir.glob("*/*.json")):
            try:
                name = json.loads(snapshot_path.read_text(encoding="utf-8")).get("league_name")
            except ValueError:
                continue
            if name:
                names[league_id] = name
                break
    return names


def _load_league_groups(
    season: int,
    round_number: int,
    out_dir: Path,
    dest_assets: Path,
    *,
    primary_league_id: int | None,
    primary_league_name: str,
    league_names: dict[int, str],
    model_cards: list[CardFile],
) -> list[LeagueCardGroup]:
    """One group per league this round has team/standings cards for.

    The primary league's cards are the flat ``out/{season}/{round}/*.png``
    files already loaded by `_load_round_cards` (just the league-specific
    subset of them, per `LEAGUE_CARD_KEYS`); every other league -- rendered
    by `render_secondary_league_cards`, or a legacy round's own per-league
    capture -- lives under its own ``out/{season}/{round}/league-{id}/``.
    """
    groups: dict[int, list[CardFile]] = {}

    if primary_league_id is not None:
        primary_cards = [c for c in model_cards if c.key in LEAGUE_CARD_KEYS]
        if primary_cards:
            groups[primary_league_id] = primary_cards

    round_dir = out_dir / str(season) / str(round_number)
    for league_dir in sorted(round_dir.glob("league-*")) if round_dir.is_dir() else []:
        try:
            league_id = int(league_dir.name.removeprefix("league-"))
        except ValueError:
            continue
        cards = groups.setdefault(league_id, [])
        seen = {c.key for c in cards}
        dest = dest_assets / league_dir.name
        for png_path in sorted(league_dir.glob("*.png")):
            key = png_path.stem
            if key in seen:
                continue
            seen.add(key)
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(png_path, dest / png_path.name)
            caption_path = png_path.with_suffix(".txt")
            cards.append(
                CardFile(
                    key=key,
                    title=CARD_TITLES.get(key, key.replace("_", " ").title()),
                    caption_html=_caption_to_html(caption_path.read_text(encoding="utf-8")) if caption_path.exists() else None,
                    image_rel=f"assets/{season}/{round_number}/{league_dir.name}/{png_path.name}",
                )
            )

    def card_sort_key(card: CardFile) -> tuple[int, str]:
        try:
            return (CARD_ORDER.index(card.key), card.key)
        except ValueError:
            return (len(CARD_ORDER), card.key)

    def league_sort_key(league_id: int) -> tuple[int, str]:
        if league_id == primary_league_id:
            return (0, "")
        if league_id in SECONDARY_VISUAL_LEAGUES:
            return (1, league_names.get(league_id, str(league_id)))
        return (2, league_names.get(league_id, str(league_id)))

    return [
        LeagueCardGroup(
            league_id=league_id,
            league_name=primary_league_name if league_id == primary_league_id else league_names.get(league_id, f"League {league_id}"),
            # Each group's own cards, looked up by *its* league_id -- not the
            # bare name `cards`, which after the for-loop above has ended
            # only holds whichever league was processed *last* (a real bug:
            # every group in this comprehension was silently getting that
            # one league's card list, since a list comprehension's own loop
            # variable is `league_id`, not `cards`, so `cards` fell through
            # to the outer for-loop's final value instead of raising).
            cards=sorted(groups[league_id], key=card_sort_key),
        )
        for league_id in sorted(groups, key=league_sort_key)
        if groups[league_id]
    ]


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
    primary_league_id: int | None = None,
    primary_league_name: str = "",
    out_dir: Path = Path("out"),
    snapshot_dir: Path = Path("snapshots"),
    backtest_path: Path = Path("data/pace/points_backtest.json"),
    comparison_dir: Path = Path("data/pace/round_comparison"),
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

    # Ciao Squadra's (the primary league's) cards come from the live tick
    # pipeline already; every other league this project tracks standings for
    # only gets a card if rendered here, from snapshots already committed --
    # no live API access needed, see f1_fantasy.site_leagues.
    race_labels = {r: e.name for r, e in events.items()}
    render_secondary_league_cards(season, race_labels, snapshot_dir=snapshot_dir, out_dir=out_dir)
    # Backfills any gap the live pipeline itself left for the primary league
    # (a round captured before a card type existed, a missed tick) -- never
    # touches a card the live pipeline already rendered.
    if primary_league_id is not None:
        backfill_primary_league_cards(season, race_labels, primary_league_id, snapshot_dir=snapshot_dir, out_dir=out_dir)
    league_names = _league_names(season, snapshot_dir)

    pages = []
    for round_number in candidate_rounds:
        event = events.get(round_number)
        dest_assets = dest_root / "assets" / str(season) / str(round_number)
        all_cards = _load_round_cards(season, round_number, out_dir, dest_assets)
        model_cards = [c for c in all_cards if c.key not in LEAGUE_CARD_KEYS]
        league_groups = _load_league_groups(
            season,
            round_number,
            out_dir,
            dest_assets,
            primary_league_id=primary_league_id,
            primary_league_name=primary_league_name,
            league_names=league_names,
            model_cards=all_cards,
        )
        comparison_payload = load_round_comparison(season, round_number, comparison_dir)
        if not model_cards and not league_groups and not comparison_payload:
            continue
        keys = {c.key for c in all_cards} | {c.key for group in league_groups for c in group.cards}
        # Card-key matching alone misses rounds where the race has happened
        # but, say, only the pre-race cards captured before a run failed --
        # falling back to the calendar date catches those too: a round whose
        # race has already happened is "completed" regardless of exactly
        # which cards got captured for it.
        race_happened = event is not None and event.starts_at <= datetime.now(timezone.utc)
        if keys & POST_RACE_CARDS or race_happened:
            status = "completed"
        elif keys & PRE_RACE_CARDS:
            status = "upcoming"
        else:
            status = "no data"

        comparison_href = None
        if comparison_payload and comparison_payload.get("drivers"):
            (dest_root / "rounds").mkdir(parents=True, exist_ok=True)
            comparison_html = render_comparison_page(
                comparison_payload,
                event_name=event.name if event else f"Round {round_number}",
                round_number=round_number,
                season=season,
            )
            comparison_path = dest_root / "rounds" / f"round-{round_number}-comparison.html"
            comparison_path.write_text(comparison_html, encoding="utf-8")
            comparison_href = f"round-{round_number}-comparison.html"

        pages.append(
            RoundPage(
                round_number=round_number,
                event_name=event.name if event else f"Round {round_number}",
                circuit=event.circuit if event else "",
                status=status,
                cards=model_cards,
                league_groups=league_groups,
                model_vs_actual=_model_vs_actual_for_round(season, round_number, backtest_path),
                comparison_href=comparison_href,
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
.league-title {
  font-family: var(--font-display); font-weight: 700; font-size: 13px; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--ink-secondary); margin: 8px 0 -4px;
}
.card-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 14px; }
.card {
  background: var(--surface); border-radius: 4px; padding: 18px; display: flex; flex-direction: column; gap: 10px;
  position: relative;
}
.card-title { font-family: var(--font-display); font-weight: 700; font-size: 16px; text-transform: uppercase; letter-spacing: 0.02em; }
.card-caption { font-size: 13px; line-height: 1.6; color: var(--ink-secondary); }
.card img { max-width: 100%; border-radius: 3px; display: block; cursor: zoom-in; }
.card-copy-btn {
  position: absolute; top: 14px; right: 14px; z-index: 3;
  width: 30px; height: 30px; border-radius: 999px; border: none;
  background: rgba(0,0,0,0.55); color: #fff; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
}
.card-copy-btn:hover { background: var(--accent); }
.card-copy-btn svg { width: 14px; height: 14px; }
.card-toast {
  position: absolute; top: 16px; right: 52px; z-index: 3;
  background: rgba(0,0,0,0.8); color: #fff; font-size: 11px; font-weight: 600; padding: 5px 9px; border-radius: 4px;
  opacity: 0; pointer-events: none; transition: opacity 0.2s; white-space: nowrap;
}
.card-toast.show { opacity: 1; }
.lb-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.88); z-index: 1002;
  display: none; flex-direction: column; align-items: center; padding: 16px;
}
.lb-overlay.open { display: flex; }
.lb-toolbar { display: flex; justify-content: flex-end; gap: 8px; width: 100%; max-width: 1100px; margin-bottom: 10px; flex-shrink: 0; }
.lb-toolbar button {
  font-family: var(--font-body); font-weight: 600; font-size: 13px; padding: 8px 14px; border-radius: 999px;
  cursor: pointer; border: 1px solid var(--hairline); background: var(--surface); color: var(--ink);
}
.lb-toolbar .lb-copy { background: var(--accent); border-color: var(--accent); }
.lb-body { flex: 1; width: 100%; max-width: 1100px; overflow: auto; text-align: center; }
.lb-body img { max-width: 100%; border-radius: 4px; }

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

.fb-btn {
  position: fixed; right: 18px; bottom: 18px; z-index: 1000;
  display: inline-flex; align-items: center; gap: 8px;
  background: var(--accent); color: #fff; border: none; border-radius: 999px;
  padding: 12px 18px; font-family: var(--font-body); font-weight: 600; font-size: 13px;
  cursor: pointer; box-shadow: 0 4px 14px rgba(0,0,0,0.4);
  transition: transform 0.15s, box-shadow 0.15s;
}
.fb-btn:hover { transform: translateY(-2px); box-shadow: 0 6px 18px rgba(0,0,0,0.5); }
.fb-btn svg { width: 16px; height: 16px; flex-shrink: 0; }
.fb-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.6); z-index: 1001;
  display: none; align-items: center; justify-content: center; padding: 16px;
}
.fb-overlay.open { display: flex; }
.fb-modal {
  background: var(--surface); border-radius: 6px; padding: 22px; width: 100%; max-width: 440px;
  border: 1px solid var(--hairline); display: flex; flex-direction: column; gap: 14px;
  max-height: 90vh; overflow-y: auto;
}
.fb-modal-title { font-family: var(--font-display); font-weight: 700; font-size: 18px; text-transform: uppercase; }
.fb-modal-context { font-size: 11px; color: var(--ink-muted); background: var(--surface-raised); border-radius: 4px; padding: 8px 10px; word-break: break-word; }
.fb-type-row { display: flex; gap: 8px; }
.fb-type-btn {
  flex: 1; padding: 8px; border-radius: 4px; border: 1px solid var(--hairline); background: var(--surface-raised);
  color: var(--ink-secondary); font-family: var(--font-body); font-weight: 600; font-size: 13px; cursor: pointer;
}
.fb-type-btn.active { border-color: var(--accent); color: var(--ink); background: rgba(225,6,0,0.14); }
.fb-modal textarea {
  width: 100%; min-height: 110px; resize: vertical; background: var(--surface-raised); color: var(--ink);
  border: 1px solid var(--hairline); border-radius: 4px; padding: 10px; font-family: var(--font-body); font-size: 13px;
  box-sizing: border-box;
}
.fb-modal-note { font-size: 11px; color: var(--ink-muted); line-height: 1.5; }
.fb-modal-actions { display: flex; justify-content: flex-end; gap: 8px; }
.fb-modal-actions button { font-family: var(--font-body); font-weight: 600; font-size: 13px; padding: 9px 16px; border-radius: 999px; cursor: pointer; border: none; }
.fb-cancel { background: transparent; color: var(--ink-secondary); }
.fb-cancel:hover { color: var(--ink); }
.fb-submit { background: var(--accent); color: #fff; }
.fb-submit:disabled { opacity: 0.5; cursor: not-allowed; }
@media (max-width: 560px) { .fb-btn span.fb-btn-label { display: none; } .fb-btn { padding: 14px; } }
"""

#: A context-aware "report a bug / request a change" button, fixed bottom-right
#: on every page. Filing an actual issue needs write access this static site
#: can never safely hold client-side, so it pre-fills a GitHub "new issue" form
#: (title, body with page title/URL, a `site-feedback` label) and lets the
#: viewer's own GitHub session send it -- one extra click, no exposed token.
_FEEDBACK_WIDGET_HTML = """
<button type="button" class="fb-btn" id="fbOpenBtn" aria-haspopup="dialog">
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 9V7a3 3 0 0 1 6 0v2"/><rect x="5" y="9" width="14" height="11" rx="4"/><path d="M2 13h3M19 13h3M9 3l1.5 2M15 3l-1.5 2M9 20v-4M15 20v-4"/></svg>
  <span class="fb-btn-label">Bug / Feature</span>
</button>
<div class="fb-overlay" id="fbOverlay">
  <div class="fb-modal" role="dialog" aria-modal="true" aria-labelledby="fbTitle">
    <div class="fb-modal-title" id="fbTitle">Report a bug or request a change</div>
    <div class="fb-modal-context" id="fbContext"></div>
    <div class="fb-type-row">
      <button type="button" class="fb-type-btn active" data-type="Bug" id="fbTypeBug">Bug</button>
      <button type="button" class="fb-type-btn" data-type="Feature" id="fbTypeFeature">Feature</button>
    </div>
    <textarea id="fbText" placeholder="What's wrong, or what would you like changed?"></textarea>
    <div class="fb-modal-note">Opens a pre-filled GitHub issue with this page's title and URL attached, so Claude has the context -- you'll just need to hit "Submit new issue" on GitHub to send it.</div>
    <div class="fb-modal-actions">
      <button type="button" class="fb-cancel" id="fbCancel">Cancel</button>
      <button type="button" class="fb-submit" id="fbSubmit">Open issue</button>
    </div>
  </div>
</div>
<script>
(function () {
  var GITHUB_REPO = "%(github_repo)s";
  var openBtn = document.getElementById('fbOpenBtn');
  var overlay = document.getElementById('fbOverlay');
  var cancelBtn = document.getElementById('fbCancel');
  var submitBtn = document.getElementById('fbSubmit');
  var textEl = document.getElementById('fbText');
  var contextEl = document.getElementById('fbContext');
  var typeBtns = [document.getElementById('fbTypeBug'), document.getElementById('fbTypeFeature')];
  var currentType = 'Bug';

  function openModal() {
    contextEl.textContent = document.title + ' \\u2014 ' + window.location.href;
    overlay.classList.add('open');
    textEl.focus();
  }
  function closeModal() {
    overlay.classList.remove('open');
  }
  openBtn.addEventListener('click', openModal);
  cancelBtn.addEventListener('click', closeModal);
  overlay.addEventListener('click', function (e) { if (e.target === overlay) closeModal(); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeModal(); });

  typeBtns.forEach(function (btn) {
    btn.addEventListener('click', function () {
      currentType = btn.getAttribute('data-type');
      typeBtns.forEach(function (b) { b.classList.toggle('active', b === btn); });
    });
  });

  submitBtn.addEventListener('click', function () {
    var text = textEl.value.trim();
    if (!text) { textEl.focus(); return; }
    var title = '[' + currentType + '] ' + text.split('\\n')[0].slice(0, 80);
    var body = '**Type:** ' + currentType + '\\n' +
      '**Page:** ' + document.title + '\\n' +
      '**URL:** ' + window.location.href + '\\n\\n' +
      text;
    var url = 'https://github.com/' + GITHUB_REPO + '/issues/new'
      + '?title=' + encodeURIComponent(title)
      + '&body=' + encodeURIComponent(body)
      + '&labels=' + encodeURIComponent('site-feedback');
    window.open(url, '_blank', 'noopener');
    textEl.value = '';
    closeModal();
  });
})();
</script>
""" % {"github_repo": GITHUB_REPO}

#: Every rendered card image gets a click-to-enlarge lightbox (the cards are
#: fixed-width PNGs made for a chat thread, so on a phone they shrink to
#: illegible before this) and a copy button, for pasting a visual straight
#: into WhatsApp without a screenshot-and-crop round trip. Only wired into
#: round pages, which are the only pages with card images.
_LIGHTBOX_HTML = """
<div class="lb-overlay" id="lbOverlay">
  <div class="lb-toolbar">
    <button type="button" class="lb-copy" id="lbCopyBtn">Copy image</button>
    <button type="button" id="lbCloseBtn">Close</button>
  </div>
  <div class="lb-body"><img id="lbImg" src="" alt=""></div>
</div>
<script>
(function () {
  var COPY_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
  var lbOverlay = document.getElementById('lbOverlay');
  var lbImg = document.getElementById('lbImg');
  var lbCopyBtn = document.getElementById('lbCopyBtn');
  var lbCloseBtn = document.getElementById('lbCloseBtn');
  var currentSrc = '';

  function openLightbox(src, title) {
    currentSrc = src;
    lbImg.src = src;
    lbImg.alt = title || '';
    lbOverlay.classList.add('open');
  }
  function closeLightbox() {
    lbOverlay.classList.remove('open');
    lbImg.src = '';
  }
  lbCloseBtn.addEventListener('click', closeLightbox);
  lbOverlay.addEventListener('click', function (e) { if (e.target === lbOverlay) closeLightbox(); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeLightbox(); });

  function showToast(cardEl, message) {
    var toast = cardEl.querySelector('.card-toast');
    if (!toast) return;
    toast.textContent = message;
    toast.classList.add('show');
    setTimeout(function () { toast.classList.remove('show'); }, 1800);
  }

  function copyImage(src, cardEl) {
    if (!navigator.clipboard || typeof ClipboardItem === 'undefined') {
      window.open(src, '_blank', 'noopener');
      showToast(cardEl, 'Opened image \\u2014 long-press to save');
      return;
    }
    var item = new ClipboardItem({
      'image/png': fetch(src).then(function (r) { return r.blob(); })
    });
    navigator.clipboard.write([item]).then(function () {
      showToast(cardEl, 'Copied!');
    }).catch(function () {
      window.open(src, '_blank', 'noopener');
      showToast(cardEl, 'Copy failed \\u2014 opened image instead');
    });
  }

  lbCopyBtn.addEventListener('click', function () {
    if (!currentSrc) return;
    var item = new ClipboardItem({
      'image/png': fetch(currentSrc).then(function (r) { return r.blob(); })
    });
    if (!navigator.clipboard || typeof ClipboardItem === 'undefined') {
      window.open(currentSrc, '_blank', 'noopener');
      return;
    }
    navigator.clipboard.write([item]).then(function () {
      lbCopyBtn.textContent = 'Copied!';
      setTimeout(function () { lbCopyBtn.textContent = 'Copy image'; }, 1500);
    }).catch(function () {
      window.open(currentSrc, '_blank', 'noopener');
    });
  });

  document.querySelectorAll('.card img').forEach(function (img) {
    var card = img.closest('.card');
    if (!card) return;

    var toast = document.createElement('div');
    toast.className = 'card-toast';
    card.appendChild(toast);

    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'card-copy-btn';
    btn.setAttribute('aria-label', 'Copy image');
    btn.innerHTML = COPY_ICON;
    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      copyImage(img.src, card);
    });
    card.appendChild(btn);

    img.addEventListener('click', function () {
      openLightbox(img.src, card.getAttribute('data-lightbox-title') || img.alt);
    });
  });
})();
</script>
"""

_HEAD = """<meta charset="utf-8">
<title>{title}</title>
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
  <footer class="site-footer">Generated from this repository's own committed snapshots and cards -- see <a href="https://github.com/{GITHUB_REPO}">the source</a> for how.</footer>
</div>
{_FEEDBACK_WIDGET_HTML}
"""
    return _HEAD.format(title=f"{league_name} — {season}", description=f"Race-by-race fantasy analysis and results for the {season} season.", css=_BASE_CSS) + body


def _model_vs_actual_html(row: dict | None, comparison_href: str | None) -> str:
    drill_down = (
        f'<div class="extras-list"><a href="{comparison_href}">Driver-by-driver breakdown &rarr;</a></div>'
        if comparison_href
        else ""
    )
    if row is None:
        empty = '<div class="empty-note">Not in the walk-forward backtest yet -- run <code>f1-fantasy points-backtest</code> to add this round once it has raced.</div>'
        return empty + drill_down
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
{drill_down}
"""


def render_round_html(page: RoundPage, *, league_name: str, season: int) -> str:
    pre_cards = [c for c in page.cards if c.key.split("-")[0] in PRE_RACE_CARDS]
    other_cards = [c for c in page.cards if c not in pre_cards]

    def card_html(c: CardFile) -> str:
        img = f'<img src="../{c.image_rel}" alt="{html.escape(c.title)}" loading="lazy">' if c.image_rel else ""
        caption = f'<div class="card-caption">{c.caption_html}</div>' if c.caption_html else ""
        return f'<div class="card" data-lightbox-title="{html.escape(c.title)}"><div class="card-title">{html.escape(c.title)}</div>{img}{caption}</div>'

    extras_html = ""
    if page.extras:
        links = "".join(f'<a href="../{href}">{html.escape(label)}</a>' for label, href in page.extras)
        extras_html = f'<div class="section-title">Deep dives</div><div class="extras-list">{links}</div>'

    sections = [extras_html] if extras_html else []
    if pre_cards:
        sections.append('<div class="section-title">Pre-race</div><div class="card-grid">' + "".join(card_html(c) for c in pre_cards) + "</div>")

    has_post_race_league_cards = any(c.key in POST_RACE_CARDS for group in page.league_groups for c in group.cards)
    if page.league_groups:
        league_html = "".join(
            f'<div class="league-title">{html.escape(group.league_name)}</div><div class="card-grid">'
            + "".join(card_html(c) for c in group.cards)
            + "</div>"
            for group in page.league_groups
        )
        sections.append('<div class="section-title">Fantasy league visuals</div>' + league_html)
        if not has_post_race_league_cards and page.status == "upcoming":
            sections.append('<div class="empty-note">Recap, winners &amp; losers, and hindsight fill in here once each league\'s round is scored.</div>')

    if has_post_race_league_cards or page.comparison_href:
        sections.append('<div class="section-title">Model vs. actuals</div>' + _model_vs_actual_html(page.model_vs_actual, page.comparison_href))
    if other_cards:
        sections.append('<div class="section-title">Other</div><div class="card-grid">' + "".join(card_html(c) for c in other_cards) + "</div>")

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
{_LIGHTBOX_HTML}
{_FEEDBACK_WIDGET_HTML}
"""
    return _HEAD.format(title=f"{page.event_name} — Round {page.round_number}", description=f"Pre-race analysis and post-race results for the {season} {page.event_name}.", css=_BASE_CSS) + body


def build_site(
    season: int,
    league_name: str,
    *,
    primary_league_id: int | None = None,
    out_dir: Path = Path("out"),
    snapshot_dir: Path = Path("snapshots"),
    backtest_path: Path = Path("data/pace/points_backtest.json"),
    comparison_dir: Path = Path("data/pace/round_comparison"),
    extras_dir: Path = Path("site_extras"),
    dest_root: Path = Path("docs"),
) -> list[RoundPage]:
    dest_root.mkdir(parents=True, exist_ok=True)
    (dest_root / "rounds").mkdir(exist_ok=True)

    pages = build_round_pages(
        season,
        primary_league_id=primary_league_id,
        primary_league_name=league_name,
        out_dir=out_dir,
        snapshot_dir=snapshot_dir,
        backtest_path=backtest_path,
        comparison_dir=comparison_dir,
        extras_dir=extras_dir,
        dest_root=dest_root,
    )
    (dest_root / "index.html").write_text(render_index_html(pages, season=season, league_name=league_name), encoding="utf-8")
    for page in pages:
        (dest_root / "rounds" / f"round-{page.round_number}.html").write_text(
            render_round_html(page, league_name=league_name, season=season), encoding="utf-8"
        )
    return pages
