"""
Response Parser — validates and parses the LLM's JSON output.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


class ParseError(Exception):
    def __init__(self, message: str, raw_response: str = ""):
        super().__init__(message)
        self.raw_response = raw_response


class Hypothesis(BaseModel):
    rank: int
    hypothesis: str
    confidence: str
    evidence: list[str]
    reasoning: str

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, v: str) -> str:
        normalized = v.lower().strip()
        return normalized if normalized in {"high", "medium", "low"} else "low"


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
        upper = v.upper().strip()
        return upper if upper in {"P1", "P2", "P3", "P4"} else "P3"


class TriageReport(BaseModel):
    summary: str
    hypotheses: list[Hypothesis] = Field(min_length=1)
    next_steps: list[NextStep] = Field(min_length=1)
    remediation_suggestions: list[RemediationSuggestion]
    severity_assessment: SeverityAssessment
    confidence_note: str
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


class ResponseParser:

    REQUIRED_FIELDS = [
        "summary", "hypotheses", "next_steps",
        "remediation_suggestions", "severity_assessment", "confidence_note",
    ]

    def parse(
        self,
        raw_response: str,
        incident_id: str = "",
        scenario_name: str = "",
        llm_provider: str = "",
    ) -> TriageReport:
        cleaned = self._strip_markdown_fences(raw_response.strip())
        cleaned = self._repair_json(cleaned)

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

        report.hypotheses.sort(key=lambda h: h.rank)
        report.next_steps.sort(key=lambda s: s.priority)
        return report

    @staticmethod
    def _strip_markdown_fences(text: str) -> str:
        pattern = r"^```(?:json)?\s*\n?(.*?)\n?```\s*$"
        match = re.match(pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip()
        return text

    @staticmethod
    def _repair_json(text: str) -> str:
        """Fix common JSON issues from local LLMs."""
        # Remove trailing commas before } or ]
        text = re.sub(r',(\s*[}\]])', r'\1', text)

        # Extract just the JSON object if prose surrounds it
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1 and end > start:
            text = text[start:end + 1]

        return text

    def _validate_required_fields(self, data: dict, raw: str) -> None:
        missing = [f for f in self.REQUIRED_FIELDS if f not in data]
        if missing:
            raise ParseError(
                f"LLM response missing required fields: {missing}",
                raw_response=raw,
            )
        if not isinstance(data.get("hypotheses"), list) or len(data["hypotheses"]) == 0:
            raise ParseError("LLM response must contain at least one hypothesis", raw_response=raw)
        if not isinstance(data.get("next_steps"), list) or len(data["next_steps"]) == 0:
            raise ParseError("LLM response must contain at least one next_step", raw_response=raw)