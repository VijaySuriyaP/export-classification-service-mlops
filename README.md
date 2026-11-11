# Mini Project 07 — Export Classification Check Service

**Course:** UE24AM342AA5 — ML System Design and AgentOps  
**Domain:** Logistics and Trade Compliance  
**Project level:** Jackfruit (Level 3)  
**Repository status:** Platform, API, web client and feedback flow are implemented and tested. The classification
agents and corpus retrieval are in progress: until they land, the stub agent runner **always escalates and never
classifies**.

**Quick links:** [Setup guide](#10-local-setup-and-execution) · [API contract](docs/api-contract.md) ·
[Platform design](docs/platform.md) · [Runbook](docs/runbook.md) · [Incident log](docs/incident-log.md) ·
[Test matrix](docs/test-matrix.md) · [Demo checklist](docs/demo-checklist.md) · [Evidence](docs/evidence/README.md) ·
[SLO](docs/slo.md) · [Load test](docs/loadtest.md)

## 1. Project Overview

The Export Classification Check Service accepts a product description, validates it, proposes a Harmonized System (HS) tariff classification with supporting rules and citations, and states the applicable export-policy outcome. It is a **decision-support service**, not a declaration-filing system. Its result is a proposed classification; a licensed customs broker makes the final decision.

Classification must follow the General Rules for Interpretation (GRI) in numerical order and account for applicable section and chapter notes. The system must not classify a product by material alone when a heading names the article or a note changes the outcome.

### Objectives

- Accept product descriptions through a documented REST API and validate them before starting an agent run.
- Retrieve relevant tariff headings, GRI rules, section/chapter notes, and export-policy passages.
- Use at least two agents with distinct responsibilities and a documented hand-off.
- Verify every proposed answer and citation against retrieved source passages.
- Return a reasoned recommendation or escalate rather than fabricate an answer.
- Run locally in Docker Compose, with health checks, logs, metrics, and a reproducible setup guide.

## 2. Scope and Reference Corpus

Use the following corpus, staying within the chapters specified in the project brief:

1. Customs Tariff — General Rules for Interpretation.
2. Customs Tariff — Chapters 73 and 96, including applicable section and chapter notes.
3. DGFT Foreign Trade Policy export-policy schedule for those chapters.

Record each source's URL, version/date, retrieval date, parsing status, and indexed chunk count. If a document is scanned, perform OCR before indexing.

### Mandatory demonstration case

> **“Stainless steel vacuum flask with a moulded plastic outer body.”**

The service must search for a heading that names the article, apply the relevant GRI and notes in order, and cite the evidence used. It must not stop after finding a steel-related heading just because the description mentions stainless steel. Confirm the reference passages and expected outcome during Evaluation 1 before treating the result as the project's reference answer.

### Out of scope

- Filing customs declarations.
- Calculating duty or preparing shipping documents.
- Training a new model.
- Cloud deployment; the target deployment is local Docker Compose.
- Confident classification of products outside the supported chapters or unresolved cases that require specialist judgement.

## 3. Team and Equal Work Allocation

Each member owns **25% of the planned project contribution**. Vijay's share is intentionally the **least technically complex**: web-client integration, documentation, and test/demo coordination. The other workstreams own the core retrieval, classification reasoning, and service/platform implementation.

| Member name | SRN | Work owned | Share |
|---|---|---|---:|
| **Vijay Suriya P** | **PES1UG24AM320** | Web client and API integration; basic feedback flow/interface; README and setup guide; runbook and incident log; manual test matrix, smoke-test scripts, demo checklist and evidence/screenshots. Coordinate integration checks with the other members. | **25%** |
| **Utkarsh** | **PES1UG24AM310** | Corpus preparation and retrieval: collect/version the official corpus; parse and chunk documents; implement idempotent embedding/indexing; implement search and clause-lookup tools; document retrieval/chunking choices and corpus freshness; support the 20-scenario evaluation set with evidence passages. | **25%** |
| **VIKAS** | **PES1UG24AM329** | Agent reasoning and classification: implement at least two distinct agents and their hand-off; use LangGraph or CrewAI; apply GRI sequentially and consider notes/modifying provisions; implement the iterative search/verification loop, citation grounding, retry strategy, escalation logic, and the mandatory vacuum-flask case. | **25%** |
| **NIKHIL** | **PES1UG24AM311** | API and platform: FastAPI data contracts and ingestion validation/quarantine; asynchronous run submission and status polling with a queue; Docker Compose and health checks; timeouts, bounded retries/backoff, circuit breaker and fallback; structured logs, correlation IDs, tracing, metrics/dashboard, and load-test/scaling report. | **25%** |

**Contribution rule:** the percentages are the agreed ownership target, not a claim that all tasks have equal difficulty. Each member must commit code or artifacts for their area, review at least one teammate's change, and help validate the integrated application. Replace the three placeholder names/SRNs before submission. If the course requires strict team size, confirm the four-person arrangement with the instructor; the brief itself lists a team size of 2–3 students.

## 4. Planned Architecture

```text
Web Client
    |
    v
FastAPI API  ---> schema/range/category/cross-field validation
    |                              |
    | valid request                +--> rejected request quarantined with field errors
    v
Queue / Task Runner
    |
    v
Classification Agent <----> Corpus Search / Clause Lookup Tools
    |
    v
Verification Agent
    |                    \
    | supported answer    \ unsupported / ambiguous / out-of-scope
    v                      v
Recommendation + citations  Escalation with reason
    |
    v
Run status/result endpoint + structured logs, traces, metrics and dashboard
```

### Suggested service boundaries

The Docker Compose stack should contain at least four services and declare dependencies and health checks. A practical starting point is `api`, `agent-worker`, `redis` (queue), and a vector-store service or persistent vector-store volume; add the observability services needed for Prometheus, Grafana, and Jaeger/Tempo. Keep environment-specific credentials and model settings out of source control.

### Suggested stack from the project brief

- API: FastAPI.
- Agent orchestration: LangGraph or CrewAI.
- Retrieval orchestration: LangChain or LlamaIndex.
- Vector index: FAISS or Chroma.
- Embeddings: local `sentence-transformers`, such as `all-MiniLM-L6-v2`.
- Queue: Redis or another documented task runner.
- Observability: structured logs, OpenTelemetry, Prometheus, Grafana, and Jaeger or Grafana Tempo.
- Load testing: Locust with an LLM stub for controlled response delays.
- Packaging: Docker Compose.

The final choices and rejected alternatives must be explained in the project report.

## 5. Execution Plan

Prioritise a working end-to-end path by the end of **Week 2**, as the brief requires. Add advanced requirements after the base request-to-result path works.

| Phase | Activities | Exit criteria / evidence | Owner(s) |
|---|---|---|---|
| **0 — Repository and contract** | Agree folder structure, branches, issue board, data contract, environment-variable names and service boundaries. Create `.env.example` without secrets. | Clean clone instructions and an agreed request/response schema. | All; Vijay coordinates README |
| **1 — Corpus confirmation (Evaluation 1)** | Download the tariff GRI, Chapters 73 and 96 with notes, and the DGFT export-policy schedule. Parse/index initial documents. Locate passages relevant to the vacuum-flask case and agree the reference test values. | Corpus inventory, source/version log, reference passages, and project plan ready for Evaluation 1. | Members 2 and 3; all review |
| **2 — Minimum end-to-end system (by Week 2)** | Implement request validation, `POST /runs` returning a run ID, background execution, `GET /runs/{run_id}` status/result polling, retrieval, agent hand-off, verification, and the web client. Wire the services in Compose. | From a clean clone, `docker compose up --build` starts the system; one valid request returns a proposed result or a reasoned escalation. | All workstreams; Vijay runs integration checklist |
| **3 — Correctness and safe escalation** | Add sequential GRI handling, section/chapter-note searches, citation checks, a different-strategy retry, and specialist escalation for unresolved or out-of-scope cases. Add prompt-injection and output guardrails. | Vacuum-flask demo works from source passages; unsupported answers are not returned confidently; out-of-scope input escalates. | Members 2 and 3; Member 4 supports guardrails |
| **4 — Validation and resilience** | Enforce schema, type, range, category and cross-field checks before any agent run; quarantine rejects. Add per-call timeouts, bounded exponential backoff, circuit breaker and a defined fallback path. | Malformed request is rejected with field-level errors and no agent run; provider-failure behavior is demonstrable. | Member 4; Vijay adds test steps |
| **5 — Evaluation and observability** | Build a fixed batch evaluation set of at least 20 cases with expected outcomes and evidence passages. Compare two prompt versions; report groundedness, context relevance, faithfulness and hallucination rate before and after one deliberate change. Add run-level metrics, cost/token tracking, traces, dashboards and feedback aggregation. | Reproducible evaluation command and saved results; run ID reconstructs tool calls, passages, prompt version, latency, cost and verification. | Members 2–4; Vijay prepares report tables |
| **6 — Performance and release** | Declare a complete-run p95 SLO before measuring; use Locust and an LLM stub; measure RPS, p50/p95, bottleneck and the effect of scaling three workers. Measure cache effects and rate-limit behavior. Complete README, report, runbook, incident log and demo. | Reproducible setup; results against the predeclared target; final repository and demonstration checklist complete. | Member 4 leads load tests; Vijay coordinates release documentation; all review |

## 6. Functional Requirements Checklist

Track implementation honestly. Do not mark a requirement complete until there is code plus test/evidence.

- [x] **F1** — Documented REST interface and published data contract. *(docs/api-contract.md, `GET /contract`, `/docs`)*
- [x] **F2 / D1** — Boundary validation; rejected requests quarantined with field-level errors before agents start.
- [ ] **F3** — Identify the article and extract materials/entities needed for classification.
- [ ] **F4** — Retrieve candidate headings, including headings that name the article, with relevant notes.
- [ ] **F5** — Proposed classification grounded in the retrieved rule, heading and notes, with resolving citations.
- [ ] **F6** — Route to `clear-for-filing`, `seek-product-clarification`, or `specialist-classification-review`, with the basis stated.
- [ ] **F7** — Escalate when equally supported headings remain after GRI 3 or when the product is outside the supported chapters.
- [x] **F8** — Health, metrics and structured logs for the pipeline.

## 7. Agent and LLMOps Checklist

- [ ] **A1–A3** — LangGraph/CrewAI reasoning layer; retrieval is an agent-callable tool; at least three tools; bounded loop; two agents with a documented hand-off; verify every answer and citation before returning it.
- [x] **A4–A5** — FastAPI asynchronous run submission and queue; timeouts, bounded retry with exponential backoff, circuit breaker and graceful fallback.
- [ ] **A6–A8** — Per-agent success/steps/tool calls/latency/retry/uptime metrics; worker scaling measurement; simple browser client. *(A6 metrics and A8 browser client done; A7 scaling **measurement** waits for the load test)*
- [ ] **L1–L2** — LangChain/LlamaIndex retrieval and FAISS/Chroma indexing; documented chunking/embedding; re-embedding without rebuilding everything; versioned prompts and comparison of at least two variants.
- [ ] **L3** — At least 20 fixed evaluation scenarios, runnable from CLI, with expected outcome and evidence passage; report groundedness, context relevance, faithfulness and hallucination rate before/after a deliberate change.
- [ ] **L4–L6** — Prompt-injection defence for free text, output filtering and toxicity detection; token/cost accounting; feedback endpoint and quality-over-time tracking. *(done: injection screen, structural output policy, token/cost, feedback endpoint + summary by prompt version/day; open: toxicity detection)*

## 8. Platform, Reliability and Performance Checklist

- [ ] **D2** — Idempotent CLI corpus pipeline with index build time, corpus versions, chunk count and throughput metrics.
- [x] **D3–D4** — At least four Compose services, declared dependencies/health checks, documented deployment boundaries, and runtime-injected configuration via `.env.example`. Never commit real credentials.
- [x] **D5** — Structured logs, cross-service correlation ID, OpenTelemetry traces, Prometheus metrics and Grafana dashboard; reconstruct a full run from its ID.
- [ ] **D6** — Declare p95 complete-run SLO before tests; run Locust against a realistic-delay stub; report RPS, p50/p95, SLO result, failure concurrency and bottleneck.
- [ ] **D7** — Justified caching and per-client rate limiting; measure p95 and cost effects.
- [x] **D8** — Runbook for two likely failure modes and at least one genuine development incident-log entry. *(docs/runbook.md: 4 rehearsed failure modes; docs/incident-log.md: INC-001)*

## 9. API Contract (Initial Proposal)

The final contract must be documented and validated in code. The following paths are the proposed interface for the execution plan; adjust the README if implementation choices change.

### Submit a run

`POST /runs`

Example request:

```json
{
  "product_description": "Stainless steel vacuum flask with a moulded plastic outer body."
}
```

Expected behavior: validate the request; reject and quarantine invalid input without creating an agent run; otherwise enqueue work and return a run identifier immediately.

### Poll a run

`GET /runs/{run_id}`

The response should indicate a lifecycle status such as `queued`, `running`, `completed`, `escalated`, or `failed`. A completed response should include, where applicable, the proposed HS classification, the applied rule and reasoning summary, citations resolving to retrieved text, export-policy finding, route/recommendation, prompt version, and relevant trace/correlation identifier. Do not imply that the service files a declaration or replaces a licensed customs broker.

## 10. Local Setup and Execution

### Prerequisites

* Docker Desktop (Windows/macOS) or Docker Engine + Compose v2 (Linux), with about 4 GB of RAM for Docker.
* Git.
* Optional, for the test scripts: Python 3.11+.
* Free host ports 8080, 8000, 8001, 3000, 9090 and 16686. Any of them can be changed in `.env`.

### 1. Clone and configure

```bash
git clone https://github.com/VijaySuriyaP/export-classification-service-mlops.git
cd export-classification-service-mlops
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

macOS/Linux:

```bash
cp .env.example .env
```

The defaults work as they are: the model provider is the bundled LLM stub, so no API key is needed. For a real
provider, set `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL` in `.env`. **Never commit `.env`**; it is git-ignored.

### 2. Build and start

```bash
docker compose up -d --build --wait
```

`--wait` returns once every health check passes. The first build takes a few minutes. Then:

```bash
docker compose ps        # every service "Up", the ones with health checks "(healthy)"
```

### 3. Use it

| What | URL |
|---|---|
| **Web client** (submit, watch the run, see the result, give feedback) | <http://localhost:8080> |
| API (interactive OpenAPI docs) | <http://localhost:8000/docs> |
| API health / readiness | <http://localhost:8000/health> · <http://localhost:8000/ready> |
| Grafana dashboard "Export Classification – Overview" (anonymous view; admin login from `.env`) | <http://localhost:3000> |
| Jaeger traces | <http://localhost:16686> |
| Prometheus | <http://localhost:9090> |

From the command line (the same flow the web client uses):

```bash
# submit -> 202 {"run_id": "...", "status": "queued", "status_url": "/runs/<id>", ...}
curl -s -X POST localhost:8000/runs -H 'Content-Type: application/json' \
     -d '{"product_description": "Stainless steel vacuum flask with a moulded plastic outer body."}'

# poll until status is completed | escalated | failed
curl -s localhost:8000/runs/<run_id>

# full reconstruction: every step, tool call, passage and model call
curl -s localhost:8000/runs/<run_id>/events

# feedback on a finished run, and the aggregate
curl -s -X POST localhost:8000/runs/<run_id>/feedback -H 'Content-Type: application/json' -d '{"rating": "up"}'
curl -s localhost:8000/feedback/summary
```

On Windows PowerShell, use `curl.exe` (not the `curl` alias) and put the JSON in single quotes, with inner double
quotes escaped as `\"`, or use `Invoke-RestMethod`:

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8000/runs -ContentType 'application/json' `
  -Body '{"product_description": "Stainless steel vacuum flask with a moulded plastic outer body."}'
```

### 4. Verify the stack

```bash
python scripts/smoke_test.py          # standard library only; expect "9/9 checks passed"
```

The `breaker` check was verified with the short timers CI uses. Put these in `.env` before `docker compose up`:
`STUB_LATENCY_MS=150`, `STUB_LATENCY_JITTER_MS=50`, `LLM_MAX_ATTEMPTS=2`, `LLM_BACKOFF_BASE_S=0.2`,
`LLM_BACKOFF_MAX_S=1`, `CB_FAILURE_THRESHOLD=3`, `CB_RESET_TIMEOUT_S=5`. With the slower `.env.example` defaults,
skip that check: `python scripts/smoke_test.py --skip breaker`.

Unit and integration tests, no Docker needed:

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows; macOS/Linux: source .venv/bin/activate
pip install -r requirements/dev.txt
python -m pytest
```

### 5. Scale, stop, reset

```bash
docker compose up -d --scale agent-worker=3 --no-recreate   # A7
docker compose logs -f api agent-worker                      # JSON logs with correlation_id / run_id / trace_id
docker compose down                                          # stop (keeps runs, quarantine, feedback)
docker compose down -v                                       # stop and DELETE all data volumes
```

Something wrong? Start at [Runbook §0 Triage](docs/runbook.md#0-first-five-minutes-triage).

> **Teammate check (required by this README):** a teammate must follow this section from a clean clone without
> verbal help and record the result here. *Not done yet. Name, date, OS, outcome:* ______

## 11. Testing and Demonstration Plan

For every test, record **test ID, precondition/input, steps, expected result, actual result, pass/fail, and evidence path**.
This is done in [`docs/test-matrix.md`](docs/test-matrix.md). The evidence (command output and screenshots of the
real running system) is in [`docs/evidence/`](docs/evidence/README.md), and the commands, owners and current
readiness of each demo item are in [`docs/demo-checklist.md`](docs/demo-checklist.md). Ticked items below are
demonstrable today.

The final demonstration checklist follows the project brief:

1. [x] Start a working system using `docker compose up` from a clean clone. *(CI `stack` job; `docs/evidence/compose-ps-1-worker.txt`)*
2. [ ] Submit a valid request and show a routed recommendation with citations that resolve to retrieved text. *(path works; needs agents)*
3. [ ] Trace one complete run, including tool calls, queries, passages, prompt version, per-step latency and cost. *(works with the stub; needs real tools)*
4. [x] Submit a malformed request and show quarantine plus proof no agent run started.
5. [ ] Submit an unanswerable/out-of-scope case and show escalation rather than fabrication. *(needs agents)*
6. [ ] Show self-verification catching an unsupported answer, followed by another search or escalation. *(needs agents)*
7. [x] Show a prompt-injection attempt and the guardrail response.
8. [x] Simulate provider unavailability and show the circuit breaker and fallback path. *(fallback = safe escalation; no second provider configured)*
9. [ ] Show Locust results against the predeclared p95 target, failure concurrency, bottleneck and worker-scaling effect.
10. [ ] Demonstrate the stainless-steel vacuum-flask case with the rule, heading and notes cited.

Save screenshots of the actual running application, test output, dashboard, trace and load-test report. Do not use mock screenshots as evidence of a real run.

## 12. Repository Layout

```text
.
├── app/
│   ├── api/                 # FastAPI routes: runs, events, feedback, quarantine, ops
│   ├── contracts/           # Pydantic data contracts (request, result, run, feedback)
│   ├── agents/              # AgentRunner seam + stub runner (real agents land here)
│   ├── worker/              # Queue consumer, run recorder (limits, events, spans)
│   ├── llm/ , llm_stub/     # Resilient model client; OpenAI-compatible stub
│   ├── guardrails/          # Prompt-injection screen, output policy
│   └── common/              # Config, Redis stores, metrics, logging, tracing, resilience
├── web/                     # Browser client (static HTML/JS/CSS) + nginx config with /api/ proxy
├── docker/                  # One Dockerfile per service (api, worker, llm-stub, web)
├── observability/           # Prometheus config, Grafana provisioning + dashboard
├── loadtest/                # Locust file and machine-readable SLO
├── scripts/                 # smoke_test, capture_screenshots, run_loadtest, loadtest_report, build_dashboard
├── tests/platform/          # Unit, integration, config-consistency tests
├── docs/                    # Contract, platform, SLO, load test, runbook, incident log, test matrix, demo, evidence/
├── .github/workflows/ci.yml # pytest + full Compose stack smoke test
├── .env.example
├── compose.yaml
└── requirements/ , pyproject.toml
```

Still to come from the agent and retrieval workstreams: `app/tools/`, `app/retrieval/`, `app/evaluation/` and
`corpus/`.

## 13. Final Deliverables

- [ ] Corpus confirmation and project plan for Evaluation 1.
- [ ] Working local Docker Compose application and reproducible setup instructions.
- [ ] Source-grounded classification result and safe escalation path.
- [ ] Data contracts, `.env.example`, evaluation set and CLI runner.
- [ ] Test matrix, genuine screenshots/output, runbook and incident log.
- [ ] Evaluation report with prompt comparison and before/after metrics.
- [ ] p95 SLO, Locust/load/scaling report, cache and cost findings.
- [ ] Plain project report covering scope, agent topology/tools/verification/loop limits, deployment-boundary diagram, design choices and rejected alternatives, evaluation results, bottleneck, cost per 100 runs, and what did not work.
- [ ] Final GitHub repository at least one week before the presentation, plus live demonstration and viva preparation.

## 14. Development Rules

1. Get the minimum end-to-end path working before adding every advanced requirement.
2. Never return an unverified classification or a citation that does not resolve to retrieved evidence.
3. Escalate uncertain, unsupported, out-of-scope, or unresolved cases with a clear basis.
4. Keep provider credentials, private endpoints and model identifiers out of version control; use runtime configuration.
5. Make each feature reproducible with tests or a command, and update this README when the implementation changes.
6. Review and merge changes in small pull requests; every member should review at least one change outside their own workstream.

---

**Important:** This README is an implementation plan, not evidence that every checklist item is already complete. Update each checkbox only after implementation and verification.
