# Context-Aware Retrieval & Completeness Plan

> Companion to [`docs/retrieval-upgrade-plan.md`](../docs/retrieval-upgrade-plan.md).
> That roadmap raises *precision* (reranker, contextual embeds, better model,
> query transforms). **This plan raises *completeness*** — making sure a unit of
> meaning ("context") that is larger than a page, or smaller than a page, is
> neither truncated by `top_k` nor blurred into a single page vector, and that the
> MCP **tells the caller when it has returned only part of what matched.**
>
> _Location note:_ the existing convention is `docs/plans/`. This file lives in
> the top-level `plans/` folder as requested — say the word and I'll relocate it
> next to `docs/plans/postgres-migration.md` for consistency.

Same guiding rule as the retrieval roadmap: **every phase is shippable on its
own and degrades gracefully — if a model is missing or a stage errors, search
falls back to the previous behaviour, never a 500.**

---

## The problem

A **granularity mismatch**: the unit we *store and retrieve* (a chunk — for PDFs,
one physical page; see [`pdf.py`](../api/adapters/parsers/pdf.py:41)) is different
from the unit of *meaning* the user cares about (a "context" — a section, an
argument, a procedure). It fails in two directions:

1. **Context spans pages (context > chunk).** A topic that runs pages 340–347 is
   scattered across 8 page-chunks. Each tail page, in isolation, has diluted query
   relevance, so it ranks low and falls past the `top_k=5` cut — the context is
   returned half-complete and the caller never knows.
2. **Two contexts on one page (chunk > context).** A page holding two topics is
   embedded as **one averaged vector** — a blend of both — so it retrieves poorly
   for either.

Today there is **no "context" concept at all**: PDFs are divided strictly by
physical page, with no headings, no table of contents, and no `context_id`
([`pdf.py`](../api/adapters/parsers/pdf.py)). Page numbers give *adjacency*, not
*boundaries* — you cannot infer where a context begins or ends from page numbers
alone.

### Verified against the live corpus (2026-07-07)

An MCP `search_documents` sweep over the 1400-page *Licitações* PDF made the abstract
problem concrete. Each finding is tagged with the phase that addresses it:

- **Hits are whole pages, ~2.3–3.5k chars each** (`char_count` 2899 / 3489), so a 3-hit search
  returns ~10k chars — not concise. → **B4** (sub-page chunks) + [Guardrails](#guardrails) budget.
- **Two subjects on one page.** Page 1390 returned the *Chapter 23* intro ("Considerações
  gerais" on sanctions) **averaged with two unrelated TCU case-law footnotes** (Acórdãos
  2.295/2025 and 316/2024) in a single vector. → **B4** + **B1/B2** (`context_id`), incl.
  footnote/apparatus separation.
- **No "there's more" signal.** 12 chunks matched, 3 were returned, and the MCP said nothing.
  → **A3** (minimal slice now shipped — see below).
- **No context anchors.** `heading_path` / `heading_level` are `null` on this corpus (unnumbered
  "CAPÍTULO" / "SANÇÕES" headings, no embedded ToC), so there is nothing to group on today.
  → **B2** ladder (`get_toc()` → font/layout → docling → LLM).
- **Provenance bug** (correctness, not completeness — see
  [Adjacent correctness](#adjacent-correctness-issues-surfaced-while-testing)): `filename` /
  `source_file` are the ingestion **temp** path, and *different chunks of the same document carry
  different temp names* (`tmpruad779z.pdf`, `tmptyy5qd14.pdf`, `tmp1qd0qkfm.pdf`).

### Why this splits into two tracks

- **Track A — Completeness at query time (deterministic, no LLM).** You do **not**
  need to *understand* a context to avoid *truncating* it. Adjacency is a strong
  enough prior: a context that overflows a page overflows onto the *next*
  page(s). Mechanical neighbor-expansion + a "there's more" signal fixes the
  truncation symptom. This is most of the pain, and none of it needs reasoning.
- **Track B — Context division at ingestion (format-aware, ending in reasoning).**
  To actually *group* by context (and to fix the "two contexts on one page"
  direction), we assign a `context_id` once per document at ingest. The right
  source of boundaries is **format-specific**, and only the unstructured cases
  fall through to a reasoning model.

Track A ships first and works standalone. Track B later makes Track A sharper
(expansion and the saturation signal become context-aware instead of
fixed-window).

---

## Summary

> **Status audit — 2026-07-08 (post-merge), verified against the code, not the
> checkboxes.** Track A is the path. **Track B (context division at ingestion) is
> shelved and its LLM rung (B3) is dropped** — see the decision note below.

| # | Change | Fixes | Track | Status |
|---|--------|-------|-------|--------|
| **A1** | Reranker on by default | Precision; rescues diluted tail pages | query-time | ✅ **done** |
| **A2** | Adjacency expansion + span merge | Spanning context truncated by `top_k` | query-time | ✅ **done + merged** |
| **A3** | Saturation signal + MCP `notice` | Caller unaware more relevant data exists | query-time | ✅ **done** — flag, per-document `coverage`, document-naming `notice` |
| **A4** | MCP expansion primitives | Agentic completion of a partial span | query-time | ⬜ **next** — the "ask for more context" mechanism |
| **A5** | Pluggable, frontend-configurable reranker (local + remote/LLM) | Model outdated, not user-selectable, no remote/LLM provider | query-time | 🟡 **shipped** — modern default model + live-key check deferred |
| **B1** | `context_id` in metadata + retrieval grouping | No context unit; page ≠ context | ingestion | 💤 shelved |
| **B2** | Format-aware `ContextResolver` | Assign context boundaries per format | ingestion | 💤 shelved |
| **B3** | ~~Reasoning fallback (LLM boundaries)~~ | Unstructured / poorly-formatted inputs | ingestion | ❌ **dropped** |
| **B4** | Structure-aware (sub-page) chunking | Coarse page vectors; 2 contexts/page | ingestion | 💤 shelved |

`[ ]` todo · `[~]` in progress · `[x]` done · 🟡 partial · 💤 shelved · ❌ dropped.

### Decision — no LLM context-identification at ingestion

We are **not** building an ingestion-time step that uses an LLM to detect
"context" boundaries (originally **B3**, plus the LLM rung of **B2**). The
**agentic query-time loop already covers it, more simply**:

- **A2** (shipped) mechanically pulls a hit's neighbours by deterministic
  `chunk_index` adjacency — a context that overflows a page is recovered with no
  reasoning.
- **A3** (done) tells the caller *"there's more of this context below the cut,"* naming which
  documents were under-delivered.
- **A4** (next) gives the caller the primitives to **fetch it on demand**
  (`get_document_chunks` / `expand_chunk` / `get_document_outline`).

So the **LLM (or the user) driving the MCP decides when to pull more of a
context at query time**, instead of us pre-computing context boundaries for every
document at ingest. This removes the most expensive, most speculative part of the
plan (per-document reasoning over 1400-page PDFs) and refocuses the remaining work
on **finishing A3 and building A4**.

---

# Track A — Completeness at query time

## A1 — Reranker on by default `[x]`

**Done.** `reranker.enabled` flipped to `true` in
[`config.yaml`](../config.yaml) and the [`RerankerConfig`](../api/models/config.py)
default, so the over-fetched candidate pool is reordered by true query–chunk
relevance before the `top_k` cut. This is the cheapest win against the
"tail page ranked low" problem and needed no new code (PR 1 machinery already
exists).

- [x] `reranker.enabled: true` in config + code default
- [x] Startup warm-up already builds the reranker when enabled
  ([`api/main.py`](../api/main.py) lifespan → `get_reranker`), so the model loads
  at **boot**, not on a user's first query, and a load failure degrades to
  RRF-only instead of 500-ing.
- [x] **Bake the model into the image.** [`api/Dockerfile`](../api/Dockerfile) runs
  [`fetch_reranker_model.py`](../api/scripts/fetch_reranker_model.py) at build time to vendor the
  cross-encoder (`ms-marco-MiniLM-L6-v2`, ~80 MB) into `/opt/models`; the adapter loads that baked
  copy, so boot is fast and offline-safe (`HF_HUB_OFFLINE=1`).
- [x] **Surface a "reranker unavailable" health signal** — `/healthz` now carries a `reranker`
  field (`ready` | `unavailable` | `disabled`), so a silent on-by-default fall-back to RRF-only is
  visible rather than quietly "off".
- [x] Reconcile `docs/retrieval-upgrade-plan.md` PR 1 (on-by-default, baked model, UI toggle,
  `/healthz` signal) — PR 1 is marked done there.
- [ ] Modern default model + provider selection + frontend config — tracked in **A5**
  (`ms-marco-MiniLM-L-6-v2` is a ~2019-era placeholder, and the UI has no reranker
  controls yet).

## A2 — Adjacency expansion + span merge `[x]`

**Why.** When a context spans consecutive pages, the tail pages fall past
`top_k`. But every chunk stores `document_id` + `chunk_index` (a contiguous
ordinal), and [`make_chunk_id`](../api/models/chunk.py) is
`SHA256(document_id:chunk_index)` — **deterministic**. So "the page after this
hit" is a *computed key*, not a search. We can recover the spilled pages with one
indexed lookup, no embeddings, no LLM.

**Design.** After the `top_k` cut in
[`search_collection`](../api/services/search.py:177):

1. For each surviving hit, compute neighbour chunk ids `SHA256(doc_id:idx ± w)`
   for a small window `w` (config `expand_neighbors`, default 1).
2. Fetch them by primary key: `SELECT ... WHERE id = ANY($ids)` — one round-trip
   for the whole batch.
3. Group by `document_id`, sort by `chunk_index`, coalesce contiguous/overlapping
   runs into a single **span**, and return spans (with their page range) instead
   of orphan chunks.

Use `chunk_index` (not `page_number`) as the adjacency key — it is contiguous and
skips blank pages, whereas `page_number` is the physical page for display. This is
auto-merging / sentence-window retrieval, trivial here because ordinals are stored
and ids are deterministic. When B1 lands, prefer "neighbours **sharing the hit's
`context_id`**" over a blind ±`w` window.

- [x] `expand_neighbors: int = 1` (on by default) + `max_expand_neighbors` / `expand_char_budget`
  in `SearchConfig` + an `effective_expand_neighbors` clamp (`config.yaml` is gitignored; the
  default lives in the model)
- [x] Neighbour-fetch by computed chunk-id set in the pgvector adapter (`chunks_by_ids`, one
  `id = ANY(...)` round-trip)
- [x] Span-merge (coalesce contiguous/overlapping `chunk_index` runs per document) in
  [`expansion.py`](../api/services/expansion.py)
- [x] **Char-budget cap** on the assembled span (farthest non-hit neighbours dropped first; a hit
  chunk is never dropped)
- [x] `page_range` on `SourceProvenance`; the span text replaces the orphan hit's text, best-ranked
  hit represents each span
- [x] **Config-tab UI** control for `expand_neighbors` (+ `SearchConfig` in `types.ts`)
- [x] Tests: window / boundary / gap / coalesce / char-budget / degrade-on-error / disabled / clamp

**Shipped (2026-07-08).** Post-`top_k` pass in
[`multi_collection_search`](../api/services/search.py), off the event loop, with a call-site backstop
(any fetch error → un-expanded hits, never a 500). Saturation + `under_delivered` are measured on the
pre-expansion ranked hits, so A3's "there's more" keeps its meaning. Wired from `SearchConfig` through
a `get_search_config` dependency into both the REST router and the MCP `search_documents` tool. B1
will later swap the blind ±`w` window for "neighbours **sharing the hit's `context_id`**".

**Insertion point.** A post-processing pass in
[`multi_collection_search`](../api/services/search.py) after per-collection results are merged.
Degrades gracefully: if the neighbour fetch errors, return the un-expanded hits.

## A3 — Saturation signal + MCP `notice` `[x]`

**Why (your explicit ask: "the MCP must warn there's more").** `search_collection`
already over-fetches (`retrieval_fan_out=4` → ~20 candidates for `top_k=5`) then
**discards** the tail at `[:top_k]`. Instead of throwing it away, measure it and
tell the caller.

**Signals to compute** (cheap, from data already in hand):

- **Pool depth** — how many candidates cleared a relevance floor vs how many were
  returned (`"18 matched, showing 5"`). BM25 already runs over the *whole*
  collection, so the total lexical-match count is essentially free.
- **Per-document coverage** — `pages_returned` vs `total_pages`, and whether the
  document had more candidates below the cut
  (`"annual-report.pdf: pages 3–5 of 9, 4 more below the cut"`). Sharper once B1
  gives `context_id`: `"context X: returned 2 of 9 chunks"`.
- **Score plateau / no elbow** — is `score@k ≥ ~0.9 × score@1`? No taper ⇒ the
  results are on a plateau ⇒ truncated. A sharp elbow ⇒ genuinely complete ⇒
  **don't** warn (avoid crying wolf).
- **Span-touches-boundary** — a merged span (A2) whose last chunk has a valid
  `chunk_index+1` that wasn't returned ⇒ cut mid-context.

**Surface it in two places** — a structured field for programs, and a
natural-language string for the model, *because the LLM reads the text, not the
Pydantic fields*:

1. **Structured** — extend [`SearchResponse`](../api/models/search.py:65) (already
   has `under_delivered: bool` and `CollectionStat`) with a `coverage` object and
   an overall `more_available` flag + counts.
2. **`notice` string returned by the MCP tool**, phrased as an *instruction*:

   > ⚠ 18 chunks matched above the relevance floor; showing the top 5.
   > `annual-report.pdf` contributed pages 3–5 of 9 and has more matching pages
   > beyond this cut. To retrieve the full span call
   > `get_document_chunks(document_id='…', pages='6-9')` or re-run with a higher
   > `top_k`.

   Update the [MCP tool description](../api/services/mcp/tools.py:39) so the model
   knows what to do when a `notice` is present. A warning the model ignores buys
   nothing.

**Shipped (2026-07-07) — minimal slice.** `search_documents` now returns a structured
`more_available` flag and, when it is set, a natural-language `notice` (e.g. *"12 chunks matched
and were ranked; showing the top 3. About 9 more, of comparable relevance, fell below the top_k
cut. …re-run with a higher top_k."*). It fires only when the post-filter ranked pool exceeds
`top_k` **and** the shown results sit on a score *plateau* (tail ≥ 0.9 × top); a sharp elbow
stays quiet so the warning keeps its meaning (verified live — the sanctions query's 6.80→5.86
taper correctly emits no notice). Implemented in
[`search.py`](../api/services/search.py) (`_more_available`),
[`models/search.py`](../api/models/search.py), and
[`mcp/tools.py`](../api/services/mcp/tools.py) (`_saturation_notice`) + the tool description in
[`server.py`](../api/services/mcp/server.py). **Deferred:** the richer per-`CollectionStat`
`coverage` object, per-document "pages X–Y of N" lines, the whole-collection BM25 match count,
and tuning the 0.9 plateau ratio against an eval set (see
[Measuring it](#measuring-it-better-must-be-provable)).

**Completed (2026-07-08) — per-document coverage.** `SearchResponse` now carries a structured
`coverage: list[DocumentCoverage]` (`document_id`, `filename`, `returned` vs `matched`), populated
only when `more_available` fires and sorted most-hidden-first. The MCP `notice` **names the
under-delivered documents** — e.g. *"report.pdf: showing 3 of 8 matched (5 more below the cut)"* —
capped at the top 5 with a "+N more" summary (the full list is always in `coverage`). To surface the
below-cut chunks per document, [`search_collection`](../api/services/search.py) now returns the
**full ranked pool** and [`multi_collection_search`](../api/services/search.py) owns the single
`[:top_k]` cut — the final results are identical (RRF ranking unchanged; deeper candidates can't
displace the top-ranked), but the merge layer can now see what was cut. New helper
`_document_coverage`; the plateau ratio is named `_PLATEAU_RATIO`.

**Deliberately not built** (YAGNI / wrong phase): the **whole-collection BM25 match count** (a
misleading number — lexical matches ≠ relevant, and the only current action, raising `top_k`, is
capped at `max_results` regardless), **"pages X–Y of N total"** (needs PDF-leaky total-page counts;
the ranked-pool `returned`/`matched` are format-agnostic), the **span-touches-boundary** signal (an
A2 refinement, not saturation), and **elbow-threshold eval tuning** (no eval harness exists — its own
phase; see [Measuring it](#measuring-it-better-must-be-provable)).

- [x] `more_available` flag + structured `coverage` object (`DocumentCoverage` per under-delivered
  document) on `SearchResponse`
- [x] Saturation from the post-filter ranked pool + score-plateau guard (`_PLATEAU_RATIO`);
  whole-collection BM25 count and elbow tuning **dropped** (rationale above)
- [x] `notice` string builder that enumerates the under-delivered documents, in the MCP response
- [x] MCP tool-description update telling the model to raise `top_k` on a `notice`
- [x] Tests: plateau / elbow / complete + notice-builder + per-document coverage
  (`_document_coverage`, notice enumeration, doc-list cap); eval-set tuning deferred with the harness

## A4 — MCP expansion primitives `[ ]`

**Why.** A4 is what makes an *agentic* reasoning loop possible: with A3's warning,
the model needs deterministic tools to act on it. All are pure key lookups on data
we already store — no new index, no LLM.

**New MCP tools** (alongside `search_documents` in
[`server.py`](../api/services/mcp/server.py:49) / [`tools.py`](../api/services/mcp/tools.py:39)):

- `get_document_chunks(document_id, page_range | chunk_range)` — fetch an explicit
  contiguous span.
- `expand_chunk(chunk_id, before=N, after=M)` — grow around a specific hit.
- `get_document_outline(document_id)` — the page/heading/`context_id` map so the
  model can see *what exists* before deciding what to pull.

The loop becomes: `search` → sees "doc X, pages 3–5/9, more below" →
`get_document_chunks(X, '6-9')` → assembles the complete context — and it fires
**only when A3's signal says so**, not on every query.

- [ ] `get_document_chunks`, `expand_chunk`, `get_document_outline` tools + impls
- [ ] REST equivalents (optional, mirrors search router)
- [ ] Token-budget cap on returned spans
- [ ] Tests

---

## A5 — Pluggable, frontend-configurable reranker (local + remote / LLM) `[~]`

> **Sequenced first among the implementation phases** (project decision) — it is the
> prerequisite for running cross-encoding on Google AI Studio, and its Gemini
> provider is validated end-to-end with a live AI Studio key once it lands.

**Why.** Two gaps in A1: (1) `ms-marco-MiniLM-L-6-v2` is a ~2019-era, 22 M-param
model — weak next to current rerankers; and (2) it's **local-only and not
user-selectable** — `grep -i rerank ui/src` returns nothing, so the settings page
has no reranker controls, and there's no way to point the rerank stage at your
remote LLM / rerank services. This phase makes the rerank *stage* provider-pluggable
and fully editable from the frontend, reusing patterns embeddings already use.

_Naming:_ with remote/LLM backends, "cross-encoder" is only **one** provider. The
umbrella is the **reranker stage**; `cross_encoder` sits alongside `rerank_api` and
`llm`. Label it "Reranker" in the UI with a provider dropdown.

**Design — mirror the embeddings adapter, which is already multi-provider**
(`api/adapters/embeddings/`: `sentence_transformers | openai_compat | gemini |
ollama`, keyed on `provider`/`model`/`base_url`/`api_key`).

1. **Config schema** — grow [`RerankerConfig`](../api/models/config.py) to match
   [`EmbeddingConfig`](../api/models/config.py:26):
   ```python
   class RerankerConfig(BaseModel):
       enabled: bool = True
       provider: str = "cross_encoder"   # "cross_encoder" | "rerank_api" | "llm"
       model: str = "<modern default — see below>"
       base_url: str | None = None       # remote providers
       api_key: str | None = None        # remote providers (secret)
       top_n: int = 50                    # candidates scored per collection
       timeout_seconds: float = 60.0      # remote providers
   ```
   Add `("reranker", "api_key")` to `SECRET_PATHS` in
   [`config_service.py`](../api/services/config_service.py:58) so it's masked on GET
   and preserved on PUT — exactly like `embedding.api_key`.

2. **Provider adapters** — `api/adapters/reranker/` gains siblings to
   `cross_encoder.py`, dispatched by the existing
   [`get_reranker`](../api/adapters/reranker/__init__.py):
   - `cross_encoder.py` (local, exists) — **modernize the default model**.
   - `rerank_api.py` — hosted, purpose-built rerank endpoints (Cohere / Jina /
     Voyage `/rerank`): send query + candidate texts, get relevance scores. Best
     remote quality-per-latency.
   - `llm.py` — **LLM-as-reranker over your remote LLM services** (your explicit
     ask). Reuse the [tag-suggester LLM plumbing](../api/adapters/tagging/llm.py)
     verbatim: same `provider`/`base_url`/`api_key`/`model` and the same `_post` to
     `/api/chat` (Ollama) or `/v1/chat/completions` (OpenAI-compat). Prompt the model
     to score query–chunk relevance via **structured output** (a JSON `{score}` field,
     not prose); read the field; reorder. `temperature=0`.

3. **Config-page validation** —
   [`_build_adapters`](../api/services/config_service.py:151) already builds the
   reranker as a PUT dry-run (loading a local model fails there *before* persist).
   Give the remote providers a **cheap reachability check** in their constructor (a
   models-list or 1-item rerank ping) so a bad `base_url`/`api_key` fails at *save*,
   not on the first query — matching how the local model validates.

4. **Frontend** — add a **Reranker** section to
   [`ConfigPanel.tsx`](../ui/src/components/settings/ConfigPanel.tsx) + types in
   [`api/types.ts`](../ui/src/api/types.ts), mirroring the embedding section: enable
   toggle, provider select, model field, `base_url`, masked `api_key`, `top_n`.
   Reuse the model-picker helper
   ([`list_ollama_models`](../api/services/config_service.py:300)) for the
   `llm`/ollama case, exactly as the tag-suggester picker already does.

**`llm`-provider caveats (bake into the adapter):**
- **Latency/cost.** An LLM rerank call is far heavier than a 22 M-param
  cross-encoder — the tag adapter already notes ~1 min/call for a *local* small
  model. So `llm` is only practical against a **fast remote** endpoint; on local CPU
  it's too slow for interactive search. When latency matters, prefer `rerank_api`
  (purpose-built) over general `llm`.
- **Pointwise vs listwise.** Scoring `top_n=50` full pages won't fit one listwise
  prompt. Default to **pointwise** scoring of truncated candidate snippets (batched,
  concurrent), or listwise over snippets with a sliding window; keep `top_n` modest
  for `llm`.
- **Graceful degrade.** Remote error/timeout → skip rerank, fall back to RRF-only
  order (never a 500) — same contract as the local reranker returning `None`.

**Modern default (replaces `ms-marco-MiniLM-L-6-v2`).** Per the roadmap's own
convention ("verify current options via Context7" + eval), benchmark before
committing. Current strong candidates:
- *Local cross-encoders:* `BAAI/bge-reranker-v2-m3` (multilingual),
  `mixedbread-ai/mxbai-rerank-large-v2`, `jinaai/jina-reranker-v2-base-multilingual`,
  `Qwen/Qwen3-Reranker-0.6B` (or 4B).
- *Hosted (`rerank_api`):* Cohere `rerank-v3.5`, Jina `reranker-v2`, Voyage
  `rerank-2.5`.
- Prefer a **multilingual** model — the `embeddinggemma` default and a likely
  non-English corpus point that way.

**Shipped (2026-07-07) — provider-pluggable + frontend-configurable reranker.** The stage now
mirrors the embeddings adapter. `RerankerConfig` grew `provider` (`cross_encoder` | `rerank_api` |
`llm`), `llm_provider`, `base_url`, `api_key` (masked via `SECRET_PATHS`), and `timeout_seconds`.
The chat transport was extracted into a shared [`llm_chat.py`](../api/adapters/llm_chat.py)
(`chat_complete` + `post_json`) with a **native Gemini `generateContent` branch**, reused by both
the tag suggester and the new [`llm.py`](../api/adapters/reranker/llm.py) reranker; a hosted
[`rerank_api.py`](../api/adapters/reranker/rerank_api.py) (Cohere/Jina/Voyage) sits alongside. A
shared [`reorder.py`](../api/adapters/reranker/reorder.py) owns the score→sort→renumber step and the
**graceful-degradation guard** (any scoring error, count mismatch, non-numeric or NaN score →
pre-rerank order; [`search.py`](../api/services/search.py) has a matching call-site backstop), so no
reranker path can 500 a search. The `llm` reranker asks each provider for **structured JSON output**
(`chat_complete(response_schema=…)` → `response_format` / `format` / `responseSchema`) and reads the
`score` field, so ranking never depends on scraping a number out of prose. The UI gained a Reranker
section in [`ConfigPanel.tsx`](../ui/src/components/settings/ConfigPanel.tsx). Verified: ruff/mypy/
`tsc` clean, 665 unit+integration tests, and agent review (correctness fixes folded in).

Checklist:
- [x] `RerankerConfig`: `base_url`/`api_key`/`timeout_seconds`/`llm_provider` + documented `provider`
- [x] `("reranker", "api_key")` in `SECRET_PATHS`
- [x] `rerank_api.py` provider (Cohere / Jina / Voyage `/rerank`)
- [x] `llm.py` reranker reusing the shared LLM transport (pointwise, `temp=0`, concurrent,
  **structured-output score** — provider-native JSON, no prose parsing)
- [x] **Native Gemini / Google AI Studio branch** in the shared LLM adapter (`generateContent` +
  `x-goog-api-key`); the shared transport also makes a Gemini *tag*-suggester a config-only change later
- [~] Remote-provider validation in the PUT dry-run — **credential-presence** check ships (a remote
  provider missing its `api_key`/`base_url` raises `ValueError` → 422 at save; boot stays safe via
  `_build_reranker_optional`). A live **network** reachability ping (à la the Ollama "Test connection"
  button) is still todo.
- [ ] Modern default cross-encoder model (chosen via a small eval set) — **deferred**: needs an eval
  set + a Docker re-bake of the vendored model. The default stays offline-safe; the real unlock (the
  model is now user-selectable, incl. a multilingual local or hosted model) has shipped.
- [x] Reranker section in `ConfigPanel.tsx` + `api/types.ts` (+ Ollama model-picker reuse)
- [x] Tests: provider dispatch, `llm` structured-output score/reorder, per-provider structured-output
  payload, `rerank_api` index-map, secret round-trip, degrade-on-error (scoring error / count mismatch
  / non-numeric / NaN / call-site backstop)
- [ ] **Validate the Gemini/AI-Studio reranker end-to-end with a live AI Studio key** — **deferred**:
  needs the live key (supplied out-of-band — never commit it to the repo)

---

# Track B — Context division at ingestion (format-aware, reasoning fallback)

> **💤 Shelved 2026-07-08 — Track A is the path.** The agentic query-time loop
> (A2 + A3 + A4) covers the completeness need without pre-computing context
> boundaries at ingest, so this whole track is deprioritised and its LLM rung
> (**B3**) is **dropped**. B1/B2/B4 remain only as optional future *sharpening*
> of A2/A3 — revisit them only if a measured eval shows query-time expansion is
> insufficient. See the [Summary](#summary) decision note.

The core insight: **the reasoning you imagined at query time should live at
ingestion, computed once per document and amortised over every future query** —
and for most formats it isn't reasoning at all, it's reading structure the file
already carries. A reasoning model is the *last rung* of a format-aware ladder,
used only where structure is absent.

## B1 — `context_id` in metadata + retrieval grouping `[ ]`

**Why.** Give retrieval a unit between "chunk" and "document" so a context that
spans pages can be pulled atomically, and two contexts on a page can be told
apart.

**Design.** Add `context_id` (and optional `context_label`, `context_level`) to
[`ChunkMetadata`](../api/models/chunk.py:6). **No SQL migration** — the store keeps
metadata as `jsonb` ([`pgvector.py`](../api/adapters/vector_store/pgvector.py)), so
adding a key is free; retrieval groups/filters on `metadata->>'context_id'`.

`context_id` is **pure metadata for grouping — it does not change the embedding**,
which decouples it from re-embedding and makes backfill cheap (see
[Migration](#migration--backfill)). Prepending a context label to the text *before*
embedding is a separate, higher-cost lever — that is exactly PR 2 (Contextual
Retrieval) in the retrieval roadmap; keep them independent and let them converge
there.

Retrieval uses `context_id` to sharpen Track A:
- **Expansion (A2):** pull siblings sharing the hit's `context_id` (up to budget)
  instead of a blind ±`w` window.
- **Saturation (A3):** `"context X has 9 chunks; you have 2"` — a precise,
  per-context "there's more".

- [ ] `context_id` / `context_label` / `context_level` on `ChunkMetadata`
- [ ] Retrieval: optional group-by-`context_id`; expansion prefers same-context
- [ ] Saturation signal reads per-`context_id` coverage
- [ ] Tests

## B2 — Format-aware `ContextResolver` `[ ]`

**Why.** `fitz.get_toc()` only works for PDFs with an embedded outline. Markdown,
plain text, and flat/scanned PDFs need different signals. So boundary detection is
a **prioritised, per-format chain** that picks the best available signal and
escalates only when needed.

**Design.** A new `ContextResolver` invoked in the worker ingestion pipeline
([`worker/tasks.py` `_run_ingestion`](../worker/tasks.py:415)) after `parse()`
produces chunks and before/with upsert. It assigns `context_id` to each chunk
using this ladder:

| Format | Primary (deterministic, free) | Fallback 1 | Fallback 2 | Final |
|--------|-------------------------------|-----------|-----------|-------|
| **Markdown** `.md` | `heading_path` / `heading_level` (already parsed) | — | — | — |
| **Code** `.py/.ts/...` | AST `symbol_name` / file (already parsed) | — | — | — |
| **CSV / JSON** | row-batch / object (already structured) | — | — | — |
| **PDF, formatted** | embedded outline `doc.get_toc()` | font/layout heading detect (`get_text("dict")`) **or** docling backend | text-tiling | **LLM (B3)** |
| **PDF, poor / scanned** | docling layout model (already a backend) | text-tiling | **LLM (B3)** | **LLM (B3)** |
| **TXT** | — | text-tiling | **LLM (B3)** | **LLM (B3)** |

Notes:
- **Markdown / code / CSV / JSON already carry their structure** — the resolver is
  a thin adapter over metadata the parsers already emit. These need no reasoning.
- **docling is already a selectable PDF backend**
  ([`__init__.py`](../api/adapters/parsers/__init__.py:55)) — for PDFs without a
  ToC, routing to docling yields headings/sections from a layout model *before*
  any LLM.
- **Text-tiling** = boundary detection from embedding-distance between consecutive
  chunks (a dip = topic shift). Content-based, cheap, no LLM; works on any text.
- Each rung reports a **confidence**; the resolver escalates when the current rung
  is absent or low-confidence.

- [ ] `ContextResolver` protocol + per-format strategies (chain of responsibility)
- [ ] Markdown/code/CSV/JSON: derive `context_id` from existing metadata
- [ ] PDF: `get_toc()` extraction (pymupdf) → `context_id` by page range
- [ ] PDF fallback: font/layout heading detect, or docling route
- [ ] Text-tiling boundary detector (reuses the embedder already in the pipeline)
- [ ] Wire into `_run_ingestion`; assign `context_id` before upsert
- [ ] Tests per format, incl. "no structure → escalates" path

## B3 — Reasoning fallback (LLM boundary detection) — ❌ DROPPED

> **Dropped 2026-07-08.** We will **not** use an LLM to identify context at
> ingestion. The agentic query-time loop (**A2** adjacency + **A3** "there's more"
> + **A4** on-demand fetch) recovers a spanning context more simply and lets the
> MCP caller (LLM or user) decide when to pull more — see the [Summary](#summary)
> decision note. The original design is kept below for the record only.

**Why (your explicit ask).** For **poorly-formatted / scanned PDFs and plain
text** — no outline, no headings, no reliable layout — structural and text-tiling
signals are insufficient. Here a reasoning model reads the content and proposes
semantic boundaries. It runs **at ingestion, once per document**, never per query.

**Design.** The final rung of the B2 chain. Reuse the existing LLM-adapter
plumbing from tag suggestion (`tagging.suggester`, config already present) and
share it with PR 2's contextual-prefix step.

**Cost control (critical for a 1400-page document — you cannot feed 1400 pages to
one call):**
- **Text-tiling proposes, the LLM confirms.** Run cheap embedding-distance tiling
  first to nominate *candidate* boundaries; call the LLM only to confirm/label the
  *uncertain* ones — not one call per chunk.
- Batch adjacent chunks with small overlap; ask for boundary indices + a short
  label per resulting context.
- Cap total LLM calls per document (config); on cap/absence/error, fall back to
  the best cheaper rung (tiling, or one-context-per-page). **Never a 500.**

- [ ] `ContextReasonerConfig` (enabled, provider/model, max_calls_per_doc, prompt)
- [ ] LLM boundary/label step as the final resolver rung
- [ ] Tiling-proposes-LLM-confirms flow to bound cost
- [ ] Graceful fallback when no LLM / cap hit / error
- [ ] Tests incl. cost cap + fallback

## B4 — Structure-aware (sub-page) chunking `[ ]`

**Why.** Whole-page PDF chunks cause the *other* direction of the problem: one
vector per page is a coarse average, so "two contexts on one page" retrieves
badly, and long pages produce oversized, blurred embeddings.

**Design.** Split PDF pages below the page boundary (paragraph/heading-aware within
a page, reusing [`sliding_window`](../api/services/ingestion.py:29) /
`heading_aware`), so each chunk is a tighter semantic unit that still carries
`page_number` (locality) **and** `context_id` (grouping from B2). This is the
structural fix that makes both overlap directions work; sequence it after B2 so
sub-page chunks inherit context assignment.

- [ ] Sub-page splitting in the PDF parser (keep `page_number`, add sub-index)
- [ ] **Separate footnote / citation apparatus from body text** — legal PDFs embed case-law
  footnotes (e.g. TCU acórdãos) into the page vector, blending an unrelated "subject" into the
  body (observed 2026-07-07, page 1390: chapter intro + two acórdãos in one chunk). Split them
  into their own chunk(s) / `context` so neither dilutes the other.
- [ ] Ensure `chunk_index` stays contiguous for A2 adjacency
- [ ] Re-ingest path (changes embeddings — see Migration)
- [ ] Tests

---

## Guardrails

- **Token budget on assembled spans.** A2/A4/B1 stop truncating, so a hot document
  or a 70-page ToC "chapter" could otherwise return enormous spans. Every
  expansion/grouping path must cap output by a configurable token budget: structure
  gives the *boundary*, budget gives the right-sized *slice*.
- **Per-hit conciseness (MCP), near-term lever.** Today a single hit returns the whole page
  (~2.3–3.5k chars, observed 2026-07-07). B4 makes chunks tighter, but *before* that re-index the
  MCP can return a bounded snippet per hit and let the model pull the full text on demand via
  A4's `get_document_chunks` — a conciseness win independent of re-chunking.
  - [ ] Snippet-per-hit + full-text-on-demand in `search_documents` (bounded char/token cap)
- **Graceful degradation.** Mirror the retrieval roadmap's rule — any stage that
  errors or lacks a model falls back to prior behaviour. Search must never 500.
- **Don't over-warn.** A3 stays quiet when results show a clear elbow (genuinely
  complete), so the `notice` stays meaningful.

## Migration & backfill

- **`context_id` (B1/B2/B3) is metadata-only → cheap backfill, no re-embed.** A job
  reads existing chunks per document, runs the resolver, and
  `UPDATE chunks SET metadata = metadata || jsonb_build_object('context_id', …)`.
  The current 1400-page PDF can be back-annotated without re-embedding.
- **Sub-page chunking (B4) changes the chunks themselves → full re-ingest** of
  affected documents (new embeddings, new `chunk_index`). Gate behind a flag and
  document as a breaking re-index, like PR 3's model swap.

## Measuring it ("better" must be provable)

The retrieval roadmap notes there is **no eval harness**. Completeness needs its
own metric beyond recall@k:
- **Context/span completeness** — of the chunks belonging to the gold context, what
  fraction did the response (after expansion) actually return?
- **Truncation-warning precision/recall** — when more relevant data existed, did A3
  fire? When results were complete, did it stay silent?

A small fixed query set over the current 1400-page PDF is enough to turn these
phases from "should help" into "measured +X%". Worth standing up alongside A2.

## Relationship to `docs/retrieval-upgrade-plan.md`

- **PR 1 (reranker)** — this plan's A1 turns it **on by default** (+ a startup
  pre-warm item). Reconcile the "off by default" note there.
- **PR 2 (contextual retrieval)** — shares ingestion plumbing with B2/B3. PR 2
  prepends an LLM context *prefix* to the embedded text; B-track assigns a
  `context_id` for *grouping*. Same ingestion pass, same LLM adapter, two outputs —
  build them together.
- **PR 4 (query transformation)** — orthogonal; benefits from A1's reranker
  cleaning up any widened candidate set.

---

## Suggested order

**Done:** A5 (reranker providers + frontend), A1 (on by default + pre-warm), A2
(adjacency expansion + span merge, merged).

**Done:** A5, A1, A2, **and A3** (saturation flag + per-document `coverage` + document-naming
`notice`).

**Remaining path — all query-time, no ingestion change, no schema change, no reasoning model:**

1. **A4** — the expansion primitives (`get_document_chunks`, `expand_chunk`,
   `get_document_outline`). **This is the mechanism the decision above depends on** — it lets the
   MCP caller act on A3's "there's more" (and its per-document `coverage`) and pull the rest of a
   context on demand. A3's `notice` already tells the model *which* document to expand; A4 gives it
   the tool to do so.

**Shelved (Track B — context division at ingestion):** B1/B2/B4 are optional
future *sharpening* of A2/A3, not the path — adjacency + the agentic loop cover the
completeness need without them. **B3 (LLM boundaries at ingestion) is dropped.**
Revisit Track B only if a measured eval shows query-time expansion is insufficient.

---

## Adjacent correctness issues (surfaced while testing)

Not completeness problems, but found during the same MCP/UI verification and recorded here so
they aren't lost — fix independently of the tracks above.

- [ ] **Search/MCP provenance shows the ingestion *temp* filename, not the document name.** For
  non-local (S3/MinIO) storage backends, [`_run_ingestion`](../worker/tasks.py) fetches the
  object to a temp file and passes that path to the parser, which stores
  `filename = os.path.basename(temp_path)` in chunk metadata — so `search_documents` and the UI
  show `tmpruad779z.pdf` / `/tmp/…` instead of `licitacao.pdf`, and *different chunks of one
  document can carry different temp names* (a resumed ingest creates a new temp file each time:
  `tmptyy5qd14.pdf`, `tmp1qd0qkfm.pdf`). The local backend is unaffected (temp file = original).
  Fix: stamp the real filename (known at `job_records.filename`) into chunk metadata at ingest,
  or resolve the display name from the `documents` table (as the Graph view already does).
  Confirmed 2026-07-07 (UI + MCP). Any already-ingested S3/MinIO docs need a metadata backfill.
