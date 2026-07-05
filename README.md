# claude-project-kit

[![Kit checks](https://github.com/braedensc/claude-project-kit/actions/workflows/ci.yml/badge.svg)](https://github.com/braedensc/claude-project-kit/actions/workflows/ci.yml)

**Claude Code is at its best with permission prompts off — and that's dangerous.**
This template makes it safe: a GitHub template repo that starts your project with a
tested hook suite that hard-blocks the failure modes (secret reads, pushes to `main`,
self-merged PRs, edits to the guards themselves), git-level secret scanning, CI gates,
ready-to-adapt deploy/backup workflows, and the process docs that keep multi-session
agentic development coherent.

Everything here is **distilled, not invented** — generalized from
[todoclaw](https://github.com/braedensc/todoclaw), a production app built with Claude
Code in a 10-day staged build, plus its retrospective. Every generalized file carries
a provenance header saying where it came from and what was verified in production.
(Headers cite todoclaw-internal artifacts — `ADR-00xx`, PR numbers, `SERVICES.md` —
as provenance breadcrumbs; they aren't links into *this* repo.) MIT licensed.

## Prerequisites

| Tool | Version | Why |
|---|---|---|
| macOS or Linux | POSIX shell | Hooks are `python3` + `sh`. **Windows: not supported natively — use WSL2** |
| git + [GitHub CLI](https://cli.github.com) (`gh`), authenticated | recent | The workflow guards (merged-PR, Stop-hook PR checks) query GitHub via `gh` and fail open without it |
| Python 3 | 3.9+ on `PATH` as `python3` | Every hook + the self-check scripts |
| Node.js | ≥ 20 (see `.nvmrc`) + npm | husky + secretlint (layer 2), regardless of your app's stack |

## Quickstart

1. **Use this template** (button above) → create your repo → clone it.
   (CLI: `gh repo create <name> --template braedensc/claude-project-kit --clone`)
2. Open a fresh **Claude Code** session in the clone — the hooks are already active.
3. Paste the prompt from **[BOOTSTRAP-PROMPT.md](BOOTSTRAP-PROMPT.md)**. It interviews
   you for stack choices, adapts every stack-specific file, fills every placeholder
   ([PLACEHOLDERS.md](PLACEHOLDERS.md)), deletes what doesn't apply, and runs the
   kit's self-checks. You keep the human-only steps (secrets, dashboards, branch
   protection).

**What you'll see on first open** (all expected — none of it is broken):

- A one-time **workspace trust** dialog, and a **Bypass Permissions warning** —
  the kit ships `defaultMode: bypassPermissions` *because* the hooks hard-block the
  dangerous operations in every mode. Uncomfortable anyway? Flip it to `acceptEdits`
  in `.claude/settings.local.json` — every guard still enforces.
- A short **orientation line** injected at session start (branch, PR, dirty tree) —
  that's the SessionStart hook.
- Occasional **hook blocks** (e.g. editing on `main`) — that's the system working;
  branch and retry, exactly as the block message says.
- A growing `.claude/audit.log` (gitignored) — the local record of every command.

## What's inside

| Path | What it is |
|---|---|
| `.claude/` | Settings (hooks wiring + native `permissions.deny`) + the PreToolUse/PostToolUse/Stop hook suite + advisory SessionStart hook + `test_hooks.py` (the block/allow battery, runs in CI) — [hooks/README.md](.claude/hooks/README.md) |
| `.claude/skills/` | `/ship` (commit → push → PR → watch CI → stop) and `/new-adr` custom slash-commands |
| `.husky/` + `.secretlintrc.json` | Layer-2 pre-commit: branch block, forbidden paths, worktree-aware secretlint |
| `.github/workflows/ci.yml` | The kit's own CI (battery, JSON/YAML validation, forbidden paths, secretlint, placeholder integrity) |
| `.github/pull_request_template.md` | The concise-PR format (≤ ~150 visible words, depth in `<details>`) |
| `templates/workflows/` | **Inert** app-project workflows — CI, deploy-on-green, backup-cron, keepalive, `@claude` Action — activated by `git mv` at bootstrap ([templates/README.md](templates/README.md)) |
| `.devcontainer/` | Minimal Claude-Code devcontainer + hardening/firewall notes for sandboxed autonomous runs |
| `.mcp.json.example` | Project-scoped MCP config example (`${VAR}` secrets only) |
| `docs/SECURITY.md` | The layered security model, secrets stores, runbooks |
| `docs/TESTING.md` | The pyramid as built: smoke-in-CI vs DB-backed-golden-local-only + the harness blueprint |
| `docs/COLLABORATION.md` | Branch workflow, worktrees, the parallel-session protocol |
| `docs/STACK-RATIONALE.md` | Every stack choice, tagged TRANSFERABLE vs STACK-SPECIFIC |
| `docs/LESSONS.md` | The gotcha catalog — every entry cost a failed run or a deadlock |
| `docs/adr/` | Date+slug ADR convention (no numbers — collision-proof) + the kit's own ADRs |
| `CLAUDE.md` | The kit's own auto-loaded context (guardrails + conventions) — a worked example; bootstrap replaces it with your project's |
| `docs/CLAUDE-template.md` | Fill-in `CLAUDE.md` for the new project (Hard Rules verbatim) |
| `BOOTSTRAP-PROMPT.md` / `PLACEHOLDERS.md` | The adaptation UX + the complete `{{…}}` token inventory |
| `scripts/check_placeholders.py` | CI-enforced: tokens used == tokens documented (`--bootstrapped`: zero remain) |

## How stack-specific is it?

The worked example is a JS full-stack app, but the core is stack-agnostic — **one repo
serves any workflow** (web, CLI, mobile, data); bootstrap adapts or deletes the
coupled parts rather than you forking variants:

| Layer | Coupling |
|---|---|
| `.claude/` hooks, skills, settings, battery | **Universal** (python3 + git + gh; the Supabase DB guards are a clearly-fenced example section) |
| docs: COLLABORATION, LESSONS, SECURITY, adr convention | **Universal** |
| `.husky/` + secretlint (+ `package.json`) | Node-based tooling — kept even in non-Node projects (it only guards commits), or swap to lefthook/pre-commit + gitleaks |
| `templates/workflows/*` | Worked examples (npm / Supabase / Vercel) — **adapt or delete at bootstrap**; the gating skeletons transfer |
| docs: TESTING, STACK-RATIONALE | Flavored examples of universal patterns — keep the shape, swap the tools |

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
- **Guards match operations, not prose — and get tested.** Every guard is verified by
  a 90+-case block/allow battery that runs in CI forever; edit a hook, add a case.

## Keep the kit living

Bootstrapped a project and learned something transferable — a guard, a gotcha, a
better template? **PRs welcome** ([CONTRIBUTING.md](CONTRIBUTING.md)). `main` is the
only supported version; bootstrap records the kit commit you started from.

## The kit protects itself

This repo runs its own medicine: the hooks guarded the kit's construction from PR #1
(the branch guard forced PR-flow from the first commit), the battery + secretlint run
on every PR, `main` is protected by the same merge-then-require sequence the docs
teach — and the hooks are self-protected, so not even the agent that built them can
edit them. See [docs/adr/](docs/adr/).
