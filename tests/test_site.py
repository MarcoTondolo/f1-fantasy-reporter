"""site.py's static-site assembly step.

Deliberately network-free and Playwright-free -- these tests build fake
``out/{season}/{round}/league-*/`` trees on disk and check the *data*
`_load_league_groups` assembles, not any rendered pixels.
"""

from __future__ import annotations

from pathlib import Path

from f1_fantasy.site import CardFile, _load_league_groups


def _write_card(league_dir: Path, key: str) -> None:
    league_dir.mkdir(parents=True, exist_ok=True)
    (league_dir / f"{key}.png").write_bytes(b"fake-png")
    (league_dir / f"{key}.txt").write_text(f"caption for {key}", encoding="utf-8")


def test_each_leagues_group_shows_that_leagues_own_cards_not_the_last_ones(tmp_path):
    """Found live on round 12/13's pages: every LeagueCardGroup's ``cards``
    came from whichever league happened to be processed *last* by the
    league-directory scan, because the closing list comprehension referenced
    the bare loop variable ``cards`` (left over from the earlier ``for
    league_dir in ...`` loop) instead of ``groups[league_id]``. DTOUR's
    section showed the primary league's chip/ownership/budget images under
    its own name.
    """
    out_dir = tmp_path / "out"
    round_dir = out_dir / "2026" / "12"
    # Two leagues, each with a genuinely different card set, processed in
    # this order by sorted(round_dir.glob("league-*")): 2623604 before
    # 4512504 -- so 4512504 (the primary) is processed *last*, which is
    # exactly the case that exposed the bug.
    _write_card(round_dir / "league-2623604", "chips")
    _write_card(round_dir / "league-2623604", "ownership")
    _write_card(round_dir / "league-4512504", "budget")

    dest_assets = tmp_path / "assets"
    groups = _load_league_groups(
        2026,
        12,
        out_dir,
        dest_assets,
        primary_league_id=4512504,
        primary_league_name="Ciao Squadra 2026",
        league_names={2623604: "DTOUR 2026"},
        model_cards=[],
    )

    by_id = {g.league_id: g for g in groups}
    assert {c.key for c in by_id[2623604].cards} == {"chips", "ownership"}
    assert {c.key for c in by_id[4512504].cards} == {"budget"}
    # And each card's own image lives under its own league's asset path --
    # not copied from (or pointing at) the other league's directory.
    dtour_chip = next(c for c in by_id[2623604].cards if c.key == "chips")
    assert "league-2623604" in dtour_chip.image_rel
    primary_budget = next(c for c in by_id[4512504].cards if c.key == "budget")
    assert "league-4512504" in primary_budget.image_rel


def test_primary_leagues_flat_cards_are_not_duplicated_by_its_own_league_dir(tmp_path):
    """The primary league's cards may come from two places for the same
    round -- flat out/{season}/{round}/*.png (passed in as model_cards) and
    a legacy out/{season}/{round}/league-{primary_id}/ capture. A key
    present in both must appear once, keeping the flat (live-pipeline)
    version."""
    out_dir = tmp_path / "out"
    round_dir = out_dir / "2026" / "12"
    _write_card(round_dir / "league-4512504", "lockout")
    _write_card(round_dir / "league-4512504", "chips")

    dest_assets = tmp_path / "assets"
    model_cards = [CardFile(key="lockout", title="Teams locked", caption_html=None, image_rel="assets/2026/12/lockout.png")]

    groups = _load_league_groups(
        2026,
        12,
        out_dir,
        dest_assets,
        primary_league_id=4512504,
        primary_league_name="Ciao Squadra 2026",
        league_names={},
        model_cards=model_cards,
    )

    (primary_group,) = groups
    keys = [c.key for c in primary_group.cards]
    assert keys.count("lockout") == 1
    lockout_card = next(c for c in primary_group.cards if c.key == "lockout")
    assert lockout_card.image_rel == "assets/2026/12/lockout.png"  # the flat version, not the league dir's
    assert "chips" in keys
