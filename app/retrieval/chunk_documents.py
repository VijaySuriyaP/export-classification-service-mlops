
import json
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

MANIFEST_PATH = ROOT / "corpus/manifests/sources.json"
PROCESSED_DIR = ROOT / "corpus/processed"
OUTPUT_PATH = PROCESSED_DIR / "all_chunks.json"

CHUNK_SIZE = 800
CHUNK_OVERLAP = 150


def generate_chunk_id(source_id, page_number, text):
    content = f"{source_id}:{page_number}:{text}"

    return hashlib.sha256(
        content.encode("utf-8")
    ).hexdigest()[:16]


def split_text(text):
    chunks = []
    start = 0

    while start < len(text):
        end = min(start + CHUNK_SIZE, len(text))

        if end < len(text):
            boundary = text.rfind(" ", start, end)

            if boundary > start:
                end = boundary

        chunk = text[start:end].strip()

        if chunk:
            chunks.append(chunk)

        if end == len(text):
            break

        start = max(start + 1, end - CHUNK_OVERLAP)

    return chunks


def chunk_all_documents():
    with open(MANIFEST_PATH, "r", encoding="utf-8") as file:
        manifest = json.load(file)

    all_chunks = []

    for source in manifest["sources"]:
        source_id = source["source_id"]

        input_path = PROCESSED_DIR / f"{source_id}.json"

        if not input_path.exists():
            raise FileNotFoundError(
                f"Parsed document missing: {input_path}"
            )

        with open(input_path, "r", encoding="utf-8") as file:
            document = json.load(file)

        document_count = 0

        for page in document["pages"]:
            page_number = page["page_number"]
            text = page["text"]

            if not text.strip():
                continue

            for index, chunk_text in enumerate(split_text(text)):
                all_chunks.append({
                    "chunk_id": generate_chunk_id(
                        source_id,
                        page_number,
                        chunk_text
                    ),
                    "source_id": source_id,
                    "page_number": page_number,
                    "locator": f"Page {page_number}",
                    "chunk_index": index,
                    "text": chunk_text
                })

                document_count += 1

        print(f"{source_id}: {document_count} chunks")

    OUTPUT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with open(OUTPUT_PATH, "w", encoding="utf-8") as file:
        json.dump(
            all_chunks,
            file,
            indent=2,
            ensure_ascii=False
        )

    print("\nMulti-document chunking completed.")
    print("Total chunks:", len(all_chunks))
    print("Output:", OUTPUT_PATH)


if __name__ == "__main__":
    chunk_all_documents()
