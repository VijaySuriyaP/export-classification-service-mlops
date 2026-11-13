
import json
import numpy as np
from pathlib import Path
from sentence_transformers import SentenceTransformer

ROOT = Path(__file__).resolve().parents[2]

INPUT_PATH = ROOT / "corpus/processed/all_chunks.json"

OUTPUT_DIR = ROOT / "corpus/index"

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


def generate_embeddings():
    with open(INPUT_PATH, "r", encoding="utf-8") as file:
        chunks = json.load(file)

    if not chunks:
        raise ValueError("No chunks found.")

    print("Loading embedding model...")

    model = SentenceTransformer(MODEL_NAME)

    texts = [chunk["text"] for chunk in chunks]

    print("Generating embeddings...")

    embeddings = model.encode(
        texts,
        batch_size=16,
        show_progress_bar=True,
        convert_to_numpy=True,
        normalize_embeddings=True
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    np.save(
        OUTPUT_DIR / "embeddings.npy",
        embeddings
    )

    with open(
        OUTPUT_DIR / "chunks_metadata.json",
        "w",
        encoding="utf-8"
    ) as file:
        json.dump(
            chunks,
            file,
            indent=2,
            ensure_ascii=False
        )

    print("Embeddings generated successfully.")
    print("Total chunks:", len(chunks))
    print("Embedding dimensions:", embeddings.shape[1])
    print("Embedding shape:", embeddings.shape)


if __name__ == "__main__":
    generate_embeddings()
