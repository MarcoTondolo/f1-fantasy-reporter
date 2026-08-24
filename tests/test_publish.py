"""Email assembly. No SMTP connection is made."""

from __future__ import annotations

from f1_fantasy.config import Credentials
from f1_fantasy.publish.base import NullPublisher, Report
from f1_fantasy.publish.email import EmailPublisher


def _credentials() -> Credentials:
    return Credentials(smtp_user="me@example.com", smtp_password="app-password")


def _report(tmp_path) -> Report:
    image = tmp_path / "recap.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n fake")
    return Report(
        kind="recap",
        title="Dutch Grand Prix — Race recap",
        caption="🏁 *Dutch Grand Prix*\nTop 3\n1. Kaz — 1222",
        images=[image],
    )


def test_message_carries_the_caption_and_the_image(tmp_path):
    publisher = EmailPublisher(_credentials(), "chris@example.com")

    message = publisher.build_message(_report(tmp_path))

    assert message["To"] == "chris@example.com"
    assert "Dutch Grand Prix" in message["Subject"]

    attachments = list(message.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "recap.png"

    body = message.get_body(preferencelist=("plain",)).get_content()
    assert "1. Kaz — 1222" in body


def test_caption_survives_intact_for_pasting(tmp_path):
    """Line structure is the chat message -- a reflowed caption is a broken one."""
    publisher = EmailPublisher(_credentials(), "chris@example.com")
    report = _report(tmp_path)

    body = publisher.build_message(report).get_body(preferencelist=("plain",)).get_content()

    for line in report.caption.split("\n"):
        assert line in body


def test_missing_attachment_is_skipped_not_fatal(tmp_path):
    """A render that failed must not also cost you the caption."""
    publisher = EmailPublisher(_credentials(), "chris@example.com")
    report = _report(tmp_path)
    report.images.append(tmp_path / "does-not-exist.png")

    message = publisher.build_message(report)

    assert len(list(message.iter_attachments())) == 1


def test_publisher_reports_itself_unconfigured_without_credentials(tmp_path):
    assert not EmailPublisher(Credentials(), "chris@example.com").configured
    assert not EmailPublisher(_credentials(), "").configured
    assert EmailPublisher(_credentials(), "chris@example.com").configured


def test_null_publisher_records_but_does_not_send(tmp_path):
    publisher = NullPublisher()

    assert publisher.publish(_report(tmp_path)) is False
    assert len(publisher.published) == 1
