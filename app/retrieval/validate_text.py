
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

INPUT_PATH = (
    ROOT / "corpus/processed/dgft_notification_09_2025.json"
)

REPORT_PATH = (
    ROOT / "corpus/processed/dgft_validation_report.json"
)


def validate_text():
    with open(INPUT_PATH, "r", encoding="utf-8") as file:
        document = json.load(file)

    page_reports = []
    flagged_pages = []

    for page in document["pages"]:
        page_number = page["page_number"]
        text = page["text"]

        character_count = len(text)
        word_count = len(text.split())

        # Heuristic only: short pages may be valid.
        if character_count == 0:
            status = "empty"
        elif word_count < 30:
            status = "review_required"
        else:
            status = "text_present"

        if status != "text_present":
            flagged_pages.append(page_number)

        page_reports.append({
            "page_number": page_number,
            "character_count": character_count,
            "word_count": word_count,
            "status": status
        })

    report = {
        "source_id": document["source_id"],
        "total_pages": document["total_pages"],
        "flagged_pages": flagged_pages,
        "pages": page_reports
    }

    REPORT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with open(REPORT_PATH, "w", encoding="utf-8") as file:
        json.dump(report, file, indent=2)

    print("Validation completed.")
    print("Total pages:", report["total_pages"])
    print("Pages flagged for review:", flagged_pages)
    print("Report saved:", REPORT_PATH)


if __name__ == "__main__":
    validate_text()
