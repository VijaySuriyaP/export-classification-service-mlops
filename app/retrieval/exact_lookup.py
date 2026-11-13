
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CHUNKS_PATH = (
    ROOT / "corpus/index/chunks_metadata.json"
)


class ExactLookup:
    def __init__(self):
        with open(CHUNKS_PATH, "r", encoding="utf-8") as file:
            self.chunks = json.load(file)

    def search_hs_code(self, hs_code):
        code = re.sub(r"\s+", "", hs_code)

        if not code.isdigit() or len(code) not in (2, 4, 6, 8):
            raise ValueError(
                "Enter a valid 2, 4, 6, or 8-digit HS code."
            )

        # Allow spaces or dots between code digits,
        # but not unrelated adjacent digits.
        pattern = (
            r"(?<!\d)"
            + r"[\s.]*".join(map(re.escape, code))
            + r"(?!\d)"
        )

        results = []

        for chunk in self.chunks:
            if re.search(pattern, chunk["text"]):
                results.append({
                    "chunk_id": chunk["chunk_id"],
                    "source_id": chunk["source_id"],
                    "locator": chunk["locator"],
                    "page_number": chunk["page_number"],
                    "text": chunk["text"]
                })

        return results

    def search_clause(self, phrase):
        phrase = phrase.strip()

        if not phrase:
            return []

        results = []

        for chunk in self.chunks:
            if phrase.casefold() in chunk["text"].casefold():
                results.append({
                    "chunk_id": chunk["chunk_id"],
                    "source_id": chunk["source_id"],
                    "locator": chunk["locator"],
                    "page_number": chunk["page_number"],
                    "text": chunk["text"]
                })

        return results


def main():
    lookup = ExactLookup()

    print("\nExact Lookup Ready!")
    print("1. Search HS Code")
    print("2. Search Exact Phrase")
    print("3. Exit")

    while True:
        choice = input("\nChoose an option: ").strip()

        if choice == "3":
            break

        if choice == "1":
            query = input("Enter HS code: ")

            try:
                results = lookup.search_hs_code(query)
            except ValueError as error:
                print(error)
                continue

        elif choice == "2":
            query = input("Enter exact phrase: ")
            results = lookup.search_clause(query)

        else:
            print("Invalid option.")
            continue

        print(f"\nMatches found: {len(results)}")

        for result in results:
            print("\n-----------------------")
            print("Source:", result["source_id"])
            print("Page:", result["page_number"])
            print("Chunk ID:", result["chunk_id"])
            print("Text:", result["text"])


if __name__ == "__main__":
    main()
