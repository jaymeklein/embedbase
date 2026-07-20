---
name: standards-check
description: >-
  Audit the current change set against this repo's own documented standards — CLAUDE.md (the change
  loop, the gate, commits) and the path-scoped rule files in .claude/rules/*.md — and report a
  per-standard pass/warn/fail checklist with file:line references and concrete fixes. Use this
  whenever the user wants to check, verify, or confirm that changes follow the project's standards,
  conventions, rules, or working agreement (phrasings like "do these follow our standards", "check
  this against the rules", "is this up to standard", "standards check", "conventions review"), and as
  the recommended compliance pass after implementing a change and before committing or opening a PR.
  It complements /code-review (which hunts correctness bugs) and the ruff/mypy/pytest gate (mechanical
  checks) by verifying adherence to the written project conventions specifically. It maps each changed
  file to the rule files that govern it and reads them live, so it never goes stale.
---

# Standards Check

Verify that the current changes actually honour **this repo's own written standards**, and report
where they don't with enough precision to fix it. The standards are not in this skill — they live in
`CLAUDE.md` and the path-scoped rule files under `.claude/rules/`. **Read them at runtime and check
against them.** Copying the rules in here would rot the moment someone edits a rule file; reading them
live keeps this check correct by construction (and honours the project's own DRY rule).

This is the *conventions* pass. It does **not** replace:
- **`/code-review`** — finds correctness bugs and generic reuse/simplification issues.
- **the gate** — `ruff` + `mypy` + `pytest`, the mechanical checks.

Run those too; this one answers a different question: *does the diff follow the documented working agreement?*

## Process

### 1. Scope the change set
Default to the work in progress — staged + unstaged + untracked. If the working tree is clean, fall
back to the branch's diff vs `main`. The user may narrow it (a path, or a ref/range like `main...HEAD`).

### 2. Map changed files → the standards that govern them
For each changed file (from step 1), read every `.claude/rules/*.md` whose `paths:` front-matter glob
matches its path — those rule files are the standards in scope. `CLAUDE.md`, `code-standards.md`, and
`architecture.md` **always** apply (they're universal). Claude Code already auto-attaches a rule file
when you open a file its glob matches, so reading the changed files pulls most of the relevant rules
into context for free — but read the matching rule files explicitly too, so nothing is missed.

### 3. Read from the rules, not from memory
Include `CLAUDE.md` alongside the rule files from step 2 — together they're your checklist. Audit from
what they actually say: the rules are specific (e.g. exact `SECRET_PATHS`, the ORM-vs-asyncpg split, the
adapter-factory shape) and that specificity is the whole point.

### 4. Audit the diff against each applicable rule
Go hunk by hunk. Judge the change against the concrete criteria in the rule files, plus the universal
loop from `CLAUDE.md`. Cover at least these categories (each cites where its criteria live):

- **Reuse-first / DRY** (`code-standards.md`) — did the change reuse an existing helper/constant/service,
  or duplicate one? `grep` the new symbol and its siblings to be sure. A **re-declared shared test double**
  (`FakeStore`/`FakeEmbedder`/`FakeRedis`) or a copied literal/logic is a fail. Find *every* copy.
- **KISS / YAGNI** (`code-standards.md`) — premature abstraction, unused params, config knobs or branches
  with no current caller, cleverness that needs a paragraph to justify.
- **Architecture / SOLID** (`architecture.md`) — a new backend must be **a new file under
  `api/adapters/<kind>/` + one branch in that kind's `get_*()` factory**, with callers depending on the
  Protocol; **graceful degradation** preserved (no new path that can 500 a search/ingest); layering kept
  (routers thin, logic in services, no raw SQL on the metadata DB).
- **Area rules** — whichever applied in step 2: config secrets added to `SECRET_PATHS`
  (`configuration.md`); metadata DB via ORM/`select()` only, schema change ⇒ Alembic migration
  (`database.md`); new `chunks` queries raw **and parameterised** in the pgvector adapter (`vector-db.md`);
  `require_auth` routes still enforce `can_access`, secrets never logged (`permissions.md`); ingestion stays
  resume-safe / injects its deps / degrades gracefully (`ingestion.md`); MCP tools keep the two-layer split
  (`mcp.md`); endpoints stay thin with schemas vs models placed correctly (`api.md`).
- **Tests** (`CLAUDE.md`, `testing.md`) — every behavioural change ships a test; unit tests touch no
  network/DB/Redis and reuse the shared fakes.
- **Libraries** (`CLAUDE.md`) — new or upgraded library usage verified against current docs via Context7 /
  pinned, not coded from memory.
- **Commits** (`CLAUDE.md`) — only if a commit/PR is in scope: Conventional Commits format, **no AI
  attribution** (no `Co-Authored-By: Claude` / "Generated with" trailer).

For every finding, give: **`file:line`**, the **rule** (name the rule file and quote the clause),
**why** the change violates it, and the **concrete fix**. Vague findings help no one — be specific enough
that someone could act on it without re-reading the diff.

### 5. Check the gate
Report whether `ruff` / `mypy` / `pytest` were run and are green — "a change that fails the gate is not
done." If their status is unknown, offer to run them:

```bash
.venv/bin/ruff check api/ worker/ tests/
.venv/bin/mypy api/ worker/ --ignore-missing-imports --explicit-package-bases
.venv/bin/python -m pytest tests/unit tests/integration -q
```

### 6. Report
Use the template below. Rank findings worst-first. End with an ordered must-fix list and an offer to fix.

## Report format
Use this exact shape so results read consistently:

```
## Standards Check — <branch> @ <short-sha> · <N> file(s)
**Scope:** <work in progress | branch vs main | custom>  ·  **Audited:** a.py, b.py, …

### Verdict: ✅ Compliant | ⚠️ Compliant with warnings | ❌ Not compliant

### Findings
❌ **<category> — <one-line>** · `path/to/file.py:42`
   Rule (<rule-file>.md): "<quoted clause>"
   <why it violates> → <concrete fix>

⚠️ **<category> — <one-line>** · `path/to/file.py:88`
   Rule (<rule-file>.md): <clause>
   <why> → <fix>

✅ **<category>** — <what was verified good> (only list the notable passes)

### Gate
ruff <✅|⚠️ not run> · mypy <…> · pytest <…>

### Must-fix (in order)
1. <the ❌ items, each a one-liner>
Then re-run this check.
```

## Rules of engagement
- **Report-first.** Do not edit code as part of the check. After presenting findings, offer to fix; only
  touch code if the user says so, then re-run the check to confirm.
- **Earn a clean bill.** A ✅ must mean you read the applicable rules and the diff and found nothing — never
  a rubber stamp. If you didn't read a rule file, you can't pass its category; say so.
- **Docs-only or config-only changes are fine** — say the code categories are N/A and check only what
  applies (e.g. commit format, no committed secrets, docs consistency).
- **Large diff?** You may fan out a subagent per rule area for independence, then merge — but the explicit
  read-the-rules checklist above is what guarantees coverage, not the parallelism.
