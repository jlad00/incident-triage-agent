"""
Metrics parser — normalizes metric snapshots to a common schema.

Expects Prometheus-compatible JSON export format:
  { "service": "...", "window_start": "...", "window_end": "...", "samples": [...] }

Design note: Breach detection (was CPU > threshold?) happens in
agent/analysis/threshold_evaluator.py, NOT here.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)


# ── Schema ───────────────────────────────────────────────────────────────────

class MetricSample(BaseModel):
    """A single point-in-time metrics snapshot."""

    timestamp: datetime
    cpu_percent: float | None = None
    mem_percent: float | None = None
    error_rate: float | None = None       # 0.0–1.0
    p99_latency_ms: float | None = None
    request_rate_rps: float | None = None
    restarts: int | None = None

    @field_validator("timestamp", mode="before")
    @classmethod
    def parse_timestamp(cls, v: Any) -> datetime:
        if isinstance(v, datetime):
            return v
        try:
            dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError) as e:
            raise ValueError(f"Cannot parse timestamp: {v!r}") from e

    @model_validator(mode="after")
    def clamp_percentages(self) -> "MetricSample":
        """Guard against bad data: percentages must be 0–100."""
        for field in ("cpu_percent", "mem_percent"):
            val = getattr(self, field)
            if val is not None and not (0 <= val <= 100):
                logger.warning("Clamping %s value %s to [0, 100]", field, val)
                setattr(self, field, max(0.0, min(100.0, val)))
        return self


class ParsedMetrics(BaseModel):
    """Result of parsing a metrics file."""

    source_file: str
    service: str
    window_start: datetime
    window_end: datetime
    sample_count: int
    samples: list[MetricSample]
    parse_warnings: list[str] = Field(default_factory=list)

    @property
    def duration_minutes(self) -> float:
        delta = self.window_end - self.window_start
        return delta.total_seconds() / 60


# ── Parser ───────────────────────────────────────────────────────────────────

class MetricsParser:
    """
    Parses a metrics JSON file into normalized MetricSample objects.

    Usage:
        parser = MetricsParser()
        result = parser.parse(Path("scenarios/bad_deploy/metrics.json"))
    """

    def parse(self, metrics_file: Path) -> ParsedMetrics:
        if not metrics_file.exists():
            raise FileNotFoundError(f"Metrics file not found: {metrics_file}")

        raw = self._load_json(metrics_file)
        return self._parse_metrics(raw, source_file=str(metrics_file))

    def parse_from_dict(self, raw: dict) -> ParsedMetrics:
        """Parse from an already-loaded dict (useful for testing)."""
        return self._parse_metrics(raw, source_file="<in-memory>")

    # ── Internal ─────────────────────────────────────────────────────────────

    def _load_json(self, path: Path) -> dict:
        try:
            with open(path) as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("Metrics file must contain a JSON object at the top level")
            return data
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in metrics file {path}: {e}") from e

    def _parse_metrics(self, raw: dict, source_file: str) -> ParsedMetrics:
        warnings: list[str] = []
        samples: list[MetricSample] = []

        for i, s in enumerate(raw.get("samples", [])):
            try:
                samples.append(MetricSample(**s))
            except Exception as e:
                warnings.append(f"Skipped sample {i}: {e}")
                logger.debug("Skipped metric sample %d: %s", i, e)

        # Sort by timestamp ascending
        samples.sort(key=lambda s: s.timestamp)

        return ParsedMetrics(
            source_file=source_file,
            service=raw.get("service", "unknown").strip().lower(),
            window_start=self._parse_dt(raw.get("window_start", "")),
            window_end=self._parse_dt(raw.get("window_end", "")),
            sample_count=len(samples),
            samples=samples,
            parse_warnings=warnings,
        )

    @staticmethod
    def _parse_dt(v: Any) -> datetime:
        if isinstance(v, datetime):
            return v
        try:
            dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError) as e:
            raise ValueError(f"Cannot parse datetime: {v!r}") from e