"""
JSON Reporter — writes the full triage output as a structured JSON file.

The JSON report is the machine-readable artifact. It combines:
  - The evidence packet (deterministic analysis)
  - The triage report (LLM reasoning)

This is what a downstream ticketing system, alert manager,
or dashboard would consume.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from agent.evidence.packet_builder import EvidencePacket
from agent.llm.response_parser import TriageReport

logger = logging.getLogger(__name__)


class JSONReporter:
    """
    Writes a combined evidence + triage JSON report to disk.

    Usage:
        reporter = JSONReporter(output_dir=Path("reports"))
        path = reporter.write(packet, report)
    """

    def __init__(self, output_dir: Path = Path("reports")):
        self._output_dir = output_dir

    def write(self, packet: EvidencePacket, report: TriageReport) -> Path:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"incident-{packet.incident_id}.json"
        output_path = self._output_dir / filename

        payload = {
            "incident_id": packet.incident_id,
            "scenario": packet.scenario_name,
            "generated_at": packet.generated_at.isoformat(),
            "severity": report.severity,
            "summary": report.summary,
            "hypotheses": [h.model_dump() for h in report.hypotheses],
            "next_steps": [s.model_dump() for s in report.next_steps],
            "remediation_suggestions": [r.model_dump() for r in report.remediation_suggestions],
            "severity_assessment": report.severity_assessment.model_dump(),
            "confidence_note": report.confidence_note,
            "evidence_packet": packet.model_dump(mode="json"),
        }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)

        logger.info("JSON report written: %s", output_path)
        return output_path