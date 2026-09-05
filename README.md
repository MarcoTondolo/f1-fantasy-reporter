# F1 Fantasy League Reporter

Pulls your private F1 Fantasy leagues each race weekend and turns them into
designed graphics for the league WhatsApp chat: standings, chip usage, who
changed what at lockout, whose moves paid off, who's carrying the biggest
budget cap, and a pre-race preview with pace and news.

Reports are rendered as PNGs and emailed to you to forward into the chat.
Nothing posts to WhatsApp automatically — see [Why not auto-post](#why-not-auto-post).

## Status

| Phase | State |
| --- | --- |
| 0 — Access spike (`probe`) | Done. `getteam` cannot read other members (silently echoes the caller's own team); the real opponent endpoints were found via DevTools and are wired in and verified against live data |
| 1 — API client, models, snapshot store | Done, confirmed against the live API |
| 2 — Diff engine | Done |
| 3 — Render pipeline + cards | Recap, lockout, chips, ownership, budget cap, winners & losers done; preview remaining |
| 4 — Actions automation + email | Done; `probe` runs green in Actions |
| 5 — News and articles | Not started |
| 6 — Pace dataset (FastF1) | Not started |

## Getting started

```bash
pip install -e ".[dev]"
playwright install chromium          # skip if a Chromium is already provided
cp config.example.toml config.toml   # then edit
```

### Credentials

F1 accounts sit behind Imperva bot protection, so scripted password login is not
reliable. Instead, capture a session cookie -- either by hand, or with
`refresh-token`, which automates everything except the login itself:

```bash
f1-fantasy refresh-token --github-repo yourname/f1-fantasy-reporter
```

This opens a real, visible Chromium window. Log in exactly as you always
have -- any 2FA/challenge is solved by you, in a real browser, so it never
fights Imperva. Once the login succeeds, it captures `Token`/`GUID` from the
same `/services/session/login` response a human would otherwise find in
DevTools, writes them to `.env`, and (with `--github-repo`, and the `gh` CLI
installed and authenticated) pushes them to that repo's `F1_FANTASY_TOKEN`/
`F1_USER_GUID` secrets too. Run it locally, on a machine with a display --
not from CI, which is exactly the headless/datacenter traffic profile
Imperva is built to block, and has no display for a login form anyway.

To do it fully by hand instead:

1. Open <https://fantasy.formula1.com> and log in.
2. DevTools (F12) → **Network**.
3. Reload, and find the request to `/services/session/login`.
4. From its **Response**, copy `Token` and `GUID`.

```bash
export F1_FANTASY_TOKEN="<Token>"
export F1_USER_GUID="<GUID>"
```

**The token lasts roughly five days.** That is the main operating cost of this
tool. When it expires the CLI says so explicitly and prints these steps again,
rather than failing with a stack trace.

### First run

```bash
f1-fantasy probe
```

`probe` answers the question the whole design depends on: **can this account read
other league members' teams?** As of 2026-08-25: **not via `getteam`** — that
endpoint silently returns the caller's own team for every guid requested, which
`probe` initially mistook for success until it was corrected to compare content
rather than just check for a non-empty response (see below). The real endpoint
was located via a DevTools capture on the live site:
`/services/user/opponentteam/opponentgamedayget/1/{guid}/{team_no}` — response
shape not yet captured, so it isn't wired in yet. Re-run `probe` any time
credentials change or you want to sanity-check a league; it's also how three
real payload-shape bugs in the parsers got caught and fixed on first contact
with the live API (see "Endpoints confirmed live" below).

### Seeing a card without credentials

```bash
f1-fantasy demo
```

Renders the recap card from synthetic data to `out/demo/`. Useful for judging
layout without waiting for a race weekend.

## Commands

| Command | What it does |
| --- | --- |
| `probe` | Check how much league data is readable |
| `doctor` | Validate credentials, config and delivery |
| `capture --phase {pre_lock,locked,final}` | Write a snapshot of each league |
| `plan` | Print what the calendar says is due (used by CI to gate) |
| `tick [--force X] [--dry-run]` | Run whatever is due |
| `demo` | Render cards from synthetic data |

## How it works

The API only ever returns *current* state, so "what changed since last race" can
only be answered from history the tool captured itself. Snapshots are plain JSON
committed to the repo:

```
snapshots/{season}/{league_id}/{race_id}/{phase}.json
```

Three phases per race — `pre_lock`, `locked`, `final` — feed two derived reports:

- **Lockout report** — diff this race's locked teams against last race's:
  transfers, captain changes, chips played, team value movement.
- **Winners and losers** — once points land, value every player moved in against
  every player moved out.

On scoring transfers: when someone makes a single swap it is fair to say "X out,
Y in, +14". When they make five on a wildcard there is no principled way to say
which incoming player replaced which outgoing one, so the delta is always
computed in aggregate and the head-to-head framing is only used when it is
genuinely one swap.

**What is and isn't backfillable:** standings history comes back via
`getusergamedaysv1`, which is keyed by race across the whole season. Team-change
history does **not** — the API exposes no past lineups. Change reports start
accumulating from the first run onward.

### Endpoints confirmed live

The API is undocumented; every field name and envelope shape below started as
an inference from a third-party client's source, and every one of the first
three turned out to need a correction on first contact with the real service —
tracked here rather than left as folklore.

| Endpoint | Envelope | Confirmed |
| --- | --- | --- |
| `getusergamedaysv1` | `Data.Value` is a **bare list**, not `{"data": [...]}` | Live, 2026-08-25 |
| `.../1/0/0/privateleague` | `Data.Value.Details` — mixes in F1's own promotional league (`isJoined: 0`), filtered out | Live, 2026-08-25 |
| `.../getuserleague/1` | `Data.Value.leaguesdata` — its own distinct misspelling, `memeberCount` | Live, 2026-08-25 |
| `pvtleagueuserrankget` (leaderboard) | `Data.Value.{leagueInfo, memRank}` | Live, 2026-08-25 — correctly differentiated per member (real distinct names, ranks, points) |
| `getteam`, **own team only** | `Data.Value.{mdid, userTeam}` | Live, 2026-08-25 |
| `getteam`, **another member's team** | same shape, **but ignores the requested guid** | **Broken, confirmed 2026-08-25 — see below** |
| `opponentteam/opponentgamedayget` (another member's points + chip state) | `Data.Value` bare dict, CamelCased chip keys unlike every other endpoint | Live, 2026-08-25 — genuinely reads another member, confirmed via DevTools capture |
| `opponentteam/opponentgamedayplayerteamget` (another member's actual picks) | Same `{mdid, userTeam}` shape as `getteam`'s own response — a real sibling backend action, not a rewrite | Live, 2026-08-25 — confirmed via DevTools capture; `parse_teams` needed no changes |
| `/feeds/drivers/{race}_en.json` | Public, `Value` is a list of player dicts | Not exercised by `probe` — first real test is the first rendered card against live data |

`probe` deliberately dumps raw JSON to the Actions log when a result looks
suspicious (zero leagues, zero members, zero picks) rather than trusting an
empty parse — that's what caught the first three rows above. If a future run
hits one of the "not yet confirmed" rows and something looks off, the same
dump will show up in the log; update this table and the corresponding parser
together.

### `getteam` does not actually support reading another member's team — resolved

**Correction to an earlier claim in this file:** `probe`'s first live run
reported "Full access" — but that verdict was a false positive from an
inadequate check (a non-empty response, not a content comparison). A
subsequent live `showcase` run against all three of the account's leagues
showed something `probe` never checked for: **every one of 35 distinct
member rows across three leagues returned byte-for-byte the same two
teams** — the caller's own. `getteam` silently ignores the `guid` in its URL
path and always serves the authenticated session's own team, regardless of
whose guid was requested.

`probe` was corrected to catch this itself: it compares each fetched team's
`(team_name, sorted player_ids)` against the caller's own before calling it
"readable", and reports a distinct `echoing own` count. Separately, DevTools
captures of the site's own team pages turned up the real fix: a dedicated
`opponentteam/` namespace (`opponentgamedayget` for points and chip state,
`opponentgamedayplayerteamget` for actual picks) that genuinely reads
another member's data. `collect_league` now routes the authenticated
account's own guid through `getteam` and every other member through
`opponentteam` — confirmed on live data across all three leagues: member
rows now carry genuinely distinct picks, points and captains (the one
remaining repeated name in any league is the account holder's own second
team, a real and separate case — see multi-team below).

**Still open:** `try_teams`/`try_opponent_teams` degrade to "no data" for an
unreadable member rather than raising, so a league with genuinely private
members (if that setting exists) fails safely, but this hasn't been observed
live — every member probed so far has been readable via the opponent
endpoint.

## Design notes

Cards render to fixed PNGs for a chat thread, so there is one committed look
rather than light/dark theming.

Constructor colours are used **only** as identity rails and dots beside a written
team name, never to encode a value. That is measured, not a preference: run as a
series palette, the ten team colours fail CVD separation badly — Haas grey against
Alpine pink is deutan ΔE 1.0, effectively identical, and Haas against Williams is
11.8 even with full colour vision. Magnitude uses one sequential blue ramp;
gains and losses use a diverging pair and always carry a sign or arrow so
direction never rests on colour alone.

Fonts (Barlow Condensed, Inter) are vendored in `assets/` and inlined as data
URIs at render time, so a card looks identical on a laptop and on a CI runner.

## Why not auto-post

Meta's official WhatsApp Groups API only covers groups the business itself
creates, capped at 8 members — it cannot post into an existing league chat.
Everything that can (Baileys, whatsapp-web.js, hosted gateways) drives a real
WhatsApp account against the Web protocol, violates the ToS, and risks a ban on
the number.

So delivery is human-in-the-loop: the tool emails you the PNG and a ready-to-paste
caption, and you forward it. The publisher sits behind an interface, so an
auto-posting adapter could be added later without touching report code.

## Privacy

Snapshots and rendered cards contain your league members' real names.
**Keep this repository private.** If it is ever made public, strip `snapshots/`
and `out/` from history first.

## Tests

```bash
pytest
```

All 126 run offline against fixtures — no live API, no credentials. The
parser tests deliberately reproduce the API's real payloads including its
misspellings (`FUllName`, `OverallPpints`, `isnonigativetaken`) and its habit of
sending numbers as strings; if the API changes shape, those break first.

## Scheduling

One hourly workflow that asks the race calendar what is due, rather than a cron
line per report. Sessions get rescheduled, sprint weekends lock a day earlier at
the Sprint rather than at Qualifying, and clocks shift twice a year — a fixed
schedule quietly reports at the wrong time through all of that.

| Report | Fires |
| --- | --- |
| Pace | After final practice, before lockout |
| Preview | ~24h before lockout |
| Lockout | 15 min after lockout, letting last-second edits settle |
| Recap | Once race points stop moving between reads |

Windows close after a grace period, so a tool that was offline for a week does
not wake up and post a preview for a race that has already run. Actions are
recorded in `state.json` only on success, so a failure stays due and retries on
the next tick.

The recap waits for two consecutive identical point reads rather than assuming a
fixed delay after the flag: penalties and classification changes move fantasy
points hours later, and a recap published from provisional numbers has to be
corrected in the chat.

### Repository secrets

| Secret | Purpose |
| --- | --- |
| `F1_FANTASY_TOKEN` | Session cookie (refresh ~every 5 days) |
| `F1_USER_GUID` | Your account guid |
| `SMTP_USER` / `SMTP_PASS` | Gmail address and **app password** |
| `SMTP_HOST` / `SMTP_PORT` | Optional, default to Gmail |

### A caution on league size: `max_team_fetches`

"Private" only means invite-only. A community league you've joined can carry
thousands of members even though it isn't public, and `collect_league` fetches
one member's team per HTTP request. Confirmed live on this tool's own first
real run: one of the three leagues on this account reported `"memberCount":
"10k+"`, and an uncapped fetch loop stalled a CI job for minutes against it
before being caught and cancelled.

`config.max_team_fetches` (default **15**) caps how many members, by rank,
get their team pulled per capture. Standings still cover every member
regardless — that's one cheap call, not one per member — only the per-member
detail behind lockout, chips, ownership and winners/losers is capped. Raise it
in `config.toml` for a league you've confirmed is small enough to want full
coverage of; the `showcase` command also takes a one-off `--top N` override.

When a run fails on authentication the workflow opens (or comments on) an issue
with the refresh steps, rather than leaving a red X in the Actions tab — an
expired cookie is a routine five-day event, not a crash.
