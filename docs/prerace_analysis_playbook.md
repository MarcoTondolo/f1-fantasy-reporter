# Pre-race deep-dive playbook

How to reproduce the round-by-round analysis this project can do beyond its
own automated cards: track character, tyre strategy, Safety Car/VSC/red-flag
risk, grid-conditioned expected points for a Final Fix or captain call, and a
pace-progression visual across the weekend's sessions. None of this is wired
into a single CLI command yet -- it is a sequence of real, tested library
calls against public data, run interactively. Wire it into a command if it
turns out to be a recurring ask rather than an occasional one.

**How to use this each race weekend:**
1. Confirm the repo is attached and up to date (`git -C
   /home/user/f1-fantasy-reporter pull --ff-only`), or clone it fresh --
   see `README.md`'s own setup section for the venv/`pip install -e .` steps.
2. If the token has just been refreshed after an `AuthExpired` failure,
   trigger the missed run manually rather than waiting for the next hourly
   tick -- see "Catching up automation" below.
3. Work through the sections below in order; each one is a handful of real
   commands, not a framework to build.

## Catching up automation after a token refresh

The hourly `tick` job stays broken until the `F1_FANTASY_TOKEN`/
`F1_USER_GUID` repo secrets are updated (see the "Flag an expired session"
step in `.github/workflows/f1-fantasy.yml`). Once they're fixed, don't wait
for the next cron tick -- force the missed action immediately:

```
# via the GitHub MCP tools, or the Actions tab's "Run workflow" button
mcp__github__actions_run_trigger(
    method="run_workflow", owner="tiptoptopher", repo="f1-fantasy-reporter",
    workflow_id="f1-fantasy.yml", ref="claude/f1-fantasy-league-reports-wemivn",
    inputs={"action": "lockout", "round": "<current round>"},
)
```

`f1-fantasy plan` (run locally, no token needed) says what the calendar
currently considers due if you're not sure which action to force. Poll
`mcp__github__actions_list` (`list_workflow_jobs`) for the run to finish,
then `git pull --ff-only` to bring the freshly committed snapshots and cards
down locally.

## Your current teams

The lockout snapshot (`snapshots/<season>/<league_id>/<round>/locked.json`)
carries every league member's roster. Find yourself by GUID (the same GUID
across every team you run -- team number distinguishes them), not by name:

```python
import json
d = json.load(open(f"snapshots/{season}/{primary_league_id}/{round}/locked.json"))
you = next(m["guid"] for m in d["members"] if m["user_name"] == "Chris Krueger")
for key, team in d["teams"].items():
    if team["guid"] == you:
        print(team["team_name"], team["picks"], team["chips"], team["bank"], team["value"])
```

`primary_league_id` is `config.toml`'s `primary_league` (4512504, "Ciao
Squadra 2026"). `d["players"]` maps `player_id` to `{tla, full_name,
constructor, price, season_points, selected_pct, captain_pct}` for the
name/price lookups.

## Track character

`f1_fantasy/pace/hers.py`'s clipping-severity and year-on-year functions are
public (FastF1-backed) and work for any already-raced round:

```python
from f1_fantasy.pace.hers import circuit_clipping_severity, round_clipping_by_team, matched_circuits, year_on_year_deltas

sev = circuit_clipping_severity(season, round_number)          # 0-1, ranks vs data/pace/hers_backtest_*.json's 12-circuit table
by_team = round_clipping_by_team(season, round_number)          # who's most power-limited here

mc = matched_circuits(season - 1, season)                       # circuit -> (round_last_year, round_this_year)
deltas = year_on_year_deltas(season - 1, season, {circuit: mc[circuit]})  # team -> seconds lost/gained
```

Read `data/pace/hers_backtest_2026_r1-12.json` for the already-ranked
comparison set (Monaco lowest clipping severity, Spa highest) so a fresh
number has context. `pace/track_profile.py::build_track_profile` gives full
corner/straight geometry from a session's fastest lap if you need more than
the clipping summary.

## Tyre strategy

Degradation slope per compound, from the weekend's own practice long runs
(raw, not fuel-corrected -- treat a small or negative slope as "low
degradation," not literally "gets faster with age"):

```python
import fastf1, numpy as np
fastf1.Cache.enable_cache(".fastf1-cache")
frames = [fastf1.get_session(season, round_number, s).load(telemetry=False, weather=False, messages=False) or fastf1.get_session(season, round_number, s).laps.assign(Session=s) for s in ("FP1", "FP2", "FP3")]
# group by Compound, keep IsAccurate laps within 5% of that compound's median, np.polyfit(TyreLife, LapSeconds, 1)
```

For the stop-count pattern, pull last year's race at the same circuit and
count `laps.groupby("Driver")["Stint"].max() - 1`.

## Safety Car / VSC / red-flag risk

Circuit-specific history beats the season's flat base rate -- check both:

```python
s = fastf1.get_session(season, "<circuit name>", "R")
s.load(laps=False, telemetry=False, weather=False, messages=True)
msgs = s.race_control_messages
has_sc = (msgs["Message"].str.contains("SAFETY CAR DEPLOYED", case=False, na=False)
          & ~msgs["Message"].str.contains("VIRTUAL", case=False, na=False)).any()
has_vsc = msgs["Message"].str.contains("VIRTUAL SAFETY CAR DEPLOYED", case=False, na=False).any()
has_red = (msgs["Message"].str.strip() == "RED FLAG").any()   # exact match -- "CHEQUERED FLAG" contains the substring "RED FLAG"
```

Run this across the last 4-5 years at the same circuit for the circuit's own
rate, and across the current season's rounds so far for the baseline to
compare it against.

## Grid-conditioned expected points (the Final Fix / captain question)

`predict.points.build_round_distributions` simulates its *own* qualifying
from season form, so it mostly never puts a fast car at the back -- it
can't answer "given the grid we now actually have, what does the race look
like." Once qualifying has happened, use
`predict.points.build_grid_conditioned_distributions` instead (added
specifically for this -- it fetches the real grid via the public
`results.fetch_qualifying` and holds it fixed while still simulating DNF,
race order, fastest lap/DOTD, and overtakes):

```python
from f1_fantasy.predict.points import build_grid_conditioned_distributions

dist = build_grid_conditioned_distributions(season, list(range(1, round_number)), round_number, n_samples=10000, seed=0)
for code, d in sorted(dist.items(), key=lambda kv: -kv[1].mean):
    print(code, d.mean, d.p10, d.p90, d.p_dnf)   # p10/p90 show the spread a mean alone hides
```

Raises `ValueError` if qualifying for that round hasn't happened yet --
that's deliberate, not a bug to work around; use
`build_round_distributions` until it has. Compare `p10`/`p90` before
recommending a captain or a Final Fix swap: a high mean with a low `p10` is
a real bet, not a safe one, especially for a captain's 2x multiplier.
Cross-check against `predict.reconcile.reconstruct_points` for the last
3-4 rounds' *actual* scoring (recent-form signal, independent of the
model) and `predict.form.rolling_form`'s own rank for the season-long
signal -- the three rarely disagree by much, and when they do it's worth
knowing which one is the outlier before trusting any of them alone.

## Pace-progression visual

For an interactive FP1-through-Q (through race, once it's run) pace-ladder
chart like the one built for round 15: pull each session's best lap per
driver (`IsAccurate` laps only for practice, best across Q1-Q3 for
qualifying), compute each driver's gap to that session's fastest, and plot
as a slope/bump chart -- x = session, y = gap to session-fastest (inverted,
0 at top), one line per driver colored by `f1_fantasy.render.teams.team_color`
(this project's own team-color table, so it matches every other card). With
22 drivers sharing 11 hues, direct end-labels (never color-only) are
required, not optional -- this project's own `assets/teams.json` says so
explicitly ("Haas grey vs Alpine pink is deutan dE 1.0"). A dense cluster of
drivers within a few tenths needs a label-collision pass (sort by natural
y, push down to a minimum gap, draw a thin leader line back to the true
point) or the labels overlap into an unreadable stack -- confirmed live on
round 15's midfield cluster before publishing. The `dataviz` skill's
color/form/interaction rules apply in full; this project's own
`f1_fantasy/render/templates/theme.css` is the design system to match
(dark broadcast-graphic palette, Barlow Condensed display + Inter body, no
light-mode variant -- one committed look, same as every rendered card).

## Verification

Before trusting a fresh number, sanity-check it the way this project checks
everything else: does the grid-conditioned mean actually differ from the
unconditioned one in the direction the real grid implies (a car starting
worse than its form suggests should show *higher* variance and a similar or
higher mean from added overtaking upside, not simply a lower mean -- see
`build_grid_conditioned_distributions`'s own docstring)? Does the clipping
severity number fall in a plausible spot on the already-ranked 12-circuit
table rather than as an outlier with no explanation? Does the SC/VSC rate
use an exact-match red-flag check, not a substring one (`"CHEQUERED FLAG"`
contains `"RED FLAG"` as a literal substring -- a real bug hit and fixed
live while building this). Run `pytest tests/test_predict_points.py -q`
after touching `predict/points.py` -- `build_grid_conditioned_distributions`
has its own tests (fixed-grid respected verbatim, missing-driver raises,
conditioning changes the outcome in the expected direction, no-qualifying-
yet raises) that should never regress.
