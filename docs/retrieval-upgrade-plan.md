# Retrieval Upgrade Plan

Living roadmap for taking EmbedBase's search from "solid 2026 baseline" (hybrid
dense + BM25 + RRF) to state-of-the-art. Four improvements, each its own PR,
done **sequentially** — every PR is shippable on its own and search keeps
working if the later ones never land.

Update the checkboxes as PRs merge. `[ ]` = todo, `[~]` = in progress, `[x]` = done.

| # | PR | Problem it fixes | Status |
|---|----|------------------|--------|
| 1 | Real reranker | RRF only *fuses scores*; nothing reads query+chunk together | `[~]` in progress |
| 2 | Contextual retrieval at ingestion | Naive window chunks lose their document context | `[ ]` todo |
| 3 | Modern embedding model | `all-MiniLM-L6-v2` (2021, 384-dim) caps recall | `[ ]` todo |
| 4 | Query transformation (HyDE / multi-query) | Raw query embedded as-is; weak on vague/multi-hop | `[ ]` todo |

Guiding rule: each PR must degrade gracefully. If a model is missing or a stage
errors, search falls back to the previous behaviour — never a 500.

---

## PR 1 — Real reranker (cross-encoder second stage) `[~]`

**Why.** Today `_rank_candidates` "re-ranks" by Reciprocal Rank Fusion — that is
*score fusion*, not semantic reranking. A cross-encoder reads the query and each
candidate chunk *together* and scores true relevance, the single biggest
precision win available. The over-fetch half of the pattern already exists
(`fan_out` pulls `top_k * fan_out` candidates); this adds the missing rerank step
before the `top_k` cut.

**Design.** Local, LLM-free: a `sentence-transformers` `CrossEncoder`
(`cross-encoder/ms-marco-MiniLM-L-6-v2`) — reuses a dependency already installed
for embeddings, mirrors how the embedding model loads. New `Reranker` adapter
(Protocol + registry), wired as an optional singleton like the embedder.

**Off by default** (`reranker.enabled: false`) so existing deployments don't
silently take on a model download + latency. Flip `enabled: true` in
`config.yaml` (or the config page) to turn it on.

- [x] `Reranker` Protocol in `api/adapters/base.py`
- [x] `RerankerConfig` in `api/models/config.py` + `AppConfig.reranker`
- [x] `api/adapters/reranker/` registry + `CrossEncoderReranker`
- [x] Optional singleton in `api/dependencies.py` (no `require_*` — search runs without it)
- [x] Warm-up build in `api/main.py` (failure logs + leaves `None`)
- [x] Live rebuild on config reload (`config_service`)
- [x] Thread through `search_collection` → rerank candidate pool before `[:top_k]`
- [x] Pass from REST router + MCP server/tools
- [x] Tests: reranker unit, registry, search wiring, warm-up
- [ ] Config-page toggle (UI) — optional, can trail the backend

**Insertion point.** Per-collection, inside `search_collection`, after
`apply_filters` and before the `top_k` truncation — so it reorders the full
over-fetched pool. Cross-collection RRF (`_merge_collections_rrf`) then merges by
rank as before, so the reranked order is what feeds the merge.

---

## PR 2 — Contextual retrieval at ingestion `[ ]`

**Why.** Chunks are fixed sliding windows (512 tok / 64 overlap). A chunk reading
"it raised throughput 30%" has lost what "it" is. Anthropic's Contextual
Retrieval prepends a short LLM-generated blurb situating each chunk in its
document before embedding — and it lifts **both** the dense vector and the BM25
sides this codebase already fuses.

**Design.** At ingestion (`worker/tasks.py`), for each chunk, one cheap LLM call
produces a 1–2 sentence context prefix; embed and index `context + chunk`. Reuse
the existing LLM-adapter plumbing from tag suggestion. Gated by config, off by
default (needs an LLM, none in this environment). Store the original chunk text
for display; index the contextualised text.

- [ ] `ContextualChunkConfig` (enabled, provider/model, prompt)
- [ ] Context-prefix step in the ingestion chunk pipeline
- [ ] Index contextualised text; keep raw text for display
- [ ] Skip/fall back cleanly when no LLM configured
- [ ] Tests

---

## PR 3 — Modern embedding model `[ ]`

**Why.** Retrieval quality is hard-capped by the embedder. `all-MiniLM-L6-v2` is
small and ~2021-era. A modern instruction-tuned model (optionally Matryoshka, to
trade dimensions for cost) raises the recall ceiling — a config-level change with
outsized impact.

**Design.** Mostly config + docs: pick a stronger default, confirm the adapter
handles its dimensions/prompts, document the re-embed/migration path (dimension
change ⇒ re-index). The adapter interface already abstracts the model, so the
code surface is small; the care is in the migration story.

- [ ] Choose default model (quality/size/licence) — verify current options via Context7
- [ ] Handle query/passage instruction prefixes if the model needs them
- [ ] Re-embed / re-index migration path + docs (dimension change is breaking)
- [ ] Tests

---

## PR 4 — Query transformation (HyDE / multi-query) `[ ]`

**Why.** The raw query is embedded verbatim. Vague or multi-hop questions retrieve
poorly. HyDE (embed a hypothetical answer) and multi-query (fan out paraphrases,
fuse with RRF) both lift recall, at the cost of one up-front LLM round-trip.

**Design.** Optional pre-retrieval stage in the search service: rewrite/expand the
query, run the existing search per variant, fuse with the RRF already in place.
Off by default (LLM + latency). Pairs naturally with the PR 1 reranker cleaning
up the wider candidate set.

- [ ] `QueryTransformConfig` (mode: off | hyde | multi_query, provider/model)
- [ ] Transform stage feeding the existing multi-collection search + RRF
- [ ] Off by default; clean fallback to raw query
- [ ] Tests

---

### Also noted (not its own PR)

- **Filtering is post-ranking** (`apply_filters` runs after the vector search), so
  restrictive `tags`/`filename` filters can under-deliver. Pushing filters into
  the vector-store query (pre-filter) is better where the backend supports it
  (pgvector/Qdrant). Fold into PR 1 or PR 3 if cheap; otherwise a follow-up.
- **No eval harness.** "Better" is unprovable without one. A small
  recall@k / nDCG harness over a fixed query set is what turns these four from
  "should help" into "measured +X%". Worth adding alongside PR 1.
