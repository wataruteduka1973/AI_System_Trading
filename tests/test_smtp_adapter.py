"""Unit 8 (docs/plans/horizon5-implementation-plan.md): SmtpNotificationAdapter
builds a correct EmailMessage and drives smtplib.SMTP as documented, with
`smtplib.SMTP` mocked (no real network I/O).
"""

from unittest.mock import MagicMock, patch

from app.notifications.adapters.smtp import SmtpConfig, SmtpNotificationAdapter


def _config(**overrides: object) -> SmtpConfig:
    defaults: dict[str, object] = dict(
        host="smtp.example.com",
        port=587,
        username=None,
        password=None,
        use_tls=True,
        sender_address="notifications@example.com",
    )
    defaults.update(overrides)
    return SmtpConfig(**defaults)


@patch("app.notifications.adapters.smtp.smtplib.SMTP")
def test_send_builds_the_message_and_starts_tls(smtp_cls: MagicMock) -> None:
    client = smtp_cls.return_value.__enter__.return_value
    adapter = SmtpNotificationAdapter(_config())

    adapter.send(recipient="owner@example.com", subject="Trading halt", body="entry_halted")

    smtp_cls.assert_called_once_with("smtp.example.com", 587, timeout=10)
    client.starttls.assert_called_once()
    client.login.assert_not_called()  # no username/password configured
    sent_message = client.send_message.call_args[0][0]
    assert sent_message["From"] == "notifications@example.com"
    assert sent_message["To"] == "owner@example.com"
    assert sent_message["Subject"] == "Trading halt"
    assert sent_message.get_content().strip() == "entry_halted"


@patch("app.notifications.adapters.smtp.smtplib.SMTP")
def test_send_logs_in_when_credentials_are_configured(smtp_cls: MagicMock) -> None:
    client = smtp_cls.return_value.__enter__.return_value
    adapter = SmtpNotificationAdapter(_config(username="bot", password="hunter2"))

    adapter.send(recipient="owner@example.com", subject="s", body="b")

    client.login.assert_called_once_with("bot", "hunter2")


@patch("app.notifications.adapters.smtp.smtplib.SMTP")
def test_send_skips_starttls_when_disabled(smtp_cls: MagicMock) -> None:
    client = smtp_cls.return_value.__enter__.return_value
    adapter = SmtpNotificationAdapter(_config(use_tls=False))

    adapter.send(recipient="owner@example.com", subject="s", body="b")

    client.starttls.assert_not_called()
