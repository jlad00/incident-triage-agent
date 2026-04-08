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
    console.print(Panel.fit(
        "[green]✔ Ingestion complete[/green]\n"
        "[dim]Next: Sprint 2 will add signal extraction and threshold evaluation[/dim]",
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


# ── Module entry ──────────────────────────────────────────────────────────────

def main():
    app()


if __name__ == "__main__":
    main()