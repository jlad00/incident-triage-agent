"""
Threshold Evaluator — checks metric samples against thresholds.yaml.

For each metric in each sample, compares the value against configured
thresholds and records breaches with severity, peak value, and timing.

Design note: Pure arithmetic — no inference. Every breach can be
explained by pointing to the exact sample, metric, and threshold rule.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from agent.ingestion.metrics_parser import MetricSample, ParsedMetrics

logger = logging.getLogger(__name__)

RULES_DIR = Path(__file__).parent / "rules"
DEFAULT_THRESHOLDS_FILE = RULES_DIR / "thresholds.yaml"


# ── Schema ────────────────────────────────────────────────────────────────────

class ThresholdBreach(BaseModel):
    """A single metric that exceeded a configured threshold."""

    metric: str                  # e.g. "cpu_percent"
    description: str             # Human-readable metric label
    severity: str                # critical / high / medium / low
    threshold_value: float       # The threshold that was breached
    peak_value: float            # Highest observed value in window
    peak_timestamp: Any          # datetime of the peak
    breach_start: Any            # datetime of first breach sample
    breach_sample_count: int     # How many samples breached this level
    operator: str                # ">=" etc.


class ThresholdEvaluationResult(BaseModel):
    """Full output of the threshold evaluation pass."""

    service: str
    window_minutes: float
    sample_count: int
    breaches: list[ThresholdBreach]
    highest_severity: str | None       # "critical" / "high" / "medium" / "low" / None


SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


# ── Evaluator ─────────────────────────────────────────────────────────────────

class ThresholdEvaluator:
    """
    Evaluates metric samples against thresholds.yaml breach rules.

    Reports only the highest breach level per metric (not every level),
    which keeps the evidence packet clean.

    Usage:
        evaluator = ThresholdEvaluator()
        result = evaluator.evaluate(parsed_metrics)
    """

    def __init__(self, thresholds_file: Path = DEFAULT_THRESHOLDS_FILE):
        self._config = self._load_config(thresholds_file)
        self._metric_rules = self._config.get("metrics", {})

    def evaluate(self, parsed_metrics: ParsedMetrics) -> ThresholdEvaluationResult:
        breaches: list[ThresholdBreach] = []

        for metric_name, rules in self._metric_rules.items():
            breach = self._evaluate_metric(
                metric_name=metric_name,
                description=rules.get("description", metric_name),
                thresholds=rules.get("thresholds", []),
                samples=parsed_metrics.samples,
            )
            if breach:
                breaches.append(breach)

        # Sort by severity
        breaches.sort(key=lambda b: SEVERITY_ORDER.get(b.severity, 99))

        highest = breaches[0].severity if breaches else None

        return ThresholdEvaluationResult(
            service=parsed_metrics.service,
            window_minutes=parsed_metrics.duration_minutes,
            sample_count=parsed_metrics.sample_count,
            breaches=breaches,
            highest_severity=highest,
        )

    # ── Internal ──────────────────────────────────────────────────────────────

    def _evaluate_metric(
        self,
        metric_name: str,
        description: str,
        thresholds: list[dict],
        samples: list[MetricSample],
    ) -> ThresholdBreach | None:
        """
        For a given metric, find the worst breach level across all samples.
        Returns one ThresholdBreach representing the highest severity breach,
        or None if no breach occurred.
        """
        # Collect (value, timestamp) for this metric across all samples
        observations: list[tuple[float, Any]] = []
        for sample in samples:
            val = getattr(sample, metric_name, None)
            if val is not None:
                observations.append((val, sample.timestamp))

        if not observations:
            return None

        # Sort thresholds by severity so we check most severe first
        sorted_thresholds = sorted(
            thresholds,
            key=lambda t: SEVERITY_ORDER.get(t["level"], 99)
        )

        for rule in sorted_thresholds:
            level = rule["level"]
            operator = rule["operator"]
            threshold_val = float(rule["value"])

            breaching = [
                (val, ts) for val, ts in observations
                if self._compare(val, operator, threshold_val)
            ]

            if not breaching:
                continue

            peak_val, peak_ts = max(breaching, key=lambda x: x[0])
            breach_start_ts = min(ts for _, ts in breaching)

            return ThresholdBreach(
                metric=metric_name,
                description=description,
                severity=level,
                threshold_value=threshold_val,
                peak_value=peak_val,
                peak_timestamp=peak_ts,
                breach_start=breach_start_ts,
                breach_sample_count=len(breaching),
                operator=operator,
            )

        return None  # No threshold breached

    @staticmethod
    def _compare(value: float, operator: str, threshold: float) -> bool:
        ops = {
            ">=": value >= threshold,
            ">":  value >  threshold,
            "<=": value <= threshold,
            "<":  value <  threshold,
            "==": value == threshold,
        }
        return ops.get(operator, False)

    def _load_config(self, path: Path) -> dict:
        if not path.exists():
            raise FileNotFoundError(f"Thresholds file not found: {path}")
        with open(path) as f:
            return yaml.safe_load(f)