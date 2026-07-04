# claude-project-kit

A GitHub **template repo** for starting full-stack projects with every system and
lesson from a real production build already in place: a Claude Code security-hook
suite (with its own test battery), git-level secret scanning, CI, deploy/backup/
keepalive workflow templates, and the process docs that make multi-session agentic
development safe.

Everything here is **distilled, not invented** — generalized from
[todoclaw](https://github.com/braedensc/todoclaw)'s build (2026-06-23 → 2026-07-03,
Stages 0–6 + retro; live in production), the project's memory files, and the v1
secure-bootstrap kit it supersedes. Every generalized file carries a provenance header
saying where it came from and what was verified in production.

## Philosophy

- **Walking skeleton first, local-first, ship-last.** Prove the thinnest end-to-end
  slice (repo → CI → deploy) before widening it with features; develop against a free,
  disposable local stack; production changes only through reviewed, gated pipelines.
- **Defense in depth — three independent layers.** Claude Code hooks (the model can't
  bypass), git pre-commit hooks (fast local mirror), CI + branch protection (the
  unbypassable gate). See [docs/SECURITY.md](docs/SECURITY.md).
- **Bypass is earned by hooks.** `settings.json` ships
  `permissions.defaultMode: bypassPermissions` *only because* the hook suite
  hard-blocks the dangerous operations in every mode. The two ship together or not at
  all.
- **Docs as scaffolding, then right-sized.** Heavy ADR/docs discipline while building
  (cold sessions reconstruct the system from written context), dialed down explicitly
  at launch. Written context is how isolated agent sessions coordinate.
- **Guards match operations, not prose.** Learned the hard way; encoded in the hook
  and verified by a 68-case battery that runs in CI forever.

## Quickstart

1. **Use this template** (button above) → create your repo → clone it.
2. Open a fresh **Claude Code** session in the clone — the hooks are already active.
3. Paste the prompt from **[BOOTSTRAP-PROMPT.md](BOOTSTRAP-PROMPT.md)**. It interviews
   you for stack choices, adapts every stack-specific file, fills every placeholder
   ([PLACEHOLDERS.md](PLACEHOLDERS.md)), deletes what doesn't apply, and runs the
   kit's self-checks. You keep the human-only steps (secrets, dashboards, branch
   protection).

## What's inside

| Path | What it is |
|---|---|
| `.claude/` | Settings + the PreToolUse/PostToolUse/Stop hook suite + `test_hooks.py` (68-case block/allow battery, runs in CI) — [hooks/README.md](.claude/hooks/README.md) |
| `.husky/` + `.secretlintrc.json` | Layer-2 pre-commit: branch block, forbidden paths, worktree-aware secretlint |
| `.github/workflows/ci.yml` | The kit's own CI (battery, JSON/YAML validation, forbidden paths, secretlint, placeholder integrity) |
| `.github/pull_request_template.md` | The concise-PR format (≤ ~150 visible words, depth in `<details>`) |
| `templates/workflows/` | **Inert** app-project workflows — CI, deploy-on-green, backup-cron, keepalive — activated by `git mv` at bootstrap ([templates/README.md](templates/README.md)) |
| `docs/SECURITY.md` | 3-layer model, three-isolated-secret-stores, DB posture, AI cost guardrails, runbooks, keychain norms |
| `docs/TESTING.md` | The pyramid as built: smoke-in-CI vs DB-backed-golden-local-only + the harness blueprint |
| `docs/COLLABORATION.md` | Branch workflow, worktrees, the parallel-session protocol, fast-merger protocol |
| `docs/STACK-RATIONALE.md` | Every stack choice, tagged TRANSFERABLE vs STACK-SPECIFIC |
| `docs/LESSONS.md` | The gotcha catalog — every entry cost a failed run or a deadlock |
| `docs/adr/` | Date+slug ADR convention (no numbers — collision-proof) + the kit's own seed ADR |
| `docs/CLAUDE-template.md` | Fill-in `CLAUDE.md` for the new project (Hard Rules verbatim) |
| `BOOTSTRAP-PROMPT.md` / `PLACEHOLDERS.md` | The adaptation UX + the complete `{{…}}` token inventory |
| `scripts/check_placeholders.py` | CI-enforced: tokens used == tokens documented (`--bootstrapped`: zero remain) |

## Keep the kit living

When any project bootstrapped from this template surfaces a transferable lesson, hook
improvement, or workflow gotcha, **PR it back here** — this repo is the single living
successor to the old per-machine `~/.claude/secure-bootstrap/` kit (and its iCloud
copy), whose two-copy sync ritual is retired. One repo, PR-gated, battery-tested.

## The kit protects itself

This repo runs its own medicine: the hooks guarded the kit's construction from PR #1
(the branch guard forced PR-flow from the first commit), the battery + secretlint run
on every PR, and `main` is protected by the same merge-then-require sequence the docs
teach. See [docs/adr/2026-07-03-kit-shape-and-conventions.md](docs/adr/2026-07-03-kit-shape-and-conventions.md).
