"""
Triage API routes.

POST /api/v1/triage
    Accept a full incident bundle as JSON and return a triage report.

POST /api/v1/triage/scenario/{scenario_name}
    Run triage against a named local scenario (for demos and testing).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agent.analysis.correlator import Correlator
from agent.analysis.signal_extractor import SignalExtractor
from agent.analysis.threshold_evaluator import ThresholdEvaluator
from agent.evidence.packet_builder import EvidencePacketBuilder
from agent.ingestion.change_event_parser import ChangeEventParser
from agent.ingestion.log_parser import LogParser
from agent.ingestion.metrics_parser import MetricsParser
from agent.ingestion.runbook_loader import RunbookLoader
from agent.llm.client import LLMClient, LLMError
from agent.llm.prompt_builder import PromptBuilder
from agent.llm.response_parser import ParseError, ResponseParser
from agent.reporting.json_reporter import JSONReporter
from agent.reporting.markdown_reporter import MarkdownReporter

load_dotenv()
logger = logging.getLogger(__name__)
router = APIRouter(tags=["triage"])

SCENARIOS_DIR = Path("scenarios")
REPORTS_DIR = Path(os.getenv("REPORT_OUTPUT_DIR", "reports"))


# ── Request / Response Models ─────────────────────────────────────────────────

class TriageRequest(BaseModel):
    """Incident bundle submitted to the triage endpoint."""

    scenario_name: str = Field(
        default="api-submission",
        description="Label for this incident (used in report filenames)"
    )
    logs: list[dict[str, Any]] = Field(
        default_factory=list,
        description="List of structured log entries"
    )
    metrics: dict[str, Any] | None = Field(
        default=None,
        description="Metrics object with service, window, and samples array"
    )
    changes: list[dict[str, Any]] = Field(
        default_factory=list,
        description="List of change/deployment events"
    )
    runbook: str | None = Field(
        default=None,
        description="Optional runbook text to include as LLM context"
    )
    skip_llm: bool = Field(
        default=False,
        description="If true, return only the deterministic evidence packet"
    )


class HypothesisResponse(BaseModel):
    rank: int
    hypothesis: str
    confidence: str
    evidence: list[str]
    reasoning: str


class NextStepResponse(BaseModel):
    priority: int
    action: str
    rationale: str


class RemediationResponse(BaseModel):
    action: str
    condition: str


class SeverityAssessmentResponse(BaseModel):
    estimate: str
    agrees_with_computed: bool
    reasoning: str


class TriageResponse(BaseModel):
    """Full triage report returned by the API."""

    incident_id: str
    scenario_name: str
    severity: str
    summary: str | None = None
    hypotheses: list[HypothesisResponse] = Field(default_factory=list)
    next_steps: list[NextStepResponse] = Field(default_factory=list)
    remediation_suggestions: list[RemediationResponse] = Field(default_factory=list)
    severity_assessment: SeverityAssessmentResponse | None = None
    confidence_note: str | None = None
    llm_provider: str | None = None
    evidence_packet: dict[str, Any]
    report_files: dict[str, str] = Field(default_factory=dict)
    llm_skipped: bool = False


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/triage", response_model=TriageResponse)
def triage(request: TriageRequest) -> TriageResponse:
    """
    Run full incident triage on a submitted incident bundle.
    Returns ranked hypotheses, next steps, and remediation suggestions.
    """
    return _run_triage(
        scenario_name=request.scenario_name,
        raw_logs=request.logs,
        raw_metrics=request.metrics,
        raw_changes=request.changes,
        runbook_text=request.runbook,
        skip_llm=request.skip_llm,
    )


@router.post("/triage/scenario/{scenario_name}", response_model=TriageResponse)
def triage_scenario(scenario_name: str, skip_llm: bool = False) -> TriageResponse:
    """
    Run triage against a named local scenario directory.
    Useful for demos and testing without constructing a full JSON payload.
    """
    scenario_dir = SCENARIOS_DIR / scenario_name
    if not scenario_dir.exists():
        available = [d.name for d in SCENARIOS_DIR.iterdir() if d.is_dir()]
        raise HTTPException(
            status_code=404,
            detail=f"Scenario '{scenario_name}' not found. Available: {available}",
        )

    # Load from files
    log_file = scenario_dir / "logs.json"
    metrics_file = scenario_dir / "metrics.json"
    changes_file = scenario_dir / "changes.json"

    import json

    raw_logs = json.loads(log_file.read_text()) if log_file.exists() else []
    raw_metrics = json.loads(metrics_file.read_text()) if metrics_file.exists() else None
    raw_changes = json.loads(changes_file.read_text()) if changes_file.exists() else []
    runbook_text = RunbookLoader().load_from_scenario_dir(scenario_dir)

    return _run_triage(
        scenario_name=scenario_name,
        raw_logs=raw_logs,
        raw_metrics=raw_metrics,
        raw_changes=raw_changes,
        runbook_text=runbook_text,
        skip_llm=skip_llm,
    )


# ── Pipeline ──────────────────────────────────────────────────────────────────

def _run_triage(
    scenario_name: str,
    raw_logs: list[dict],
    raw_metrics: dict | None,
    raw_changes: list[dict],
    runbook_text: str | None,
    skip_llm: bool,
) -> TriageResponse:
    """Core triage pipeline — shared by both endpoints."""

    # ── Parse ─────────────────────────────────────────────────────────────────
    parsed_logs = LogParser().parse_from_list(raw_logs)
    parsed_metrics = MetricsParser().parse_from_dict(raw_metrics) if raw_metrics else None
    parsed_changes = ChangeEventParser().parse_from_list(raw_changes)

    if parsed_logs.parsed_entries == 0:
        raise HTTPException(status_code=422, detail="No valid log entries could be parsed.")

    # ── Analyse ───────────────────────────────────────────────────────────────
    signal_result = SignalExtractor().extract(parsed_logs)
    threshold_result = ThresholdEvaluator().evaluate(parsed_metrics) if parsed_metrics else None
    correlation_result = Correlator().correlate(parsed_changes, signal_result, threshold_result)

    # ── Evidence Packet ───────────────────────────────────────────────────────
    packet = EvidencePacketBuilder().build(
        scenario_name=scenario_name,
        parsed_logs=parsed_logs,
        parsed_metrics=parsed_metrics,
        parsed_changes=parsed_changes,
        signal_result=signal_result,
        threshold_result=threshold_result,
        correlation_result=correlation_result,
        runbook_context=runbook_text,
    )

    evidence_dict = packet.model_dump(mode="json")

    if skip_llm:
        return TriageResponse(
            incident_id=packet.incident_id,
            scenario_name=scenario_name,
            severity=packet.severity_estimate,
            evidence_packet=evidence_dict,
            llm_skipped=True,
        )

    # ── LLM ───────────────────────────────────────────────────────────────────
    try:
        llm_client = LLMClient.from_env()
        system_prompt, user_prompt = PromptBuilder().build(packet)
        raw_response = llm_client.complete(system_prompt, user_prompt)
        provider_name = type(llm_client).__name__.replace("Client", "").lower()
        report = ResponseParser().parse(
            raw_response,
            incident_id=packet.incident_id,
            scenario_name=scenario_name,
            llm_provider=provider_name,
        )
    except LLMError as e:
        logger.error("LLM call failed: %s", e)
        raise HTTPException(status_code=502, detail=f"LLM unavailable: {e}")
    except ParseError as e:
        logger.error("LLM response parse failed: %s", e)
        raise HTTPException(status_code=502, detail=f"LLM response invalid: {e}")

    # ── Write Reports ─────────────────────────────────────────────────────────
    report_files: dict[str, str] = {}
    try:
        json_path = JSONReporter(REPORTS_DIR).write(packet, report)
        md_path = MarkdownReporter(REPORTS_DIR).write(packet, report)
        report_files = {"json": str(json_path), "markdown": str(md_path)}
    except Exception as e:
        logger.warning("Report write failed (non-fatal): %s", e)

    return TriageResponse(
        incident_id=packet.incident_id,
        scenario_name=scenario_name,
        severity=report.severity,
        summary=report.summary,
        hypotheses=[HypothesisResponse(**h.model_dump()) for h in report.hypotheses],
        next_steps=[NextStepResponse(**s.model_dump()) for s in report.next_steps],
        remediation_suggestions=[RemediationResponse(**r.model_dump()) for r in report.remediation_suggestions],
        severity_assessment=SeverityAssessmentResponse(**report.severity_assessment.model_dump()),
        confidence_note=report.confidence_note,
        llm_provider=report.llm_provider,
        evidence_packet=evidence_dict,
        report_files=report_files,
        llm_skipped=False,
    )