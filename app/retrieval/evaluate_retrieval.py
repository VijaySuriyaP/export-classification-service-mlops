
import json
from pathlib import Path

from app.retrieval.hybrid_search import HybridSearch

ROOT = Path(__file__).resolve().parents[2]

QUESTIONS_PATH = (
    ROOT / "corpus/evaluation/retrieval_questions.json"
)

OUTPUT_PATH = (
    ROOT / "corpus/evaluation/evaluation_results.json"
)

TOP_K = 5


def evaluate():
    with open(QUESTIONS_PATH, "r", encoding="utf-8") as file:
        questions = json.load(file)

    engine = HybridSearch()
    results_report = []

    total_precision = 0

    for question in questions:
        retrieved = engine.search(
            question["query"],
            top_k=TOP_K
        )

        retrieved_ids = {
            result["chunk_id"]
            for result in retrieved
        }

        relevant_ids = set(
            question.get("relevant_chunk_ids", [])
        )

        matched_ids = retrieved_ids & relevant_ids

        precision = len(matched_ids) / TOP_K
        total_precision += precision

        results_report.append({
            "question_id": question["id"],
            "query": question["query"],
            "relevant_retrieved": len(matched_ids),
            "precision_at_5": round(precision, 4),
            "retrieved_chunk_ids": [
                result["chunk_id"]
                for result in retrieved
            ]
        })

        print(
            f"{question['id']}: "
            f"Precision@5 = {precision:.2f}"
        )

    mean_precision = (
        total_precision / len(questions)
        if questions else 0
    )

    report = {
        "evaluation_type": "preliminary_top_5_labels",
        "questions_evaluated": len(questions),
        "mean_precision_at_5": round(mean_precision, 4),
        "note": (
            "Labels were created from retrieved results. "
            "This is not an independent ground-truth evaluation."
        ),
        "results": results_report
    }

    OUTPUT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with open(OUTPUT_PATH, "w", encoding="utf-8") as file:
        json.dump(report, file, indent=2)

    print("\nEvaluation completed.")
    print(f"Mean Precision@5: {mean_precision:.4f}")
    print("Report saved:", OUTPUT_PATH)


if __name__ == "__main__":
    evaluate()
