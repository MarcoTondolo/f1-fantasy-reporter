"""Configuration: a committed TOML file for choices, environment for secrets.

Secrets never go in the file -- they arrive as environment variables so the same
code runs from a shell with a `.env` and from Actions with repository secrets.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import Field

from f1_fantasy.api.models import Model

DEFAULT_CONFIG_PATH = Path("config.toml")


class Credentials(Model):
    """Secrets, all sourced from the environment."""

    token: str = ""
    guid: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""

    @property
    def can_send_email(self) -> bool:
        return bool(self.smtp_user and self.smtp_password)

    @classmethod
    def from_env(cls) -> "Credentials":
        return cls(
            token=os.environ.get("F1_FANTASY_TOKEN", "").strip(),
            guid=os.environ.get("F1_USER_GUID", "").strip(),
            smtp_host=os.environ.get("SMTP_HOST", "smtp.gmail.com").strip(),
            smtp_port=int(os.environ.get("SMTP_PORT", "587")),
            smtp_user=os.environ.get("SMTP_USER", "").strip(),
            smtp_password=os.environ.get("SMTP_PASS", "").strip(),
        )


class Config(Model):
    """Everything that is a choice rather than a secret."""

    season: int = 2026
    #: League ids to report on. Empty means "every private league you are in".
    leagues: list[int] = Field(default_factory=list)
    #: The league whose reports get published; others are captured but quiet.
    primary_league: int | None = None
    #: Where reports are emailed.
    email_to: str = ""
    #: Local timezone for rendering session times in reports.
    timezone: str = "Europe/London"

    reports: dict[str, bool] = Field(
        default_factory=lambda: {
            "preview": True,
            "lockout": True,
            "recap": True,
            "chips": True,
            "ownership": True,
        }
    )

    #: Directories, relative to the repo root.
    snapshot_dir: Path = Path("snapshots")
    data_dir: Path = Path("data")
    output_dir: Path = Path("out")

    @classmethod
    def load(cls, path: Path | str = DEFAULT_CONFIG_PATH) -> "Config":
        path = Path(path)
        if not path.exists():
            return cls()
        with path.open("rb") as handle:
            return cls(**tomllib.load(handle))

    def wants(self, report: str) -> bool:
        return bool(self.reports.get(report, True))
