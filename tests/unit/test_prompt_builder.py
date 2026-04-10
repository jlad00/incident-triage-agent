"""Unit tests for PromptBuilder."""

import pytest
from agent.evidence.packet_builder import EvidencePacket
from agent.llm.prompt_builder import PromptBuilder, SYSTEM_PROMPT
from datetime import datetime, timezone


@pytest.fixture
def minimal_packet():
    return EvidencePacket(
        incident_id="test-01",
        generated_at=datetime.now(timezone.utc),
        scenario_name="bad_deploy",
        services_affected=["payment-service", "api-gateway"],
        incident_window_start=datetime(2024, 11, 14, 2, 12, tzinfo=timezone.utc),
        incident_window_end=datetime(2024, 11, 14, 2, 15, tzinfo=timezone.utc),
        metrics_window_minutes=30.0,
        signal_count=3,
        signals=[
            {
                "name": "circuit_breaker_open",
                "severity": "high",
                "category": "reliability",
                "description": "Circuit breaker opened",
                "count": 1,
                "first_seen": "2024-11-14T02:13:10+00:00",
                "last_seen": "2024-11-14T02:13:10+00:00",
                "services_affected": ["payment-service"],
                "evidence_messages": ["Circuit breaker OPEN: db-primary"],
            }
        ],
        breach_count=2,
        metric_breaches=[
            {
                "metric": "error_rate",
                "description": "Request error rate",
                "severity": "critical",
                "threshold_value": 0.50,
                "peak_value": 0.81,
                "peak_timestamp": "2024-11-14T02:16:00+00:00",
                "breach_start": "2024-11-14T02:14:00+00:00",
                "breach_sample_count": 2,
                "operator": ">=",
            }
        ],
        highest_breach_severity="critical",
        correlated_change_count=1,
        correlated_changes=[
            {
                "change_type": "deployment",
                "service": "payment-service",
                "version": "v2.4.1",
                "timestamp": "2024-11-14T02:10:02+00:00",
                "change_summary": "Updated DB connection pool settings",
                "rollback_available": True,
                "correlated_signals": ["circuit_breaker_open"],
                "delta_seconds": 128,
                "delta_human": "2m 8s",
                "same_service": True,
                "strength": "high",
                "strength_reasoning": "High-severity signals appeared 2m 8s after deployment to the same service",
            }
        ],
        severity_score=28,
        severity_estimate="P1",
        severity_reasoning="Score 28: error_rate breached critical threshold",
        unmatched_error_count=0,
        log_entry_count=13,
        metric_sample_count=8,
        change_event_count=1,
    )


@pytest.fixture
def builder():
    return PromptBuilder()


class TestPromptBuilder:
    def test_returns_tuple_of_two_strings(self, builder, minimal_packet):
        system, user = builder.build(minimal_packet)
        assert isinstance(system, str)
        assert isinstance(user, str)

    def test_system_prompt_is_constant(self, builder, minimal_packet):
        system, _ = builder.build(minimal_packet)
        assert system == SYSTEM_PROMPT

    def test_user_prompt_contains_incident_id(self, builder, minimal_packet):
        _, user = builder.build(minimal_packet)
        assert "test-01" in user

    def test_user_prompt_contains_services(self, builder, minimal_packet):
        _, user = builder.build(minimal_packet)
        assert "payment-service" in user
        assert "api-gateway" in user

    def test_user_prompt_contains_signal_names(self, builder, minimal_packet):
        _, user = builder.build(minimal_packet)
        assert "circuit_breaker_open" in user

    def test_user_prompt_contains_metric_breach(self, builder, minimal_packet):
        _, user = builder.build(minimal_packet)
        assert "error_rate" in user
        assert "0.81" in user

    def test_user_prompt_contains_correlated_change(self, builder, minimal_packet):
        _, user = builder.build(minimal_packet)
        assert "v2.4.1" in user
        assert "2m 8s" in user

    def test_user_prompt_contains_severity_estimate(self, builder, minimal_packet):
        _, user = builder.build(minimal_packet)
        assert "P1" in user

    def test_runbook_section_included_when_present(self, builder, minimal_packet):
        minimal_packet.runbook_context = "Step 1: Check db-primary connection pool."
        _, user = builder.build(minimal_packet)
        assert "db-primary connection pool" in user

    def test_runbook_section_absent_when_none(self, builder, minimal_packet):
        minimal_packet.runbook_context = None
        _, user = builder.build(minimal_packet)
        assert "Runbook" not in user