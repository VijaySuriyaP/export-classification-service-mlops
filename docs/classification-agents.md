# Two-agent classification runner

`app/agents/classification_runner.py` implements the classifier -> verifier workflow described in
`docs/platform.md` section 7. This page covers setup and the retrieval adapter contract.

## Status

The runner is implemented, but `.env.example` still selects the stub so platform smoke tests remain
deterministic. The local `.env` can select `ClassificationRunner` and the corpus adapter. Do not describe
classification as fully validated until:

1. the model provider returns schema-valid classifier and verifier output,
2. the vacuum-flask code, GRI steps, notes and citations are checked by a person against official passages, and
3. the classification and safe-escalation cases are recorded in the test matrix.

The local Cohere-backed run completed once with a verified six-digit `961700` result. A later 0.5 L run
correctly escalated when the model reconstructed PDF table citations instead of quoting them exactly. The
runner now materializes a verbatim citation excerpt from the cited retrieved chunk only when the model's
quote contains the proposed code and that code is present in the chunk. The ordinary exact-text check and
verifier remain in place. This code path still needs a live run and human review. The unit tests use synthetic
passages and prove control flow/safety behavior, not tariff accuracy.

## Enable it

```env
AGENT_RUNNER=app.agents.classification_runner:ClassificationRunner
RETRIEVAL_TOOL=<trusted.module>:<search_function>
```

Rebuild the worker image. A `RETRIEVAL_TOOL` that is set but cannot be imported stops the worker at
start-up (`RetrievalConfigError`) so the mistake is visible. An empty `RETRIEVAL_TOOL` is allowed: every
run then escalates to specialist review with no code.

`langgraph` is already pinned in `requirements/worker.txt`. No change to `config.py` is needed
(`retrieval_tool` already exists).

## Retrieval contract

```python
def search(query: str, limit: int = 5) -> list[dict]:
    ...
```

Each returned dict:

| key | required | notes |
|---|---|---|
| `chunk_id` | yes | stable id of the chunk within the corpus |
| `text` | yes | official text; citations are checked against it |
| `source_id` | recommended | corpus document id; when present, a citation naming a different source is rejected |
| `locator` | recommended | e.g. `Heading 9617`, `Chapter 96 Note 3` |
| `score` | no | float |

The function is synchronous and thread-safe. It runs under `recorder.tool_call`, so it has a timeout
(`TOOL_TIMEOUT_S`), counts against `RUN_MAX_TOOL_CALLS`, and its query and passages appear in
`GET /runs/{id}/events`. A raised exception or empty result escalates the run. It must return official
corpus text only, never samples.

## Workflow

| step (agent.step) | what happens |
|---|---|
| `classifier.initial_search` | one search from the submitted description and materials |
| `classifier.draft` | model returns a JSON draft; code checks schema and scope (ch. 73/96), confirms each citation belongs to a chunk retrieved in this run and the stated source, and requires the final quote to be an exact excerpt from that chunk. Reformatted HS-code quotes can be replaced by an exact excerpt only when that code appears in both the model quote and cited chunk. |
| `verifier.verify` | independent model check of code, GRI steps, notes and citations against the passages: `approve`, `research` (one different query) or `escalate` |
| `verifier.followup_search` | only on `research`; a repeated or empty query, or a result with no new passages, escalates |
| `classifier.revise` | revised draft using the new passages and the verifier's issues; same code checks |
| `verifier.verify_final` | second and last check; anything but `approve` escalates |

At most 6 steps, 4 model calls and 2 searches per run, inside the platform's own limits
(`RUN_MAX_STEPS=12`, `RUN_MAX_LLM_CALLS=10`, `RUN_MAX_TOOL_CALLS=20` by default).

Events added to the run log: `search_strategy` (query, rationale, passages returned, new chunk ids),
`citation_quote_materialized` (exact excerpt copied from a cited retrieved chunk when code-based recovery
is valid), `citation_check`, `handoff` (classifier -> verifier with the draft summary; verifier -> classifier with
the issues and follow-up query) and `verdict`. `Verification.strategy_log` lists the searches in order.

## Outcomes

| situation | route | code returned |
|---|---|---|
| verifier approves a draft whose citations resolve | `clear-for-filing` | yes |
| classifier says key product facts are missing | `seek-product-clarification` | no |
| no retrieval, no passages, invalid model JSON, out of scope, bad citation, verifier escalates, unresolved follow-up | `specialist-classification-review` | no |
| model provider down, budget exhausted | worker escalates (exceptions propagate) | no |

Export policy is returned only when the draft quotes a passage for it and the verifier confirms it;
otherwise it is `Not determined`.

## Prompts and caching

`prompt_version` is `classifier-v2+verifier-v1`. Bump the constants in `classification_prompts.py` when
either prompt changes: they are recorded on every model call and are part of the run-cache key. Prompts
end with `[[stub:classifier]]` / `[[stub:verifier]]` so `STUB_RESPONSES_FILE` can serve canned JSON in
load tests.

## Tests

```bash
pip install -r requirements/base.txt -r requirements/worker.txt pytest
pytest tests/agents -q
```
