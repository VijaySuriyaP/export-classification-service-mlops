# Mini Project 07 — Export Classification Check Service

**Course:** UE24AM342AA5 — ML System Design and AgentOps  
**Domain:** Logistics and Trade Compliance  
**Project level:** Jackfruit (Level 3)  
**Repository status:** Execution plan / implementation checklist

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

- [ ] **F1** — Documented REST interface and published data contract.
- [ ] **F2 / D1** — Boundary validation; rejected requests quarantined with field-level errors before agents start.
- [ ] **F3** — Identify the article and extract materials/entities needed for classification.
- [ ] **F4** — Retrieve candidate headings, including headings that name the article, with relevant notes.
- [ ] **F5** — Proposed classification grounded in the retrieved rule, heading and notes, with resolving citations.
- [ ] **F6** — Route to `clear-for-filing`, `seek-product-clarification`, or `specialist-classification-review`, with the basis stated.
- [ ] **F7** — Escalate when equally supported headings remain after GRI 3 or when the product is outside the supported chapters.
- [ ] **F8** — Health, metrics and structured logs for the pipeline.

## 7. Agent and LLMOps Checklist

- [ ] **A1–A3** — LangGraph/CrewAI reasoning layer; retrieval is an agent-callable tool; at least three tools; bounded loop; two agents with a documented hand-off; verify every answer and citation before returning it.
- [ ] **A4–A5** — FastAPI asynchronous run submission and queue; timeouts, bounded retry with exponential backoff, circuit breaker and graceful fallback.
- [ ] **A6–A8** — Per-agent success/steps/tool calls/latency/retry/uptime metrics; worker scaling measurement; simple browser client.
- [ ] **L1–L2** — LangChain/LlamaIndex retrieval and FAISS/Chroma indexing; documented chunking/embedding; re-embedding without rebuilding everything; versioned prompts and comparison of at least two variants.
- [ ] **L3** — At least 20 fixed evaluation scenarios, runnable from CLI, with expected outcome and evidence passage; report groundedness, context relevance, faithfulness and hallucination rate before/after a deliberate change.
- [ ] **L4–L6** — Prompt-injection defence for free text, output filtering and toxicity detection; token/cost accounting; feedback endpoint and quality-over-time tracking.

## 8. Platform, Reliability and Performance Checklist

- [ ] **D2** — Idempotent CLI corpus pipeline with index build time, corpus versions, chunk count and throughput metrics.
- [ ] **D3–D4** — At least four Compose services, declared dependencies/health checks, documented deployment boundaries, and runtime-injected configuration via `.env.example`. Never commit real credentials.
- [ ] **D5** — Structured logs, cross-service correlation ID, OpenTelemetry traces, Prometheus metrics and Grafana dashboard; reconstruct a full run from its ID.
- [ ] **D6** — Declare p95 complete-run SLO before tests; run Locust against a realistic-delay stub; report RPS, p50/p95, SLO result, failure concurrency and bottleneck.
- [ ] **D7** — Justified caching and per-client rate limiting; measure p95 and cost effects.
- [ ] **D8** — Runbook for two likely failure modes and at least one genuine development incident-log entry.

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

These are the planned clean-clone steps. They become final run instructions once the service names, ports and environment variables are fixed in the implementation.

```bash
git clone <repository-url>
cd <repository-folder>
```

Create a local environment file from the template. On Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

On macOS/Linux:

```bash
cp .env.example .env
```

Fill only the required local values in `.env`; never commit it. Then build and start the stack:

```bash
docker compose up --build
```

Useful checks:

```bash
docker compose ps
docker compose logs -f
```

Once the API health route and port are finalised, record the exact health-check URL and one example `curl` request here. The README must be verified by a teammate following it from a clean clone without verbal help.

## 11. Testing and Demonstration Plan

For every test, record **test ID, precondition/input, steps, expected result, actual result, pass/fail, and evidence path**.

The final demonstration checklist follows the project brief:

1. [ ] Start a working system using `docker compose up` from a clean clone.
2. [ ] Submit a valid request and show a routed recommendation with citations that resolve to retrieved text.
3. [ ] Trace one complete run, including tool calls, queries, passages, prompt version, per-step latency and cost.
4. [ ] Submit a malformed request and show quarantine plus proof no agent run started.
5. [ ] Submit an unanswerable/out-of-scope case and show escalation rather than fabrication.
6. [ ] Show self-verification catching an unsupported answer, followed by another search or escalation.
7. [ ] Show a prompt-injection attempt and the guardrail response.
8. [ ] Simulate provider unavailability and show the circuit breaker and fallback path.
9. [ ] Show Locust results against the predeclared p95 target, failure concurrency, bottleneck and worker-scaling effect.
10. [ ] Demonstrate the stainless-steel vacuum-flask case with the rule, heading and notes cited.

Save screenshots of the actual running application, test output, dashboard, trace and load-test report. Do not use mock screenshots as evidence of a real run.

## 12. Repository Layout (Suggested)

```text
.
├── app/
│   ├── api/                 # FastAPI routes and request/response contracts
│   ├── agents/              # Classification and verification agents
│   ├── tools/               # Corpus search, clause lookup, request-field lookup
│   ├── retrieval/           # Parsing, chunking, embeddings and index operations
│   ├── evaluation/          # Fixed scenarios and batch evaluation runner
│   └── observability/       # Logging, tracing and metrics helpers
├── corpus/                  # Source inventory and permitted source documents/notes
├── tests/                   # Unit, integration, API and safety tests
├── scripts/                 # Ingest, evaluate, smoke-test and load-test commands
├── dashboards/              # Grafana dashboard/configuration
├── docs/                    # Architecture, decisions, runbook and incident log
├── .env.example
├── compose.yaml
├── README.md
└── requirements.txt or pyproject.toml
```

Adapt this layout if needed, but keep ingestion, agents, API, evaluations, tests and deployment configuration easy to find.

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
