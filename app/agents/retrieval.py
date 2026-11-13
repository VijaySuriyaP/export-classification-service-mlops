"""Retrieval seam for the classification runner.

The corpus search is owned by the retrieval workstream. The runner only depends on this contract:

    def search(query: str, limit: int = 5) -> list[dict]

* ``query``  - free text built from the submitted product description (and, for the verifier's
               follow-up, a different query written by the verifier).
* ``limit``  - maximum number of passages wanted.
* returns    - a list of passage dicts. Required keys: ``chunk_id`` (str) and ``text`` (str).
               Strongly recommended: ``source_id`` (str, corpus document id, used to check that a
               citation names the right document) and ``locator`` (str, e.g. "Heading 9617").
               Optional: ``score`` (float).

The function must be synchronous, thread-safe and must return official corpus text only. It is
executed through ``recorder.tool_call`` (timeout, budget, event log), so it should not implement
its own long retries. It is configured with ``RETRIEVAL_TOOL=module.path:function`` and is trusted
code in the same sense as ``AGENT_RUNNER``: it is imported from the worker's own environment.
"""

from __future__ import annotations

import importlib
from typing import Any, Callable, Protocol

DEFAULT_LIMIT = 5


class SearchFunction(Protocol):
    def __call__(self, query: str, limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]: ...


class RetrievalConfigError(RuntimeError):
    """RETRIEVAL_TOOL is set but cannot be loaded. Raised at worker start-up so it is not silent."""


def load_retrieval_tool(path: str) -> Callable[..., list[dict[str, Any]]] | None:
    """Resolve ``module:function``. An empty value means "not configured" and returns ``None``."""
    path = (path or "").strip()
    if not path:
        return None
    module_name, sep, func_name = path.partition(":")
    if not sep or not module_name or not func_name:
        raise RetrievalConfigError(f"RETRIEVAL_TOOL must look like 'module.path:function', got {path!r}")
    try:
        fn = getattr(importlib.import_module(module_name), func_name)
    except (ImportError, AttributeError) as exc:
        raise RetrievalConfigError(f"RETRIEVAL_TOOL {path!r} could not be loaded: {exc}") from exc
    if not callable(fn):
        raise RetrievalConfigError(f"RETRIEVAL_TOOL {path!r} is not callable")
    return fn


def _optional_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def normalise_passages(raw: Any) -> list[dict[str, Any]]:
    """Keep only well-formed passages; drop anything without a chunk_id and non-empty text.

    Duplicate chunk ids inside one result: the first one wins.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        chunk_id, text = item.get("chunk_id"), item.get("text")
        if chunk_id is None or not isinstance(text, str) or not text.strip():
            continue
        cid = str(chunk_id).strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        passage: dict[str, Any] = {
            "chunk_id": cid,
            "text": text,
            "source_id": _optional_str(item.get("source_id")),
            "locator": _optional_str(item.get("locator")),
        }
        score = item.get("score")
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            passage["score"] = float(score)
        out.append(passage)
    return out
