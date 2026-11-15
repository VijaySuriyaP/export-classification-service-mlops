"""Run the full corpus build and print duration, version and throughput information."""

import json
import time
from pathlib import Path

from app.retrieval.build_index import build_index
from app.retrieval.chunk_documents import chunk_all_documents
from app.retrieval.generate_embeddings import generate_embeddings
from app.retrieval.parse_all_documents import parse_all_documents
from app.retrieval.register_corpus import register_documents

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "corpus/manifests/sources.json"
CHUNKS_PATH = ROOT / "corpus/index/chunks_metadata.json"


def run_pipeline() -> None:
    started = time.perf_counter()
    register_documents()
    with MANIFEST_PATH.open(encoding="utf-8") as file:
        manifest = json.load(file)

    parse_all_documents()
    chunk_all_documents()
    generate_embeddings()
    build_index()

    with CHUNKS_PATH.open(encoding="utf-8") as file:
        chunks = json.load(file)
    elapsed = time.perf_counter() - started
    versions = sorted({str(source.get("version", "unknown")) for source in manifest["sources"]})
    throughput = len(chunks) / elapsed if elapsed else 0.0

    print("\nCorpus pipeline summary")
    print("Corpus:", manifest.get("corpus_name", "unnamed"))
    print("Source versions:", ", ".join(versions))
    print("Sources:", len(manifest["sources"]))
    print("Indexed chunks:", len(chunks))
    print("Elapsed seconds:", round(elapsed, 2))
    print("Chunk throughput per second:", round(throughput, 2))


if __name__ == "__main__":
    run_pipeline()
