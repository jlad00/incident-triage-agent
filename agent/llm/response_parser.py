"""
Response Parser — validates and parses the LLM's JSON output.

The LLM is instructed to return strict JSON. This module:
  1. Strips any accidental markdown fences (LLMs sometimes add these)
  2. Parses the JSON
  3. Validates required fields are present and well-formed
  4. Returns a typed TriageReport object

If the LLM returns malformed output, ParseError is raised with
a message that includes the raw response for debugging.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


# ── Exceptions ────────────────────────────────────────────────────────────────

class ParseError(Exception):
    """Raised when the LLM response cannot be parsed or validated."""

    def __init__(self, message: str, raw_response: str = ""):
        super().__init__(message)
        self.raw_response = raw_response


# ── Output Schema ─────────────────────────────────────────────────────────────

class Hypothesis(BaseModel):
    rank: int
    hypothesis: str
    confidence: str
    evidence: list[str]
    reasoning: str

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, v: str) -> str:
        valid = {"high", "medium", "low"}
        normalized = v.lower().strip()
        if normalized not in valid:
            logger.warning("Unexpected confidence value '%s' — defaulting to 'low'", v)
            return "low"
        return normalized


class NextStep(BaseModel):
    priority: int
    action: str
    rationale: str


class RemediationSuggestion(BaseModel):
    action: str
    condition: str


class SeverityAssessment(BaseModel):
    estimate: str
    agrees_with_computed: bool
    reasoning: str

    @field_validator("estimate")
    @classmethod
    def validate_estimate(cls, v: str) -> str:
        valid = {"P1", "P2", "P3", "P4"}
        upper = v.upper().strip()
        if upper not in valid:
            logger.warning("Unexpected severity estimate '%s' — defaulting to 'P3'", v)
            return "P3"
        return upper


class TriageReport(BaseModel):
    """Validated, typed output from the LLM reasoning layer."""

    summary: str
    hypotheses: list[Hypothesis] = Field(min_length=1)
    next_steps: list[NextStep] = Field(min_length=1)
    remediation_suggestions: list[RemediationSuggestion]
    severity_assessment: SeverityAssessment
    confidence_note: str

    # Metadata added by the parser (not from LLM)
    incident_id: str = ""
    scenario_name: str = ""
    llm_provider: str = ""
    raw_response: str = Field(default="", exclude=True)

    @property
    def top_hypothesis(self) -> Hypothesis:
        return sorted(self.hypotheses, key=lambda h: h.rank)[0]

    @property
    def severity(self) -> str:
        return self.severity_assessment.estimate


# ── Parser ────────────────────────────────────────────────────────────────────

class ResponseParser:
    """
    Parses and validates the raw LLM text response into a TriageReport.

    Usage:
        parser = ResponseParser()
        report = parser.parse(raw_llm_text, incident_id="abc123")
    """

    def parse(
        self,
        raw_response: str,
        incident_id: str = "",
        scenario_name: str = "",
        llm_provider: str = "",
    ) -> TriageReport:
        """
        Parse raw LLM output into a TriageReport.
        Raises ParseError if the output is invalid.
        """
        cleaned = self._strip_markdown_fences(raw_response.strip())

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise ParseError(
                f"LLM response is not valid JSON: {e}",
                raw_response=raw_response,
            ) from e

        self._validate_required_fields(data, raw_response)

        try:
            report = TriageReport(
                **data,
                incident_id=incident_id,
                scenario_name=scenario_name,
                llm_provider=llm_provider,
                raw_response=raw_response,
            )
        except Exception as e:
            raise ParseError(
                f"LLM response failed schema validation: {e}",
                raw_response=raw_response,
            ) from e

        # Sort hypotheses by rank ascending
        report.hypotheses.sort(key=lambda h: h.rank)
        # Sort next steps by priority ascending
        report.next_steps.sort(key=lambda s: s.priority)

        logger.debug(
            "Parsed triage report: %d hypotheses, severity %s",
            len(report.hypotheses),
            report.severity,
        )
        return report

    # ── Internal ──────────────────────────────────────────────────────────────

    @staticmethod
    def _strip_markdown_fences(text: str) -> str:
        """
        Strip ```json ... ``` or ``` ... ``` fences that LLMs sometimes add
        despite being told not to.
        """
        # Match opening fence with optional language tag
        pattern = r"^```(?:json)?\s*\n?(.*?)\n?```\s*$"
        match = re.match(pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip()
        return text

    REQUIRED_FIELDS = [
        "summary",
        "hypotheses",
        "next_steps",
        "remediation_suggestions",
        "severity_assessment",
        "confidence_note",
    ]

    def _validate_required_fields(self, data: dict[str, Any], raw: str) -> None:
        missing = [f for f in self.REQUIRED_FIELDS if f not in data]
        if missing:
            raise ParseError(
                f"LLM response missing required fields: {missing}",
                raw_response=raw,
            )

        if not isinstance(data.get("hypotheses"), list) or len(data["hypotheses"]) == 0:
            raise ParseError(
                "LLM response must contain at least one hypothesis",
                raw_response=raw,
            )

        if not isinstance(data.get("next_steps"), list) or len(data["next_steps"]) == 0:
            raise ParseError(
                "LLM response must contain at least one next_step",
                raw_response=raw,
            )