"""
Evidence Packet Builder — assembles all analysis outputs into one
structured evidence packet for the LLM reasoning layer.

This is the handoff point between the deterministic analysis layer
and the LLM. The LLM receives ONLY this packet — never raw logs.

Design goals:
  - Every field is explainable by pointing to a source
  - Compact enough to fit in a single LLM context window
  - Structured enough for the LLM to produce cited hypotheses
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from agent.ingestion.log_parser import ParsedLogs
from agent.ingestion.metrics_parser import ParsedMetrics
from agent.ingestion.change_event_parser import ParsedChangeEvents
from agent.analysis.signal_extractor import SignalExtractionResult
from agent.analysis.threshold_evaluator import ThresholdEvaluationResult
from agent.analysis.correlator import CorrelationResult


# ── Schema ────────────────────────────────────────────────────────────────────

class EvidencePacket(BaseModel):
    """
    The structured evidence packet passed to the LLM.

    Every field here was produced by the deterministic layer.
    The LLM's job is to synthesize and reason — not to extract.
    """

    # Metadata
    incident_id: str
    generated_at: datetime
    scenario_name: str

    # Scope
    services_affected: list[str]
    incident_window_start: Any | None
    incident_window_end: Any | None
    metrics_window_minutes: float | None

    # Signals (from signal_extractor)
    signal_count: int
    signals: list[dict]                # Serialized ExtractedSignal list

    # Metric breaches (from threshold_evaluator)
    breach_count: int
    metric_breaches: list[dict]        # Serialized ThresholdBreach list
    highest_breach_severity: str | None

    # Correlation (from correlator)
    correlated_change_count: int
    correlated_changes: list[dict]     # Serialized CorrelatedChange list

    # Severity
    severity_score: int
    severity_estimate: str
    severity_reasoning: str

    # Unmatched errors — signals the LLM should be aware of
    unmatched_error_count: int

    # Optional runbook context
    runbook_context: str | None = None

    # Audit trail
    log_entry_count: int
    metric_sample_count: int
    change_event_count: int


# ── Builder ───────────────────────────────────────────────────────────────────

class EvidencePacketBuilder:
    """
    Assembles all analysis outputs into a single EvidencePacket.

    Usage:
        builder = EvidencePacketBuilder()
        packet = builder.build(
            scenario_name="bad_deploy",
            parsed_logs=...,
            parsed_metrics=...,
            parsed_changes=...,
            signal_result=...,
            threshold_result=...,
            correlation_result=...,
            runbook_context=None,
        )
    """

    def build(
        self,
        scenario_name: str,
        parsed_logs: ParsedLogs,
        parsed_metrics: ParsedMetrics | None,
        parsed_changes: ParsedChangeEvents,
        signal_result: SignalExtractionResult,
        threshold_result: ThresholdEvaluationResult | None,
        correlation_result: CorrelationResult,
        runbook_context: str | None = None,
    ) -> EvidencePacket:

        # Collect all affected services from signals + changes
        services: set[str] = set()
        for s in signal_result.signals:
            services.update(s.services_affected)
        for e in parsed_changes.events:
            services.add(e.service)

        return EvidencePacket(
            incident_id=str(uuid.uuid4())[:8],
            generated_at=datetime.now(timezone.utc),
            scenario_name=scenario_name,

            services_affected=sorted(services),
            incident_window_start=correlation_result.incident_window_start,
            incident_window_end=correlation_result.incident_window_end,
            metrics_window_minutes=parsed_metrics.duration_minutes if parsed_metrics else None,

            signal_count=len(signal_result.signals),
            signals=[self._serialize_signal(s) for s in signal_result.signals],

            breach_count=len(threshold_result.breaches) if threshold_result else 0,
            metric_breaches=[self._serialize_breach(b) for b in (threshold_result.breaches if threshold_result else [])],
            highest_breach_severity=threshold_result.highest_severity if threshold_result else None,

            correlated_change_count=len(correlation_result.correlated_changes),
            correlated_changes=[self._serialize_correlation(c) for c in correlation_result.correlated_changes],

            severity_score=correlation_result.severity_score,
            severity_estimate=correlation_result.severity_estimate,
            severity_reasoning=correlation_result.severity_reasoning,

            unmatched_error_count=signal_result.unmatched_error_count,

            runbook_context=runbook_context,

            log_entry_count=parsed_logs.total_entries,
            metric_sample_count=parsed_metrics.sample_count if parsed_metrics else 0,
            change_event_count=parsed_changes.total_events,
        )

    # ── Serializers ───────────────────────────────────────────────────────────

    @staticmethod
    def _serialize_signal(s) -> dict:
        return {
            "name": s.name,
            "severity": s.severity,
            "category": s.category,
            "description": s.description,
            "count": s.count,
            "first_seen": s.first_seen.isoformat() if s.first_seen else None,
            "last_seen": s.last_seen.isoformat() if s.last_seen else None,
            "services_affected": s.services_affected,
            "evidence_messages": s.evidence_messages,
        }

    @staticmethod
    def _serialize_breach(b) -> dict:
        return {
            "metric": b.metric,
            "description": b.description,
            "severity": b.severity,
            "threshold_value": b.threshold_value,
            "peak_value": b.peak_value,
            "peak_timestamp": b.peak_timestamp.isoformat() if b.peak_timestamp else None,
            "breach_start": b.breach_start.isoformat() if b.breach_start else None,
            "breach_sample_count": b.breach_sample_count,
        }

    @staticmethod
    def _serialize_correlation(c) -> dict:
        return {
            "change_type": c.change_event.type,
            "service": c.change_event.service,
            "version": c.change_event.version,
            "timestamp": c.change_event.timestamp.isoformat(),
            "change_summary": c.change_event.change_summary,
            "rollback_available": c.change_event.rollback_available,
            "correlated_signals": c.correlated_signals,
            "delta_seconds": c.delta_seconds,
            "delta_human": c.delta_human,
            "same_service": c.same_service,
            "strength": c.strength,
            "strength_reasoning": c.strength_reasoning,
        }