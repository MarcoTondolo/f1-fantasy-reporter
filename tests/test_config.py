"""Credentials.from_env, against a synthetic environment.

Regression coverage for a real production failure: GitHub Actions sets an
env var to the empty string when the referenced secret doesn't exist
(``${{ secrets.SMTP_PORT }}`` with no such secret configured), rather than
leaving it unset. ``os.environ.get(key, default)`` only falls back to
*default* when the key is absent, not when it's present-but-empty, so this
crashed ``int("")`` on every scheduled run for real -- see the "F1 Fantasy
session cookie needs refreshing" issue GitHub Actions kept re-opening,
which was actually this bug the whole time, not an expired cookie.
"""

from __future__ import annotations

from f1_fantasy.config import Credentials


def test_from_env_falls_back_to_the_default_port_when_the_env_var_is_unset(monkeypatch):
    monkeypatch.delenv("SMTP_PORT", raising=False)

    assert Credentials.from_env().smtp_port == 587


def test_from_env_falls_back_to_the_default_port_when_the_env_var_is_empty(monkeypatch):
    """The actual real-world failure mode: the secret exists in name only
    (never configured, or cleared), so GitHub Actions passes down "" rather
    than omitting the variable entirely."""
    monkeypatch.setenv("SMTP_PORT", "")

    assert Credentials.from_env().smtp_port == 587


def test_from_env_uses_a_real_configured_port(monkeypatch):
    monkeypatch.setenv("SMTP_PORT", "2525")

    assert Credentials.from_env().smtp_port == 2525


def test_from_env_falls_back_to_the_default_host_when_the_env_var_is_empty(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "")

    assert Credentials.from_env().smtp_host == "smtp.gmail.com"


def test_from_env_uses_a_real_configured_host(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")

    assert Credentials.from_env().smtp_host == "smtp.example.com"
