
import json
from pathlib import Path

import faiss
import numpy as np

ROOT = Path(__file__).resolve().parents[2]

INDEX_DIR = ROOT / "corpus/index"

EMBEDDINGS_PATH = INDEX_DIR / "embeddings.npy"
METADATA_PATH = INDEX_DIR / "chunks_metadata.json"
FAISS_INDEX_PATH = INDEX_DIR / "faiss.index"


def build_index():
    if not EMBEDDINGS_PATH.exists():
        raise FileNotFoundError(
            "Embeddings not found. Generate embeddings first."
        )

    embeddings = np.load(
        EMBEDDINGS_PATH,
        allow_pickle=False
    )

    with open(METADATA_PATH, "r", encoding="utf-8") as file:
        chunks = json.load(file)

    if embeddings.ndim != 2:
        raise ValueError("Embeddings must be a 2D array.")

    if embeddings.shape[0] != len(chunks):
        raise ValueError(
            "Number of embeddings and chunks do not match."
        )

    if not np.isfinite(embeddings).all():
        raise ValueError("Embeddings contain invalid values.")

    embeddings = np.ascontiguousarray(
        embeddings,
        dtype=np.float32
    )

    dimension = embeddings.shape[1]

    print("Building FAISS index...")

    # Inner product on normalized vectors equals cosine similarity.
    index = faiss.IndexFlatIP(dimension)

    index.add(embeddings)

    INDEX_DIR.mkdir(parents=True, exist_ok=True)

    faiss.write_index(
        index,
        str(FAISS_INDEX_PATH)
    )

    print("FAISS index created successfully.")
    print("Total indexed vectors:", index.ntotal)
    print("Vector dimensions:", index.d)
    print("Index saved:", FAISS_INDEX_PATH)


if __name__ == "__main__":
    build_index()
