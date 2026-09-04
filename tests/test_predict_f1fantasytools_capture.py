"""f1fantasytools.com screenshot + vision extraction, against synthetic
Anthropic API responses and monkeypatched Playwright/HTTP calls -- no real
network or browser, matching this project's offline-fixture convention.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from f1_fantasy.predict import f1fantasytools_capture as capture_module
from f1_fantasy.predict.f1fantasytools_capture import (
    capture_f1fantasytools_snapshot,
    capture_screenshot,
    extract_driver_points,
)


class FakeResponse:
    def __init__(self, payload: dict, *, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)

    def json(self) -> dict:
        return self._payload


def _text_response(text: str) -> FakeResponse:
    return FakeResponse({"content": [{"type": "text", "text": text}]})


def test_extract_driver_points_skips_the_api_call_with_no_key(monkeypatch, tmp_path):
    image = tmp_path / "shot.png"
    image.write_bytes(b"not a real png")
    called = False

    def fake_post(*args, **kwargs):
        nonlocal called
        called = True
        return _text_response("{}")

    monkeypatch.setattr(capture_module.httpx, "post", fake_post)

    assert extract_driver_points(image, api_key="") == {}
    assert not called


def test_extract_driver_points_returns_empty_for_a_missing_image(monkeypatch):
    def fake_post(*args, **kwargs):
        raise AssertionError("should not be called for a missing image")

    monkeypatch.setattr(capture_module.httpx, "post", fake_post)

    assert extract_driver_points("/no/such/file.png", api_key="sk-test") == {}


def test_extract_driver_points_parses_a_clean_json_response(monkeypatch, tmp_path):
    image = tmp_path / "shot.png"
    image.write_bytes(b"fake png bytes")
    monkeypatch.setattr(
        capture_module.httpx, "post", lambda *a, **k: _text_response('{"VER": 185, "NOR": 172.5}')
    )

    entries = extract_driver_points(image, api_key="sk-test")

    assert entries == {"VER": 185.0, "NOR": 172.5}


def test_extract_driver_points_strips_markdown_code_fences(monkeypatch, tmp_path):
    image = tmp_path / "shot.png"
    image.write_bytes(b"fake png bytes")
    fenced = '```json\n{"HAM": 150}\n```'
    monkeypatch.setattr(capture_module.httpx, "post", lambda *a, **k: _text_response(fenced))

    assert extract_driver_points(image, api_key="sk-test") == {"HAM": 150.0}


def test_extract_driver_points_returns_empty_on_malformed_json(monkeypatch, tmp_path):
    image = tmp_path / "shot.png"
    image.write_bytes(b"fake png bytes")
    monkeypatch.setattr(capture_module.httpx, "post", lambda *a, **k: _text_response("not json at all"))

    assert extract_driver_points(image, api_key="sk-test") == {}


def test_extract_driver_points_ignores_non_numeric_entries_but_keeps_the_rest(monkeypatch, tmp_path):
    image = tmp_path / "shot.png"
    image.write_bytes(b"fake png bytes")
    monkeypatch.setattr(
        capture_module.httpx,
        "post",
        lambda *a, **k: _text_response(json.dumps({"VER": 185, "NOR": "n/a"})),
    )

    assert extract_driver_points(image, api_key="sk-test") == {"VER": 185.0}


def test_extract_driver_points_returns_empty_when_the_api_call_raises(monkeypatch, tmp_path):
    image = tmp_path / "shot.png"
    image.write_bytes(b"fake png bytes")

    def raising_post(*args, **kwargs):
        raise httpx.ConnectError("blocked")

    monkeypatch.setattr(capture_module.httpx, "post", raising_post)

    assert extract_driver_points(image, api_key="sk-test") == {}


def test_extract_driver_points_logs_the_response_body_on_a_400(monkeypatch, tmp_path, caplog):
    """Regression test: the real first run hit a 400 Bad Request (an
    oversized image) and the original code only logged the status line,
    not the body -- which is where the actual validation error message
    lives, and is what made this failure hard to diagnose from the logs
    alone."""
    image = tmp_path / "shot.jpg"
    image.write_bytes(b"fake jpeg bytes")

    request = httpx.Request("POST", capture_module.ANTHROPIC_API_URL)
    response = httpx.Response(400, json={"error": {"message": "image exceeds 5 MB maximum"}}, request=request)

    def fake_post(*args, **kwargs):
        return response

    monkeypatch.setattr(capture_module.httpx, "post", fake_post)

    with caplog.at_level("WARNING"):
        result = extract_driver_points(image, api_key="sk-test")

    assert result == {}
    assert "image exceeds 5 MB maximum" in caplog.text


def test_capture_screenshot_returns_none_when_playwright_fails(monkeypatch, tmp_path):
    def raising_sync_playwright():
        raise RuntimeError("no browser available")

    import playwright.sync_api

    monkeypatch.setattr(playwright.sync_api, "sync_playwright", raising_sync_playwright)

    assert capture_screenshot("https://example.invalid", tmp_path / "shot.png") is None


def test_capture_screenshot_falls_back_to_viewport_only_when_full_page_is_oversized(monkeypatch, tmp_path):
    """Regression test: the real first run against f1fantasytools.com
    produced a 4.67MB full-page PNG, which the Anthropic API rejected
    with a 400 (payload too large). A fake Playwright page whose
    full-page capture exceeds MAX_IMAGE_BYTES must trigger the
    viewport-only retry, landing under the limit."""
    import playwright.sync_api

    out_path = tmp_path / "shot.jpg"

    class FakePage:
        def goto(self, *a, **k):
            pass

        def wait_for_timeout(self, *a, **k):
            pass

        def screenshot(self, *, path, full_page, type, quality):
            data = b"x" * (capture_module.MAX_IMAGE_BYTES + 1000) if full_page else b"x" * 1000
            Path(path).write_bytes(data)

    class FakeBrowser:
        def new_page(self, **kwargs):
            return FakePage()

        def close(self):
            pass

    class FakeChromium:
        def launch(self, **kwargs):
            return FakeBrowser()

    class FakePlaywrightContext:
        chromium = FakeChromium()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(playwright.sync_api, "sync_playwright", lambda: FakePlaywrightContext())

    result = capture_screenshot("https://example.invalid", out_path)

    assert result == out_path
    assert out_path.stat().st_size <= capture_module.MAX_IMAGE_BYTES


def test_capture_f1fantasytools_snapshot_is_none_when_the_screenshot_fails(monkeypatch):
    monkeypatch.setattr(capture_module, "capture_screenshot", lambda *a, **k: None)

    def fail_extract(*args, **kwargs):
        raise AssertionError("should not attempt extraction without a screenshot")

    monkeypatch.setattr(capture_module, "extract_driver_points", fail_extract)

    assert capture_f1fantasytools_snapshot(2026, 13, api_key="sk-test") is None


def test_capture_f1fantasytools_snapshot_is_none_when_nothing_is_extracted(monkeypatch, tmp_path):
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"fake")
    monkeypatch.setattr(capture_module, "capture_screenshot", lambda *a, **k: shot)
    monkeypatch.setattr(capture_module, "extract_driver_points", lambda *a, **k: {})

    assert capture_f1fantasytools_snapshot(2026, 13, api_key="sk-test") is None


def test_capture_f1fantasytools_snapshot_builds_a_real_snapshot_on_success(monkeypatch, tmp_path):
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"fake")
    monkeypatch.setattr(capture_module, "capture_screenshot", lambda *a, **k: shot)
    monkeypatch.setattr(capture_module, "extract_driver_points", lambda *a, **k: {"VER": 185.0, "NOR": 172.5})

    snapshot = capture_f1fantasytools_snapshot(2026, 13, api_key="sk-test", session_label="FP2")

    assert snapshot is not None
    assert snapshot.source == "f1fantasytools"
    assert snapshot.season == 2026
    assert snapshot.round_number == 13
    assert snapshot.session_label == "FP2"
    assert snapshot.entries == {"VER": 185.0, "NOR": 172.5}
