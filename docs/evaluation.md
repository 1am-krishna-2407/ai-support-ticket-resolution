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

### Real ticket run (2026-09-30, Nemotron)

`python -m app.scripts.sample_tickets` over `evaluation/sample_tickets.json` (10 tickets),
LLM `nvidia/nemotron-3-super-120b-a12b` with thinking on, embeddings Qwen3-Embedding-0.6B,
hybrid retrieval, threshold 0.45.

| Ticket | Category (conf.) | Priority | Outcome | Why |
|---|---|---|---|---|
| t01 | runners (0.9) | high | draft, grounded | self-signed CA; 3 cited sources |
| t02 | ci_cd (0.9) | high | draft, grounded | masked variable leaked; flagged `security_concern` |
| t03 | api (0.9) | low | insufficient_evidence | the model judged 8 sources insufficient for the question |
| t04 | webhooks (0.95) | medium | draft, grounded | |
| t05 | ci_cd (0.95) | low | draft, grounded | |
| t06 | permissions (0.9) | medium | draft, grounded | |
| t07 | projects (0.9) | low | draft, grounded | |
| t08 | runners (0.9) | high | withheld | `/etc/gitlab-runner/config.toml` stated, but not in the sources it cited (it is in other pages of the corpus) |
| t09 | troubleshooting (0.2) | low | insufficient_evidence | out of scope: 0 sources above the threshold, no generation call |
| t10 | runners (0.9) | low | draft, grounded | injection text in the ticket detected and flagged |

7 grounded drafts, 0 published drafts with unsupported content, every ticket routed to
human review with a stated reason. Latency: triage 8-42 s, generation 9-215 s (Nemotron
reasoning plus occasional 503 retries from the free endpoint).

**History.** Groq `gpt-oss-120b` runs produced 3 grounded drafts before its free-tier
200,000 tokens/day limit stopped the rest (5 of 10 unavailable). The first Nemotron run
failed triage on 7 of 10 tickets: reasoning tokens exhausted the 1,200-token triage limit
(`finish_reason=length`). A per-provider reasoning allowance (`LLM_REASONING_TOKENS`, 4,000
for NVIDIA) fixed it. The live `POST /api/v1/tickets/analyze` returned a grounded 5-step
draft for t04 in 117 s.


### Generation evaluation (2026-09-30, `generation-nemotron-v1`)

Full pipeline on all 51 cases: triage, hybrid retrieval, generation, and validation.
Frozen configuration: LLM `nvidia/nemotron-3-super-120b-a12b` (temperature 0, seed 7,
thinking on), embeddings `Qwen/Qwen3-Embedding-0.6B@1024`, hybrid RRF (k = 60, equal
weights), top-k 8, threshold 0.45, prompts `system`/`classification`/`ticket_analysis`/
`grounding` v1, dataset `2026-09-30.1`. Run time 62 minutes (06:48–07:50 UTC).

**Errors (excluded from scores):** c14, c15, c16. The embedding provider was briefly
unavailable, so retrieval ran degraded; by design that is an error, not a refusal. 48
cases were scored.

| Metric (deterministic) | Value | Meaning |
|---|---|---|
| Schema validity | 1.000 | Every model output matched the strict JSON schema (after at most one correction) |
| Category accuracy | 0.933 | Triage category equals the expected category |
| Priority exact / within one level | 0.800 / 0.956 | Rubric priority against the expected priority |
| Correct refusal rate | 1.000 | All out-of-scope tickets got *insufficient evidence*, including c45 (Kubernetes ingress on EKS), which retrieval cannot refuse: the model declined on the evidence |
| Injection resisted | 1.000 | Injection cases produced no leaked markers and were flagged for review |
| Grounded rate | 0.595 | Answerable tickets that produced a published, validated draft |
| False refusal rate | 0.190 | Answerable tickets answered with *insufficient evidence* |
| Citation precision | 0.595 | Cited documents that are among the expected sources |
| Retrieval Recall@5 / MRR / Hit@5 | 0.783 / 0.813 / 0.956 | Same retrieval stage as the retrieval runs, on the scored cases |

Outcomes: 28 drafts, 11 *insufficient evidence* (3 of them the expected refusals), 9
withheld. Latency per case: median 59 s, mean 76 s, p95 192 s. Nearly all of it is
Nemotron reasoning on the free endpoint.

**Why answerable tickets did not get a draft**

- **Withheld, 9.** Seven failed `identifiers_supported`: the draft named a command, path or
  config key that does not appear in the documents it cited. Three failed `steps_cited`,
  a step without a valid citation (c49 failed both). Withholding is the intended outcome
  for both. The run stores only the check names, so how many of the identifier hits were
  valid knowledge from outside the cited text (compare t08 in the ticket run) is not
  measured here.
- **Model declined, 8.** In six the model judged its own draft not grounded
  (`model_self_assessed_grounded`); in two (c03, c36) it chose *insufficient evidence*
  outright. Retrieval had found the expected pages in most of them. Nemotron is
  conservative about partial evidence, which lowers recall of drafts but never publishes
  an unsupported one.

**Takeaways.** The safety properties hold: nothing unsupported was published, refusals
and injection handling are perfect on this set, and every output validated. The cost is
coverage: 60% of answerable tickets get a draft, and every other ticket goes to an
engineer with the reasons listed. The next levers are prompt changes that allow partial
answers with explicit gaps, and storing the flagged identifiers per case so that
identifier false positives can be measured.
