---
paths:
  - "api/**"
  - "worker/**"
  - "tests/**"
---

# Required skills — how a change earns "done"

The gate proves the code is *mechanically* sound; these skills prove it's *actually* done — that it
follows the working agreement and doesn't ship a bug. Run them at the end of every implementation,
before you report the task complete or offer to commit. `/standards-check` is this repo's own skill
(`.claude/skills/standards-check/`); the rest are built in.

## The order (and why)
1. **The gate** — `ruff` + `mypy` + `pytest` green (commands in `CLAUDE.md`). Prerequisite: don't run the
   review skills over code that doesn't lint / type / pass. The toolchain lives in the WSL virtualenv, so
   from a Windows host wrap commands with `wsl.exe -e bash -lc '…'`.
2. **`/standards-check`** — conventions. Cheap and structural; catches DRY / layering / secret-masking /
   ORM issues that would otherwise waste a review pass. Fixing them can move code, so do it *before* the
   deep review.
3. **`/code-review`** — correctness. The final bug hunt, on now-convention-clean code. It's the last thing
   between you and "done".

**Fix-and-re-run:** after non-trivial fixes from either skill, run it again — a fix can introduce a new issue.

## `/standards-check` (this repo's skill)
- **What** — audits the current diff against `CLAUDE.md` + the path-scoped `.claude/rules/*.md`, emitting a
  pass / warn / fail checklist with `file:line` and concrete fixes. Report-first: it won't edit unless asked.
- **When** — after the gate is green, before `/code-review`; or any time you want to confirm a change
  follows the project's standards.
- **How** — `/standards-check` audits the work in progress. Pass a scope if needed: a path, or a range like
  `main...HEAD`. Fix every ❌ (fix or justify each ⚠️), then re-run until the verdict is clean.

## `/code-review` (built-in)
- **What** — reviews the diff for correctness bugs plus reuse / simplification / efficiency cleanups.
- **When** — last, before calling the work finished or offering to commit. **Mandatory** — don't report a
  task complete without it.
- **How** — `/code-review` (default effort). Raise depth with `high` / `max`, or `ultra` for a deep
  multi-agent cloud review (user-triggered, billed — you can't launch it yourself). `--fix` applies findings
  to the working tree; `--comment` posts them on a PR. Address every finding; re-review if fixes are non-trivial.

## Situational — run when they apply
- **`/security-review`** — a security pass over the pending branch. Run whenever a change touches auth /
  permissions (`api/services/auth.py`, API keys), secrets / `SECRET_PATHS`, raw SQL (the pgvector adapter),
  file upload or other external input, or the MCP surface.
- **`/verify`** — exercises a change end-to-end to confirm it does what it should (not just that tests pass).
  Run for a nontrivial behavioural change — a new/changed endpoint, pipeline stage, or search behaviour —
  before committing.
- **`/simplify`** — quality-only cleanup (reuse / simplification / efficiency), applied. Use when you want
  the cleanup without the bug hunt; `/code-review` already covers this ground, so it's optional on top.

## Rules of engagement
- **Address every finding** — fix it, or state explicitly why it's a non-issue. An unaddressed ❌ means the
  change isn't done; a skill's output is not advisory noise.
- **Don't stop at the first pass** — re-run after non-trivial fixes.
- **Don't report complete or offer to commit** until `/standards-check` and `/code-review` are clean.
- These skills *verify*; they don't excuse skipping step 1 of the loop (understand + reuse first). A clean
  review of duplicated code is still a DRY violation — see [`code-standards.md`](code-standards.md).
