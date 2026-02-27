"""Tests for structured JSON logging."""

import json
import logging
import sys
from pathlib import Path

from polymarket_agent.monitoring.logging import JSONFormatter, setup_structured_logging


class TestJSONFormatter:
    def test_formats_basic_message(self) -> None:
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="hello %s",
            args=("world",),
            exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert data["message"] == "hello world"
        assert data["level"] == "INFO"
        assert data["logger"] == "test.logger"
        assert "timestamp" in data

    def test_formats_exception(self) -> None:
        formatter = JSONFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            exc_info = sys.exc_info()
            record = logging.LogRecord(
                name="test",
                level=logging.ERROR,
                pathname="test.py",
                lineno=1,
                msg="boom",
                args=(),
                exc_info=exc_info,
            )
        output = formatter.format(record)
        data = json.loads(output)
        assert "exception" in data
        assert "ValueError" in data["exception"]

    def test_includes_extra_data(self) -> None:
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="with data",
            args=(),
            exc_info=None,
        )
        record.extra_data = {"key": "value"}  # type: ignore[attr-defined]
        output = formatter.format(record)
        data = json.loads(output)
        assert data["data"] == {"key": "value"}

    def test_no_extra_data_key_when_absent(self) -> None:
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="plain",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert "data" not in data

    def test_output_is_single_line(self) -> None:
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="line one\nline two",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        # JSON output should be a single line (no embedded newlines in JSON)
        assert json.loads(output)["message"] == "line one\nline two"


class TestSetupStructuredLogging:
    def test_configures_root_logger(self) -> None:
        setup_structured_logging()
        root = logging.getLogger()
        assert len(root.handlers) >= 1
        assert isinstance(root.handlers[0].formatter, JSONFormatter)
        # Clean up
        root.handlers.clear()

    def test_adds_file_handler(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.log"
        setup_structured_logging(log_file=log_file)
        root = logging.getLogger()
        assert len(root.handlers) >= 2
        # Write a test log and verify it goes to file
        test_logger = logging.getLogger("test_file_handler")
        test_logger.info("file log test")
        # Flush handlers
        for handler in root.handlers:
            handler.flush()
        assert log_file.exists()
        content = log_file.read_text()
        assert "file log test" in content
        # Clean up
        root.handlers.clear()

    def test_creates_log_directory(self, tmp_path: Path) -> None:
        log_file = tmp_path / "subdir" / "nested" / "test.log"
        setup_structured_logging(log_file=log_file)
        root = logging.getLogger()
        assert log_file.parent.exists()
        root.handlers.clear()
