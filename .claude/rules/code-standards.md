---
paths:
  - "api/**"
  - "worker/**"
  - "tests/**"
---

# Code standards — DRY · KISS · YAGNI · SOLID

The bar is **make it work, simply, without repeating yourself — and prove it.** These are the
principles; the concrete repo conventions that implement them live in
[`architecture.md`](architecture.md).

## DRY — one fact, one place
- **Search for an existing implementation before writing a new one**, and reuse it. Duplication
  discovered later is a defect, not a style nit.
- **Shared test doubles live in `tests/unit/fakes.py` and `tests/unit/fake_redis.py`** — import them
  (`from tests.unit.fakes import FakeStore, FakeEmbedder`). **Never** re-declare `FakeStore` /
  `FakeEmbedder` / `FakeRedis` inside a test module. A new reusable double goes in those modules,
  not inline. (More in [`testing.md`](testing.md).)
- Shared domain logic belongs in a service/adapter/module — never copy-pasted across routers, tasks, or tests.
- A literal used in more than one place becomes a named constant (see `api/constants.py`).
- **Fixing a DRY violation means finding *every* copy**, not only the one that was pointed out.

## KISS — the simplest thing that works
- Prefer a function to a class, a plain call to a framework, a straight line to a clever one.
  Optimise for the next reader.
- No premature abstraction: the **second** concrete case justifies an interface, the first does not.
- If a change needs a paragraph to explain why it's clever, it's probably wrong.

## YAGNI — build what's needed now
- No config knobs, parameters, hooks, or "for later" branches without a **current** caller.
  The roadmaps in `plans/` and `docs/` are staged on purpose — don't pull a future phase forward.

## SOLID — where it earns its keep
- **SRP** — one reason to change per unit. Parsing ≠ chunking ≠ embedding ≠ storing; keep them
  separate, mirroring `api/services/` and `api/adapters/`.
- **OCP** — extend by adding, not by editing callers. A new embedding / reranker / parser backend is
  **a new file under `api/adapters/<kind>/` plus one branch in that kind's `get_*()` factory** —
  callers depend on the factory, never on a concrete class. (Pattern in [`architecture.md`](architecture.md).)
- **LSP** — a new adapter honours its Protocol's full contract, including graceful degradation.
- **ISP** — keep the Protocols in `api/adapters/base.py` minimal. Optional capabilities stay *off* the
  Protocol (see the `on_progress` note on `ParserAdapter`) so implementations that lack them still conform.
- **DIP** — depend on the `Protocol`, inject the dependency. `_run_ingestion` takes
  `embedder` / `vector_store` / `storage` as parameters — that's *why* it is testable with the fakes.
  Follow that shape.

## Libraries — code against *current* docs
Before writing or debugging code against a library / framework / SDK / API / CLI, pull its **current**
docs via the **Context7 MCP** — don't code from memory, training data lags real releases. This repo pins
and verifies real versions (e.g. the `# verified against … via Context7` note atop
`api/adapters/vector_store/pgvector.py`); keep that habit for new adapters and upgrades.
Canonical rule: `CLAUDE.md` § Libraries & docs.
