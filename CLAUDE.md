# CLAUDE.md

Guidance for Claude Code when working in **this repository — the claude-project-kit
template itself.** Claude Code auto-loads this file every session, so it's where a
session learns the rules without a human pasting them.

> **Two audiences, two files:**
> - **This `CLAUDE.md`** applies when you're *maintaining the kit* (editing hooks,
>   docs, workflow templates).
> - **`docs/CLAUDE-template.md`** is the fill-in template that *becomes a new
>   project's* `CLAUDE.md` at bootstrap — it replaces this file. If you're in a fresh
>   copy of this template starting a real project, **run `BOOTSTRAP-PROMPT.md` first**;
>   don't follow this file.

---

## What this is

A GitHub **template repo** that ships a Claude Code hook suite, git-level secret
scanning, CI, deploy/backup/keepalive workflow templates, and process docs — distilled
from a production build (see `README.md`). The kit **self-hosts its own hooks**: the
guardrails below are live in this repo and guarded your predecessors' work from PR #1.

**Stack** (the kit is infrastructure, not an app): Python 3 hooks (`.claude/hooks/`),
POSIX `sh` git hooks (`.husky/`), GitHub Actions (`.github/workflows/` + inert
`templates/workflows/`), skills (`.claude/skills/`), a devcontainer, a project-MCP
example (`.mcp.json.example`), Markdown docs, and a tiny `package.json`.

---

## The guardrails you're working under (know these — they're enforced, not advisory)

The PreToolUse hook (`.claude/hooks/pre-tool-use.py`) **blocks** these in real time;
the model cannot bypass them. A block is the system working — **branch/fix and retry,
never work around it.** Full reference: `.claude/hooks/README.md`.

- **Branch guard** — no `Edit`/`Write`/`git commit` on `main`/`master`. Branch first:
  `git checkout -b <type>/<short-kebab-desc>` (type ∈ feat|fix|chore|refactor|docs).
- **Branch-naming guard** — the branch must match `<type>/<short-kebab-desc>`. Rename
  an auto-generated `claude/<codename>` worktree branch before working
  (`git branch -m <type>/<desc>`).
- **Cross-worktree guard** — no `Edit`/`Write` into a *different* worktree than your
  session (it would land silently past the branch guard). Prefer `git -C <dir>` over a
  persisted `cd`.
- **Merged-PR guard** — no `git commit`/`git push` on a branch whose PR already merged
  (the commit would be stranded). Branch fresh off updated `main`.
- **Never-merge guard** — **`gh pr merge` (including `--auto`) is blocked. Merging is
  the human's action only.** Open the PR (`gh pr create`) and stop. Never enable
  auto-merge. (Even *mentioning* `gh pr merge` in a shell command — e.g. a grep
  pattern — trips it; that's expected.)
- **Self-protection** — **you cannot edit the hook scripts (`pre-tool-use.py`,
  `audit.py`, `stop-pr-check.py`) or `.claude/settings.json`.** Edit/Write and Bash
  mutations (`>`, `sed -i`, `cp`/`mv`/`rm`, `git checkout/restore`, inline `-c`/`-e`)
  of them are blocked; reads are fine. **To change one: write the new version to a
  scratch file, validate it, then hand the human a terminal command to apply it**
  (e.g. a small `--write` patch script). This is by design — a guard you can edit is
  theater. Compose + validate *before* the change lands, since the running hook
  forbids editing itself and a syntax error fails closed. (docs/LESSONS.md.)
- **Secrets / destructive ops** — no reading/writing `.env*`/`*.pem`/`*.key`, no
  embedding secret values, no `rm -rf`, no `curl|sh`, no push to `main`, no bare
  `--force`. Stack-specific remote-DB guards are fenced in the hook.

**Stop hook** (`.claude/hooks/stop-pr-check.py`) blocks *ending a turn* on a pushed
branch that has **no PR**, a PR with **failing CI**, or a **DIRTY** (conflict) PR. So:
open the PR, then watch CI to green (`gh pr checks <n> --watch`) before calling a task
done. A DIRTY PR is *not* green — GitHub skips the required CI, so side checks alone can
look passing; rebase, resolve, force-push.

Two non-enforcing complements: `.claude/settings.json` also carries native
`permissions.deny` rules that hard-block reads of secret files independently of the
Python hook (deny wins even under `bypassPermissions`); and
`.claude/hooks/session-start.py` is an *advisory* SessionStart hook that injects repo
orientation at startup — deliberately **not** self-protected, since it informs rather
than blocks.

---

## Conventions

- **Commits:** conventional (`feat:`/`fix:`/`chore:`/`refactor:`/`docs:`). Write
  messages to a file and use `git commit -F` / `gh pr create --body-file` (also dodges
  the hook's prose scanners).
- **PRs:** scannable in under a minute — 2–3 sentence what/why, one-line bullets, one
  verification line, depth in `<details>`, ≤ ~150 visible words. After committing on a
  feature branch, push and open the PR without asking. **Then stop — you never merge.**
- **Docs are right-sized:** fix any doc a change makes stale in the same PR; don't
  expand proactively. New ADR (`docs/adr/YYYY-MM-DD-slug.md`, no numbers) only for a
  decision that changes the kit's shape, a guard, or the security model.
- **Every hook/workflow edit updates the battery + docs in lockstep:** add a
  `test_hooks.py` case for any guard you change, and keep
  `docs/COLLABORATION.md`'s enforcement section + `.claude/hooks/README.md` in sync.
- **On a hard environment block, explain the fix and HALT** — no sandbox-disabling,
  shims, or symlink hacks.

---

## Commands

```bash
npm run test:hooks      # the block/allow battery (must stay green; also runs in CI)
npm run lint:secrets    # secretlint over all tracked files
python3 scripts/check_placeholders.py   # {{…}} tokens used == documented in PLACEHOLDERS.md
npm install             # installs husky + secretlint, wires the pre-commit hook
```

CI (`.github/workflows/ci.yml`, job **Kit checks**) runs the battery, JSON/YAML
validation, the forbidden-paths gate, placeholder integrity, and secretlint on every
PR. `main` is protected (that context required, admins enforced).

---

## Skills

Custom `/`-commands live in `.claude/skills/<name>/SKILL.md` (the current form —
commands were folded into skills in 2026; `.claude/commands/*.md` still works as
legacy).

- **`/ship <summary>`** — the kit's ship ritual: commit (`-F`) → push → PR
  (`--body-file`) → watch CI → **stop** (never merges). You can invoke it, and Claude
  may run it when a task is done — it does nothing Claude can't already do (merging
  stays blocked), so it just packages the routine reliably.
- **`/new-adr <slug>`** — scaffolds a dated ADR + index row.

Before reinventing, note the bundled skills Claude Code already ships: `/code-review`,
`/security-review`, `/debug`, `/run`, `/verify`, `/loop`.

## Cost & memory

- **Delegate cheaply:** fan search/read work to subagents with `model: haiku` (or set
  `CLAUDE_CODE_SUBAGENT_MODEL`); reserve the main model for judgment. Verify model
  IDs/pricing against live docs before asserting (they move faster than any cutoff).
- **Two memory layers, don't conflate them:** `CLAUDE.md` (this file) is *authored*
  rules loaded every session; `~/.claude/projects/<proj>/memory/MEMORY.md` is
  Claude-*discovered* learnings, machine-local and gitignored. Write durable
  conventions here; let auto-memory hold session-to-session findings.

---

## Where the depth lives

`docs/SECURITY.md` (3-layer model, secrets stores, self-protection, runbooks) ·
`docs/COLLABORATION.md` (branch/worktree/parallel-session protocol + the enforcement
list) · `docs/TESTING.md` · `docs/LESSONS.md` (every gotcha, incl. the self-protection
build-before-lock lesson) · `docs/STACK-RATIONALE.md` · `.claude/hooks/README.md` ·
`docs/adr/` (why the kit is shaped as it is).
