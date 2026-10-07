"""The Notification Worker's channel setup (app/notifications/worker/__main__.py): in-app
notifications always work; email only when SMTP is configured."""

from app.notifications.adapters.in_app import InAppNotificationAdapter
from app.notifications.adapters.smtp import SmtpNotificationAdapter
from app.notifications.worker import __main__ as worker
from pydantic import SecretStr


def _settings(monkeypatch, **overrides):
    values = dict(smtp_host=None, smtp_sender_address=None, smtp_username=None, smtp_password=None)
    for name, value in {**values, **overrides}.items():
        monkeypatch.setattr(worker.settings, name, value)


def test_without_smtp_only_the_in_app_channel_is_available(monkeypatch) -> None:
    _settings(monkeypatch)

    config = worker._smtp_config()
    adapters = worker._build_adapters(config)

    assert config is None
    assert set(adapters) == {"in_app"}
    assert isinstance(adapters["in_app"], InAppNotificationAdapter)


def test_with_smtp_email_is_added_and_the_password_is_unwrapped(monkeypatch) -> None:
    _settings(
        monkeypatch,
        smtp_host="smtp.example.com",
        smtp_sender_address="alerts@example.com",
        smtp_username="alerts",
        smtp_password=SecretStr("s3cret"),
    )

    config = worker._smtp_config()
    adapters = worker._build_adapters(config)

    assert config is not None and config.password == "s3cret"
    assert set(adapters) == {"in_app", "email"}
    assert isinstance(adapters["email"], SmtpNotificationAdapter)


def test_the_in_app_adapter_has_nothing_to_send() -> None:
    assert InAppNotificationAdapter().send(recipient="u", subject="s", body="b") is None
