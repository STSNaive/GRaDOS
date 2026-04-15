# AGENTS.md

Persistent repo context for Codex. Keep this file short and stable. Put detailed design rationale in `ADR.md`, not here.

## Repo Doc Map

- `README.md`
  - Primary user/developer entry point.
- `README.zh-CN.md`
  - Chinese mirror of the main README.
- `CHANGELOG.md`
  - Record completed, user-visible behavior changes.
- `TODO.md`
  - Only track unfinished work and current priorities.
- `ADR.md`
  - Record accepted architectural decisions and rationale.
- `grados-python-implementation-plan.md`, `status.md`, old files under `docs/`
  - Historical reference material.

## Update Expectations After Changes

- Update `README.md` and `README.zh-CN.md` when user-facing install/setup/runtime behavior changes.
- Update `CHANGELOG.md` when a completed change affects users, operators, or downstream agents.
- Update `ADR.md` when a design decision is accepted and is expected to guide future implementation.
- Update `TODO.md` when priorities change or when a task is completed, descoped, or replaced.
- If a task changes both behavior and architecture, update both `CHANGELOG.md` and `ADR.md`.
- When executing a task from `TODO.md`, complete the code change first, verify that the task is actually done, and only then update the related docs (`TODO.md`, `CHANGELOG.md`, `ADR.md`, `README*` as needed).

## Repo Guardrails

- Treat `papers/*.md` as the canonical full-text source of truth.
- Treat `database/chroma` as a rebuildable retrieval index, not as the canonical paper store.
- Prefer "index recall + canonical reread" over returning index-resident chunk text as final evidence.
- Surface partial-success, parser warnings, and actionable debug context instead of silent fallback.
