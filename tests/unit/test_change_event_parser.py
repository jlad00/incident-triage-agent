"""Unit tests for ChangeEventParser."""

import pytest
from datetime import datetime, timezone
from agent.ingestion.change_event_parser import ChangeEventParser


@pytest.fixture
def parser():
    return ChangeEventParser()


@pytest.fixture
def valid_events():
    return [
        {
            "timestamp": "2024-11-14T02:10:02Z",
            "type": "deployment",
            "service": "payment-service",
            "version": "v2.4.1",
            "previous_version": "v2.4.0",
            "author": "ci-pipeline",
            "environment": "production",
            "change_summary": "Updated DB connection pool settings",
            "rollback_available": True,
        }
    ]


class TestChangeEventParserBasic:
    def test_parses_valid_event(self, parser, valid_events):
        result = parser.parse_from_list(valid_events)
        assert result.parsed_events == 1
        assert result.skipped_events == 0
        assert result.events[0].type == "deployment"

    def test_service_normalized_to_lowercase(self, parser, valid_events):
        valid_events[0]["service"] = "Payment-Service"
        result = parser.parse_from_list(valid_events)
        assert result.events[0].service == "payment-service"

    def test_timestamps_timezone_aware(self, parser, valid_events):
        result = parser.parse_from_list(valid_events)
        assert result.events[0].timestamp.tzinfo is not None

    def test_unknown_type_normalized_to_other(self, parser, valid_events):
        valid_events[0]["type"] = "magic_restart"
        result = parser.parse_from_list(valid_events)
        assert result.events[0].type == "other"

    def test_rollback_available_defaults_false(self, parser):
        events = [{"timestamp": "2024-11-14T02:10:00Z", "type": "deployment", "service": "svc"}]
        result = parser.parse_from_list(events)
        assert result.events[0].rollback_available is False

    def test_events_for_service_filter(self, parser):
        events = [
            {"timestamp": "2024-11-14T02:10:00Z", "type": "deployment", "service": "payment-service"},
            {"timestamp": "2024-11-14T02:11:00Z", "type": "deployment", "service": "auth-service"},
        ]
        result = parser.parse_from_list(events)
        filtered = result.events_for_service("payment-service")
        assert len(filtered) == 1
        assert filtered[0].service == "payment-service"

    def test_events_in_window(self, parser):
        events = [
            {"timestamp": "2024-11-14T02:05:00Z", "type": "deployment", "service": "svc-a"},
            {"timestamp": "2024-11-14T02:15:00Z", "type": "deployment", "service": "svc-b"},
            {"timestamp": "2024-11-14T02:25:00Z", "type": "deployment", "service": "svc-c"},
        ]
        result = parser.parse_from_list(events)
        window_start = datetime(2024, 11, 14, 2, 10, tzinfo=timezone.utc)
        window_end = datetime(2024, 11, 14, 2, 20, tzinfo=timezone.utc)
        in_window = result.events_in_window(window_start, window_end)
        assert len(in_window) == 1
        assert in_window[0].service == "svc-b"

    def test_skips_bad_events_and_continues(self, parser):
        events = [
            {"timestamp": "bad-date", "type": "deployment", "service": "svc"},
            {"timestamp": "2024-11-14T02:10:00Z", "type": "deployment", "service": "svc"},
        ]
        result = parser.parse_from_list(events)
        assert result.parsed_events == 1
        assert result.skipped_events == 1