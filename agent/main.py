"""
Incident Triage Agent — CLI entrypoint (Sprint 1)

Usage:
    python -m agent.main scenarios/bad_deploy
    python -m agent.main scenarios/bad_deploy --json

Sprint 1: loads and parses all inputs, prints normalized output.
Sprint 2: adds signal extraction.
Sprint 3: adds LLM reasoning.
Sprint 4: adds reports + API.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import print as rprint

from agent.ingestion.log_parser import LogParser
from agent.ingestion.metrics_parser import MetricsParser
from agent.ingestion.change_event_parser import ChangeEventParser
from agent.ingestion.runbook_loader import RunbookLoader

import yaml
from agent.analysis.signal_extractor import SignalExtractor
from agent.analysis.threshold_evaluator import ThresholdEvaluator
from agent.analysis.correlator import Correlator
from agent.evidence.packet_builder import EvidencePacketBuilder

from dotenv import load_dotenv
from agent.llm.client import LLMClient, LLMError
from agent.llm.prompt_builder import PromptBuilder
from agent.llm.response_parser import ResponseParser, ParseError
from agent.reporting.json_reporter import JSONReporter
from agent.reporting.markdown_reporter import MarkdownReporter

load_dotenv()

app = typer.Typer(
    name="triage-agent",
    help="Incident Triage & Root Cause Agent",
    add_completion=False,
)
console = Console()

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s | %(name)s | %(message)s",
)


@app.command()
def triage(
    scenario_dir: Path = typer.Argument(
        ..., help="Path to scenario directory containing logs.json, metrics.json, changes.json"
    ),
    output_json: bool = typer.Option(
        False, "--json", help="Output parsed data as raw JSON"
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show debug logging"
    ),
    no_llm: bool = typer.Option(
        False, "--no-llm", help="Skip LLM call, output evidence packet only"
    ),
    output_dir: Path = typer.Option(
        Path("reports"), "--output-dir", help="Directory to write reports"
    ),
):
    """
    Load and parse an incident scenario directory.
    Prints normalized ingestion output.
    """
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # ── Validate scenario directory ──────────────────────────────────────────
    if not scenario_dir.exists() or not scenario_dir.is_dir():
        console.print(f"[red]Error:[/red] Scenario directory not found: {scenario_dir}")
        raise typer.Exit(1)

    console.print(Panel.fit(
        f"[bold cyan]Incident Triage Agent[/bold cyan]\n"
        f"[dim]Scenario:[/dim] [yellow]{scenario_dir}[/yellow]",
        border_style="cyan"
    ))

    # ── Parse inputs ─────────────────────────────────────────────────────────
    results = {}

    # Logs
    log_file = scenario_dir / "logs.json"
    if log_file.exists():
        console.print("\n[bold]→ Parsing logs...[/bold]")
        try:
            parsed_logs = LogParser().parse(log_file)
            results["logs"] = parsed_logs
            _print_log_summary(parsed_logs)
        except Exception as e:
            console.print(f"[red]Log parse error:[/red] {e}")
    else:
        console.print("[dim]No logs.json found — skipping[/dim]")

    # Metrics
    metrics_file = scenario_dir / "metrics.json"
    if metrics_file.exists():
        console.print("\n[bold]→ Parsing metrics...[/bold]")
        try:
            parsed_metrics = MetricsParser().parse(metrics_file)
            results["metrics"] = parsed_metrics
            _print_metrics_summary(parsed_metrics)
        except Exception as e:
            console.print(f"[red]Metrics parse error:[/red] {e}")
    else:
        console.print("[dim]No metrics.json found — skipping[/dim]")

    # Change events
    changes_file = scenario_dir / "changes.json"
    if changes_file.exists():
        console.print("\n[bold]→ Parsing change events...[/bold]")
        try:
            parsed_changes = ChangeEventParser().parse(changes_file)
            results["changes"] = parsed_changes
            _print_changes_summary(parsed_changes)
        except Exception as e:
            console.print(f"[red]Changes parse error:[/red] {e}")
    else:
        console.print("[dim]No changes.json found — skipping[/dim]")

    # Runbook
    runbook_text = RunbookLoader().load_from_scenario_dir(scenario_dir)
    if runbook_text:
        console.print(f"\n[bold]→ Runbook loaded[/bold] [dim]({len(runbook_text)} chars)[/dim]")
        results["runbook"] = runbook_text

    # ── JSON output mode ─────────────────────────────────────────────────────
    if output_json:
        output = {}
        if "logs" in results:
            output["logs"] = results["logs"].model_dump(mode="json")
        if "metrics" in results:
            output["metrics"] = results["metrics"].model_dump(mode="json")
        if "changes" in results:
            output["changes"] = results["changes"].model_dump(mode="json")
        if "runbook" in results:
            output["runbook"] = results["runbook"]
        print(json.dumps(output, indent=2, default=str))
        return

    # ── Summary footer ───────────────────────────────────────────────────────
    # ── Analysis ─────────────────────────────────────────────────────────────
    console.print("\n[bold]→ Extracting signals...[/bold]")
    if "logs" in results:
        signal_result = SignalExtractor().extract(results["logs"])
        results["signals"] = signal_result
        _print_signals(signal_result)
    
    if "metrics" in results:
        console.print("\n[bold]→ Evaluating thresholds...[/bold]")
        threshold_result = ThresholdEvaluator().evaluate(results["metrics"])
        results["thresholds"] = threshold_result
        _print_breaches(threshold_result)

    if "signals" in results and "changes" in results:
        console.print("\n[bold]→ Correlating change events...[/bold]")
        correlator = Correlator()
        correlation_result = correlator.correlate(
            results["changes"],
            results["signals"],
            results.get("thresholds"),
        )
        results["correlation"] = correlation_result
        _print_correlation(correlation_result)

    # ── Evidence Packet ───────────────────────────────────────────────────────
    if "signals" in results:
        packet = EvidencePacketBuilder().build(
            scenario_name=scenario_dir.name,
            parsed_logs=results["logs"],
            parsed_metrics=results.get("metrics"),
            parsed_changes=results.get("changes", ChangeEventParser().parse_from_list([])),
            signal_result=results["signals"],
            threshold_result=results.get("thresholds"),
            correlation_result=results.get("correlation"),
            runbook_context=results.get("runbook"),
        )
        results["packet"] = packet

    if output_json and "packet" in results:
        print(json.dumps(results["packet"].model_dump(mode="json"), indent=2, default=str))
        return

    console.print(Panel.fit(
        f"[green]✔ Analysis complete[/green]  "
        f"[yellow]{results.get('correlation', None) and results['correlation'].severity_estimate or '—'}[/yellow] severity\n"
        f"[dim]Signals: {len(results.get('signals', type('', (), {'signals': []})()).signals) if 'signals' in results else 0} | "
        f"Breaches: {len(results.get('thresholds', type('', (), {'breaches': []})()).breaches) if 'thresholds' in results else 0} | "
        f"Next: Sprint 3 adds LLM reasoning[/dim]",
        border_style="green"
    
    ))
    # ── LLM Reasoning ────────────────────────────────────────────────────────
    if "packet" not in results or no_llm:
        if no_llm:
            console.print("[dim]→ LLM skipped (--no-llm flag set)[/dim]")
        console.print(Panel.fit(
            "[green]✔ Deterministic analysis complete[/green]\n"
            "[dim]Run without --no-llm to add LLM reasoning[/dim]",
            border_style="green"
        ))
        return

    packet = results["packet"]
    console.print("\n[bold]→ Running LLM reasoning...[/bold]")

    try:
        llm_client = LLMClient.from_env()
        system_prompt, user_prompt = PromptBuilder().build(packet)

        with console.status("[dim]Waiting for LLM response...[/dim]"):
            raw_response = llm_client.complete(system_prompt, user_prompt)

        provider_name = type(llm_client).__name__.replace("Client", "").lower()
        report = ResponseParser().parse(
            raw_response,
            incident_id=packet.incident_id,
            scenario_name=packet.scenario_name,
            llm_provider=provider_name,
        )

        # ── Print report to console ───────────────────────────────────────
        _print_triage_report(report)

        # ── Write reports ─────────────────────────────────────────────────
        json_path = JSONReporter(output_dir).write(packet, report)
        md_path = MarkdownReporter(output_dir).write(packet, report)
        console.print(f"\n[dim]Reports written:[/dim]")
        console.print(f"  [cyan]{json_path}[/cyan]")
        console.print(f"  [cyan]{md_path}[/cyan]")

    except LLMError as e:
        console.print(f"\n[red]LLM error:[/red] {e}")
        console.print("[dim]Tip: Check your .env file. Use --no-llm to run deterministic analysis only.[/dim]")
        raise typer.Exit(1)
    except ParseError as e:
        console.print(f"\n[red]LLM response parse error:[/red] {e}")
        if verbose:
            console.print(f"[dim]Raw response:\n{e.raw_response[:500]}[/dim]")
        raise typer.Exit(1)

    sev_color = "red" if report.severity == "P1" else "yellow" if report.severity == "P2" else "green"
    console.print(Panel.fit(
        f"[green]✔ Triage complete[/green]  "
        f"[{sev_color}]{report.severity}[/{sev_color}]  "
        f"[dim]{len(report.hypotheses)} hypothesis/es | "
        f"{len(report.next_steps)} next steps[/dim]",
        border_style="green"
    ))

# ── Print helpers ─────────────────────────────────────────────────────────────

def _print_log_summary(parsed):
    table = Table(show_header=True, header_style="bold magenta", box=None)
    table.add_column("Timestamp", style="dim", width=24)
    table.add_column("Service", width=20)
    table.add_column("Level", width=10)
    table.add_column("Message")

    level_colors = {
        "DEBUG": "dim",
        "INFO": "green",
        "WARN": "yellow",
        "ERROR": "red",
        "CRITICAL": "bold red",
    }

    for entry in parsed.entries:
        color = level_colors.get(entry.level, "white")
        table.add_row(
            entry.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            entry.service,
            f"[{color}]{entry.level}[/{color}]",
            entry.message[:80] + ("..." if len(entry.message) > 80 else ""),
        )

    console.print(table)
    console.print(
        f"[dim]  {parsed.parsed_entries}/{parsed.total_entries} entries parsed"
        + (f" | {parsed.skipped_entries} skipped" if parsed.skipped_entries else "")
        + "[/dim]"
    )
    if parsed.parse_warnings:
        for w in parsed.parse_warnings:
            console.print(f"[yellow]  ⚠ {w}[/yellow]")


def _print_metrics_summary(parsed):
    table = Table(show_header=True, header_style="bold magenta", box=None)
    table.add_column("Timestamp", style="dim", width=24)
    table.add_column("CPU%", width=7)
    table.add_column("Mem%", width=7)
    table.add_column("Err Rate", width=10)
    table.add_column("P99 Lat (ms)", width=14)
    table.add_column("Restarts", width=10)

    for s in parsed.samples:
        err_color = "red" if (s.error_rate or 0) > 0.1 else "green"
        cpu_color = "red" if (s.cpu_percent or 0) > 80 else "white"
        table.add_row(
            s.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            f"[{cpu_color}]{s.cpu_percent or '—'}[/{cpu_color}]",
            str(s.mem_percent or "—"),
            f"[{err_color}]{s.error_rate or '—'}[/{err_color}]",
            str(s.p99_latency_ms or "—"),
            str(s.restarts or "—"),
        )

    console.print(table)
    console.print(
        f"[dim]  Service: {parsed.service} | "
        f"Window: {parsed.duration_minutes:.0f} min | "
        f"{parsed.sample_count} samples[/dim]"
    )


def _print_changes_summary(parsed):
    table = Table(show_header=True, header_style="bold magenta", box=None)
    table.add_column("Timestamp", style="dim", width=24)
    table.add_column("Type", width=16)
    table.add_column("Service", width=20)
    table.add_column("Version", width=10)
    table.add_column("Author", width=14)
    table.add_column("Summary")

    for event in parsed.events:
        table.add_row(
            event.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            f"[yellow]{event.type}[/yellow]",
            event.service,
            event.version or "—",
            event.author or "—",
            (event.change_summary or "")[:60],
        )

    console.print(table)
    console.print(f"[dim]  {parsed.parsed_events} events parsed[/dim]")

def _print_signals(result):
    sev_colors = {"high": "red", "medium": "yellow", "low": "dim"}
    table = Table(show_header=True, header_style="bold magenta", box=None)
    table.add_column("Severity", width=10)
    table.add_column("Signal", width=30)
    table.add_column("Category", width=18)
    table.add_column("Count", width=7)
    table.add_column("Services")

    for s in result.signals:
        color = sev_colors.get(s.severity, "white")
        table.add_row(
            f"[{color}]{s.severity.upper()}[/{color}]",
            s.name,
            s.category,
            str(s.count),
            ", ".join(s.services_affected),
        )
    console.print(table)
    if result.unmatched_error_count:
        console.print(f"[yellow]  ⚠ {result.unmatched_error_count} ERROR/CRITICAL entries matched no pattern[/yellow]")


def _print_breaches(result):
    sev_colors = {"critical": "bold red", "high": "red", "medium": "yellow", "low": "dim"}
    table = Table(show_header=True, header_style="bold magenta", box=None)
    table.add_column("Severity", width=10)
    table.add_column("Metric", width=22)
    table.add_column("Peak Value", width=12)
    table.add_column("Threshold", width=12)
    table.add_column("Breach Start")

    for b in result.breaches:
        color = sev_colors.get(b.severity, "white")
        table.add_row(
            f"[{color}]{b.severity.upper()}[/{color}]",
            b.metric,
            str(b.peak_value),
            f"{b.operator} {b.threshold_value}",
            b.breach_start.strftime("%H:%M:%S") if b.breach_start else "—",
        )
    if not result.breaches:
        console.print("[dim]  No threshold breaches detected[/dim]")
    else:
        console.print(table)


def _print_correlation(result):
    if not result.correlated_changes:
        console.print("[dim]  No change events correlated with incident window[/dim]")
        return

    for c in result.correlated_changes:
        strength_color = {"high": "red", "medium": "yellow", "low": "dim"}.get(c.strength, "white")
        console.print(
            f"  [{strength_color}]{c.strength.upper()} correlation[/{strength_color}]"
            f" — {c.change_event.type} to [cyan]{c.change_event.service}[/cyan]"
            f" ({c.change_event.version or 'unknown version'})"
            f" → first signal [yellow]{c.delta_human}[/yellow] later"
        )
        console.print(f"  [dim]{c.strength_reasoning}[/dim]")

    console.print(
        f"\n  Severity estimate: [bold {'red' if result.severity_estimate == 'P1' else 'yellow'}]"
        f"{result.severity_estimate}[/bold {'red' if result.severity_estimate == 'P1' else 'yellow'}]"
        f"  (score: {result.severity_score})"
    )
    console.print(f"  [dim]{result.severity_reasoning}[/dim]")

def _print_triage_report(report):
    conf_colors = {"high": "red", "medium": "yellow", "low": "dim"}
    sev_color = {"P1": "bold red", "P2": "red", "P3": "yellow", "P4": "green"}.get(report.severity, "white")

    console.print(f"\n[bold]TRIAGE REPORT[/bold]")
    console.print(Panel(report.summary, title="Summary", border_style="cyan"))

    console.print("\n[bold magenta]Root Cause Hypotheses[/bold magenta]")
    for h in report.hypotheses:
        color = conf_colors.get(h.confidence, "white")
        console.print(f"  [{color}]{h.rank}. [{h.confidence.upper()}][/{color}] {h.hypothesis}")
        console.print(f"     [dim]{h.reasoning}[/dim]")
        for ev in h.evidence:
            console.print(f"     • {ev}")
        console.print()

    console.print("[bold magenta]Next Steps[/bold magenta]")
    for s in report.next_steps:
        console.print(f"  [cyan]{s.priority}.[/cyan] {s.action}")
        console.print(f"     [dim]{s.rationale}[/dim]")

    if report.remediation_suggestions:
        console.print("\n[bold magenta]Remediation[/bold magenta]")
        for r in report.remediation_suggestions:
            console.print(f"  • {r.action}")
            console.print(f"    [dim]{r.condition}[/dim]")

    console.print(
        f"\n[bold magenta]Severity:[/bold magenta] [{sev_color}]{report.severity}[/{sev_color}]"
        f"  [dim](LLM {'agrees' if report.severity_assessment.agrees_with_computed else 'disagrees'} with computed estimate)[/dim]"
    )
    console.print(f"[dim]{report.confidence_note}[/dim]")
    
# ── Module entry ──────────────────────────────────────────────────────────────

def main():
    app()


if __name__ == "__main__":
    main()