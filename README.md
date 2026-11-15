# Mini Project 07 — Export Classification Check Service

**Course:** UE24AM342AA5 — ML System Design and AgentOps
**Domain:** Logistics and Trade Compliance
**Project level:** Jackfruit (Level 3)
**Status audited:** 2026-11-15

> **Current state:** The API, web client, queue-backed worker, safeguards, observability, two-agent workflow, corpus retrieval pipeline, and indexed corpus are in the repository. The default configuration still selects the stub agent. A recent local run with the classification runner and retrieval adapter found relevant Chapter 96 and DGFT passages, but the bundled LLM stub returned “OK” instead of the JSON required by the classifier. The run correctly escalated without returning a code. Retrieval is integrated, but a valid end-to-end classification has not yet been demonstrated.

This service provides decision support. It does not file customs declarations, and a licensed customs broker makes the final decision.

**Quick links:** [Setup](#run-locally) · [API contract](docs/api-contract.md) · [Platform design](docs/platform.md) · [Classification runner](docs/classification-agents.md) · [Corpus](#corpus-and-retrieval) · [Test matrix](docs/test-matrix.md) · [Runbook](docs/runbook.md) · [Incident log](docs/incident-log.md) · [SLO](docs/slo.md) · [Load test](docs/loadtest.md) · [Demo checklist](docs/demo-checklist.md) · [Evidence](docs/evidence/README.md)

## Project goal

Accept a product description, validate it, retrieve relevant Indian Customs Tariff and DGFT export-policy passages, and return a supported HS classification or a clear escalation. Classification must follow the General Rules for Interpretation (GRI) and account for applicable section and chapter notes. The system must not assign a code based on material alone when the tariff names the article or a legal note changes the result.

The required demonstration case is:

> Stainless steel vacuum flask with a moulded plastic outer body.

The project must find and assess the heading for vacuum flasks, apply relevant rules and notes, and cite the evidence. The expected answer must be checked by the team against the official sources before it is treated as ground truth.

Out of scope: filing declarations, calculating duties, preparing shipping documents, training a new model, and confidently classifying unsupported or unresolved cases.

## Current implementation and remaining work

| Area | Present in the repository | Still needed |
|---|---|---|
| API and contract | FastAPI validation, quarantine, async run submission/status/events, feedback, readiness and metrics routes | Re-run integration checks after the latest configuration and refresh dated evidence |
| Worker and resilience | Redis-backed queue/state, worker heartbeats, bounded loops, timeouts, retries/backoff, circuit breaker and safe escalation | Confirm full stack from a clean clone |
| Web client | Submit/poll runs, display results/events, feedback, responsive layout and API proxy | Re-capture screenshots after a valid classification is available |
| Classification agents | LangGraph classifier → verifier workflow, bounded follow-up search, JSON schemas, GRI ordering checks and citation checks | Use a model that returns the required JSON; verify the vacuum-flask case and other scenarios against official sources |
| Corpus/retrieval | Five registered sources, parsing/chunking/embedding/index scripts, semantic/exact/hybrid search and agent adapter | OCR/review extraction gaps; synchronize manifest status/counts; expand independent evaluation and assess retrieval quality |
| Evaluation | Five retrieval questions and a preliminary Precision@5 report | At least 20 independently labeled classification scenarios, expected outcomes/evidence, CLI evaluation, prompt comparison and before/after metrics |
| Load/performance | Locust scenario, report tooling and proposed SLO are present | Record SLO declaration before measurement; run and save load/scaling results; measure cache/cost effects |
| Docs/release | API/platform docs, runbook, incident log, test matrix, demo checklist and historical evidence | Refresh docs/evidence to current configuration and produce the final project report |

The repository’s checked-in integration evidence and test matrix record a 134-test pass, a 9/9 smoke run, and a three-worker smoke run from 2026-11-11. These are historical results from the stub configuration, not proof that the current classifier returns a supported classification. See [evidence notes](docs/evidence/README.md) and the [test matrix](docs/test-matrix.md).

## Architecture

    Browser
      └── Web client and same-origin API proxy
            └── FastAPI: validate → quarantine invalid input / enqueue valid run
                  └── Redis: queue, run state, event log, feedback and cache
                        └── Agent worker
                              ├── LangGraph classifier → verifier
                              ├── corpus retrieval tool → FAISS + chunk metadata
                              └── OpenAI-compatible LLM provider or local stub

The default Compose stack has nine services: web, API, agent worker, Redis, LLM stub, Prometheus, Grafana, Jaeger and Redis exporter. Locust is available through the optional loadtest profile. The API and worker scale independently; scale agent workers with Compose. See [platform design](docs/platform.md) for service boundaries and [runbook](docs/runbook.md) for operational procedures.

### Classification and model configuration

The repository’s .env.example defaults to:

    AGENT_RUNNER=app.agents.stub_runner:StubAgentRunner
    RETRIEVAL_TOOL=

This stub is for platform smoke tests and failure demonstrations. It always escalates and does not classify.

The implemented classifier can be selected in .env with:

    AGENT_RUNNER=app.agents.classification_runner:ClassificationRunner
    RETRIEVAL_TOOL=app.retrieval.tool:search

The local LLM stub’s default response is “OK”; that response is not valid classifier/verifier JSON. With this response, the classifier rejects the output and escalates safely. For a classification attempt, configure an OpenAI-compatible model that follows the schemas in app/agents/classification_schemas.py. The stub supports canned responses, but this repository does not currently wire a response file into the Compose service; that needs a file mount and STUB_RESPONSES_FILE setting. Rebuild the agent-worker after changing its runner or retrieval configuration. Do not describe the classifier as working end to end until a result and its citations have been reviewed against the official documents.

The classifier requires a valid draft and verifier response. A code is returned only when schema/scope checks pass, citations resolve to passages retrieved during that run, and the verifier approves. Invalid output, missing evidence, unsupported citations, unresolved follow-up searches, and provider failures lead to safe escalation.

## Corpus and retrieval

The repository contains five source PDFs, parsed text, chunk metadata, normalized embeddings and a FAISS index. The current checked-in index contains **3,505 chunks/vectors** with **384 dimensions**, using sentence-transformers/all-MiniLM-L6-v2.

| Source | Version/date recorded | Pages parsed | Chunks |
|---|---|---:|---:|
| DGFT Notification 09/2025-26 | 2025-05-19 | 38 | 80 |
| Indian Customs Tariff, Chapter 73 | As on 2025-06-30 | 18 | 55 |
| Indian Customs Tariff, General Rules for Interpretation | As on 2025-06-30 | 41 | 162 |
| Indian Customs Tariff, Chapter 96 | As on 2025-06-30 | 9 | 26 |
| DGFT ITC(HS) 2022 Schedule II, Export Policy | Notification 50/2024-25; 2025-01-13 | 1,020 | 3,182 |
| **Total** | | **1,126** | **3,505** |

Source metadata and checksums are in corpus/manifests/sources.json. Source PDFs are in corpus/raw/. Extracted source pages are in corpus/processed/, and the FAISS index, embeddings and aligned metadata are in corpus/index/. These generated artifacts are currently checked in because the worker image copies corpus/index/ at build time.

### Retrieval implementation

- Page-wise PDF text extraction: PyMuPDF.
- Character-based chunks: 800 characters with 150-character overlap; chunk metadata preserves source, page and locator.
- Embeddings: normalized 384-dimensional vectors from all-MiniLM-L6-v2.
- Vector search: FAISS inner-product index over normalized vectors (cosine similarity).
- Exact search: HS-code and phrase lookup over chunk metadata.
- Hybrid search: exact HS-code hits first, then semantic hits, with duplicate chunks removed.
- Agent adapter: app/retrieval/tool.py exposes search(query, limit) to the classification runner.

The checked-in retrieval evaluation covers five queries and reports preliminary Mean Precision@5 of 0.72. Its relevance labels were selected from retrieved results, so this is not independent ground truth and is not a classification accuracy result. A recent manual vacuum-flask query retrieved the Chapter 96 vacuum-flask heading and DGFT Chapter 96 passages; this confirms the tool can surface relevant passages for that example, not that the final classification is correct.

Known limitations:

- Text extraction has not been OCR-corrected. The DGFT Notification 09 report flags pages 2 and 30 as empty and page 29 for review. Local inspection also found an image-only first page in the large Schedule II PDF. Treat these pages as missing evidence until reviewed/OCRed.
- Chapter 73 and Chapter 96 PDFs have a blank final page; the extracted page counts/chunks above reflect the current files.
- Tariff tables may lose row/column relationships in extracted text. Character-based splitting can break a legal clause or tariff entry.
- Hybrid search uses a simple digit pattern to detect a possible HS code. A four-digit number elsewhere in a query can be mistaken for a code; verify such results.
- The current manifest still records parsing_status as pending and indexed_chunks as 0 for all sources, even though parsed files and the 3,505-vector index exist. The pipeline does not update these manifest fields.
- The current pipeline has individual commands, but does not yet provide a single end-to-end idempotent command with build time, corpus version, chunk count and throughput reporting.

For fuller corpus notes, see corpus/README.md; note that it has not yet been brought up to date with the five-source corpus and current index.

### Rebuild the corpus

Run from the repository root in an environment with the retrieval dependencies installed. The repository’s worker requirements include NumPy, FAISS CPU and sentence-transformers. PDF parsing also needs PyMuPDF; it is not currently listed in the project requirements files.

    python -m app.retrieval.register_corpus
    python -m app.retrieval.parse_all_documents
    python -m app.retrieval.chunk_documents
    python -m app.retrieval.generate_embeddings
    python -m app.retrieval.build_index
    python -m app.retrieval.evaluate_retrieval

The embedding and FAISS rebuild can require substantial disk space and CPU time. The worker Docker build installs CPU PyTorch and downloads the sentence-transformer model.

## Run locally

### Requirements

- Docker Engine or Docker Desktop with Docker Compose v2.
- Git.
- For Python scripts/tests: Python 3.11 or newer.
- For corpus parsing: PyMuPDF in addition to the worker retrieval dependencies.

### Start

    git clone https://github.com/VijaySuriyaP/export-classification-service-mlops.git
    cd export-classification-service-mlops
    cp .env.example .env
    docker compose up -d --build --wait
    docker compose ps

Windows PowerShell:

    Copy-Item .env.example .env
    docker compose up -d --build --wait

The default environment uses the local LLM stub and the stub agent. It needs no provider API key, but it will not return a classification. For real provider settings, set LLM_BASE_URL, LLM_API_KEY and LLM_MODEL in .env. Never commit .env or real credentials. The default worker image includes the corpus index; rebuild it after changing the index or agent configuration.

### Local URLs

| Service | URL |
|---|---|
| Web client | http://localhost:8080 |
| API OpenAPI documentation | http://localhost:8000/docs |
| API health/readiness | http://localhost:8000/health and http://localhost:8000/ready |
| Prometheus metrics | http://localhost:9090 |
| Grafana dashboard | http://localhost:3000 |
| Jaeger traces | http://localhost:16686 |
| LLM stub controls | http://localhost:8001 |

Locust is optional: run docker compose --profile loadtest up locust and open http://localhost:8089.

### Submit and inspect a run

Only product_description is required; materials, when provided, must be a JSON list.

    curl -s -X POST http://localhost:8000/runs \
      -H 'Content-Type: application/json' \
      -d '{"product_description":"Stainless steel vacuum flask with a moulded plastic outer body.","materials":["stainless steel","plastic"]}'

The response provides a run_id and status_url. Poll the run until it is terminal:

    curl -s http://localhost:8000/runs/<run_id>

Inspect the full event record when needed. It contains internal prompts and retrieved passages, so the response can be large:

    curl -s http://localhost:8000/runs/<run_id>/events

A run can end as completed, escalated or failed. Escalation with no code is an expected safe result when the stub is active or the model output/evidence does not meet the contract. The API contract is documented in docs/api-contract.md and exposed at /contract and /docs.

### Feedback

    curl -s -X POST http://localhost:8000/runs/<run_id>/feedback \
      -H 'Content-Type: application/json' \
      -d '{"rating":"up"}'
    curl -s http://localhost:8000/feedback/summary

Feedback is accepted for finished runs. The web client provides the same flow.

### Smoke checks and tests

Historical evidence is recorded in docs/evidence/. To reproduce locally:

    python scripts/smoke_test.py
    python -m pytest

The documented smoke test expects 9 checks with the default timings/configuration. For a fast circuit-breaker check, the test docs specify temporary stub latency/retry settings; see docs/test-matrix.md. Do not treat a passing stub smoke test as proof of classification quality.

Scale workers:

    docker compose up -d --scale agent-worker=3 --no-recreate --wait

Stop services while retaining volumes:

    docker compose down

This removes services but keeps Redis, Prometheus and Grafana data volumes. To also delete those local volumes:

    docker compose down -v

## Verified capabilities and open work

### Implemented in code

- REST contract and input validation, including type/range/cross-field checks and quarantine before a run is created.
- Async run submission, Redis queue/state, status and event polling, client rate limiting, run cache controls and feedback aggregation.
- Prompt-injection input screening and structural output policy.
- LLM client with timeout, retry/backoff, circuit breaker, optional fallback settings and token/cost tracking.
- LangGraph classifier/verifier workflow with bounded search/revision path, schema validation, citation resolution and safe escalation.
- Corpus registration, parsing, chunking, embeddings, FAISS construction, exact/semantic/hybrid search and retrieval adapter.
- Compose services, health checks, Prometheus/Grafana/Jaeger telemetry, structured logs and web client.
- Runbook and incident record.

### Still to complete or verify

1. **Make the classifier produce valid outputs:** configure a compatible model or canned JSON responses, then run the vacuum-flask case and review every code, GRI step, note, export-policy statement and citation against the official passages.
2. **Review corpus extraction gaps:** OCR or manually validate flagged pages; inspect tariff text/table structure; update the manifest parsing_status and indexed_chunks fields to reflect the actual artifacts.
3. **Improve evaluation:** build at least 20 independently labeled cases with expected outcomes and evidence passages; report groundedness, context relevance, faithfulness and hallucination rate; compare two prompt versions before and after a deliberate change.
4. **Complete corpus pipeline reporting:** make the registration/parse/chunk/embed/index process reproducible and idempotent, with corpus versions and time/count/throughput output.
5. **Run the load test:** loadtest/slo.json currently says PROPOSED. The team should record its agreement and commit the declaration before measuring. Then run the 1- and 3-worker matrix in docs/loadtest.md and preserve the reports/evidence.
6. **Measure cache and rate-limit effects:** compare run latency, cost and cache hit rate with the cache disabled/enabled.
7. **Refresh project evidence and report:** the checked-in test matrix and screenshots describe the 2026-11-11 stub configuration. Re-run applicable checks after model integration, capture current UI/Grafana/Jaeger evidence, and finish the report/demo checklist.
8. **Check toxicity handling:** prompt-injection screening and structural output filtering exist; the project checklist still lists toxicity detection as open.

The SLO values are already filled in: complete-run p95 60,000 ms, submit p95 300 ms, maximum failure rate 1%. The status is PROPOSED, with declaration date and declarer unset. Follow docs/loadtest.md; do not measure before the declaration is recorded.

## Testing and evidence

The checked-in evidence in docs/evidence/ was captured on 2026-11-11 using the stub runner. It includes platform smoke results, 1- and 3-worker Compose records, resilience drills and web screenshots. That evidence remains useful for the platform, but screenshots/results labelled “stub” do not show a valid classification. The current project run demonstrated retrieval and safe escalation but has not yet been added to the evidence log.

Use docs/test-matrix.md for test IDs and results, docs/demo-checklist.md for the presentation sequence, docs/runbook.md for operational procedures, and docs/incident-log.md for development incidents. Update evidence only from actual runs.

## Team ownership recorded in the project plan

These are planned work areas, not a claim that the tasks are complete.

| Member | Planned focus |
|---|---|
| Vijay Suriya P | Web/API integration, documentation, test and demo coordination |
| Utkarsh | Official corpus, parsing/indexing, retrieval and retrieval evaluation |
| VIKAS | Classification agents, GRI reasoning, verification, citations and escalation |
| NIKHIL | API/platform, resilience, observability and load/scaling work |

## Repository map

    app/api/                 FastAPI routes
    app/agents/              Stub and LangGraph classification runners, prompts and schemas
    app/contracts/           Request, result, run and feedback contracts
    app/guardrails/          Input screen and output policy
    app/llm/                 OpenAI-compatible model client
    app/llm_stub/            Local provider stub and failure controls
    app/retrieval/            Corpus pipeline, search and agent adapter
    app/worker/              Queue consumer and event/trace recorder
    corpus/raw/               Source PDFs
    corpus/processed/         Parsed pages, chunks and validation reports
    corpus/index/             Embeddings, FAISS index and chunk metadata
    corpus/manifests/         Source metadata/checksums
    corpus/evaluation/        Retrieval questions and preliminary results
    web/                      Browser client
    compose.yaml              Local multi-service deployment
    docker/                   Service Dockerfiles
    observability/             Prometheus, Grafana and Jaeger configuration
    loadtest/                  Locust scenario and SLO
    scripts/                   Smoke, screenshot and load-test utilities
    tests/                     Agent and platform tests
    docs/                      Contracts, design, procedures, evidence and checklists

**Use this README as the current implementation status.** Keep code presence, successful platform tests, and demonstrated classification quality distinct; a feature is complete only when its behavior has current evidence.
