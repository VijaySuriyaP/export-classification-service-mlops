# Corpus Management and Retrieval

## 1. Overview

This module manages the document corpus and retrieves relevant evidence for the Export Classification Check Service.

Its responsibilities include:

- Registering official documents and recording source metadata.
- Extracting text from PDF documents.
- Dividing extracted text into searchable chunks.
- Generating embeddings for semantic retrieval.
- Building a FAISS vector index.
- Supporting exact, semantic, and hybrid search.
- Evaluating retrieved passages.

This module provides evidence for downstream components. It does not make final export classification decisions.

## 2. Current Corpus

Five source PDFs are registered in `manifests/sources.json` and stored in `raw/`:

| Source | Recorded edition/date | Parsed pages | Indexed chunks |
|---|---|---:|---:|
| DGFT Notification 09/2025-26 | 2025-05-19 | 38 | 80 |
| Customs Tariff, Chapter 73 | As on 2025-06-30 | 18 | 55 |
| Customs Tariff, General Rules for Interpretation | As on 2025-06-30 | 41 | 162 |
| Customs Tariff, Chapter 96 | As on 2025-06-30 | 9 | 26 |
| DGFT ITC(HS) 2022 Schedule II, Export Policy | Notification 50/2024-25; 2025-01-13 | 1,020 | 3,182 |
| **Total** | | **1,126** | **3,505** |

The PDF parsing status and per-source chunk counts are synchronized in the source manifest. The raw PDFs,
processed text and index artifacts are checked into this repository; the worker image packages the FAISS
index and its metadata.

## 3. Document Processing Pipeline

The pipeline consists of the following stages:

1. **Document registration:** Store document metadata and calculate a SHA-256 checksum.
2. **PDF parsing:** Extract page-wise text using PyMuPDF.
3. **Chunking:** Divide the extracted text into overlapping chunks while retaining source and page metadata.
4. **Embedding generation:** Convert chunks into numerical vectors using `sentence-transformers/all-MiniLM-L6-v2`.
5. **Index construction:** Store normalized vectors in a FAISS `IndexFlatIP` index.
6. **Retrieval:** Search the indexed corpus using semantic similarity, exact matching, or hybrid retrieval.

Current chunking configuration:

- Chunk size: 800 characters
- Chunk overlap: 150 characters
- Embedding dimensions: 384

## 4. Retrieval Methods

### Semantic Retrieval

Uses sentence embeddings and FAISS to retrieve passages based on semantic similarity.

### Exact Retrieval

Supports exact HS-code lookup and text phrase matching.

### Hybrid Retrieval

Combines exact and semantic retrieval.

The current implementation prioritizes exact matches, adds semantic results, and removes duplicate chunks.

It does not yet implement weighted score fusion.

## 5. Source Traceability

Each chunk retains metadata including:

- `chunk_id`
- `source_id`
- `page_number`
- `locator`
- `text`

This information allows downstream components to reference the source document and its page.

## 6. Evaluation

The preliminary retrieval evaluation uses five questions, mostly about Notification 09/2025-26. It reports
Mean Precision@5 of **0.72**, but the relevance labels were selected from retrieved results. This is not an
independent retrieval score and is not classification accuracy. The project still needs independently labeled
classification scenarios and reference evidence checked against official sources.

## 7. Running the Pipeline

Install the development requirements, then run the full pipeline from the repository root:

```bash
pip install -r requirements/dev.txt
python -m app.retrieval.run_pipeline
```

The pipeline registers source checksums, parses PDFs, chunks text, generates embeddings, builds the FAISS
index, updates manifest status/counts, and reports elapsed time and throughput. Individual stage commands
remain available in `app/retrieval/`.

To evaluate retrieval:

```bash
python3 -m app.retrieval.evaluate_retrieval
```

## 8. Current Limitations

- OCR has not been run. Notification 09/2025-26 pages 2 and 30 have no extracted text and page 29 is flagged
  for review. The first page of Schedule II was also found to be image-only during local inspection.
- Chapter 73 and Chapter 96 PDFs include a blank final page.
- PDF tables may lose row and column relationships during text extraction.
- Character-based chunking may split tariff entries or legal clauses.
- Hybrid HS-code detection can mistake an unrelated four-digit number in a query for a tariff code.
- Evaluation is preliminary and self-labeled; retrieval quality and classification accuracy are not established.
- Rebuild the checked-in index after changing source PDFs; the running services do not refresh it automatically.

## 9. Scope

This module is responsible for corpus preparation, indexing, retrieval, source traceability, and retrieval evaluation.

Agent orchestration, LangGraph, frontend development, and final classification decisions are handled by other project components.
