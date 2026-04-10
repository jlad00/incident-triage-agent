"""
Prompt Builder — converts the evidence packet into a structured LLM prompt.

Design principles:
  1. The LLM receives ONLY the evidence packet — never raw logs
  2. The system prompt defines the persona, output format, and constraints
  3. The user prompt injects the specific evidence for this incident
  4. Output format is strict JSON — validated downstream by response_parser.py
  5. Every hypothesis the LLM generates must cite evidence from the packet

The prompt is designed so that if the LLM ignores instructions and returns
free text, response_parser.py will catch it and raise a clear error.
"""

from __future__ import annotations

import json
from agent.evidence.packet_builder import EvidencePacket


# ── System Prompt ─────────────────────────────────────────────────────────────
# This is fixed across all incidents. It defines the agent's role,
# the output schema, and the constraints on reasoning.

SYSTEM_PROMPT = """You are an expert Site Reliability Engineer and incident responder with deep experience in distributed systems, cloud infrastructure, and production incident analysis.

You will be given a structured evidence packet produced by a deterministic analysis pipeline. The packet contains:
- Extracted signals from log pattern matching
- Metric threshold breaches
- Correlated deployment/change events
- A computed severity estimate

Your job is to synthesize this evidence into a clear, actionable incident triage report.

## Output Format

You MUST respond with ONLY valid JSON. No preamble, no explanation, no markdown fences.
The JSON must conform exactly to this schema:

{
  "summary": "string — 2-4 sentence plain-English summary of what happened, which services were affected, and the likely impact",
  "hypotheses": [
    {
      "rank": 1,
      "hypothesis": "string — concise statement of the probable root cause",
      "confidence": "high|medium|low",
      "evidence": ["string — cite specific signals, breaches, or events from the packet"],
      "reasoning": "string — 2-3 sentence explanation of why this evidence supports the hypothesis"
    }
  ],
  "next_steps": [
    {
      "priority": 1,
      "action": "string — specific, actionable investigation step",
      "rationale": "string — why this step will help confirm or rule out the top hypothesis"
    }
  ],
  "remediation_suggestions": [
    {
      "action": "string — specific remediation action",
      "condition": "string — when to apply this (e.g. 'if rollback is available', 'if db connection pool is confirmed exhausted')"
    }
  ],
  "severity_assessment": {
    "estimate": "P1|P2|P3|P4",
    "agrees_with_computed": true,
    "reasoning": "string — brief explanation of severity, note if you disagree with the computed estimate and why"
  },
  "confidence_note": "string — any caveats about confidence, data gaps, or alternative explanations worth flagging"
}

## Rules

1. Hypotheses must be ranked by likelihood. Rank 1 = most probable.
2. Every hypothesis evidence array must cite specific signals or breaches by name from the packet — do not invent evidence.
3. next_steps must be specific and actionable — not generic advice like "check the logs". Name specific commands, dashboards, or metrics to inspect.
4. If a deployment is correlated with the incident, always include a rollback assessment in remediation_suggestions.
5. Generate 1-3 hypotheses. Do not generate hypotheses without supporting evidence.
6. Generate 3-5 next_steps.
7. Generate 1-4 remediation_suggestions.
8. If the evidence is ambiguous, say so in confidence_note — do not overclaim.
9. Do not reproduce raw log messages verbatim — paraphrase or reference by signal name.
"""


# ── User Prompt Builder ───────────────────────────────────────────────────────

class PromptBuilder:
    """
    Converts an EvidencePacket into the user-turn prompt for the LLM.

    Usage:
        builder = PromptBuilder()
        system, user = builder.build(packet)
        response = llm_client.complete(system, user)
    """

    def build(self, packet: EvidencePacket) -> tuple[str, str]:
        """Return (system_prompt, user_prompt) tuple."""
        user_prompt = self._build_user_prompt(packet)
        return SYSTEM_PROMPT, user_prompt

    def _build_user_prompt(self, packet: EvidencePacket) -> str:
        sections: list[str] = []

        sections.append(self._section_header(packet))
        sections.append(self._section_signals(packet))
        sections.append(self._section_metric_breaches(packet))
        sections.append(self._section_correlated_changes(packet))
        sections.append(self._section_severity_context(packet))

        if packet.runbook_context:
            sections.append(self._section_runbook(packet))

        sections.append(self._section_footer(packet))

        return "\n\n".join(sections)

    # ── Sections ──────────────────────────────────────────────────────────────

    def _section_header(self, p: EvidencePacket) -> str:
        window_start = (
            p.incident_window_start.strftime("%Y-%m-%d %H:%M:%S UTC")
            if p.incident_window_start else "unknown"
        )
        window_end = (
            p.incident_window_end.strftime("%Y-%m-%d %H:%M:%S UTC")
            if p.incident_window_end else "unknown"
        )
        return f"""## Incident Evidence Packet
**Incident ID:** {p.incident_id}
**Scenario:** {p.scenario_name}
**Services Affected:** {", ".join(p.services_affected) or "unknown"}
**Incident Window:** {window_start} → {window_end}
**Evidence Sources:** {p.log_entry_count} log entries, {p.metric_sample_count} metric samples, {p.change_event_count} change events"""

    def _section_signals(self, p: EvidencePacket) -> str:
        if not p.signals:
            return "## Extracted Signals\nNo signals extracted from logs."

        lines = ["## Extracted Signals (from deterministic log pattern matching)"]
        lines.append(f"Total signals: {p.signal_count}")
        if p.unmatched_error_count:
            lines.append(f"⚠ {p.unmatched_error_count} ERROR/CRITICAL log entries matched no known pattern")
        lines.append("")

        for s in p.signals:
            first = s["first_seen"][:19].replace("T", " ") if s["first_seen"] else "?"
            last = s["last_seen"][:19].replace("T", " ") if s["last_seen"] else "?"
            lines.append(
                f"**[{s['severity'].upper()}] {s['name']}** ({s['category']})\n"
                f"  Description: {s['description']}\n"
                f"  Occurrences: {s['count']} | Services: {', '.join(s['services_affected'])}\n"
                f"  First seen: {first} | Last seen: {last}\n"
                f"  Evidence samples: {'; '.join(s['evidence_messages'][:2])}"
            )

        return "\n".join(lines)

    def _section_metric_breaches(self, p: EvidencePacket) -> str:
        if not p.metric_breaches:
            return "## Metric Threshold Breaches\nNo threshold breaches detected."

        lines = ["## Metric Threshold Breaches"]
        for b in p.metric_breaches:
            peak_ts = b["peak_timestamp"][:19].replace("T", " ") if b["peak_timestamp"] else "?"
            breach_start = b["breach_start"][:19].replace("T", " ") if b["breach_start"] else "?"
            lines.append(
                f"**[{b['severity'].upper()}] {b['metric']}** — {b['description']}\n"
                f"  Peak: {b['peak_value']} (threshold: {b['operator']} {b['threshold_value']})\n"
                f"  Breach started: {breach_start} | Peak at: {peak_ts}\n"
                f"  Samples in breach: {b['breach_sample_count']}"
            )

        return "\n".join(lines)

    def _section_correlated_changes(self, p: EvidencePacket) -> str:
        if not p.correlated_changes:
            return "## Correlated Change Events\nNo change events correlated with the incident window."

        lines = ["## Correlated Change Events"]
        for c in p.correlated_changes:
            ts = c["timestamp"][:19].replace("T", " ") if c["timestamp"] else "?"
            rollback = "YES" if c["rollback_available"] else "NO"
            lines.append(
                f"**[{c['strength'].upper()} CORRELATION] {c['change_type']}** → {c['service']}\n"
                f"  Version: {c.get('version') or 'unknown'} | Timestamp: {ts}\n"
                f"  Time to first signal: {c['delta_human']}\n"
                f"  Same service as signals: {c['same_service']}\n"
                f"  Rollback available: {rollback}\n"
                f"  Summary: {c.get('change_summary') or 'no summary'}\n"
                f"  Correlated signals: {', '.join(c['correlated_signals'])}\n"
                f"  Reasoning: {c['strength_reasoning']}"
            )

        return "\n".join(lines)

    def _section_severity_context(self, p: EvidencePacket) -> str:
        return (
            f"## Computed Severity Estimate\n"
            f"**Estimate:** {p.severity_estimate}\n"
            f"**Score:** {p.severity_score}\n"
            f"**Reasoning:** {p.severity_reasoning}\n"
            f"**Highest metric breach:** {p.highest_breach_severity or 'none'}\n\n"
            f"You may agree or disagree with this estimate. "
            f"If you disagree, explain why in severity_assessment.reasoning."
        )

    def _section_runbook(self, p: EvidencePacket) -> str:
        return (
            f"## Runbook / Known Issue Context\n"
            f"The following runbook context is available for this service. "
            f"Use it to inform next_steps and remediation_suggestions.\n\n"
            f"{p.runbook_context}"
        )

    def _section_footer(self, p: EvidencePacket) -> str:
        return (
            "## Your Task\n"
            "Analyze the evidence above and produce a triage report in the required JSON format.\n"
            "Remember:\n"
            "- Cite evidence by signal name (e.g. 'circuit_breaker_open', 'connection_pool_exhausted')\n"
            "- Make next_steps specific — name commands, metric queries, or dashboards\n"
            "- If a deployment is correlated, assess rollback as a remediation option\n"
            "- Respond with ONLY the JSON object — no markdown, no explanation"
        )