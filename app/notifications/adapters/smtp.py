"""Generic SMTP notification adapter (Horizon5 Group D / Unit 8). Standard
library only (`smtplib` + `email.message.EmailMessage`) -- no new dependency,
per docs/plans/horizon5-implementation-plan.md's Unit 8 file list.
"""

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    username: str | None
    password: str | None
    use_tls: bool
    sender_address: str


class SmtpNotificationAdapter:
    def __init__(self, config: SmtpConfig) -> None:
        self._config = config

    def send(self, *, recipient: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = self._config.sender_address
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)

        with smtplib.SMTP(self._config.host, self._config.port, timeout=10) as client:
            if self._config.use_tls:
                client.starttls()
            if self._config.username and self._config.password:
                client.login(self._config.username, self._config.password)
            client.send_message(message)
