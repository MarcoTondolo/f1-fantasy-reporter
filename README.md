# F1 Fantasy League Reporter

Pulls your private F1 Fantasy leagues each race weekend and turns them into
designed graphics for the league WhatsApp chat: standings, chip usage, who
changed what at lockout, whose moves paid off, and a pre-race preview with pace
and news.

Reports are rendered as PNGs and emailed to you to forward into the chat.
Nothing posts to WhatsApp automatically — see [Why not auto-post](#why-not-auto-post).

## Status

| Phase | State |
| --- | --- |
| 0 — Access spike (`probe`) | Ready to run, **needs your cookie** |
| 1 — API client, models, snapshot store | Done |
| 2 — Diff engine | Done, 53 tests |
| 3 — Render pipeline + recap card | Recap done; four cards remaining |
| 4 — Actions automation + email | Not started |
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
reliable. Instead, capture a session cookie by hand:

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
other league members' teams?** The leaderboard hands out every member's `guid`,
and the team endpoint takes a `guid` in its path — so it plausibly works for
anyone in the league, but that is unverified until someone runs it against a real
account.

- **Full access** → every report works: lockout changes, chip watch, ownership,
  transfer winners and losers.
- **Partial** → reports cover the readable members and say so on the card.
- **None** → open the league standings page with DevTools, click through to a
  rival's team, and note which request the site makes; that endpoint gets wired
  in. Until then reports fall back to standings only.

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

Everything runs offline against fixtures — no live API, no credentials. The
parser tests deliberately reproduce the API's real payloads including its
misspellings (`FUllName`, `OverallPpints`, `isnonigativetaken`) and its habit of
sending numbers as strings; if the API changes shape, those break first.
