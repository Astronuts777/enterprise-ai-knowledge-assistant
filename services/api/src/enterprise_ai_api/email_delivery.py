"""Minimal transactional email adapters without persisting message secrets."""

import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol


class EmailDeliveryError(RuntimeError):
    """Raised when a configured provider cannot deliver a message."""


class EmailSender(Protocol):
    enabled: bool

    def send(self, *, recipient: str, subject: str, text: str) -> None: ...


class DisabledEmailSender:
    enabled = False

    def send(self, *, recipient: str, subject: str, text: str) -> None:
        raise EmailDeliveryError("Transactional email delivery is not configured")


@dataclass(frozen=True, slots=True)
class SentEmail:
    recipient: str
    subject: str
    text: str


class MemoryEmailSender:
    """Deterministic sender used by tests and local integration checks."""

    enabled = True

    def __init__(self) -> None:
        self.messages: list[SentEmail] = []

    def send(self, *, recipient: str, subject: str, text: str) -> None:
        self.messages.append(SentEmail(recipient=recipient, subject=subject, text=text))


class SmtpEmailSender:
    enabled = True

    def __init__(
        self,
        *,
        host: str,
        port: int,
        sender: str,
        username: str | None,
        password: str | None,
        use_tls: bool,
        use_ssl: bool,
    ) -> None:
        self.host = host
        self.port = port
        self.sender = sender
        self.username = username
        self.password = password
        self.use_tls = use_tls
        self.use_ssl = use_ssl

    def send(self, *, recipient: str, subject: str, text: str) -> None:
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(text)
        try:
            smtp_type = smtplib.SMTP_SSL if self.use_ssl else smtplib.SMTP
            with smtp_type(self.host, self.port, timeout=15) as client:
                if self.use_tls and not self.use_ssl:
                    client.starttls(context=ssl.create_default_context())
                if self.username:
                    client.login(self.username, self.password or "")
                client.send_message(message)
        except (OSError, smtplib.SMTPException) as error:
            raise EmailDeliveryError("Transactional email could not be delivered") from error


def email_sender_from_environment() -> EmailSender:
    backend = os.getenv("EMAIL_DELIVERY_BACKEND", "disabled").strip().lower()
    if backend == "disabled":
        return DisabledEmailSender()
    if backend != "smtp":
        raise RuntimeError("EMAIL_DELIVERY_BACKEND must be either 'disabled' or 'smtp'")

    required = {
        "SMTP_HOST": os.getenv("SMTP_HOST"),
        "SMTP_FROM_EMAIL": os.getenv("SMTP_FROM_EMAIL"),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"Missing SMTP settings: {', '.join(missing)}")
    use_tls = os.getenv("SMTP_USE_TLS", "true").lower() in {"1", "true", "yes"}
    use_ssl = os.getenv("SMTP_USE_SSL", "false").lower() in {"1", "true", "yes"}
    if use_tls and use_ssl:
        raise RuntimeError("SMTP_USE_TLS and SMTP_USE_SSL cannot both be enabled")
    return SmtpEmailSender(
        host=required["SMTP_HOST"] or "",
        port=int(os.getenv("SMTP_PORT", "587")),
        sender=required["SMTP_FROM_EMAIL"] or "",
        username=os.getenv("SMTP_USERNAME") or None,
        password=os.getenv("SMTP_PASSWORD") or None,
        use_tls=use_tls,
        use_ssl=use_ssl,
    )
