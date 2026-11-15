"""Adapter exposing hybrid corpus search to the classification agent."""

from functools import lru_cache

from app.retrieval.hybrid_search import HybridSearch


@lru_cache(maxsize=1)
def _searcher() -> HybridSearch:
    return HybridSearch()


def search(query: str, limit: int = 5) -> list[dict]:
    """Return indexed corpus passages in the agent's expected format."""
    return _searcher().search(query, top_k=limit)
