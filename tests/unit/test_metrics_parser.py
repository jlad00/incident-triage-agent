"""Unit tests for MetricsParser."""

import pytest
from agent.ingestion.metrics_parser import MetricsParser


@pytest.fixture
def parser():
    return MetricsParser()


@pytest.fixture
def valid_metrics():
    return {
        "service": "payment-service",
        "window_start": "2024-11-14T02:00:00Z",
        "window_end": "2024-11-14T02:30:00Z",
        "samples": [
            {"timestamp": "2024-11-14T02:00:00Z", "cpu_percent": 22, "mem_percent": 48, "error_rate": 0.002, "p99_latency_ms": 145, "restarts": 0},
            {"timestamp": "2024-11-14T02:15:00Z", "cpu_percent": 88, "mem_percent": 74, "error_rate": 0.79, "p99_latency_ms": 8200, "restarts": 1},
        ],
    }


class TestMetricsParserBasic:
    def test_parses_valid_metrics(self, parser, valid_metrics):
        result = parser.parse_from_dict(valid_metrics)
        assert result.sample_count == 2
        assert result.service == "payment-service"

    def test_samples_sorted_by_timestamp(self, parser):
        data = {
            "service": "svc",
            "window_start": "2024-11-14T02:00:00Z",
            "window_end": "2024-11-14T02:30:00Z",
            "samples": [
                {"timestamp": "2024-11-14T02:20:00Z", "cpu_percent": 50},
                {"timestamp": "2024-11-14T02:05:00Z", "cpu_percent": 20},
            ],
        }
        result = parser.parse_from_dict(data)
        assert result.samples[0].cpu_percent == 20
        assert result.samples[1].cpu_percent == 50

    def test_service_name_normalized(self, parser, valid_metrics):
        valid_metrics["service"] = "Payment-Service"
        result = parser.parse_from_dict(valid_metrics)
        assert result.service == "payment-service"

    def test_duration_minutes(self, parser, valid_metrics):
        result = parser.parse_from_dict(valid_metrics)
        assert result.duration_minutes == 30.0

    def test_timestamps_timezone_aware(self, parser, valid_metrics):
        result = parser.parse_from_dict(valid_metrics)
        for sample in result.samples:
            assert sample.timestamp.tzinfo is not None

    def test_optional_fields_can_be_absent(self, parser):
        data = {
            "service": "svc",
            "window_start": "2024-11-14T02:00:00Z",
            "window_end": "2024-11-14T02:30:00Z",
            "samples": [{"timestamp": "2024-11-14T02:05:00Z"}],
        }
        result = parser.parse_from_dict(data)
        assert result.samples[0].cpu_percent is None

    def test_empty_samples(self, parser):
        data = {
            "service": "svc",
            "window_start": "2024-11-14T02:00:00Z",
            "window_end": "2024-11-14T02:30:00Z",
            "samples": [],
        }
        result = parser.parse_from_dict(data)
        assert result.sample_count == 0