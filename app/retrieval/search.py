
import json
from pathlib import Path

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

ROOT = Path(__file__).resolve().parents[2]

INDEX_DIR = ROOT / "corpus/index"

INDEX_PATH = INDEX_DIR / "faiss.index"
METADATA_PATH = INDEX_DIR / "chunks_metadata.json"

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


class SemanticSearch:
    def __init__(self):
        if not INDEX_PATH.exists():
            raise FileNotFoundError(
                "FAISS index not found. Build the index first."
            )

        if not METADATA_PATH.exists():
            raise FileNotFoundError(
                "Chunk metadata not found."
            )

        print("Loading embedding model...")
        self.model = SentenceTransformer(MODEL_NAME)

        print("Loading FAISS index...")
        self.index = faiss.read_index(str(INDEX_PATH))

        with open(METADATA_PATH, "r", encoding="utf-8") as file:
            self.chunks = json.load(file)

        if self.index.ntotal != len(self.chunks):
            raise ValueError(
                "FAISS index and chunk metadata are out of sync."
            )

        if self.index.d != self.model.get_sentence_embedding_dimension():
            raise ValueError(
                "Embedding model dimension does not match FAISS index."
            )

    def search(self, query, top_k=3):
        if not query or not query.strip():
            return []

        if top_k <= 0:
            raise ValueError("top_k must be positive.")

        query_embedding = self.model.encode(
            [query],
            convert_to_numpy=True,
            normalize_embeddings=True
        )

        query_embedding = np.ascontiguousarray(
            query_embedding,
            dtype=np.float32
        )

        k = min(top_k, self.index.ntotal)

        if k == 0:
            return []

        scores, indices = self.index.search(
            query_embedding,
            k
        )

        results = []

        for score, index in zip(scores[0], indices[0]):
            if index < 0:
                continue

            chunk = self.chunks[int(index)]

            results.append({
                "chunk_id": chunk["chunk_id"],
                "source_id": chunk["source_id"],
                "locator": chunk["locator"],
                "page_number": chunk["page_number"],
                "text": chunk["text"],
                "score": float(score)
            })

        return results


def main():
    search_engine = SemanticSearch()

    print("\nSemantic Search Ready!")
    print("Type 'exit' to stop.\n")

    while True:
        query = input("Enter your query: ").strip()

        if query.lower() == "exit":
            break

        results = search_engine.search(query, top_k=3)

        if not results:
            print("No results found.")
            continue

        for i, result in enumerate(results, start=1):
            print(f"\n--- Result {i} ---")
            print("Source:", result["source_id"])
            print("Page:", result["page_number"])
            print("Similarity:", round(result["score"], 4))
            print("Chunk ID:", result["chunk_id"])
            print("Text:")
            print(result["text"])
            print("-" * 50)


if __name__ == "__main__":
    main()
