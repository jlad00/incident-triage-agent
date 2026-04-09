"""Unit tests for SignalExtractor."""

import pytest
from agent.ingestion.log_parser import LogParser
from agent.analysis.signal_extractor import SignalExtractor


@pytest.fixture
def extractor():
    return SignalExtractor()


@pytest.fixture
def bad_deploy_logs():
    return LogParser().parse_from_list([
        {"timestamp": "2024-11-14T02:12:15Z", "service": "payment-service", "level": "ERROR",
         "message": "NullPointerException in PaymentProcessor.charge() at line 84"},
        {"timestamp": "2024-11-14T02:12:18Z", "service": "payment-service", "level": "ERROR",
         "message": "Connection pool exhausted: db-primary:5432. Active connections: 50/50"},
        {"timestamp": "2024-11-14T02:12:22Z", "service": "api-gateway", "level": "ERROR",
         "message": "Upstream payment-service returned HTTP 500. Retrying (1/3)"},
        {"timestamp": "2024-11-14T02:13:10Z", "service": "payment-service", "level": "CRITICAL",
         "message": "Circuit breaker OPEN: db-primary. Rejecting all database requests."},
        {"timestamp": "2024-11-14T02:15:30Z", "service": "api-gateway", "level": "WARN",
         "message": "High error rate detected on route /api/v1/payments: 78% over last 60s"},
    ])


class TestSignalExtractor:
    def test_extracts_null_pointer_signal(self, extractor, bad_deploy_logs):
        result = extractor.extract(bad_deploy_logs)
        names = [s.name for s in result.signals]
        assert "null_pointer_exception" in names

    def test_extracts_circuit_breaker_signal(self, extractor, bad_deploy_logs):
        result = extractor.extract(bad_deploy_logs)
        names = [s.name for s in result.signals]
        assert "circuit_breaker_open" in names

    def test_extracts_connection_pool_signal(self, extractor, bad_deploy_logs):
        result = extractor.extract(bad_deploy_logs)
        names = [s.name for s in result.signals]
        assert "connection_pool_exhausted" in names

    def test_extracts_upstream_http_5xx_signal(self, extractor, bad_deploy_logs):
        result = extractor.extract(bad_deploy_logs)
        names = [s.name for s in result.signals]
        assert "upstream_http_5xx" in names

    def test_signals_sorted_high_severity_first(self, extractor, bad_deploy_logs):
        result = extractor.extract(bad_deploy_logs)
        severities = [s.severity for s in result.signals]
        # All high signals should precede medium/low
        seen_non_high = False
        for sev in severities:
            if sev != "high":
                seen_non_high = True
            if seen_non_high and sev == "high":
                pytest.fail("High severity signal appeared after non-high signal")

    def test_signal_records_affected_services(self, extractor, bad_deploy_logs):
        result = extractor.extract(bad_deploy_logs)
        npe = next(s for s in result.signals if s.name == "null_pointer_exception")
        assert "payment-service" in npe.services_affected

    def test_signal_has_evidence_messages(self, extractor, bad_deploy_logs):
        result = extractor.extract(bad_deploy_logs)
        for signal in result.signals:
            assert len(signal.evidence_messages) >= 1
            assert len(signal.evidence_messages) <= 3

    def test_empty_logs_returns_no_signals(self, extractor):
        empty_logs = LogParser().parse_from_list([])
        result = extractor.extract(empty_logs)
        assert result.signals == []
        assert result.unmatched_error_count == 0

    def test_case_insensitive_pattern_matching(self, extractor):
        logs = LogParser().parse_from_list([
            {"timestamp": "2024-11-14T02:10:00Z", "service": "svc", "level": "ERROR",
             "message": "NULLPOINTEREXCEPTION in module.py"},
        ])
        result = extractor.extract(logs)
        names = [s.name for s in result.signals]
        assert "null_pointer_exception" in names

    def test_unmatched_error_count(self, extractor):
        logs = LogParser().parse_from_list([
            {"timestamp": "2024-11-14T02:10:00Z", "service": "svc", "level": "ERROR",
             "message": "Some completely unknown error xyz123"},
        ])
        result = extractor.extract(logs)
        assert result.unmatched_error_count == 1