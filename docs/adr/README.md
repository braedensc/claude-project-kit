# ADRs — one file per decision, date+slug, no numbers

The convention comes from a production build where numbered ADRs (`0001`–`0024`)
collided **three times** during parallel sessions: numbers are claimed at merge-to-main,
so two open branches draft the same number and the later merge renumbers (one ADR was
renumbered twice, 0019 → 0020 → landing as 0022). The structural fix:

## The convention

- **One file per decision** in `docs/adr/`.
- **Filename: `YYYY-MM-DD-short-slug.md`** — no sequence number. There is no shared
  counter to claim and no common file tail to append to, so parallel sessions add ADRs
  with zero coordination.
- **Slug on significant words only** — don't auto-truncate titles (an auto-generator
  once produced `…-branch-protection-the.md`); trim trailing stopwords.
- After adding a file, add **one row** to the project's `docs/ARCHITECTURE.md` index
  table (`| ADR | Date | Decision |`) — a single-row edit, the only shared touch.

## What goes inside

```markdown
# <Title>

**Date:** YYYY-MM-DD · **Status:** Accepted · **Context:** <stage / PR #>

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
- **Status stays truthful:** when a decision *is* replaced, the PR that supersedes an
  ADR flips the old one's `**Status:**` to `Superseded by <file>` *and* its index row,
  in the same change.
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
| [Hook hardening + session root](2026-08-23-hook-hardening-and-session-root.md) | 2026-08-23 | stderr block reasons; path-target secret guard (+ SSH/cloud credential files); egress guard; anchored rm flags; `settings.local.json` + widened mutation net self-protected; fail-closed dispatch; session root anchored on `CLAUDE_PROJECT_DIR` (subagent worktree fix) |
| [Untrusted-ticket-data fence](2026-08-24-untrusted-ticket-data-fence.md) | 2026-08-24 | Tracker text is third-party data, never instruction: one fence everywhere, tag neutralized inside the payload, length capped; the pin is authority and `session-start.py`'s output explicitly is not |
| [Attempt-counter durable home](2026-08-24-attempt-counter-durable-home.md) | 2026-08-24 | The attempt count lives in a dispatcher-owned `pipeline-dispatcher-state/1` record bound per backend by `dispatch.statePath`; ticket-comment and `pinsRoot` transports rejected by name; bounded-not-exact, with `agent:needs-human` as the durable backstop |
| [Pipeline guards, dispatcher-anchored](2026-08-24-pipeline-guards-dispatcher-anchored.md) | 2026-08-24 | Six delivery-pipeline guards, inert unless `delivery.json` exists; pin file outside the worktree + config from the default branch as the trust anchors; per-guard fail direction |
