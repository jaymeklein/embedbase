# CLAUDE.md — working agreement for **embedbase**

Binding for every change, Claude and humans alike. The bar is not "make it work."
The bar is **make it work, simply, without repeating yourself — and prove it.**

This file is the **index**. The detail lives in [`.claude/rules/`](.claude/rules/) — each
file there carries a `paths:` front-matter glob, so it loads into context **automatically
when you edit the code it governs**, and stays out of the way otherwise. Read the matching
rule file *before* you touch an area; don't rediscover conventions that are already written down.

---

## The loop — every change, no exceptions

1. **Understand before writing.** Search the repo for an existing pattern, helper, constant,
   config field, adapter, or test double that already does what you need, and reuse or extend it.
   About to add a `class`/`def`/constant? `grep` its name and its siblings **first** — a sibling
   means follow it; a copy means stop, you're creating duplication.
2. **Implement to the standards** — DRY · KISS · YAGNI · SOLID
   (see [`code-standards.md`](.claude/rules/code-standards.md)).
3. **Pass the gate** — ruff + mypy + pytest all green (below). A change that fails the gate is not done.
4. **Run the required skills** — `/standards-check` (conventions), then `/code-review` (bugs) — at the
   end of every implementation, before you call the work finished or offer to commit. Address every
   finding; re-run if the fixes are non-trivial. Don't report a task complete without them.
   Details & order: [Required skills](#required-skills) → [`skills.md`](.claude/rules/skills.md).

Skipping step 1 or step 4 is the exact failure mode this document prevents.

---

## The gate — run before every "done"

The toolchain lives in the WSL virtualenv. From the repo root:

```bash
.venv/bin/ruff check api/ worker/ tests/
.venv/bin/mypy api/ worker/ --ignore-missing-imports --explicit-package-bases
.venv/bin/python -m pytest tests/unit tests/integration -q
```

All three green, **then the required skills** (see [Required skills](#required-skills)). This mirrors CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)):
ruff `E,F,I,UP,B,SIM` (line-length 100, py312), mypy on `api/ worker/`, the unit + integration
suites, and the docker-build smoke test. `tests/` is linted too — test code meets the same bar.

---

## Required skills

At the end of every implementation — once the gate is green — run these and address every finding
before calling the work done or offering to commit. Full when / how / order in
[`skills.md`](.claude/rules/skills.md):

- **`/standards-check`** — audits the diff against this working agreement (`CLAUDE.md` + `.claude/rules/`).
  Run it first; fix every ❌ and re-run until clean.
- **`/code-review`** — hunts correctness bugs and reuse / simplification issues. Run it last; address
  findings, re-review if the fixes are non-trivial.

Situational: **`/security-review`** when touching auth, secrets, SQL, or external input; **`/verify`**
when the change has runtime behaviour worth exercising end-to-end.

---

## Rule index — read the file for the area you're touching

| Area | Rule file | Read it when you're working on… |
|------|-----------|--------------------------------|
| Code standards | [`code-standards.md`](.claude/rules/code-standards.md) | any code — DRY/KISS/YAGNI/SOLID, reuse-first, the shared test doubles |
| Architecture | [`architecture.md`](.claude/rules/architecture.md) | adapters, the `get_*()` factory pattern, graceful degradation, router→service→adapter layering |
| Configuration | [`configuration.md`](.claude/rules/configuration.md) | `AppConfig` pydantic models, secrets & masking (`SECRET_PATHS`), the config save / hot-reload / rollback flow |
| API | [`api.md`](.claude/rules/api.md) | adding an endpoint, routers, middleware, lifespan, `schemas/` vs `models/`, error/status conventions |
| Permissions | [`permissions.md`](.claude/rules/permissions.md) | API-key auth, `require_auth` / `require_master`, collection scoping, minting & revoking keys |
| Ingestion | [`ingestion.md`](.claude/rules/ingestion.md) | upload→parse→chunk→embed→store, Celery jobs, retries / rate-limit pause, retry-all-failed, progress events |
| MCP | [`mcp.md`](.claude/rules/mcp.md) | the embedded MCP server, adding or changing an MCP tool, its transport & auth |
| Metadata DB | [`database.md`](.claude/rules/database.md) | the SQLAlchemy ORM, `api/tables/`, Alembic migrations — the metadata DB |
| Vector DB | [`vector-db.md`](.claude/rules/vector-db.md) | the `chunks` table, raw asyncpg, pgvector / ParadeDB operators, hybrid-search SQL |
| Tests | [`testing.md`](.claude/rules/testing.md) | test layout, the shared fakes, what a unit test may touch |

---

## Commits (only when explicitly asked)

- **Conventional Commits**: `type(scope): summary` (`feat`, `fix`, `refactor`, `docs`, `test`, `chore`, …).
  Same format for PR titles.
- **No AI attribution** — commit under the user's identity. No `Co-Authored-By: Claude`, no "Generated with" trailer.
- Don't commit or push unless asked; branch off `main` first when needed.

## Libraries & docs

Use the **Context7 MCP** for any library / framework / SDK / API / CLI question (syntax, config,
migrations, debugging) *before* answering from memory — training data lags real releases.
