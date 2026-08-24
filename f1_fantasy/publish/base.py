"""The publisher interface.

Delivery is human-in-the-loop: reports are emailed to you and you forward them
into the league chat. Posting to WhatsApp automatically is not possible without
violating its terms -- Meta's official Groups API only covers groups the business
itself created, capped at eight members, and everything else drives a real
account against the Web protocol at risk of a ban.

The interface exists anyway so that decision stays reversible without touching
any report code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from f1_fantasy.api.models import Model


class Report(Model):
    """One finished report, ready to send."""

    #: Short identifier, e.g. "recap" or "lockout".
    kind: str
    #: Subject line / headline.
    title: str
    #: The text to paste into the chat alongside the image.
    caption: str
    #: Rendered card images, in the order they should be sent.
    images: list[Path] = []


class Publisher(Protocol):
    def publish(self, report: Report) -> bool:
        """Deliver *report*. Returns whether it actually went out."""
        ...


class NullPublisher:
    """Writes nothing anywhere. Used by --dry-run and when email is unconfigured."""

    def __init__(self) -> None:
        self.published: list[Report] = []

    def publish(self, report: Report) -> bool:
        self.published.append(report)
        return False
