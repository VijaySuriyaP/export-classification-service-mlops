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

The currently registered document is:

**DGFT Notification No. 09/2025-26**

Source: Directorate General of Foreign Trade, Government of India.

The notification concerns amendments to Schedule II (Export Policy), ITC (HS) 2022, in alignment with the Finance Act, 2025.

The corpus currently contains one registered PDF. The complete applicable tariff chapters, notes, General Rules for Interpretation, and export policy schedule still need to be collected and indexed.

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

The initial evaluation uses five questions and manually selected relevant passages from the top five retrieved results.

**Preliminary Mean Precision@5: 0.72**

This is not an independently validated retrieval accuracy score because the relevance labels were selected from the retrieval system's own results.

A more comprehensive evaluation should include independently labeled queries and relevant passages across the full document corpus.

## 7. Running the Pipeline

Run the following commands from the repository root after registering the source documents:

```bash
python3 -m app.retrieval.parse_all_documents
python3 -m app.retrieval.chunk_documents
python3 -m app.retrieval.generate_embeddings
python3 -m app.retrieval.build_index
```

To evaluate retrieval:

```bash
python3 -m app.retrieval.evaluate_retrieval
```

## 8. Current Limitations

- Only one official notification is currently indexed.
- PDF tables may lose row and column relationships during text extraction.
- Character-based chunking may split tariff entries or legal clauses.
- Exact matching may miss codes broken across chunks.
- Evaluation is preliminary and uses a small dataset.
- The current index does not automatically refresh when source documents change.

## 9. Scope

This module is responsible for corpus preparation, indexing, retrieval, source traceability, and retrieval evaluation.

Agent orchestration, LangGraph, frontend development, and final classification decisions are handled by other project components.