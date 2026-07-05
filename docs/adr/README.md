# ADRs — one file per decision, date+slug, no numbers

The convention distilled from todoclaw, where numbered ADRs (`0001`–`0024`) collided
**three times** during a parallel-session build: numbers are claimed at merge-to-main,
so two open branches draft the same number and the later merge renumbers (one ADR was
renumbered twice, 0019 → 0020 → landing as 0022). The structural fix (todoclaw PR #51):

## The convention

- **One file per decision** in `docs/adr/`.
- **Filename: `YYYY-MM-DD-short-slug.md`** — no sequence number. There is no shared
  counter to claim and no common file tail to append to, so parallel sessions add ADRs
  with zero coordination.
- **Slug on significant words only** — don't auto-truncate titles (todoclaw's generator
  produced `…-branch-protection-the.md`); trim trailing stopwords.
- After adding a file, add **one row** to the project's `docs/ARCHITECTURE.md` index
  table (`| ADR | Date | Decision |`) — a single-row edit, the only shared touch.

## What goes inside

```markdown
# <Title>

**Date:** YYYY-MM-DD · **Context:** <stage / PR #>

## Decision
<what, in 2–4 sentences>

## Why
<forces, alternatives rejected by name, accepted tradeoffs + future hardening>

## Verified
<the proof it works — adversarial where possible: two-user tests, curl matrices,
run-twice idempotency. An ADR without evidence is an opinion.>
```

Conventions that proved their worth:
- **Amend in place** with dated `**Update (…)**` blocks rather than superseding files —
  cross-references stay stable.
- **Deferrals get ADRs too**, with a named revisit trigger — and a dated re-decision
  ADR when the trigger fires. The log records what was *not* done and when.
- **Deviations from plan** are recorded with owner sign-off + date + the re-entry path.

## When to write one

- **Bootstrap phase:** liberally — roughly one per significant PR. The density is
  deliberate scaffolding (docs/LESSONS.md → "docs are scaffolding first").
- **Post-launch:** only for decisions that change architecture, a security boundary, or
  an external service. Routine features and fixes need none.

## Index

| ADR | Date | Decision |
|---|---|---|
| [Kit shape & conventions](2026-07-03-kit-shape-and-conventions.md) | 2026-07-03 | The kit's own key choices: inert templates dir, self-hosted hooks from PR #1, battery-as-permanent-test, PR-flow-from-commit-1 |
| [Ecosystem parity](2026-07-04-ecosystem-parity.md) | 2026-07-04 | Skills (/ship, /new-adr), native permissions.deny, inert @claude Action + .mcp.json.example, minimal devcontainer, advisory SessionStart hook |
| [Publish readiness](2026-07-04-publish-readiness.md) | 2026-07-04 | MIT license; no shipped PATH override; fail-closed on missing python3; enumerated deny list (.env.example readable); stranger-proofed docs |
