"""Unit tests for Correlator."""

import pytest
from datetime import datetime, timezone
from agent.ingestion.change_event_parser import ChangeEventParser
from agent.analysis.signal_extractor import SignalExtractor
from agent.analysis.threshold_evaluator import ThresholdEvaluator
from agent.ingestion.log_parser import LogParser
from agent.ingestion.metrics_parser import MetricsParser
from agent.analysis.correlator import Correlator


@pytest.fixture
def correlator():
    return Correlator(correlation_window_minutes=30)


@pytest.fixture
def deploy_changes():
    return ChangeEventParser().parse_from_list([{
        "timestamp": "2024-11-14T02:10:02Z",
        "type": "deployment",
        "service": "payment-service",
        "version": "v2.4.1",
        "rollback_available": True,
    }])


@pytest.fixture
def post_deploy_signals():
    logs = LogParser().parse_from_list([
        {"timestamp": "2024-11-14T02:12:15Z", "service": "payment-service",
         "level": "ERROR", "message": "NullPointerException in PaymentProcessor"},
        {"timestamp": "2024-11-14T02:13:10Z", "service": "payment-service",
         "level": "CRITICAL", "message": "Circuit breaker OPEN: db-primary"},
        {"timestamp": "2024-11-14T02:12:18Z", "service": "payment-service",
         "level": "ERROR", "message": "Connection pool exhausted: db-primary:5432"},
    ])
    return SignalExtractor().extract(logs)


@pytest.fixture
def normal_threshold_result():
    metrics = MetricsParser().parse_from_dict({
        "service": "payment-service",
        "window_start": "2024-11-14T02:00:00Z",
        "window_end": "2024-11-14T02:30:00Z",
        "samples": [
            {"timestamp": "2024-11-14T02:14:00Z", "cpu_percent": 88,
             "error_rate": 0.79, "p99_latency_ms": 8200, "restarts": 1},
        ],
    })
    return ThresholdEvaluator().evaluate(metrics)


class TestCorrelator:
    def test_detects_correlated_change(self, correlator, deploy_changes, post_deploy_signals, normal_threshold_result):
        result = correlator.correlate(deploy_changes, post_deploy_signals, normal_threshold_result)
        assert result.correlated_change_count == 1

    def test_correlation_strength_is_high(self, correlator, deploy_changes, post_deploy_signals, normal_threshold_result):
        result = correlator.correlate(deploy_changes, post_deploy_signals, normal_threshold_result)
        assert result.correlated_changes[0].strength == "high"

    def test_same_service_flag_set(self, correlator, deploy_changes, post_deploy_signals, normal_threshold_result):
        result = correlator.correlate(deploy_changes, post_deploy_signals, normal_threshold_result)
        assert result.correlated_changes[0].same_service is True

    def test_delta_seconds_is_positive(self, correlator, deploy_changes, post_deploy_signals, normal_threshold_result):
        result = correlator.correlate(deploy_changes, post_deploy_signals, normal_threshold_result)
        assert result.correlated_changes[0].delta_seconds > 0

    def test_severity_estimate_is_p1(self, correlator, deploy_changes, post_deploy_signals, normal_threshold_result):
        result = correlator.correlate(deploy_changes, post_deploy_signals, normal_threshold_result)
        assert result.severity_estimate == "P1"

    def test_no_correlation_when_change_after_signals(self, correlator, normal_threshold_result):
        # Change event AFTER signals → should NOT correlate
        late_changes = ChangeEventParser().parse_from_list([{
            "timestamp": "2024-11-14T03:00:00Z",
            "type": "deployment",
            "service": "payment-service",
            "version": "v2.4.2",
        }])
        early_signals_logs = LogParser().parse_from_list([
            {"timestamp": "2024-11-14T02:12:00Z", "service": "payment-service",
             "level": "ERROR", "message": "NullPointerException in processor"},
        ])
        early_signals = SignalExtractor().extract(early_signals_logs)
        result = correlator.correlate(late_changes, early_signals, normal_threshold_result)
        assert result.correlated_change_count == 0

    def test_delta_human_format(self, correlator, deploy_changes, post_deploy_signals, normal_threshold_result):
        result = correlator.correlate(deploy_changes, post_deploy_signals, normal_threshold_result)
        delta = result.correlated_changes[0].delta_human
        # Should be something like "2m 13s" or "130s"
        assert any(c in delta for c in ["m", "s"])

    def test_incident_window_captured(self, correlator, deploy_changes, post_deploy_signals, normal_threshold_result):
        result = correlator.correlate(deploy_changes, post_deploy_signals, normal_threshold_result)
        assert result.incident_window_start is not None
        assert result.incident_window_end is not None