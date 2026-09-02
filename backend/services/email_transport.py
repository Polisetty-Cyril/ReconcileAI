"""
ReconcileAI - Mock Email Transport (Phase 12C-3)
Provides an in-memory, deterministic mock email transport for development and testing.
Captures attempted deliveries in memory without real SMTP, network calls, or external APIs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Any
import logging

logger = logging.getLogger(__name__)


@dataclass
class EmailSendResult:
    """Result of an email transport delivery attempt."""
    success: bool
    recipient_email: str
    subject: str
    error: Optional[str] = None


class MockEmailTransport:
    """
    In-memory mock email transport.
    Records sent messages in memory and allows deterministic failure simulation.
    """

    def __init__(
        self,
        should_fail: bool = False,
        fail_error: str = "Simulated mock email delivery failure"
    ) -> None:
        self.sent_messages: List[Dict[str, Any]] = []
        self.should_fail: bool = should_fail
        self.fail_error: str = fail_error

    def send(
        self,
        recipient_email: str,
        subject: str,
        body: str
    ) -> EmailSendResult:
        """
        Simulates sending an email.
        If should_fail is True, returns an error result without recording to sent_messages.
        Otherwise, records the email to in-memory sent_messages and returns success.
        """
        if self.should_fail:
            logger.warning(
                "[MockEmailTransport] Delivery to %s failed (simulated): %s",
                recipient_email,
                self.fail_error
            )
            return EmailSendResult(
                success=False,
                recipient_email=recipient_email,
                subject=subject,
                error=self.fail_error
            )

        message = {
            "recipient_email": recipient_email,
            "subject": subject,
            "body": body,
        }
        self.sent_messages.append(message)
        logger.info("[MockEmailTransport] Recorded email to %s: %s", recipient_email, subject)
        return EmailSendResult(
            success=True,
            recipient_email=recipient_email,
            subject=subject,
            error=None
        )

    def clear(self) -> None:
        """Clears all recorded messages."""
        self.sent_messages.clear()

    def get_sent_messages(self) -> List[Dict[str, Any]]:
        """Returns copies of all recorded sent messages."""
        return list(self.sent_messages)
