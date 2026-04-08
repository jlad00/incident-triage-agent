"""Unit tests for LogParser."""

import pytest
from datetime import timezone
from agent.ingestion.log_parser import LogParser, LogEntry


@pytest.fixture
def parser():
    return LogParser()


@pytest.fixture
def valid_entries():
    return [
        {
            "timestamp": "2024-11-14T02:12:15Z",
            "service": "payment-service",
            "level": "ERROR",
            "message": "NullPointerException in PaymentProcessor",
            "host": "pod-payment-7d9f",
            "trace_id": "trace-abc",
        },
        {
            "timestamp": "2024-11-14T02:12:18Z",
            "service": "payment-service",
            "level": "CRITICAL",
            "message": "Circuit breaker OPEN",
            "host": "pod-payment-7d9f",
            "trace_id": None,
        },
    ]


class TestLogParserBasic:
    def test_parses_valid_entries(self, parser, valid_entries):
        result = parser.parse_from_list(valid_entries)
        assert result.parsed_entries == 2
        assert result.skipped_entries == 0
        assert len(result.entries) == 2

    def test_entries_sorted_by_timestamp(self, parser):
        # Feed entries out of order — should come back sorted
        entries = [
            {"timestamp": "2024-11-14T02:15:00Z", "service": "svc", "level": "INFO", "message": "b"},
            {"timestamp": "2024-11-14T02:10:00Z", "service": "svc", "level": "INFO", "message": "a"},
        ]
        result = parser.parse_from_list(entries)
        assert result.entries[0].message == "a"
        assert result.entries[1].message == "b"

    def test_timestamps_are_timezone_aware(self, parser, valid_entries):
        result = parser.parse_from_list(valid_entries)
        for entry in result.entries:
            assert entry.timestamp.tzinfo is not None

    def test_service_names_normalized_to_lowercase(self, parser):
        entries = [{"timestamp": "2024-11-14T02:10:00Z", "service": "Payment-Service", "level": "INFO", "message": "x"}]
        result = parser.parse_from_list(entries)
        assert result.entries[0].service == "payment-service"

    def test_skips_invalid_entries_and_continues(self, parser):
        entries = [
            {"timestamp": "not-a-date", "service": "svc", "level": "INFO", "message": "bad"},
            {"timestamp": "2024-11-14T02:10:00Z", "service": "svc", "level": "INFO", "message": "good"},
        ]
        result = parser.parse_from_list(entries)
        assert result.parsed_entries == 1
        assert result.skipped_entries == 1
        assert len(result.parse_warnings) == 1


class TestLogLevelNormalization:
    def test_warning_normalized_to_warn(self, parser):
        entries = [{"timestamp": "2024-11-14T02:10:00Z", "service": "svc", "level": "WARNING", "message": "x"}]
        result = parser.parse_from_list(entries)
        assert result.entries[0].level == "WARN"

    def test_fatal_normalized_to_critical(self, parser):
        entries = [{"timestamp": "2024-11-14T02:10:00Z", "service": "svc", "level": "FATAL", "message": "x"}]
        result = parser.parse_from_list(entries)
        assert result.entries[0].level == "CRITICAL"

    def test_case_insensitive_level(self, parser):
        entries = [{"timestamp": "2024-11-14T02:10:00Z", "service": "svc", "level": "error", "message": "x"}]
        result = parser.parse_from_list(entries)
        assert result.entries[0].level == "ERROR"


class TestLogParserEdgeCases:
    def test_empty_list_returns_zero_entries(self, parser):
        result = parser.parse_from_list([])
        assert result.parsed_entries == 0
        assert result.total_entries == 0

    def test_missing_optional_fields_ok(self, parser):
        entries = [{"timestamp": "2024-11-14T02:10:00Z", "service": "svc", "level": "INFO", "message": "x"}]
        result = parser.parse_from_list(entries)
        assert result.entries[0].host is None
        assert result.entries[0].trace_id is None

    def test_missing_service_defaults_to_unknown(self, parser):
        entries = [{"timestamp": "2024-11-14T02:10:00Z", "level": "INFO", "message": "x"}]
        result = parser.parse_from_list(entries)
        assert result.entries[0].service == "unknown"