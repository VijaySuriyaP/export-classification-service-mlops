
import re

from app.retrieval.search import SemanticSearch
from app.retrieval.exact_lookup import ExactLookup


class HybridSearch:
    def __init__(self):
        self.semantic = SemanticSearch()
        self.exact = ExactLookup()

    def search(self, query, top_k=5, hs_code=None):
        if not query or not query.strip():
            return []

        if top_k <= 0:
            raise ValueError("top_k must be positive.")

        results = []
        seen = set()

        # Find an HS code supplied explicitly or in the query.
        if hs_code is None:
            match = re.search(
                r"(?<!\d)\d{8}(?!\d)|"
                r"(?<!\d)\d{6}(?!\d)|"
                r"(?<!\d)\d{4}(?!\d)",
                query
            )
            if match:
                hs_code = match.group()

        # 1. Exact HS-code matches take priority.
        if hs_code:
            exact_results = self.exact.search_hs_code(
                hs_code
            )

            for result in exact_results:
                if result["chunk_id"] not in seen:
                    results.append({
                        **result,
                        "retrieval_method": "exact"
                    })
                    seen.add(result["chunk_id"])

        # 2. Retrieve semantically similar passages.
        semantic_results = self.semantic.search(
            query,
            top_k=max(top_k * 3, 10)
        )

        for result in semantic_results:
            chunk_id = result["chunk_id"]

            if chunk_id not in seen:
                results.append({
                    **result,
                    "retrieval_method": "semantic"
                })
                seen.add(chunk_id)

        return results[:top_k]


def main():
    engine = HybridSearch()

    print("\nHybrid Retrieval Ready!")
    print("Type 'exit' to stop.")

    while True:
        query = input("\nEnter your query: ").strip()

        if query.lower() == "exit":
            break

        if not query:
            continue

        results = engine.search(query, top_k=5)

        print(f"\nRetrieved {len(results)} passages.")

        for i, result in enumerate(results, start=1):
            print(f"\n--- Result {i} ---")
            print("Method:", result["retrieval_method"])
            print("Source:", result["source_id"])
            print("Page:", result["page_number"])
            print("Chunk ID:", result["chunk_id"])

            if "score" in result:
                print("Similarity:", round(result["score"], 4))

            print("Text:")
            print(result["text"])


if __name__ == "__main__":
    main()
