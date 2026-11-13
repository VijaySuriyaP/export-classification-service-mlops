
import json
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[2]

MANIFEST_PATH = ROOT / "corpus/manifests/sources.json"
OUTPUT_DIR = ROOT / "corpus/processed"


def parse_all_documents():
    with open(MANIFEST_PATH, "r", encoding="utf-8") as file:
        manifest = json.load(file)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for source in manifest["sources"]:
        source_id = source["source_id"]
        pdf_path = ROOT / source["local_path"]

        if not pdf_path.is_file():
            print(f"Skipping missing file: {pdf_path}")
            continue

        pages = []

        with fitz.open(pdf_path) as document:
            for page_number, page in enumerate(
                document, start=1
            ):
                pages.append({
                    "source_id": source_id,
                    "page_number": page_number,
                    "text": page.get_text("text").strip()
                })

        output_path = OUTPUT_DIR / f"{source_id}.json"

        with open(
            output_path, "w", encoding="utf-8"
        ) as file:
            json.dump(
                {
                    "source_id": source_id,
                    "total_pages": len(pages),
                    "pages": pages
                },
                file,
                indent=2,
                ensure_ascii=False
            )

        print(
            f"Parsed {source_id}: "
            f"{len(pages)} pages"
        )

    print("Multi-document parsing completed.")


if __name__ == "__main__":
    parse_all_documents()
