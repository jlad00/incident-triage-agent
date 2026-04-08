"""
Runbook loader — loads optional runbook / known-issue snippets.

These are passed as context to the LLM in Sprint 3 to ground
its recommendations in your org's actual procedures.

Format: plain text or markdown files in scenarios/<name>/runbook.md
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_RUNBOOK_CHARS = 4000  # Prevent runbook from dominating the LLM context


class RunbookLoader:
    """
    Loads runbook snippets from a text/markdown file.

    Usage:
        loader = RunbookLoader()
        text = loader.load(Path("scenarios/bad_deploy/runbook.md"))
    """

    def load(self, runbook_file: Path) -> str | None:
        """
        Load and return runbook text. Returns None if file doesn't exist.
        Truncates at MAX_RUNBOOK_CHARS to control token usage.
        """
        if not runbook_file.exists():
            logger.debug("No runbook found at %s — skipping", runbook_file)
            return None

        try:
            text = runbook_file.read_text(encoding="utf-8").strip()
            if len(text) > MAX_RUNBOOK_CHARS:
                logger.warning(
                    "Runbook truncated from %d to %d chars", len(text), MAX_RUNBOOK_CHARS
                )
                text = text[:MAX_RUNBOOK_CHARS] + "\n\n[... truncated ...]"
            return text if text else None
        except OSError as e:
            logger.warning("Could not read runbook %s: %s", runbook_file, e)
            return None

    def load_from_scenario_dir(self, scenario_dir: Path) -> str | None:
        """
        Convenience method — looks for runbook.md or runbook.txt
        in a scenario directory.
        """
        for name in ("runbook.md", "runbook.txt", "runbook"):
            candidate = scenario_dir / name
            if candidate.exists():
                return self.load(candidate)
        return None