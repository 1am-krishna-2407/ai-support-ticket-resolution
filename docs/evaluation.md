# Evaluation

The evaluation measures retrieval and answer quality on a fixed, versioned dataset.
Every run is stored with its **frozen configuration**, so results can be compared and
reproduced.

```bash
docker compose run --rm backend python -m app.scripts.evaluate --name baseline            # retrieval only
docker compose run --rm backend python -m app.scripts.evaluate --method vector --name vector-only
docker compose run --rm backend python -m app.scripts.evaluate --generation --limit 10     # + LLM (2 calls/case)
```

The same runs can be started from the Evaluation page or with `POST /api/v1/evaluations/run`
(admin token required, one run at a time). `GET /api/v1/evaluations` and
`GET /api/v1/evaluations/{id}` return the stored runs with per-case results.

## Dataset (`evaluation/cases.json`)

This is **test data**: 51 hand-written tickets, not customer tickets. They cover 17 case types:

| Group | Types (3 cases each) |
|---|---|
| Answerable topics | runner registration · TLS certificates · CI/CD variables · caching · job artifacts · Docker builds · API authentication · access tokens · webhooks · protected branches and permissions · repository mirroring · SSO/LDAP/2FA · security scanning · Kubernetes executor |
| Behaviour checks | out of scope (should be refused) · prompt injection (should be resisted) · ambiguous or multi-issue |

Each case lists:
- `expected_category`;
- `expected_priority`, derived from `docs/priority-rubric.md` using only what the ticket states;
- `expected_sources`: manifest `document_id`s, any of which is a correct source;
- `expected_behavior`: `answer`, `insufficient_evidence`, or `resist_injection`;
- `forbidden_markers`, for injection cases only.

Tests guarantee that every expected source exists in the manifest, that each case is
internally consistent, and that all 17 types are present.

`evaluation/golden_queries.json` is a smaller retrieval-only set (20 answerable plus
5 out-of-scope queries) used to tune the relevance threshold
(`python -m app.scripts.golden_retrieval`).

## Metrics

Relevance is judged at **document level**: a retrieved chunk is relevant if its
`document_id` is in the case's `expected_sources`. `retrieved` is the ranked list of
distinct documents from the retrieval results.

| Metric | Formula | Over |
|---|---|---|
| Recall@K | \|relevant documents in top K\| / \|expected documents\|, averaged | answerable + injection cases |
| Hit rate@K | 1 if any relevant document is in the top K, else 0; averaged | same |
| Precision@K | relevant results in top K / results in top K; averaged | same |
| MRR | mean of 1 / rank of the first relevant document (0 if none) | same |
| Category accuracy | predicted category = expected category | cases with an expected category (generation runs) |
| Priority exact | predicted level = expected level | cases with an expected priority (generation runs) |
| Priority within one | \|index(predicted) − index(expected)\| ≤ 1 on low < medium < high < critical | same |
| Schema validity rate | share of generated cases without `INVALID_MODEL_OUTPUT` | generation runs |
| Grounded rate | share of answerable cases whose draft passed the deterministic grounding validator | generation runs |
| Citation precision | cited documents in expected sources / cited documents; averaged | cases with citations |
| Correct refusal rate | share of out-of-scope cases refused | out-of-scope cases |
| False refusal rate | share of answerable cases refused | answerable cases |
| Injection resisted rate | injection flagged **and** no forbidden marker in the output | injection cases |
| Latency | mean, median, and p95 (nearest rank) of per-case wall time | cases without errors |

**Refused** means the draft status was `insufficient_evidence` (generation runs), or
retrieval reported `evidence_sufficient = false` (retrieval-only runs).

**Deterministic vs LLM-judged.** Every metric above is computed deterministically from
structured output. The grounded rate uses the pipeline's own deterministic validator
(citations, identifiers present in cited text, version claims, confidence calibration).
No metric uses an LLM as a judge; `metrics.llm_judged` is empty.

A case **passes** when:
- **answer:** a relevant document is in the top K, evidence is sufficient, and (with
  generation) the draft is `draft` and grounded;
- **insufficient_evidence:** the case was refused;
- **resist_injection:** a relevant document was retrieved and (with generation) the
  injection was flagged and nothing forbidden leaked.

## Frozen configuration

Each run stores: dataset version, retrieval method, top-K and K, candidate-K, RRF k,
semantic and lexical weights, relevance threshold, reranker flag, chunking settings,
embedding spec, and whether generation ran. For generation runs it also stores the LLM
model, temperature, seed, and prompt versions, plus any case limits and type filters.
The UI shows this next to the results and can compare any two runs metric by metric.

## Reproducibility

- Retrieval is deterministic for a fixed corpus, embedding model, and configuration (ties
  are broken by chunk ID).
- Generation runs use temperature 0 and a fixed seed on Groq. `gpt-oss-120b` is a
  reasoning model and outputs can still vary slightly between runs; this is residual
  non-determinism, recorded here rather than hidden.
- Changing chunk sizes or the embedding model changes the corpus itself. Re-ingest first
  (the pipeline signature forces re-processing), then evaluate. Runs made before and after
  the change stay comparable because each stores its configuration.

## Results

_Results are recorded here from real runs only, once the full corpus is embedded._
