"""refresh_token.py's pure logic: response parsing, .env writing, gh degradation.

`capture_session` itself drives a real, visible browser through a real login
and isn't exercised here -- there's nothing to assert against without an
actual human logging in on an actual machine with a display. These tests
cover the parts that don't need one.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from f1_fantasy.refresh_token import Captured, parse_login_response, push_github_secrets, write_env_file


def test_parse_login_response_reads_the_documented_field_names():
    assert parse_login_response({"Token": "abc123", "GUID": "guid-1"}) == Captured("abc123", "guid-1")


def test_parse_login_response_falls_back_to_lowercase_keys():
    assert parse_login_response({"token": "abc123", "guid": "guid-1"}) == Captured("abc123", "guid-1")


def test_parse_login_response_falls_back_to_subscriber_id_for_guid():
    assert parse_login_response({"Token": "abc123", "SubscriberId": "guid-1"}) == Captured("abc123", "guid-1")


def test_parse_login_response_returns_none_when_a_field_is_missing():
    assert parse_login_response({"Token": "abc123"}) is None
    assert parse_login_response({"GUID": "guid-1"}) is None


def test_parse_login_response_returns_none_for_a_non_dict_body():
    assert parse_login_response(["not", "a", "dict"]) is None
    assert parse_login_response(None) is None


def test_write_env_file_creates_a_new_file(tmp_path: Path):
    env_path = tmp_path / ".env"

    write_env_file(Captured("tok", "guid"), env_path=env_path)

    content = env_path.read_text(encoding="utf-8")
    assert "F1_FANTASY_TOKEN=tok" in content
    assert "F1_USER_GUID=guid" in content


def test_write_env_file_updates_existing_values_in_place_and_keeps_other_lines(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "SMTP_USER=me@example.com\nF1_FANTASY_TOKEN=stale\nF1_USER_GUID=stale-guid\n",
        encoding="utf-8",
    )

    write_env_file(Captured("fresh", "fresh-guid"), env_path=env_path)

    lines = env_path.read_text(encoding="utf-8").splitlines()
    assert "SMTP_USER=me@example.com" in lines
    assert "F1_FANTASY_TOKEN=fresh" in lines
    assert "F1_USER_GUID=fresh-guid" in lines
    assert "F1_FANTASY_TOKEN=stale" not in lines
    assert len(lines) == 3  # updated in place, not appended a second time


def test_push_github_secrets_returns_false_when_gh_is_not_installed(monkeypatch):
    monkeypatch.setattr("f1_fantasy.refresh_token.shutil.which", lambda _name: None)

    assert push_github_secrets(Captured("tok", "guid"), repo="owner/repo") is False


def test_push_github_secrets_returns_true_when_both_secrets_are_set(monkeypatch):
    monkeypatch.setattr("f1_fantasy.refresh_token.shutil.which", lambda _name: "/usr/bin/gh")
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append((cmd, kwargs.get("input")))
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    result = push_github_secrets(Captured("tok", "guid"), repo="owner/repo", runner=fake_runner)

    assert result is True
    assert calls == [
        (["gh", "secret", "set", "--repo", "owner/repo", "F1_FANTASY_TOKEN"], "tok"),
        (["gh", "secret", "set", "--repo", "owner/repo", "F1_USER_GUID"], "guid"),
    ]


def test_push_github_secrets_returns_false_when_gh_secret_set_fails(monkeypatch):
    monkeypatch.setattr("f1_fantasy.refresh_token.shutil.which", lambda _name: "/usr/bin/gh")

    def fake_runner(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="not authenticated")

    assert push_github_secrets(Captured("tok", "guid"), runner=fake_runner) is False
