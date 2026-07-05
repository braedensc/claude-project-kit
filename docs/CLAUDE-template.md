<!-- ═══════════════════════════════════════════════════════════════════════════
     CLAUDE.md TEMPLATE — copy to the project root as CLAUDE.md at bootstrap,
     fill every {{…}} token, delete sections that don't apply, then DELETE
     THIS COMMENT BLOCK. Structure distilled from todoclaw's CLAUDE.md (the
     coordination document for every session of a full production build).
     The **Hard Rules** and **Branch Workflow** sections are verbatim policy —
     adapt names, don't weaken them.
     ═══════════════════════════════════════════════════════════════════════════ -->

# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

---

## What This Is

{{PROJECT_ONE_PARAGRAPH}} <!-- what the app is, who it's for, the one-line invariant
that must always hold (todoclaw's was "fully usable without AI") -->

**Reference material** (under `planning/`, gitignored — read it to port logic, never
commit it): {{REFERENCE_MATERIAL_LIST_OR_DELETE}}

---

## Stack

| Layer | Technology |
|---|---|
| Frontend | {{FRONTEND_STACK}} |
| Server state | {{SERVER_STATE_LIB}} |
| UI state | {{UI_STATE_APPROACH}} |
| Backend | {{BACKEND_PLATFORM}} |
| Hosting | {{HOSTING}} |
| AI (if any) | {{AI_PROVIDER}} — server-side only, never called from the frontend |
| Testing | {{TEST_STACK}} |

<!-- Record taste constraints here too (e.g. "No Redux, ever — lightweight state only").
     See docs/STACK-RATIONALE.md in the kit for the reasoning behind each default. -->

---

## Commands

<!-- Keep this section near the TOP — Anthropic calls exact build/test/lint/run
     invocations "the highest-ROI section by a wide margin"; Claude gets them wrong
     most often without them. Don't restate what a formatter enforces deterministically.
     npm is the worked example — swap every invocation for your runner (pnpm, cargo,
     uv, gradle, …). -->

> Keep this real: every command here must work today. Note the Node/toolchain gate
> (e.g. "run `nvm use` first — this shell defaults to an older Node").

```bash
# Dev
npm run dev            # {{DEV_URL}}
{{LOCAL_BACKEND_START}} # e.g. supabase start (Docker)

# Quality
npm run lint
npm run format:check   # check only — what CI runs
npm run typecheck

# Test
npm test               # unit + component
npm run test:e2e       # CI-safe smoke
{{GOLDEN_SUITE_CMD}}    # DB-backed golden suite — LOCAL ONLY, exclusive resource

# DB (if applicable)
{{MIGRATION_COMMANDS}}
```

**Skills** (`.claude/skills/<name>/SKILL.md`, invoked `/name`): the kit ships `/ship`
(commit → push → PR → watch CI → stop) and `/new-adr`. Add project-specific ones for
repeatable rituals. Reserve `disable-model-invocation: true` (user-only) for the
genuinely *irreversible or expensive* — a real deploy, anything that spends money;
routine git rituals like `/ship` are fine for Claude to run, since the hooks still
bound them. Bundled skills already exist (`/code-review`, `/security-review`, `/debug`,
`/run`, `/verify`) — don't reinvent them.

**Cost & memory:** delegate search/read to `model: haiku` subagents; keep the main
model for judgment. `CLAUDE.md` is *authored* rules (loaded every session); the
machine-local `MEMORY.md` auto-memory is Claude-*discovered* learnings — don't conflate.

---

## Architecture

```
{{DIRECTORY_TREE_WITH_ONE_LINE_PER_ENTRY}}
```

<!-- One folder per feature/system, each with its own README.md, is the single biggest
     parallel-work enabler (docs/COLLABORATION.md). -->

---

## Conventions

**Types:** strict mode. Schema validation (e.g. Zod) at every boundary; the inferred
type IS the type — one source of truth.

**Files:** small and focused. Logic lives in pure lib modules; components stay
presentational. Three similar files beat a premature abstraction.

**Naming:** {{NAMING_CONVENTIONS}} <!-- todoclaw: kebab-case filenames, PascalCase
components, camelCase everything else -->

**Commits:** conventional commits — `feat:`, `fix:`, `chore:`, `refactor:`, `docs:`.
No direct commits to `main`; all work via feature branches + PR. CI must pass before
merge. Write messages to a file and use `git commit -F` / `gh pr create --body-file`.

**PRs:** bodies scannable in under a minute — 2–3 plain sentences of what/why, one-line
bullets for changes, one verification line. Everything deeper goes in a `<details>`
block, never the visible body. Target ≤ ~150 visible words. After committing on a
feature branch, push and open the PR without asking.

**Working agreements (every session):** explain any tool or service the owner may not
know in a few plain sentences (what + why) before adopting it. Flag any security risk
and any recurring cost BEFORE incurring it; default to free tiers. When a project
surfaces a transferable lesson or security improvement, PR it back to the template
repo this project was bootstrapped from (its URL + commit are recorded in the
bootstrap PR).

**Docs lifecycle (docs-as-scaffolding, then right-sized):**
- **Bootstrap phase:** heavy discipline on purpose — an ADR per significant PR,
  SETUP/SERVICES kept current, co-located READMEs. The docs let cold sessions (and
  future-you) reconstruct the system while it's being built.
- **Post-launch:** dial it down — a new ADR only for a decision that changes
  architecture, a security boundary, or an external service. Fix any doc a change makes
  **stale** in the same PR, but don't expand docs proactively.

---

## Branch Workflow (do this automatically — never wait to be told)

Full workflow, worktrees, and team conventions: `docs/COLLABORATION.md`.

**At the start of any new task — before your first `Edit`/`Write` — check the branch
and create one if needed:**

```bash
git rev-parse --abbrev-ref HEAD          # what branch am I on?
git checkout main && git pull --ff-only  # start from latest (skip if offline/no remote)
git checkout -b <type>/<short-kebab-desc>
```

- **Branch name:** `<type>/<short-kebab-desc>`, type ∈ `feat | fix | chore | refactor | docs`.
  A hook *enforces* this — rename an auto-generated `claude/<codename>` worktree branch
  before working (`git branch -m <type>/<desc>`).
- **One task = one branch = one PR.** Small and short-lived.
- **The hooks enforce this:** `Edit`/`Write`/`git commit` are *blocked* on `main` or a
  mis-named branch; writes into a *different worktree* are blocked; so are
  `git commit`/`git push` on a branch whose PR already **merged**, and `gh pr merge` in
  any form. A block isn't a bug — branch fresh (or fix the path) and retry; never work
  around it.
- **Ordered/generated files are serialized** (migrations etc.): pull latest main
  immediately before generating one; never two generating branches in parallel.
- **Open a PR when the task is done** (`gh pr create`) — and stop there. **Merging is
  the human's action only: never run `gh pr merge` (including `--auto`), never enable
  auto-merge.** Opening the PR is the end of Claude's involvement.
- **Watch CI to green before considering the task done:** `gh pr checks <n> --watch`.
  On a red check: read the failing job's log, fix, push, re-watch — never hand back a
  red PR. Local checks passing is necessary, not sufficient; the PR's CI status is
  the source of truth. **A `DIRTY` (conflicted) PR is not green** — GitHub skips the
  required CI entirely, so only side checks report; rebase, resolve, force-push. (A
  Stop hook also blocks ending a turn with no PR, a failing check, or a DIRTY PR.)
- **Before a follow-up commit to an open PR, confirm it's still open**
  (`gh pr view <n> --json state`) — a commit pushed to a merged PR's branch is
  silently orphaned. If merged, branch fresh off updated `main`. (Also enforced by
  the merged-PR hook guard.)

---

## Hard Rules

These apply every session without exception:

1. **`planning/` is reference, never published.** It is gitignored. Never stage,
   commit, or push anything from it. Reading it to port logic is expected; copying its
   files is not. <!-- delete if the project has no reference dir -->

2. **Secrets are never output.** Never echo, log, comment, or paste the value of any
   API key, token, password, or private key. Reference by name only
   (e.g. `process.env.{{EXAMPLE_KEY_NAME}}`).

3. **No secret values in code.** If a secret value appears in any file about to be
   committed, block and flag it. Only `.env.example` (placeholder values) is committed.

4. **Admin/server keys are server-only.** {{ADMIN_KEY_NAME}} has admin access. It must
   never appear in any frontend file or client bundle.

5. **No direct or force push to `main`.** All changes via PR. CI is the unbypassable
   gate.

6. **On a hard environment block, explain the fix and HALT.** If every path is blocked
   (broken hook, permission wall, dead auth), surface what's wrong + the exact fix
   command and wait — no sandbox-disabling, shim files, symlink hacks, or other clever
   workarounds. A deadlock is a signal, not a challenge.

{{PROJECT_INVARIANT_RULE_OR_DELETE}} <!-- e.g. todoclaw rule 6: "the entire planner
works without AI — AI is additive, never required." Give your project's one
non-negotiable product invariant a numbered slot here. -->

---

## Security Model (three independent layers)

1. **Claude Code hooks** (`PreToolUse`) — guard real-time tool calls; the model cannot
   bypass these, and cannot edit them: the hook scripts + `settings.json` are
   self-protected (human-only — changes come as a terminal command you run).
2. **Git pre-commit hooks** (Husky + secretlint) — guard commit contents locally
   (bypassable with `--no-verify`, caught by CI).
3. **CI + branch protection** — the unbypassable gate on every PR.

Full model, secrets stores, runbooks: `docs/SECURITY.md`.
{{DATA_LAYER_LINE}} <!-- e.g. "At the database layer: RLS on every table
(user_id = auth.uid()). No raw SQL from the app; input validated with Zod at every
boundary." -->

---

## Key Design Decisions

<!-- Once real decisions exist, list the living, load-bearing ones here (formulas,
     thresholds, invariants another session must not re-derive differently) and link
     the full log: -->

Full decision log with rationale: `docs/ARCHITECTURE.md` (index) + `docs/adr/`
(one file per decision — see docs/adr/README.md for the convention).
