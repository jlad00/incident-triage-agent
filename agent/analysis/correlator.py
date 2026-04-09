"""
Correlator — temporal correlation between change events and incident signals.

Core question: "Did a deployment (or config change) happen shortly before
the first signal appeared? If so, how strong is that correlation?"

Correlation strength is based on:
  - Time delta between the change event and first signal
  - Whether the same service is involved in both
  - Whether multiple signals appeared after a single change

Design note: This is pure time arithmetic and rule-based scoring.
The LLM receives the correlation result as evidence, not the raw timestamps.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from pydantic import BaseModel

from agent.ingestion.change_event_parser import ChangeEvent, ParsedChangeEvents
from agent.analysis.signal_extractor import ExtractedSignal, SignalExtractionResult

logger = logging.getLogger(__name__)

# Default correlation window: signals within this many minutes of a
# change event are considered potentially correlated.
DEFAULT_CORRELATION_WINDOW_MINUTES = 30


# ── Schema ────────────────────────────────────────────────────────────────────

class CorrelatedChange(BaseModel):
    """A change event correlated with one or more signals."""

    change_event: ChangeEvent
    correlated_signals: list[str]      # Signal names that appeared after this change
    delta_seconds: float               # Time from change to first signal
    delta_human: str                   # e.g. "2m 16s"
    same_service: bool                 # Change and signals share a service
    strength: str                      # "high" / "medium" / "low" / "none"
    strength_reasoning: str            # One-sentence explanation


class CorrelationResult(BaseModel):
    """Full output of the correlation pass."""

    correlated_changes: list[CorrelatedChange]
    severity_score: int
    severity_estimate: str
    severity_reasoning: str
    incident_window_start: Any | None
    incident_window_end: Any | None

    @property
    def correlated_change_count(self) -> int:
        return len(self.correlated_changes)


# ── Correlator ────────────────────────────────────────────────────────────────

class Correlator:
    """
    Correlates change events with extracted signals using temporal proximity
    and service overlap.

    Usage:
        correlator = Correlator()
        result = correlator.correlate(parsed_changes, signal_result, threshold_result)
    """

    def __init__(
        self,
        correlation_window_minutes: int = DEFAULT_CORRELATION_WINDOW_MINUTES,
        severity_weights: dict | None = None,
        severity_score_thresholds: dict | None = None,
    ):
        self._window = timedelta(minutes=correlation_window_minutes)
        # Defaults — can be overridden with values from thresholds.yaml
        self._weights = severity_weights or {
            "critical": 10, "high": 5, "medium": 2, "low": 1
        }
        self._score_thresholds = severity_score_thresholds or {
            "P1": 15, "P2": 6, "P3": 1
        }

    def correlate(
        self,
        parsed_changes: ParsedChangeEvents,
        signal_result: SignalExtractionResult,
        threshold_result,       # ThresholdEvaluationResult — avoid circular import
    ) -> CorrelationResult:
        """
        Main correlation entry point.
        Correlates change events → signals, then computes severity estimate.
        """
        signals = signal_result.signals
        changes = parsed_changes.events

        # Determine the incident window from signal timestamps
        incident_start, incident_end = self._compute_incident_window(signals)

        # Correlate each change event against signals
        correlated: list[CorrelatedChange] = []
        for change in changes:
            result = self._correlate_change(change, signals)
            if result:
                correlated.append(result)

        # Score severity from signals + breaches
        score, estimate, reasoning = self._score_severity(
            signals=signals,
            threshold_result=threshold_result,
            has_correlated_change=len(correlated) > 0,
        )

        return CorrelationResult(
            correlated_changes=correlated,
            severity_score=score,
            severity_estimate=estimate,
            severity_reasoning=reasoning,
            incident_window_start=incident_start,
            incident_window_end=incident_end,
        )

    # ── Internal ──────────────────────────────────────────────────────────────

    def _correlate_change(
        self,
        change: ChangeEvent,
        signals: list[ExtractedSignal],
    ) -> CorrelatedChange | None:
        """
        Check if any signals appeared within the correlation window
        after this change event.
        """
        window_end = change.timestamp + self._window

        # Find signals whose first_seen falls after the change
        # and within the correlation window
        post_change_signals = [
            s for s in signals
            if change.timestamp <= s.first_seen <= window_end
        ]

        if not post_change_signals:
            return None

        first_signal_time = min(s.first_seen for s in post_change_signals)
        delta = (first_signal_time - change.timestamp).total_seconds()

        # Check service overlap
        affected_services = set()
        for s in post_change_signals:
            affected_services.update(s.services_affected)
        same_service = change.service in affected_services

        strength, reasoning = self._compute_strength(
            delta_seconds=delta,
            same_service=same_service,
            signal_count=len(post_change_signals),
            has_high_severity=any(s.severity == "high" for s in post_change_signals),
        )

        return CorrelatedChange(
            change_event=change,
            correlated_signals=[s.name for s in post_change_signals],
            delta_seconds=delta,
            delta_human=self._format_delta(delta),
            same_service=same_service,
            strength=strength,
            strength_reasoning=reasoning,
        )

    def _compute_strength(
        self,
        delta_seconds: float,
        same_service: bool,
        signal_count: int,
        has_high_severity: bool,
    ) -> tuple[str, str]:
        """
        Rule-based correlation strength scoring.

        HIGH:   same service + within 5 min + high-severity signals
        MEDIUM: same service or within 10 min + multiple signals
        LOW:    within window but weak overlap
        """
        delta_minutes = delta_seconds / 60

        if same_service and delta_minutes <= 5 and has_high_severity:
            return "high", (
                f"High-severity signals appeared {self._format_delta(delta_seconds)} "
                f"after deployment to the same service"
            )
        elif same_service and delta_minutes <= 10:
            return "high", (
                f"Signals in same service appeared {self._format_delta(delta_seconds)} "
                f"after the change event"
            )
        elif same_service and delta_minutes <= 30:
            return "medium", (
                f"Same-service signals appeared {self._format_delta(delta_seconds)} "
                f"after the change — within correlation window but not immediate"
            )
        elif delta_minutes <= 10 and signal_count >= 2:
            return "medium", (
                f"Multiple signals appeared {self._format_delta(delta_seconds)} "
                f"after change, different service — possible cascade"
            )
        else:
            return "low", (
                f"Signals appeared {self._format_delta(delta_seconds)} after change "
                f"— weak temporal overlap, different services"
            )

    def _score_severity(
        self,
        signals: list[ExtractedSignal],
        threshold_result,
        has_correlated_change: bool,
    ) -> tuple[int, str, str]:
        """
        Compute an overall severity score from signals and metric breaches.
        Returns (score, estimate, reasoning).
        """
        score = 0
        factors: list[str] = []

        # Score from extracted signals
        for signal in signals:
            weight = self._weights.get(signal.severity, 0)
            score += weight * min(signal.count, 3)  # Cap multiplier at 3

        # Score from metric breaches
        for breach in threshold_result.breaches:
            weight = self._weights.get(breach.severity, 0)
            score += weight
            if breach.severity in ("critical", "high"):
                factors.append(f"{breach.metric} breached {breach.severity} threshold (peak: {breach.peak_value})")

        # Bonus: correlated change event elevates confidence
        if has_correlated_change:
            score += 3
            factors.append("change event correlated with incident window")

        # Determine estimate
        if score >= self._score_thresholds.get("P1", 15):
            estimate = "P1"
        elif score >= self._score_thresholds.get("P2", 6):
            estimate = "P2"
        elif score >= self._score_thresholds.get("P3", 1):
            estimate = "P3"
        else:
            estimate = "P4"

        reasoning = f"Score {score}: " + ("; ".join(factors) if factors else "low signal volume")
        return score, estimate, reasoning

    def _compute_incident_window(
        self, signals: list[ExtractedSignal]
    ) -> tuple[Any, Any]:
        if not signals:
            return None, None
        start = min(s.first_seen for s in signals)
        end = max(s.last_seen for s in signals)
        return start, end

    @staticmethod
    def _format_delta(seconds: float) -> str:
        """Format a timedelta in seconds to a human-readable string."""
        seconds = int(seconds)
        if seconds < 60:
            return f"{seconds}s"
        minutes, secs = divmod(seconds, 60)
        if secs == 0:
            return f"{minutes}m"
        return f"{minutes}m {secs}s"