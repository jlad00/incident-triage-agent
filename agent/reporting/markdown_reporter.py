"""
Markdown Reporter — writes a human-readable incident triage report.

This is what you'd post in a Slack incident channel, attach to a JIRA ticket,
or include in a postmortem. It's designed to be readable by anyone on-call,
not just the person who ran the tool.
"""

from __future__ import annotations

import logging
from pathlib import Path

from agent.evidence.packet_builder import EvidencePacket
from agent.llm.response_parser import TriageReport

logger = logging.getLogger(__name__)

CONFIDENCE_EMOJI = {"high": "🔴", "medium": "🟡", "low": "🟢"}
SEVERITY_EMOJI = {"P1": "🚨", "P2": "🔶", "P3": "🟡", "P4": "ℹ️"}


class MarkdownReporter:
    """
    Writes a human-readable Markdown triage report.

    Usage:
        reporter = MarkdownReporter(output_dir=Path("reports"))
        path = reporter.write(packet, report)
    """

    def __init__(self, output_dir: Path = Path("reports")):
        self._output_dir = output_dir

    def write(self, packet: EvidencePacket, report: TriageReport) -> Path:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"incident-{packet.incident_id}.md"
        output_path = self._output_dir / filename

        content = self._render(packet, report)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info("Markdown report written: %s", output_path)
        return output_path

    def render_to_string(self, packet: EvidencePacket, report: TriageReport) -> str:
        """Render to string without writing to disk (useful for display)."""
        return self._render(packet, report)

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _render(self, packet: EvidencePacket, report: TriageReport) -> str:
        sev_emoji = SEVERITY_EMOJI.get(report.severity, "❓")
        window_start = (
            packet.incident_window_start.strftime("%Y-%m-%d %H:%M:%S UTC")
            if packet.incident_window_start else "unknown"
        )
        window_end = (
            packet.incident_window_end.strftime("%Y-%m-%d %H:%M:%S UTC")
            if packet.incident_window_end else "unknown"
        )

        sections = [
            self._header(packet, report, sev_emoji, window_start, window_end),
            self._summary(report),
            self._hypotheses(report),
            self._next_steps(report),
            self._remediation(report),
            self._evidence_summary(packet),
            self._audit_trail(packet, report),
        ]
        return "\n\n---\n\n".join(sections)

    def _header(self, p, r, sev_emoji, window_start, window_end) -> str:
        return f"""# {sev_emoji} Incident Triage Report — {p.scenario_name}

| Field | Value |
|---|---|
| **Incident ID** | `{p.incident_id}` |
| **Severity** | **{r.severity}** |
| **Services Affected** | {", ".join(p.services_affected)} |
| **Incident Window** | {window_start} → {window_end} |
| **Generated** | {p.generated_at.strftime("%Y-%m-%d %H:%M:%S UTC")} |
| **LLM Provider** | {r.llm_provider or "unknown"} |"""

    def _summary(self, r: TriageReport) -> str:
        return f"## Summary\n\n{r.summary}"

    def _hypotheses(self, r: TriageReport) -> str:
        lines = ["## Root Cause Hypotheses\n"]
        for h in r.hypotheses:
            emoji = CONFIDENCE_EMOJI.get(h.confidence, "⚪")
            lines.append(f"### {h.rank}. {h.hypothesis}")
            lines.append(f"**Confidence:** {emoji} {h.confidence.capitalize()}\n")
            lines.append(f"**Reasoning:** {h.reasoning}\n")
            lines.append("**Supporting Evidence:**")
            for ev in h.evidence:
                lines.append(f"- {ev}")
            lines.append("")
        return "\n".join(lines)

    def _next_steps(self, r: TriageReport) -> str:
        lines = ["## Recommended Next Steps\n"]
        for s in r.next_steps:
            lines.append(f"**{s.priority}. {s.action}**")
            lines.append(f"> {s.rationale}\n")
        return "\n".join(lines)

    def _remediation(self, r: TriageReport) -> str:
        if not r.remediation_suggestions:
            return "## Remediation Suggestions\n\nNone identified at this time."
        lines = ["## Remediation Suggestions\n"]
        for rem in r.remediation_suggestions:
            lines.append(f"- **{rem.action}**")
            lines.append(f"  _{rem.condition}_\n")
        return "\n".join(lines)

    def _evidence_summary(self, p: EvidencePacket) -> str:
        lines = ["## Evidence Summary\n"]

        lines.append(f"**Signals Detected:** {p.signal_count}")
        for s in p.signals:
            lines.append(f"- `[{s['severity'].upper()}]` **{s['name']}** — {s['description']} ({s['count']} occurrence{'s' if s['count'] != 1 else ''})")

        lines.append(f"\n**Metric Breaches:** {p.breach_count}")
        for b in p.metric_breaches:
            lines.append(f"- `[{b['severity'].upper()}]` **{b['metric']}** — peak {b['peak_value']} (threshold: {b['operator']} {b['threshold_value']})")

        if p.correlated_changes:
            lines.append(f"\n**Correlated Changes:** {p.correlated_change_count}")
            for c in p.correlated_changes:
                lines.append(
                    f"- `[{c['strength'].upper()}]` {c['change_type']} → **{c['service']}** "
                    f"({c.get('version', 'unknown')}) — first signal {c['delta_human']} later"
                )
        else:
            lines.append("\n**Correlated Changes:** None")

        return "\n".join(lines)

    def _audit_trail(self, p: EvidencePacket, r: TriageReport) -> str:
        agrees = "✅ Agrees" if r.severity_assessment.agrees_with_computed else "⚠️ Disagrees"
        return f"""## Audit Trail

| Field | Value |
|---|---|
| **Log entries analyzed** | {p.log_entry_count} |
| **Metric samples analyzed** | {p.metric_sample_count} |
| **Change events analyzed** | {p.change_event_count} |
| **Unmatched error entries** | {p.unmatched_error_count} |
| **Computed severity** | {p.severity_estimate} (score: {p.severity_score}) |
| **LLM severity** | {r.severity} ({agrees} with computed) |
| **LLM confidence note** | {r.confidence_note} |

> This report was generated by the Incident Triage Agent.
> The deterministic analysis layer extracted all signals and breaches before any LLM call was made.
> All hypotheses are grounded in the evidence packet above."""