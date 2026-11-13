
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "corpus/manifests/sources.json"

DOCUMENTS = [
    {
        "source_id": "dgft-notification-09-2025",
        "title": "DGFT Notification 09/2025-26",
        "authority": "Directorate General of Foreign Trade",
        "url": (
            "https://content.dgft.gov.in/Website/dgftprod/"
            "06446c7d-4804-419e-bf41-88c2a3702776/"
            "NOTIFICATION%20009_UPLOAD%20ENG.pdf"
        ),
        "local_path": "corpus/raw/dgft_notification_09_2025.pdf",
        "version": "09/2025-26",
        "document_date": "2025-05-19"
    },
    {
        "source_id": "customs-tariff-chapter-73",
        "title": "Indian Customs Tariff - Chapter 73: Articles of Iron or Steel",
        "authority": "Central Board of Indirect Taxes and Customs",
        "url": "",
        "local_path": "corpus/raw/chapter_73.pdf",
        "version": "Edition to be verified",
        "document_date": None
    }
]


def calculate_sha256(file_path):
    sha256 = hashlib.sha256()

    with open(file_path, "rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            sha256.update(block)

    return sha256.hexdigest()


def register_documents():
    with open(MANIFEST_PATH, "r", encoding="utf-8") as file:
        manifest = json.load(file)

    for document_info in DOCUMENTS:
        pdf_path = ROOT / document_info["local_path"]

        if not pdf_path.is_file():
            print("PDF not found:", pdf_path)
            continue

        checksum = calculate_sha256(pdf_path)
        source_id = document_info["source_id"]

        existing = next(
            (
                source for source in manifest["sources"]
                if source["source_id"] == source_id
            ),
            None
        )

        if existing and existing.get("sha256") == checksum:
            print(f"{source_id}: Already registered.")
            continue

        document = {
            **document_info,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "sha256": checksum,
            "parsing_status": "pending",
            "indexed_chunks": 0
        }

        if existing:
            manifest["sources"] = [
                document if source["source_id"] == source_id else source
                for source in manifest["sources"]
            ]
            print(f"{source_id}: Registration updated.")
        else:
            manifest["sources"].append(document)
            print(f"{source_id}: Registered successfully.")

    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)

    with open(MANIFEST_PATH, "w", encoding="utf-8") as file:
        json.dump(manifest, file, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    register_documents()
