"""
Signal Extractor — pattern matches log entries against error_patterns.yaml.

For each log entry, runs every regex pattern and tags matching entries.
Returns a list of ExtractedSignal objects, deduplicated and sorted by severity.

Design note: This module is purely deterministic. No LLM, no inference.
Every signal can be explained by pointing to the exact log line and rule.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from agent.ingestion.log_parser import LogEntry, ParsedLogs

logger = logging.getLogger(__name__)

RULES_DIR = Path(__file__).parent / "rules"
DEFAULT_PATTERNS_FILE = RULES_DIR / "error_patterns.yaml"

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


# ── Schema ────────────────────────────────────────────────────────────────────

class ExtractedSignal(BaseModel):
    """A named signal extracted from one or more log entries."""

    name: str                          # Pattern name from YAML
    severity: str                      # high / medium / low
    category: str                      # memory / database / upstream / etc.
    description: str                   # Human-readable label
    count: int                         # How many log entries matched
    first_seen: Any                    # datetime of first match
    last_seen: Any                     # datetime of last match
    services_affected: list[str]       # Deduplicated list of services
    evidence_messages: list[str]       # Up to 3 representative messages
    matched_rule: str                  # Rule name for audit trail


class SignalExtractionResult(BaseModel):
    """Full output of the signal extraction pass."""

    total_log_entries: int
    signals: list[ExtractedSignal]
    unmatched_error_count: int         # ERROR/CRITICAL entries with no pattern match


# ── Extractor ─────────────────────────────────────────────────────────────────

class SignalExtractor:
    """
    Matches log entries against compiled regex patterns from error_patterns.yaml.

    Usage:
        extractor = SignalExtractor()
        result = extractor.extract(parsed_logs)
    """

    def __init__(self, patterns_file: Path = DEFAULT_PATTERNS_FILE):
        self._patterns = self._load_patterns(patterns_file)
        self._compiled = self._compile_patterns(self._patterns)

    def extract(self, parsed_logs: ParsedLogs) -> SignalExtractionResult:
        """Run all patterns against all log entries. Return extracted signals."""

        # Group matches by pattern name
        # { pattern_name: [LogEntry, ...] }
        matches: dict[str, list[LogEntry]] = defaultdict(list)

        error_entries = []
        matched_error_entries = set()   # track by index to find unmatched errors

        for idx, entry in enumerate(parsed_logs.entries):
            is_error = entry.level in ("ERROR", "CRITICAL")
            if is_error:
                error_entries.append(idx)

            for pattern in self._compiled:
                # Skip if pattern is service-restricted and doesn't match
                allowed = pattern.get("services", [])
                if allowed and entry.service not in allowed:
                    continue

                if pattern["regex"].search(entry.message):
                    matches[pattern["name"]].append(entry)
                    if is_error:
                        matched_error_entries.add(idx)

        # Build ExtractedSignal objects
        signals: list[ExtractedSignal] = []
        for pattern in self._patterns:
            name = pattern["name"]
            if name not in matches:
                continue

            matched_entries = matches[name]
            services = sorted(set(e.service for e in matched_entries))
            timestamps = [e.timestamp for e in matched_entries]

            # Collect up to 3 unique representative messages
            seen_msgs: set[str] = set()
            evidence: list[str] = []
            for e in matched_entries:
                if e.message not in seen_msgs:
                    seen_msgs.add(e.message)
                    evidence.append(e.message)
                if len(evidence) >= 3:
                    break

            signals.append(ExtractedSignal(
                name=name,
                severity=pattern["severity"],
                category=pattern["category"],
                description=pattern["description"],
                count=len(matched_entries),
                first_seen=min(timestamps),
                last_seen=max(timestamps),
                services_affected=services,
                evidence_messages=evidence,
                matched_rule=name,
            ))

        # Sort: severity first, then count descending
        signals.sort(key=lambda s: (SEVERITY_ORDER.get(s.severity, 99), -s.count))

        unmatched = len([i for i in error_entries if i not in matched_error_entries])

        return SignalExtractionResult(
            total_log_entries=parsed_logs.total_entries,
            signals=signals,
            unmatched_error_count=unmatched,
        )

    # ── Internal ──────────────────────────────────────────────────────────────

    def _load_patterns(self, path: Path) -> list[dict]:
        if not path.exists():
            raise FileNotFoundError(f"Error patterns file not found: {path}")
        with open(path) as f:
            data = yaml.safe_load(f)
        return data.get("patterns", [])

    def _compile_patterns(self, patterns: list[dict]) -> list[dict]:
        """Pre-compile all regexes at init time for performance."""
        compiled = []
        for p in patterns:
            try:
                entry = dict(p)
                entry["regex"] = re.compile(p["regex"], re.IGNORECASE)
                # Normalize service list to lowercase
                entry["services"] = [s.lower() for s in p.get("services", [])]
                compiled.append(entry)
            except re.error as e:
                logger.warning("Invalid regex in pattern '%s': %s", p.get("name"), e)
        return compiled