#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
# Incident Triage Agent — Demo Script
# Runs all 5 scenarios and saves reports.
# Usage: bash scripts/run_demo.sh [--no-llm]
# ─────────────────────────────────────────────────────────────────

set -e

NO_LLM=""
if [[ "$1" == "--no-llm" ]]; then
  NO_LLM="--no-llm"
  echo "Running in deterministic-only mode (--no-llm)"
fi

SCENARIOS=(
  "bad_deploy"
  "oom_kill"
  "cascade_failure"
  "cert_expiry"
  "noisy_neighbor"
)

PASS=0
FAIL=0

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Incident Triage Agent — Running ${#SCENARIOS[@]} scenarios"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

for scenario in "${SCENARIOS[@]}"; do
  echo ""
  echo "──────────────────────────────────────────────────────────────"
  echo "  Scenario: $scenario"
  echo "──────────────────────────────────────────────────────────────"

  if python -m agent.main "scenarios/$scenario" $NO_LLM; then
    echo "  ✔ $scenario completed"
    PASS=$((PASS + 1))
  else
    echo "  ✘ $scenario FAILED"
    FAIL=$((FAIL + 1))
  fi
done

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Results: $PASS passed, $FAIL failed"
if [[ -z "$NO_LLM" ]]; then
  echo "  Reports saved to: reports/"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if [[ $FAIL -gt 0 ]]; then
  exit 1
fi