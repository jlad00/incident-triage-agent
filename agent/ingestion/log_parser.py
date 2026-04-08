"""
Log parser — normalizes raw log entries to a common schema.

Handles:
  - Structured JSON logs (the primary format)
  - Basic validation and field normalization
  - Graceful handling of missing optional fields

Design note: This module does NO analysis. It only normalizes.
Signal extraction happens in agent/analysis/signal_extractor.py.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


# ── Schema ──────────────────────────────────────────────────────────────────

VALID_LEVELS = {"DEBUG", "INFO", "WARN", "WARNING", "ERROR", "CRITICAL", "FATAL"}

LEVEL_NORMALIZE = {
    "WARN": "WARN",
    "WARNING": "WARN",
    "FATAL": "CRITICAL",
}


class LogEntry(BaseModel):
    """A single normalized log entry."""

    timestamp: datetime
    service: str
    level: str
    message: str
    host: str | None = None
    trace_id: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)

    @field_validator("level", mode="before")
    @classmethod
    def normalize_level(cls, v: str) -> str:
        upper = str(v).upper()
        return LEVEL_NORMALIZE.get(upper, upper)

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

    @field_validator("service", mode="before")
    @classmethod
    def normalize_service(cls, v: str) -> str:
        return str(v).strip().lower()


class ParsedLogs(BaseModel):
    """Result of parsing a log file."""

    source_file: str
    total_entries: int
    parsed_entries: int
    skipped_entries: int
    entries: list[LogEntry]
    parse_warnings: list[str] = Field(default_factory=list)


# ── Parser ───────────────────────────────────────────────────────────────────

class LogParser:
    """
    Parses structured JSON log files into normalized LogEntry objects.

    Usage:
        parser = LogParser()
        result = parser.parse(Path("scenarios/bad_deploy/logs.json"))
    """

    def parse(self, log_file: Path) -> ParsedLogs:
        """Parse a JSON log file. Returns ParsedLogs with all entries."""
        if not log_file.exists():
            raise FileNotFoundError(f"Log file not found: {log_file}")

        raw_data = self._load_json(log_file)
        entries, warnings, skipped = self._parse_entries(raw_data)

        return ParsedLogs(
            source_file=str(log_file),
            total_entries=len(raw_data),
            parsed_entries=len(entries),
            skipped_entries=skipped,
            entries=entries,
            parse_warnings=warnings,
        )

    def parse_from_list(self, raw_entries: list[dict]) -> ParsedLogs:
        """Parse from an already-loaded list of dicts (useful for testing)."""
        entries, warnings, skipped = self._parse_entries(raw_entries)
        return ParsedLogs(
            source_file="<in-memory>",
            total_entries=len(raw_entries),
            parsed_entries=len(entries),
            skipped_entries=skipped,
            entries=entries,
            parse_warnings=warnings,
        )

    # ── Internal ─────────────────────────────────────────────────────────────

    def _load_json(self, path: Path) -> list[dict]:
        try:
            with open(path) as f:
                data = json.load(f)
            if not isinstance(data, list):
                raise ValueError("Log file must contain a JSON array at the top level")
            return data
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in log file {path}: {e}") from e

    def _parse_entries(
        self, raw_data: list[dict]
    ) -> tuple[list[LogEntry], list[str], int]:
        entries: list[LogEntry] = []
        warnings: list[str] = []
        skipped = 0

        for i, raw in enumerate(raw_data):
            try:
                entry = LogEntry(
                    timestamp=raw.get("timestamp", ""),
                    service=raw.get("service", "unknown"),
                    level=raw.get("level", "INFO"),
                    message=raw.get("message", ""),
                    host=raw.get("host"),
                    trace_id=raw.get("trace_id"),
                    raw=raw,
                )
                entries.append(entry)
            except Exception as e:
                skipped += 1
                warnings.append(f"Skipped entry {i}: {e}")
                logger.debug("Skipped log entry %d: %s", i, e)

        # Sort by timestamp ascending — logs may arrive out of order
        entries.sort(key=lambda e: e.timestamp)
        return entries, warnings, skipped