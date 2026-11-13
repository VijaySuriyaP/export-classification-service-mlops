
import json
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[2]

PDF_PATH = ROOT / "corpus/raw/dgft_notification_09_2025.pdf"

JSON_PATH = (
    ROOT / "corpus/processed/dgft_notification_09_2025.json"
)

OUTPUT_DIR = ROOT / "corpus/processed/page_previews"

FLAGGED_PAGES = [2, 29, 30]


def inspect_pages():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with open(JSON_PATH, "r", encoding="utf-8") as file:
        data = json.load(file)

    extracted_pages = {
        page["page_number"]: page["text"]
        for page in data["pages"]
    }

    with fitz.open(PDF_PATH) as document:
        for page_number in FLAGGED_PAGES:
            if not 1 <= page_number <= len(document):
                print(f"Invalid page: {page_number}")
                continue

            page = document[page_number - 1]

            image_path = (
                OUTPUT_DIR / f"page_{page_number}.png"
            )

            pixmap = page.get_pixmap(
                matrix=fitz.Matrix(2, 2)
            )

            pixmap.save(image_path)

            text = extracted_pages.get(page_number, "")

            print(f"\n--- PAGE {page_number} ---")
            print("Extracted characters:", len(text))
            print("Extracted text:")
            print(text[:1000] if text else "[EMPTY]")
            print("Preview saved:", image_path)


if __name__ == "__main__":
    inspect_pages()
