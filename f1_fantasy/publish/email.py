"""Email delivery.

The card arrives as an attachment with the caption in the body, so the phone
flow is: open mail, share image into the league chat, long-press the caption,
paste. The caption is also sent as a plain-text part with no wrapping applied,
because a mail client that reflows it would break the line structure the chat
message depends on.
"""

from __future__ import annotations

import logging
import mimetypes
import smtplib
from email.message import EmailMessage

from f1_fantasy.config import Credentials
from f1_fantasy.publish.base import Report

log = logging.getLogger(__name__)


class EmailPublisher:
    def __init__(self, credentials: Credentials, recipient: str) -> None:
        self.credentials = credentials
        self.recipient = recipient

    @property
    def configured(self) -> bool:
        return bool(self.recipient) and self.credentials.can_send_email

    def build_message(self, report: Report) -> EmailMessage:
        message = EmailMessage()
        message["Subject"] = f"[F1 Fantasy] {report.title}"
        message["From"] = self.credentials.smtp_user
        message["To"] = self.recipient
        message.set_content(_body(report))

        for path in report.images:
            if not path.exists():
                log.warning("skipping missing attachment %s", path)
                continue
            guessed, _ = mimetypes.guess_type(path.name)
            maintype, _, subtype = (guessed or "image/png").partition("/")
            message.add_attachment(
                path.read_bytes(),
                maintype=maintype,
                subtype=subtype,
                filename=path.name,
            )

        return message

    def publish(self, report: Report) -> bool:
        if not self.configured:
            log.info("email not configured; %s written to disk only", report.kind)
            return False

        message = self.build_message(report)
        with smtplib.SMTP(self.credentials.smtp_host, self.credentials.smtp_port) as smtp:
            smtp.starttls()
            smtp.login(self.credentials.smtp_user, self.credentials.smtp_password)
            smtp.send_message(message)

        log.info("emailed %s to %s", report.kind, self.recipient)
        return True


def _body(report: Report) -> str:
    return (
        f"{report.title}\n"
        f"{'=' * len(report.title)}\n\n"
        "Caption to paste into the chat:\n"
        "-------------------------------\n"
        f"{report.caption}\n"
        "-------------------------------\n\n"
        f"{len(report.images)} image(s) attached.\n"
    )
