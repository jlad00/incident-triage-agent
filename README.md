# Incident Triage & Root Cause Agent

An AI-assisted incident triage system for platform and operations teams. Ingests logs, metrics, and deployment context — produces ranked root cause hypotheses with evidence citations, confidence levels, and actionable next steps.

Built as a portfolio project targeting **Platform Engineering**, **SRE**, and **Infrastructure / IT Ops** roles. Designed to look like something a real ops team could pilot internally.

---

## What It Does

```
Logs + Metrics + Change Events
         │
         ▼
┌─────────────────────────────┐
│  Deterministic Analysis     │  ← pattern matching, threshold evaluation,
│  (no LLM, fully auditable)  │    temporal correlation, severity scoring
└────────────┬────────────────┘
             │ structured evidence packet
             ▼
┌─────────────────────────────┐
│  LLM Reasoning Layer        │  ← synthesis, hypothesis ranking,
│  (Ollama or Anthropic)      │    next steps, remediation suggestions
└────────────┬────────────────┘
             │
             ▼
   Triage Report (JSON + Markdown)
   • Incident summary
   • Root cause hypotheses ranked by confidence
   • Evidence citations for every hypothesis
   • Specific next investigation steps
   • Remediation suggestions
   • Severity estimate (P1–P4)
```

**Key design decision:** The LLM receives only a structured evidence packet — never raw logs. Every hypothesis must cite signals extracted by the deterministic layer. If the LLM is unavailable, the system still produces a complete evidence packet and severity estimate from deterministic logic alone.

---

## Demo

Five simulation scenarios covering the most common production incident archetypes:

| Scenario | Root Cause | Key Signal |
|---|---|---|
| `bad_deploy` | Regression in v2.4.1 causing DB connection pool exhaustion | Deploy event 116s before first signal |
| `oom_kill` | ML inference container OOM killing under load | Memory climbing 61% → 83% → OOM → restart cycle |
| `cascade_failure` | Auth DB failure cascading across 4 downstream services | Circuit breaker open on auth-db-primary |
| `cert_expiry` | TLS certificate expired on payment-processor.internal | 100% error rate, CPU/mem completely normal |
| `noisy_neighbor` | Shared DB I/O contention affecting multiple services | Slow queries + timeouts, no code changes, no OOM |

**Run all 5 scenarios:**

```bash
# Deterministic only — fast, no API key needed
bash scripts/run_demo.sh --no-llm

# Full pipeline with LLM (requires Ollama or Anthropic API key)
bash scripts/run_demo.sh
```

---

## Quick Start

**Prerequisites:** Python 3.10+, Ollama (for local LLM)

```bash
# Clone and set up
git clone https://github.com/yourusername/incident-triage-agent
cd incident-triage-agent
python -m venv .venv

# Activate venv
source .venv/Scripts/activate   # Windows (Git Bash)
source .venv/bin/activate        # Linux / macOS

pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env: set LLM_PROVIDER=ollama and verify OLLAMA_BASE_URL

# Pull a local model
ollama pull llama3

# Run a single scenario
python -m agent.main scenarios/bad_deploy

# Run without LLM (deterministic analysis only)
python -m agent.main scenarios/bad_deploy --no-llm

# Start the API server
uvicorn api.app:app --host 0.0.0.0 --port 8000 --reload
```

---

## Architecture

```
incident-triage-agent/
├── agent/
│   ├── ingestion/          # Log, metrics, change event parsers
│   ├── analysis/           # Signal extractor, threshold evaluator, correlator
│   │   └── rules/          # error_patterns.yaml, thresholds.yaml
│   ├── evidence/           # Evidence packet builder
│   ├── llm/                # LLM client, prompt builder, response parser
│   └── reporting/          # JSON and Markdown report generators
├── api/                    # FastAPI wrapper
│   └── routes/
├── scenarios/              # Simulation scenarios
│   ├── bad_deploy/
│   ├── oom_kill/
│   ├── cascade_failure/
│   ├── cert_expiry/
│   └── noisy_neighbor/
├── tests/                  # Unit tests (76 passing)
└── scripts/                # Demo runner
```

### Deterministic vs. LLM — The Core Design Decision

| Concern | Approach | Rationale |
|---|---|---|
| Log parsing and normalization | **Deterministic** | Speed, reliability, testability |
| Error pattern matching | **Deterministic** (regex + YAML rules) | Auditable, no hallucination risk |
| Metrics threshold evaluation | **Deterministic** | Arithmetic, not reasoning |
| Temporal correlation (deploy → incident) | **Deterministic** | Time math, fully explainable |
| Severity scoring | **Deterministic** | Consistent, rule-based |
| Incident summary | **LLM** | Multi-signal narrative synthesis |
| Hypothesis ranking | **LLM** | Cross-signal pattern reasoning |
| Next investigation steps | **LLM** | Contextual, runbook-aware |
| Remediation suggestions | **LLM** | Domain knowledge + context |

This separation means:

- The deterministic layer is fully unit-tested and independently auditable
- The LLM provider can be swapped (Anthropic ↔ Ollama) without touching analysis logic
- If the LLM is unavailable, the system still delivers a structured evidence packet and severity estimate

---

## API

```bash
# Health check
GET /health

# Triage a named scenario (great for demos)
POST /api/v1/triage/scenario/{scenario_name}?skip_llm=false

# Triage a custom incident bundle
POST /api/v1/triage
Content-Type: application/json

{
  "scenario_name": "my-incident",
  "logs": [...],
  "metrics": {...},
  "changes": [...],
  "runbook": "optional runbook text"
}
```

Interactive docs available at `http://localhost:8000/docs`

---

## Example Output

See [`docs/example_report.md`](docs/example_report.md) for a real triage report generated from the `bad_deploy` scenario.

Excerpt from the deterministic analysis phase:

```
SIGNALS EXTRACTED: 8
  [HIGH]   null_pointer_exception      — payment-service x3 (02:12:15–02:13:05)
  [HIGH]   connection_pool_exhausted   — payment-service (02:12:18)
  [HIGH]   circuit_breaker_open        — payment-service (02:13:10)
  [HIGH]   upstream_http_5xx           — api-gateway x2 (02:12:22–02:12:25)
  [HIGH]   max_retries_exceeded        — api-gateway (02:12:28)

THRESHOLD BREACHES: 5
  error_rate     peaked at 0.81  (threshold >= 0.50)  breach started 02:14:00
  p99_latency_ms peaked at 9100ms (threshold >= 5000ms) breach started 02:14:00
  cpu_percent    peaked at 91%   (threshold >= 80%)   breach started 02:14:00

TEMPORAL CORRELATION:
  deployment v2.4.1 → payment-service @ 02:10:02
  First signal appeared 1m 56s later
  Correlation strength: HIGH (same service, within 5-minute window)

SEVERITY ESTIMATE: P1 (score: 83)
```

---

## Adding Real Integrations

The ingestion layer uses a simple adapter pattern. Connecting to a real observability stack means writing a new parser — not modifying the analysis or LLM layers.

```
Prometheus remote write  →  metrics_parser.py adapter   →  ParsedMetrics
Graylog / Loki API       →  log_parser.py adapter        →  ParsedLogs
Jenkins / ArgoCD         →  change_event_parser.py       →  ParsedChangeEvents
PagerDuty webhook        →  api/routes/triage.py trigger →  full pipeline
```

Priority integrations for a production deployment:

- Prometheus remote write receiver
- Graylog or Loki API pull
- PagerDuty webhook → automatic triage trigger
- Jira / ServiceNow ticket creation from report output

---

## Testing

```bash
# Run all tests
pytest tests/ -v

# With coverage report
pytest tests/ -v --cov=agent --cov-report=term-missing
```

76 tests covering all parsers, signal extractor, threshold evaluator, correlator, prompt builder, and response parser. The LLM layer is tested with fixture responses — no live API calls in the test suite.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.10+ |
| API framework | FastAPI |
| Data validation | Pydantic v2 |
| LLM (local) | Ollama (llama3) |
| LLM (cloud, optional) | Anthropic Claude |
| CLI | Typer + Rich |
| Config and rules | YAML (externalized — no code change needed to tune thresholds or add patterns) |
| Testing | pytest + pytest-cov |
| Linting | ruff |
| Containerization | Docker + Docker Compose |

---

## Background & Motivation

My background is in hybrid infrastructure, systems engineering, and platform-adjacent work — Windows, Linux, VMware, Active Directory, Entra ID, Azure, Prometheus, Grafana, Graylog, Jenkins, Terraform, Ansible.

This project demonstrates applying AI directly to that domain:

- **Platform engineering** — modular ingestion adapters, externalized rule configuration, Docker Compose local dev
- **Incident response** — realistic scenario library based on common production failure modes
- **Observability** — Prometheus-compatible metric format, structured JSON log schema
- **Operations automation** — CLI + REST API, runbook integration, structured report generation
- **AI-assisted workflows** — hybrid deterministic + LLM pipeline, evidence-cited output, graceful LLM degradation

The design philosophy throughout: use deterministic logic for everything that can be computed, and reserve the LLM for synthesis and reasoning where human-like judgment adds genuine value.

---

## Known Limitations & What's Next

**Current limitations:**

- Single-service metrics per incident bundle (multi-service metric correlation is a planned extension)
- Local model output quality varies — Anthropic Claude produces more precise evidence citations
- No persistent incident history — each run is stateless

**Planned extensions:**

- Streamlit UI for visual demo without the CLI
- Prometheus remote write adapter
- PagerDuty webhook integration
- Incident history with PostgreSQL backend
- Multi-service metric correlation across a single incident window