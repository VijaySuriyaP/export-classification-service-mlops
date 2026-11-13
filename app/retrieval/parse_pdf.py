
import json
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[2]

PDF_PATH = ROOT / "corpus/raw/dgft_notification_09_2025.pdf"

OUTPUT_PATH = (
    ROOT / "corpus/processed/dgft_notification_09_2025.json"
)

SOURCE_ID = "dgft-notification-09-2025"


def parse_pdf():
    if not PDF_PATH.is_file():
        raise FileNotFoundError(f"PDF not found: {PDF_PATH}")

    pages = []
    empty_pages = []

    with fitz.open(PDF_PATH) as document:
        for page_number, page in enumerate(document, start=1):
            text = page.get_text("text").strip()

            if not text:
                empty_pages.append(page_number)

            pages.append({
                "source_id": SOURCE_ID,
                "page_number": page_number,
                "text": text
            })

    result = {
        "source_id": SOURCE_ID,
        "filename": PDF_PATH.name,
        "total_pages": len(pages),
        "empty_pages": empty_pages,
        "pages": pages
    }

    OUTPUT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with open(OUTPUT_PATH, "w", encoding="utf-8") as file:
        json.dump(
            result,
            file,
            indent=2,
            ensure_ascii=False
        )

    print("PDF parsing completed.")
    print("Total pages:", len(pages))
    print("Empty text pages:", len(empty_pages))
    print("Output:", OUTPUT_PATH)


if __name__ == "__main__":
    parse_pdf()
