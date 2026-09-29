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

### Retrieval evaluation (2026-09-30)

Corpus: 217 documents and 4,600 chunks (Qwen3-Embedding-0.6B@1024). Threshold 0.45, top-K 8,
K = 5. Dataset `2026-09-30.1` (51 cases). Stored runs: `retrieval-{hybrid,vector,lexical}-v2`.

| Method | Scored / total | Recall@5 | Hit rate@5 | Precision@5 | MRR | False refusals | Correct refusals |
|---|---|---|---|---|---|---|---|
| vector | 48 / 51 | **0.867** | **1.000** | 0.394 | **0.875** | 0.00 | 0.67 (2 of 3) |
| hybrid | 46 / 51 | 0.773 | 0.953 | 0.354 | 0.855 | 0.00 | 0.67 (2 of 3) |
| lexical | 47 / 51 | 0.720 | 0.933 | 0.294 | 0.676 | 0.00 | 1.00 (2 of 2)* |

\* The hard out-of-scope case (c45) errored in the lexical run, so only two out-of-scope
cases were scored; the 1.00 is not comparable with the other rows.

Every run resisted all three prompt-injection cases at the retrieval stage (a relevant
document was retrieved and the injection text was flagged).

**Errors (excluded from scores).** 3-5 cases per run hit Hugging Face 402 ("credits
depleted") on every retry, roughly every tenth request. They are provider outages, recorded
as errors (`error_cases`) and not scored as misses or refusals. The first attempt, before
this was fixed, wrongly counted them as false refusals.

**Findings**

- **Vector outperforms hybrid on this dataset** (MRR 0.875 vs 0.855, Recall@5 0.867 vs
  0.773). Equal-weight RRF lets strong keyword matches push down semantically better pages.
  A lower `RETRIEVAL_LEXICAL_WEIGHT` is the next experiment. Hybrid stays the default
  because exact error strings (for example `x509: certificate signed by unknown authority`)
  are found through the lexical and phrase lists.
- **No false refusals** in any run: the 0.45 threshold (docs/retrieval.md §7) holds.
- **Near-miss out-of-scope (c45, Kubernetes ingress on EKS)** scores 0.55-0.60 similarity
  against runner and Kubernetes pages. No similarity threshold can refuse it without
  refusing real answers (the weakest relevant score is 0.591). Rejecting it is the job of
  the generation stage (an `insufficient_evidence` draft).
- **Genuine ranking misses:** c02 ("runner shows offline") ranks the runners API and
  fleet-scaling pages above the FAQ; c03 ("install Runner in a Docker container") ranks
  Docker-build pages above the install page.
- Latency: median about 1 s per case; p95 about 19-24 s, dominated by retry backoff on
  provider 402s rather than search time.
