# Kit shape & conventions

**Date:** 2026-07-03 · **Context:** initial build of claude-project-kit (distilled from
todoclaw, Stages 0–6 + retro)

## Decision

The kit is a **public GitHub template repo** that self-hosts its own protections:
1. **Hooks land in PR #1 and guard the kit's own construction** — the same
   `.claude/hooks/` suite the template ships is live in this repo, with the 48-case
   block/allow battery committed as a permanent CI-run test (`test_hooks.py`), not a
   one-off verification.
2. **App-project workflows live in `templates/workflows/` (inert), not
   `.github/workflows/` (live)** — activated by `git mv` at bootstrap. The kit's own CI
   (`.github/workflows/ci.yml`, job `Kit checks`) stays green while still
   YAML-validating the templates on every PR.
3. **All work flows through PRs from commit #1**, gated by the kit's own CI and branch
   protection (required context `Kit checks`, admins enforced) — protection enabled
   only after the context first reported on main (merge-then-require).
4. **ADRs use the date+slug convention** this file demonstrates (docs/adr/README.md).
5. **`bypassPermissions` ships in settings.json** only because the hook suite
   hard-blocks in every mode — remove the hooks, remove the bypass.

## Why

- *Self-hosting* is the only honest proof the hooks work: the branch guard forced this
  repo's own PR flow (main was seeded via a one-time API commit so PR #1 had a base —
  local commits to main were already blocked).
- *Inert templates* resolve a real conflict: the deliverable says "ship the app
  workflows," but live copies would run (and fail) against the bare kit. An `if:`-guard
  per job was rejected (repo-name coupling, noise in every template); a separate dir
  with a one-command activation step is self-documenting.
- *Battery as permanent test*: todoclaw's v2 hook was verified by an 18-case battery
  that lived only in a PR description. Rebuilt here as a committed test (expanded to 48
  cases incl. sandboxed branch-guard cases that survive CI's detached HEAD), so every
  future hook edit is regression-gated.
- *Public repo*: unlimited Actions minutes and branch protection on the free tier
  (private free repos get neither); the kit is placeholders-only by design and its own
  layers enforce that.

## Verified

- Battery 48/48 locally and in CI on every PR (runs 1–3).
- Kit CI green on main; branch protection active with `Kit checks` required and
  `enforce_admins: true` — verified by the API response and by this very PR needing
  green checks to merge.
- `is_template: true` confirmed via `gh api` (the "Use this template" button exists).
- The missing-script deadlock during bootstrap (docs/LESSONS.md) empirically confirmed
  the fail-closed property.
