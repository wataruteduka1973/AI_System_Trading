from typing import Protocol


class NotificationAdapter(Protocol):
    """One delivery channel's send operation. `SmtpNotificationAdapter` (email)
    is the only implementation for now -- see `app/notifications/adapters/smtp.py`.
    docs/plans/horizon5-implementation-plan.md §0.3 (スコープ外): adapters
    beyond generic SMTP (Gmail API etc.) are explicitly a separate task, this
    Protocol exists so adding one later does not touch the delivery loop."""

    def send(self, *, recipient: str, subject: str, body: str) -> None: ...
