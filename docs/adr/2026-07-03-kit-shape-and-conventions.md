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

**Update (2026-07-03, completeness pass):** the battery grew to 51 cases
(master-branch guard, Read `.pem`, shell-read `.key` alternatives) and the battery
step now ships inside `templates/workflows/ci.yml` — its permanence in bootstrapped
projects is structural, not instructional.

**Update (2026-07-03, workflow-guards port):** the enforcement surface gained a
fourth layer from the todoclaw handoff (PRs #59/#61/#63): a merged-PR guard and a
never-merge `gh pr merge` block in the PreToolUse hook, plus a Stop hook
(`stop-pr-check.py`) that blocks ending a turn with no PR or failing CI. The battery
grew to 68 cases, now including mocked-`gh` sandboxes and the Stop hook's
exit-0/JSON protocol.

**Update (2026-07-04, hook-guardrails port):** three more deterministic guards from
todoclaw PR #77 (+ its branch-naming guard): a **cross-worktree write guard** (blocks
Edit/Write into a different checkout — the one way a session wrote to `main` past the
branch guard), a **branch-naming guard** (blocks work on a non-`<type>/<slug>` branch
so an auto-generated `claude/<codename>` never lands unrenamed), and a **DIRTY-PR
guard** in the Stop hook (a conflicted PR skips required CI and can look green on side
checks alone). Battery → 75 cases, now with a real sibling-worktree sandbox.

**Update (2026-07-04, self-protection):** the hooks now protect *themselves* — the
PreToolUse guard blocks Edit/Write and Bash mutations of the hook scripts +
`settings.json`, so a block can't be edited away or unwired (the easiest bypass). This
closes the model's biggest gap: previously any guard was defeatable by rewriting the
hook. Changing a hook is now a human-only terminal step; the guard is validated by
build-before-lock discipline (it self-references) and new battery cases (→ 91). A
first-pass false positive (a benign `2>&1` near a hook path over-blocked) was fixed by
making the mutation match *target* the protected path — and, fittingly, applied via the
human-only terminal flow the guard itself mandates.
Consequence: bootstrap's NODE_BIN_PATH-placeholder fill in settings.json (the
placeholder was later retired — see 2026-07-04-publish-readiness) becomes a
human terminal step (BOOTSTRAP-PROMPT Phase 4 step 6).
