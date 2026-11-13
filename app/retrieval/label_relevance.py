
import json
from pathlib import Path

from app.retrieval.hybrid_search import HybridSearch

ROOT = Path(__file__).resolve().parents[2]

QUESTIONS_PATH = (
    ROOT / "corpus/evaluation/retrieval_questions.json"
)


def label_relevance():
    with open(QUESTIONS_PATH, "r", encoding="utf-8") as file:
        questions = json.load(file)

    engine = HybridSearch()

    for question in questions:
        print("\n" + "=" * 60)
        print("Question:", question["id"])
        print(question["query"])
        print("=" * 60)

        results = engine.search(
            question["query"],
            top_k=5
        )

        for index, result in enumerate(results, start=1):
            print(f"\n--- Result {index} ---")
            print("Chunk ID:", result["chunk_id"])
            print("Source:", result["source_id"])
            print("Page:", result["page_number"])
            print("Text:")
            print(result["text"])

        print("\nEnter relevant result numbers.")
        print("Example: 1,3,5")
        print("Press Enter if none are relevant.")

        while True:
            selection = input("Relevant results: ").strip()

            try:
                selected = (
                    []
                    if not selection
                    else [
                        int(value.strip())
                        for value in selection.split(",")
                    ]
                )

                if any(
                    number < 1 or number > len(results)
                    for number in selected
                ):
                    raise ValueError

                break

            except ValueError:
                print("Invalid selection. Try again.")

        selected_ids = {
            results[number - 1]["chunk_id"]
            for number in selected
        }

        # Preserve any labels already assigned.
        existing_ids = set(
            question.get("relevant_chunk_ids", [])
        )

        question["relevant_chunk_ids"] = sorted(
            existing_ids | selected_ids
        )

    with open(QUESTIONS_PATH, "w", encoding="utf-8") as file:
        json.dump(
            questions,
            file,
            indent=2,
            ensure_ascii=False
        )

    print("\nRelevance labels saved successfully.")
    print("File:", QUESTIONS_PATH)


if __name__ == "__main__":
    label_relevance()
