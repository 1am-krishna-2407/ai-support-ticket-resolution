# Retrieval

`RetrievalService` (`backend/app/services/retrieval/`) turns a query or ticket into ranked,
scored evidence from **active, enabled** documents only.

```
ticket/query ─► transform ─► embed query ─┬─► vector search (HNSW, cosine)        ─┐
                                          ├─► full-text search (OR over lexemes)  ─┼─► RRF ─► dedupe/cap ─► rerank? ─► threshold ─► SRC-001…
                                          └─► exact-phrase search (error strings) ─┘
```

## 1. Query transformation (`query.py`)

- Unicode NFKC normalisation; control and zero-width characters removed; whitespace collapsed.
- Deterministic extraction, kept **verbatim**: CI/CD variables (`CI_*`, `GITLAB_*`), CLI flags
  (`--tls-ca-file`), versions, HTTP status codes, file names (`config.toml`,
  `.gitlab-ci.yml`), snake_case config keys, commands (`gitlab-runner register`).
- Phrases for exact matching: quoted strings and error-looking lines
  (`x509: certificate signed by unknown authority`, `ERROR: Job failed: exit code 137`).
- `retrieval_query` = title, then error lines, then other description lines, then any
  extracted terms not already present. It is capped at `RETRIEVAL_MAX_QUERY_CHARS`, trimming
  prose, never the identifiers.

## 2. Candidate searches (`repositories/chunks.py`)

| List | How | Score |
|---|---|---|
| semantic | `ORDER BY embedding <=> :q` on HNSW with `hnsw.iterative_scan = relaxed_order`, so the active/enabled filter still fills the list | cosine similarity `1 - distance` |
| lexical | OR-query over the query's lexemes against the weighted `search_vector` (heading = A, body = B). `websearch_to_tsquery` would AND every word, which almost never matches a multi-sentence ticket | `ts_rank_cd(…, 32)` ∈ [0, 1) |
| phrase | case-insensitive substring match of each extracted phrase in chunk content | fraction of phrases matched |

Every candidate also gets its cosine similarity, including lexical-only hits, so all
results share one comparable score. All user-derived values are bound parameters.

## 3. Fusion, dedupe, reranking

- **Reciprocal Rank Fusion:** `Σ wᵢ / (k + rankᵢ)` with `k = RETRIEVAL_RRF_K` (60) and weights
  `RETRIEVAL_SEMANTIC_WEIGHT` / `RETRIEVAL_LEXICAL_WEIGHT` (the phrase list uses the lexical
  weight). Ties go to the higher cosine similarity, then to `chunk_id`, so results are
  deterministic.
- **Dedupe:** identical content hash or identical normalised text is kept once.
- **Diversity:** at most `RETRIEVAL_MAX_CHUNKS_PER_DOCUMENT` chunks per document.
- **Reranker:** `Reranker` protocol; the default `NoopReranker` keeps fused order.

## 4. Threshold and evidence (ADR D8)

RRF scores are rank-based and not comparable across queries, so they only order results.
Relevance is judged on `final_score`: the reranker score when one is enabled, otherwise
the cosine similarity. A result is usable evidence (`passes_threshold`) when:

- `final_score ≥ RETRIEVAL_RELEVANCE_THRESHOLD`, **or**
- it contains an exact phrase from the query (a precise error string is strong evidence
  even when embeddings rank it low).

`evidence_sufficient` is true when at least one result passes, and `reason` explains the
decision. Passing results are listed first, then below-threshold results for inspection.
Only passing results are ever sent to the LLM.

## 5. Degraded mode

If embeddings are unavailable (no credentials, provider outage, credits or quota used up),
`lexical` and `hybrid` still answer, using full-text and exact-phrase matching, with
`degraded: true` and a warning. With no comparable score, **only exact-phrase matches
count as evidence**. `vector` returns `EMBEDDING_PROVIDER_ERROR`. Ticket analysis adds the
`retrieval_degraded` review reason.

## 6. Response contract

Each result has a per-request `source_id` (`SRC-001`…), the chunk and document IDs, title,
section path, a URL with the section anchor, content, all scores (`semantic`, `lexical`,
`fused`, `rerank`, `final`), each list's rank, `exact_phrase_match`, and `passes_threshold`.
Diagnostics include candidate counts per list, the count after dedupe, per-stage timings,
the embedding spec, and the reranker name.

Endpoints: `POST /api/v1/search`, `POST /api/v1/tickets/retrieve`,
`GET /api/v1/documents/{document_id}/chunks`.

## 7. Threshold tuning (2026-09-30)

Corpus: 217 documents and 4,600 chunks, embedded with `Qwen/Qwen3-Embedding-0.6B@1024`.
Dataset: `evaluation/golden_queries.json` (20 answerable queries, 5 out-of-scope queries).
Command: `python -m app.scripts.golden_retrieval --k 5`.

| Method | Recall@5 | Hit rate@5 | MRR |
|---|---|---|---|
| vector | 0.942 | 1.000 | 0.917 |
| lexical | 0.892 | 1.000 | 0.768 |
| hybrid | 0.942 | 1.000 | 0.892 |

Best cosine similarity per query (hybrid retrieval):

- relevant document, answerable queries: 0.591 (lowest) to 0.857;
- any document, out-of-scope queries: 0.224 to 0.355 (highest).

| Threshold | Correct refusals (out of scope) | False refusals (answerable) |
|---|---|---|
| 0.30-0.35 | 0.80 | 0.00 |
| **0.375-0.60** | **1.00** | **0.00** |
| 0.625 | 1.00 | 0.05 |
| 0.65-0.675 | 1.00 | 0.10 |
| 0.70 | 1.00 | 0.20 |

**Chosen threshold: 0.45**, near the middle of the fully separating range. That is about
0.10 above the highest out-of-scope score and 0.14 below the weakest relevant one. The old
placeholder, 0.35, let one out-of-scope query through. With only 25 queries, treat this as a
first calibration: the 51-case evaluation re-checks it, including near-miss negatives.

The threshold depends on the embedding model, so re-run this sweep after any model change.
