"""Unit tests for ThresholdEvaluator."""

import pytest
from agent.ingestion.metrics_parser import MetricsParser
from agent.analysis.threshold_evaluator import ThresholdEvaluator


@pytest.fixture
def evaluator():
    return ThresholdEvaluator()


@pytest.fixture
def bad_deploy_metrics():
    return MetricsParser().parse_from_dict({
        "service": "payment-service",
        "window_start": "2024-11-14T02:00:00Z",
        "window_end": "2024-11-14T02:30:00Z",
        "samples": [
            {"timestamp": "2024-11-14T02:00:00Z", "cpu_percent": 22, "error_rate": 0.002, "p99_latency_ms": 145, "restarts": 0},
            {"timestamp": "2024-11-14T02:14:00Z", "cpu_percent": 88, "error_rate": 0.79, "p99_latency_ms": 8200, "restarts": 1},
            {"timestamp": "2024-11-14T02:16:00Z", "cpu_percent": 91, "error_rate": 0.81, "p99_latency_ms": 9100, "restarts": 1},
        ],
    })


class TestThresholdEvaluator:
    def test_detects_cpu_breach(self, evaluator, bad_deploy_metrics):
        result = evaluator.evaluate(bad_deploy_metrics)
        metrics = [b.metric for b in result.breaches]
        assert "cpu_percent" in metrics

    def test_detects_error_rate_breach(self, evaluator, bad_deploy_metrics):
        result = evaluator.evaluate(bad_deploy_metrics)
        metrics = [b.metric for b in result.breaches]
        assert "error_rate" in metrics

    def test_detects_latency_breach(self, evaluator, bad_deploy_metrics):
        result = evaluator.evaluate(bad_deploy_metrics)
        metrics = [b.metric for b in result.breaches]
        assert "p99_latency_ms" in metrics

    def test_error_rate_breach_is_critical(self, evaluator, bad_deploy_metrics):
        result = evaluator.evaluate(bad_deploy_metrics)
        er_breach = next(b for b in result.breaches if b.metric == "error_rate")
        assert er_breach.severity == "critical"

    def test_peak_value_is_highest_observed(self, evaluator, bad_deploy_metrics):
        result = evaluator.evaluate(bad_deploy_metrics)
        cpu_breach = next(b for b in result.breaches if b.metric == "cpu_percent")
        assert cpu_breach.peak_value == 91.0

    def test_highest_severity_is_critical(self, evaluator, bad_deploy_metrics):
        result = evaluator.evaluate(bad_deploy_metrics)
        assert result.highest_severity == "critical"

    def test_no_breach_when_all_normal(self, evaluator):
        normal_metrics = MetricsParser().parse_from_dict({
            "service": "svc",
            "window_start": "2024-11-14T02:00:00Z",
            "window_end": "2024-11-14T02:30:00Z",
            "samples": [
                {"timestamp": "2024-11-14T02:00:00Z", "cpu_percent": 20, "error_rate": 0.001, "p99_latency_ms": 100, "restarts": 0},
                {"timestamp": "2024-11-14T02:10:00Z", "cpu_percent": 25, "error_rate": 0.002, "p99_latency_ms": 120, "restarts": 0},
            ],
        })
        result = evaluator.evaluate(normal_metrics)
        assert result.breaches == []
        assert result.highest_severity is None

    def test_breach_start_is_first_breaching_sample(self, evaluator, bad_deploy_metrics):
        result = evaluator.evaluate(bad_deploy_metrics)
        er_breach = next(b for b in result.breaches if b.metric == "error_rate")
        # First breach should be at 02:14:00 (error_rate 0.79, past 0.50 critical threshold)
        assert "02:14" in er_breach.breach_start.isoformat()

    def test_breach_sample_count(self, evaluator, bad_deploy_metrics):
        result = evaluator.evaluate(bad_deploy_metrics)
        er_breach = next(b for b in result.breaches if b.metric == "error_rate")
        # Both 02:14 and 02:16 samples breach the 0.50 critical threshold
        assert er_breach.breach_sample_count == 2