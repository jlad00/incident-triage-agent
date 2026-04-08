"""
Change event parser — normalizes deployment and configuration change records.

This is a critical input for temporal correlation:
  "Did a deployment happen N minutes before the incident started?"

Design note: The correlation logic lives in agent/analysis/correlator.py.
This module only normalizes the raw events.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


# ── Schema ───────────────────────────────────────────────────────────────────

ChangeType = Literal[
    "deployment",
    "config_change",
    "scaling_event",
    "certificate_rotation",
    "database_migration",
    "feature_flag",
    "other",
]


class ChangeEvent(BaseModel):
    """A single normalized change/deployment event."""

    timestamp: datetime
    type: ChangeType
    service: str
    version: str | None = None
    previous_version: str | None = None
    author: str | None = None
    environment: str | None = None
    change_summary: str | None = None
    ticket: str | None = None
    rollback_available: bool = False

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

    @field_validator("type", mode="before")
    @classmethod
    def normalize_type(cls, v: str) -> str:
        normalized = str(v).strip().lower().replace(" ", "_").replace("-", "_")
        valid = {
            "deployment", "config_change", "scaling_event",
            "certificate_rotation", "database_migration", "feature_flag",
        }
        return normalized if normalized in valid else "other"


class ParsedChangeEvents(BaseModel):
    """Result of parsing a change events file."""

    source_file: str
    total_events: int
    parsed_events: int
    skipped_events: int
    events: list[ChangeEvent]
    parse_warnings: list[str] = Field(default_factory=list)

    def events_for_service(self, service: str) -> list[ChangeEvent]:
        """Filter events to a specific service."""
        return [e for e in self.events if e.service == service.lower()]

    def events_in_window(
        self, start: datetime, end: datetime
    ) -> list[ChangeEvent]:
        """Return events that fall within [start, end]."""
        return [e for e in self.events if start <= e.timestamp <= end]


# ── Parser ───────────────────────────────────────────────────────────────────

class ChangeEventParser:
    """
    Parses a change events JSON file into normalized ChangeEvent objects.

    Usage:
        parser = ChangeEventParser()
        result = parser.parse(Path("scenarios/bad_deploy/changes.json"))
    """

    def parse(self, changes_file: Path) -> ParsedChangeEvents:
        if not changes_file.exists():
            raise FileNotFoundError(f"Changes file not found: {changes_file}")

        raw_data = self._load_json(changes_file)
        return self._parse_events(raw_data, source_file=str(changes_file))

    def parse_from_list(self, raw_entries: list[dict]) -> ParsedChangeEvents:
        """Parse from an already-loaded list (useful for testing)."""
        return self._parse_events(raw_entries, source_file="<in-memory>")

    # ── Internal ─────────────────────────────────────────────────────────────

    def _load_json(self, path: Path) -> list[dict]:
        try:
            with open(path) as f:
                data = json.load(f)
            if not isinstance(data, list):
                raise ValueError("Changes file must contain a JSON array at the top level")
            return data
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in changes file {path}: {e}") from e

    def _parse_events(
        self, raw_data: list[dict], source_file: str
    ) -> ParsedChangeEvents:
        events: list[ChangeEvent] = []
        warnings: list[str] = []
        skipped = 0

        for i, raw in enumerate(raw_data):
            try:
                event = ChangeEvent(**raw)
                events.append(event)
            except Exception as e:
                skipped += 1
                warnings.append(f"Skipped event {i}: {e}")
                logger.debug("Skipped change event %d: %s", i, e)

        # Sort by timestamp ascending
        events.sort(key=lambda e: e.timestamp)

        return ParsedChangeEvents(
            source_file=source_file,
            total_events=len(raw_data),
            parsed_events=len(events),
            skipped_events=skipped,
            events=events,
            parse_warnings=warnings,
        )