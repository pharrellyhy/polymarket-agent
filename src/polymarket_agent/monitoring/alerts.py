"""Alert system for trade execution and risk events."""

from __future__ import annotations

import json
import logging
import urllib.request
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class AlertSink(ABC):
    """Base class for alert destinations."""

    @abstractmethod
    def send(self, message: str) -> None:
        """Send an alert message."""


class ConsoleAlertSink(AlertSink):
    """Write alerts to stderr via the logging module."""

    def send(self, message: str) -> None:
        logger.warning("[ALERT] %s", message)


class WebhookAlertSink(AlertSink):
    """POST alerts to a webhook URL as JSON."""

    def __init__(self, url: str, *, timeout: int = 10) -> None:
        self._url = url
        self._timeout = timeout

    def send(self, message: str) -> None:
        payload = json.dumps({"text": message}).encode()
        req = urllib.request.Request(
            self._url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout):  # noqa: S310
                pass
        except Exception:
            logger.exception("Failed to send webhook alert to %s", self._url)


class AlertManager:
    """Dispatch alert messages to registered sinks."""

    def __init__(self) -> None:
        self._sinks: list[AlertSink] = []

    def register(self, sink: AlertSink) -> None:
        """Add an alert sink."""
        self._sinks.append(sink)

    def alert(self, message: str) -> None:
        """Send a message to all registered sinks."""
        for sink in self._sinks:
            try:
                sink.send(message)
            except Exception:
                logger.exception("Alert sink %s failed", type(sink).__name__)

    @property
    def sink_count(self) -> int:
        """Number of registered sinks."""
        return len(self._sinks)
