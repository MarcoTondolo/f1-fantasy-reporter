"""Jinja template -> HTML -> PNG.

The HTML is made fully self-contained before rendering: fonts are inlined as
data URIs and the stylesheet is embedded. That keeps a rendered card identical
in this sandbox, on a laptop, and on an Actions runner -- no dependency on
whatever fonts the host happens to have, which is the usual way generated
graphics drift between environments.
"""

from __future__ import annotations

import base64
import functools
import logging
import os
import re
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup

log = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"
ASSET_DIR = Path(__file__).resolve().parents[2] / "assets"

#: Portrait, close to 4:5 -- the tallest aspect WhatsApp shows without cropping
#: the preview, so the whole card is readable before anyone taps it.
CARD_WIDTH = 1080
#: Rendered at 2x for a crisp result on a phone screen.
SCALE = 2

_FONT_SRC = re.compile(r"url\(['\"]?([^'\")]+\.woff2)['\"]?\)")


@functools.lru_cache(maxsize=1)
def load_css() -> str:
    """Read theme.css with every font reference inlined as a data URI."""
    css = (TEMPLATE_DIR / "theme.css").read_text(encoding="utf-8")

    def inline(match: re.Match[str]) -> str:
        name = Path(match.group(1)).name
        path = ASSET_DIR / "fonts" / name
        if not path.exists():
            log.warning("font %s not found; falling back to system fonts", name)
            return match.group(0)
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"url(data:font/woff2;base64,{encoded}) format('woff2')"

    # Drop the now-redundant format() that follows the original url().
    css = _FONT_SRC.sub(inline, css)
    return re.sub(r"(format\('woff2'\))\s+format\('woff2'\)", r"\1", css)


@functools.lru_cache(maxsize=1)
def environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=True,
        # StrictUndefined so a renamed field fails loudly at render time rather
        # than silently producing a card with a blank where a name should be.
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["signed"] = _signed
    env.filters["points"] = _points
    return env


def _signed(value: float | int | None) -> str:
    """Format a delta with an explicit sign, so polarity never rests on colour."""
    if value is None:
        return "-"
    rounded = round(float(value), 1)
    if rounded == 0:
        return "0"
    trimmed = f"{abs(rounded):g}"
    return f"+{trimmed}" if rounded > 0 else f"-{trimmed}"


def _points(value: float | int | None) -> str:
    if value is None:
        return "-"
    return f"{round(float(value), 1):g}"


def _chromium_path() -> str | None:
    """Use a preinstalled Chromium when one is provided.

    Some environments ship a browser whose build number does not match the
    installed Playwright, which makes the bundled resolver fail even though a
    perfectly good Chromium is sitting there. ``F1_CHROMIUM_PATH`` overrides;
    otherwise a browser under ``PLAYWRIGHT_BROWSERS_PATH`` is picked up.
    Returning None lets Playwright resolve its own download as normal.
    """
    override = os.environ.get("F1_CHROMIUM_PATH")
    if override:
        return override

    root = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers"))
    if not root.exists():
        return None

    candidates = sorted(root.glob("chromium-*/chrome-linux/chrome"), reverse=True)
    return str(candidates[0]) if candidates else None


def render_html(template: str, context: dict[str, Any]) -> str:
    """Render a template to a self-contained HTML string.

    The stylesheet is wrapped in Markup so autoescaping leaves it alone -- it is
    our own file, and escaping it silently produces a card with no styling at
    all rather than an error.
    """
    tpl = environment().get_template(template)
    return tpl.render(css=Markup(load_css()), **context)


def render_card(
    template: str,
    context: dict[str, Any],
    output: Path | str,
    *,
    width: int = CARD_WIDTH,
    scale: int = SCALE,
) -> Path:
    """Render a template and screenshot its ``.card`` element to a PNG.

    Clipping to the element rather than the viewport lets a card grow with its
    content -- a twelve-member league produces a taller image than a four-member
    one, instead of being cut off at a fixed height.
    """
    from playwright.sync_api import sync_playwright  # imported lazily: heavy

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    html = render_html(template, context)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            args=["--force-color-profile=srgb"],
            executable_path=_chromium_path(),
        )
        try:
            page = browser.new_page(
                viewport={"width": width, "height": 1350},
                device_scale_factor=scale,
            )
            page.set_content(html, wait_until="load")
            # Fonts are embedded, but layout still settles after they parse.
            page.evaluate("document.fonts.ready")
            card = page.query_selector(".card")
            if card is None:
                raise RuntimeError(f"{template} rendered without a .card element")
            card.screenshot(path=str(output))
        finally:
            browser.close()

    log.info("rendered %s", output)
    return output
