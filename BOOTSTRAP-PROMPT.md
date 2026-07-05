# BOOTSTRAP-PROMPT — turning this template into your project

This file is the kit's UX. You (the human) do three things:

1. On GitHub: **Use this template** → create your new repo → clone it.
2. Open a **fresh Claude Code session** in the clone.
3. Paste everything between the markers below as the **first message**.

The kit's hooks are already live the moment the session opens (`.claude/` ships in the
template — scripts and settings together, so the missing-script deadlock in
docs/LESSONS.md can't happen here). Claude adapts the kit to your stack; you keep the
human-only steps at the end.

---

## The prompt (copy from here)

> **Bootstrap this project from the claude-project-kit template before we write any
> feature code.** This repo was created from a template that ships working security
> hooks, git hooks, CI, workflow templates, and process docs — your job is to adapt it
> to THIS project through an interview with me, not to rebuild it. Never invent secret
> values; anything secret is a human step you hand me explicitly.
>
> **Phase 0 — ground rules (apply all session, every session):**
> - Explain any tool or service I may not know in a few plain sentences (what + why)
>   before adopting it.
> - Flag any security risk and any recurring cost BEFORE incurring it; default to
>   free tiers and defense-in-depth.
> - Concise PRs and commits: 2–3 sentence what/why, one-line bullets, one verification
>   line, depth in `<details>`, ≤ ~150 visible words. Write bodies to a file and use
>   `git commit -F` / `gh pr create --body-file`.
> - After committing on a feature branch, push and open the PR without asking.
> - On a hard environment block: explain the exact fix and HALT — no workarounds.
>
> **Phase 1 — read the kit (in this order, before asking me anything):**
> README.md · docs/SECURITY.md · docs/COLLABORATION.md · docs/TESTING.md ·
> docs/LESSONS.md · docs/STACK-RATIONALE.md · .claude/hooks/README.md ·
> templates/README.md · PLACEHOLDERS.md · docs/CLAUDE-template.md.
> These are distilled from a real production build — treat them as policy, not
> suggestions.
>
> **Phase 2 — interview me.** Batch your questions; recommend defaults from
> docs/STACK-RATIONALE.md (the TRANSFERABLE rows are kit policy; the STACK-SPECIFIC
> rows are my choices). Cover at least:
> 0. Project type: web app / CLI / mobile / library / data pipeline — then skip any
>    stack question below that doesn't apply (a CLI has no frontend or hosting).
> 1. Project name + one-paragraph description + the one product invariant that must
>    always hold.
> 2. Frontend stack (framework, styling, state) — pin exact majors.
> 3. Backend/datastore + hosting (and whether a free tier pauses on inactivity).
> 4. AI features? If yes: provider + the guardrail constants (per-user rate limits,
>    monthly budget cap) from docs/SECURITY.md → "AI cost guardrails".
> 5. Reference-material dir: is there licensed/private reference content? What's its
>    name? (Kit default: `planning/` — gitignored, never published, hook-guarded.)
> 6. My machine specifics for `.claude/settings.local.json` (gitignored): e.g. my Node
>    bin dir if `node` doesn't resolve in your shell — I can get it by running
>    `dirname "$(which node)"` in my own terminal.
> 7. Testing depth: unit + CI smoke are kit policy; confirm whether a DB-backed golden
>    suite applies (it does for any app with a real datastore).
>
> **Phase 3 — propose the adaptation plan, then STOP and show me** before executing:
> the stack table, every file you'll change/delete, every placeholder value, and the
> stage plan (walking skeleton first: repo hygiene → thin end-to-end slice deployed →
> features; local-first, ship-last).
>
> **Phase 4 — execute (on a feature branch; the hooks will insist anyway). Two setup
> facts change *how* you do a couple of these steps:**
> - **`.claude/hooks/*.py` and `.claude/settings.json` are self-protected** — the live
>   hook blocks you from editing them (Edit/Write and shell mutations alike). To change
>   one: write the full new version to a scratch file, then hand ME a terminal command
>   to apply it (e.g. `cp <scratch> .claude/hooks/pre-tool-use.py` — run in my terminal,
>   no hook intercepts it), then verify and `git add`/commit it (staging them IS
>   allowed). `.claude/settings.local.json` is NOT protected — write it directly.
> - **Check Node resolves before any `npm`:** the committed settings ship NO `PATH`
>   override (a machine-specific PATH in a template breaks other machines). If
>   `node`/`npm` don't resolve in your shell (version managers like nvm don't apply
>   non-interactively), write `.claude/settings.local.json` (gitignored, NOT protected)
>   with an `env.PATH` that prepends my Node bin — ask me for
>   `dirname "$(which node)"` from my terminal.
>
> 1. `docs/CLAUDE-template.md` → `CLAUDE.md` (repo root), **overwriting the kit's own
>    CLAUDE.md that ships here** (it describes maintaining the kit; yours describes my
>    project). `CLAUDE.md` is the file Claude Code auto-loads every session, so this is
>    how future sessions learn my project's rules + the live guardrails — fill every
>    `{{…}}` token, keep the Hard Rules and Branch Workflow sections' strength, delete
>    the template comment block and `docs/CLAUDE-template.md` itself.
> 2. `.claude/hooks/pre-tool-use.py` (**self-protected — apply via a terminal command,
>    per above**): replace the fenced STACK-SPECIFIC section for my datastore (keep the
>    shape: local/disposable frictionless, remote/irreplaceable hard-blocked). Compose
>    the full new hook, hand me the `cp` command, and once I've run it: **update
>    `test_hooks.py`** (NOT protected — edit it directly) with block/allow cases for
>    every guard you changed, run the battery green, and commit both together.
> 3. Rename the reference dir if I chose a different name — in `.gitignore`,
>    `.husky/pre-commit`, the hook's `git add` guard, the app CI's forbidden-paths
>    grep, AND CLAUDE.md Hard Rule 1 (every enforcement layer must agree). The hook's
>    guard is self-protected — fold that one edit into step 2's `cp` (one new hook
>    version covers both changes) rather than a second terminal round-trip. Delete
>    the concept only if I said none.
> 4. Activate workflows: `git rm .github/workflows/ci.yml && git mv
>    templates/workflows/ci.yml .github/workflows/ci.yml` — it ships with the
>    hook-battery step built in (`python3 .claude/hooks/test_hooks.py`); the battery
>    must stay in CI. Move `deploy-on-green.yml`, `backup-cron.yml`,
>    `keepalive.yml` the same way; adapt their fenced stack sections; DELETE any that
>    don't apply (no DB → no backup-cron; nothing pauses → no keepalive). Fill
>    `{{EDGE_FUNCTION_NAMES}}`, `{{SMOKE_FUNCTION_NAME}}`, `{{KEEPALIVE_TABLE}}` or
>    remove with their files. Also adapt `templates/scripts/dev-worktree-login.sh`
>    to my backend and `git mv` it to `scripts/` (or delete it if no local
>    backend). Keep every provenance header. Finish with `git rm -r templates/`
>    (its README goes too — activation is one-way).
> 5. `.env.example`: replace the example vars with this project's real public-env
>    contract (placeholder values only). I create `.env.local` myself — you cannot
>    (the hook blocks it; that's the design).
> 6. Runtime pinning: verify/update the shipped `.nvmrc` and `package.json` `engines`
>    for my Node choice. `.claude/settings.json` normally needs NO edit at bootstrap
>    (machine PATH lives in `settings.local.json`, per the Node note above); if my
>    stack DOES need a settings change, it's self-protected — terminal command per
>    above, and NEVER reorder the hooks-scripts-then-settings relationship
>    (docs/LESSONS.md).
> 6b. The Claude-layer extras — decide each with me: keep `.claude/skills/` (`/ship`,
>    `/new-adr` — adapt `/ship`'s allowed-tools if my remote isn't GitHub);
>    `.mcp.json.example` → rename to `.mcp.json` and fill (env-var secrets only) or
>    delete; `.devcontainer/` → keep (swap the base image for my stack) or delete;
>    `templates/workflows/claude.yml` moves with the other workflow templates in step
>    4 — activate only if I want @claude-on-GitHub and will add the
>    `ANTHROPIC_API_KEY` secret (it spends real money), else delete.
> 7. Scaffold the app toolchain if needed (Vite/Next/etc.) — **run generators in a
>    temp dir and merge their output INTO the kit's files by hand; never let a
>    generator overwrite package.json, .gitignore, .husky/, or .claude/**
>    (docs/STACK-RATIONALE.md, "explicit over clever"). Merge kit package.json
>    scripts/devDeps into the scaffold's result; add `lint-staged` if the stack wants
>    it (the pre-commit hook picks it up automatically) — todoclaw's proven config:
>    `"lint-staged": {"*.{ts,tsx}": ["eslint --fix", "prettier --write"],
>    "*.{css,html}": "prettier --write"}`.
> 8. Create `docs/SETUP.md` (prerequisites table w/ version floors + the Node gotcha
>    callout, first-time setup, env-var recipe, command table, testing tiers,
>    local-services table, troubleshooting) and `docs/SERVICES.md` (per service:
>    identity, auth method + scopes, config-as-code paths, which keys live in which of
>    the three stores, exact dashboard click-paths labeled "(you, in dashboards)",
>    date-stamped provisioning record, deferred-hardening list). Create
>    `docs/ARCHITECTURE.md` as the ADR index (docs/adr/README.md has the convention).
>    In `docs/adr/`: keep README.md but delete its kit-specific "Index" section
>    (your index lives in docs/ARCHITECTURE.md) and delete the kit's seed ADR
>    (`2026-07-03-kit-shape-and-conventions.md`) — it documents the kit, not your
>    project.
>    Keep docs/{SECURITY,COLLABORATION,TESTING,LESSONS,STACK-RATIONALE}.md — fix any
>    line my stack choices made stale.
> 9. Delete `BOOTSTRAP-PROMPT.md` and `PLACEHOLDERS.md`, then run
>    `python3 scripts/check_placeholders.py --bootstrapped` — it must report zero
>    `{{…}}` tokens remaining. Keep the script (it guards regressions).
>
> **Phase 5 — self-checks (all must pass before you call it done):**
> - `python3 .claude/hooks/test_hooks.py` → 100% (with my stack's cases).
> - `npm install` → husky live; then prove layer 2 the way git invokes it: stage a
>   fake `ghp_` + 36-char token in a scratch file → commit must BLOCK; commit
>   `.env.example` → must pass; confirm `.claude/audit.log` grew this session.
>   (Non-Node project? Node ≥20 stays a prerequisite just for this commit-guard
>   layer — or swap it: lefthook/pre-commit + gitleaks, and adapt the CI secretlint
>   step to match. The Claude-hook layer needs no Node either way.)
> - `python3 scripts/check_placeholders.py --bootstrapped` → clean.
> - Every JSON/YAML in the repo parses.
> - Open the bootstrap PR (concise body; include the template repo URL + the kit
>   commit SHA this project started from), and show me: the stack table as configured,
>   files deleted, checks output, and the human-only list from Phase 6.
>
> **Phase 6 — hand me the human-only list** (you cannot do these; give exact
> commands/click-paths): create the cloud services; set secrets in ALL THREE stores as
> applicable (local `.env.local`, GitHub Actions secrets/variables, host env — they're
> isolated; docs/SECURITY.md); GitHub → Settings → Security: enable secret scanning +
> push protection + Dependabot; and branch protection AFTER the app CI's first green
> run on main — merge THEN require (docs/LESSONS.md), contexts = the new CI job names.
>
> (end of prompt)

---

## After Claude finishes (you, in dashboards)

1. **Branch protection** — after the new CI has reported on `main`
   (a working example ships at `docs/examples/protection.json`):
   ```bash
   gh api -X PUT repos/<you>/<repo>/branches/main/protection \
     --input docs/examples/protection.json
   # Edit its "contexts" first if your CI job names differ — they must match your
   # app CI's job names, NOT the kit's old "Kit checks".
   ```
2. **GitHub security toggles**: secret scanning, push protection, Dependabot.
3. **Service dashboards**: create projects, set secrets (`supabase secrets set …`,
   host env vars, Actions secrets) — the three stores are isolated; walk all of them.
4. Optional permanent fix for hook Node issues: `nvm alias default 22` (or current).
5. When the walking skeleton deploys, take the on-demand backup and run the restore
   drill once (docs/SECURITY.md runbooks) — before you need it.
