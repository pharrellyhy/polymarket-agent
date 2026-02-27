"""Tests for the alert system."""

from unittest.mock import MagicMock, patch

from polymarket_agent.monitoring.alerts import (
    AlertManager,
    ConsoleAlertSink,
    WebhookAlertSink,
)


class TestConsoleAlertSink:
    def test_sends_via_logger(self) -> None:
        sink = ConsoleAlertSink()
        with patch("polymarket_agent.monitoring.alerts.logger") as mock_logger:
            sink.send("test alert message")
            mock_logger.warning.assert_called_once()
            assert "test alert message" in mock_logger.warning.call_args[0][1]


class TestWebhookAlertSink:
    def test_posts_json_payload(self) -> None:
        sink = WebhookAlertSink("https://hooks.example.com/test")
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = MagicMock()
            mock_urlopen.return_value.__exit__ = MagicMock()
            sink.send("webhook test")
            mock_urlopen.assert_called_once()
            req = mock_urlopen.call_args[0][0]
            assert req.full_url == "https://hooks.example.com/test"
            assert b"webhook test" in req.data

    def test_handles_failure_gracefully(self) -> None:
        sink = WebhookAlertSink("https://hooks.example.com/fail")
        with patch("urllib.request.urlopen", side_effect=Exception("network error")):
            # Should not raise
            sink.send("this will fail")


class TestAlertManager:
    def test_dispatches_to_all_sinks(self) -> None:
        manager = AlertManager()
        sink_a = MagicMock()
        sink_b = MagicMock()
        manager.register(sink_a)
        manager.register(sink_b)
        manager.alert("alert message")
        sink_a.send.assert_called_once_with("alert message")
        sink_b.send.assert_called_once_with("alert message")

    def test_empty_manager_no_error(self) -> None:
        manager = AlertManager()
        manager.alert("no sinks")  # Should not raise

    def test_sink_count(self) -> None:
        manager = AlertManager()
        assert manager.sink_count == 0
        manager.register(ConsoleAlertSink())
        assert manager.sink_count == 1

    def test_continues_on_sink_failure(self) -> None:
        manager = AlertManager()
        failing_sink = MagicMock()
        failing_sink.send.side_effect = Exception("broken")
        good_sink = MagicMock()
        manager.register(failing_sink)
        manager.register(good_sink)
        manager.alert("partial failure")
        # Good sink should still be called despite first failing
        good_sink.send.assert_called_once_with("partial failure")
