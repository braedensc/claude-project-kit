# Claude Code Hooks

Project-scoped hooks configured in `.claude/settings.json`. They guard Claude's real-time tool calls before execution — unlike git pre-commit hooks, **the model cannot bypass them** (no `--no-verify` equivalent). This is why `settings.json` can ship `permissions.defaultMode: bypassPermissions`: the bypass is *earned* by these hard blocks running in every mode.

Distilled from todoclaw's hook suite — in production 2026-06-23 → 2026-07-03; v2 hardening (prose-stripping, branch-scoped push guard) verified by an 18-case battery in todoclaw's post-launch retrospective PR, expanded here into the permanent `test_hooks.py`.

> **Bootstrap order warning:** hook wiring hot-loads the instant `settings.json` is written, and a missing hook script **blocks every tool call** (fail-closed). Create the scripts in `.claude/hooks/` *first*, write `settings.json` *last*. A missing python3 *interpreter* is also fail-closed — the PreToolUse wiring wraps the command in `command -v python3 … || exit 2`, so a machine without python3 blocks everything instead of silently running unguarded.

---

## PreToolUse — `pre-tool-use.py`

Runs before every tool call. Exit 2 = block with reason. Exit 0 = allow.

### Universal guards (keep in every project)

| What it blocks | Tool | Why |
|---|---|---|
| **Editing the hooks themselves** — `pre-tool-use.py`, `audit.py`, `stop-pr-check.py`, `.claude/settings.json` | Edit/Write/NotebookEdit, **and** Bash mutations (`>`, `tee`, `sed -i`, `cp`/`mv`/`rm`, `git checkout/restore`, inline `-c`/`-e` interpreters) | Otherwise every block below is theater — Claude could just rewrite the guard away or unwire it in settings. These files are **human-only**: to change one, Claude prints a terminal command for the human to run. Reads (`cat`/`grep`/Read) are allowed. A first line, not a sandbox — the real backstop is still git + review |
| Edit/Write while on `main`/`master` | Edit/Write | Forces the feature-branch workflow automatically (`docs/COLLABORATION.md`) — keeps `main` clean for collaborators |
| `git commit` while on `main`/`master` | Bash | Same — no direct commits to `main` |
| Edit/Write/`git commit` on a branch not matching `<type>/<slug>` | Edit/Write, Bash | Forces a rename before work, so an auto-generated `claude/<codename>` worktree branch never lands unrenamed in a PR |
| Edit/Write into a **different worktree** | Edit/Write | Target's owning worktree (via `git worktree list`) ≠ this session's. A write to another checkout (classically the main checkout, reached via a persisted `cd`) skips every branch guard and lands there **silently** — tests here still pass against the unmodified files. The block prints the corrected in-worktree path; fails open; same-worktree writes and paths outside the repo (scratchpad, `~/.claude`, `/tmp`) are unaffected |
| `rm -rf` / `rm --recursive` | Bash | Accidental mass deletion |
| `curl/wget \| sh` | Bash | Supply-chain attack vector |
| `git add planning/` | Bash | `planning/` is gitignored reference material; staging it would publish it |
| `git add .env*` (non-example) | Bash | Secrets leak via git |
| Any push naming `main`/`master` | Bash | Bypasses PR + CI gate |
| Bare `--force`/`-f` push (any branch) | Bash | Can clobber unseen remote commits; `--force-with-lease` is allowed on feature branches |
| `git commit`/`git push` on a branch whose PR is **MERGED** | Bash | Pushes there are silently stranded (GitHub stops syncing the head + running CI); fails open if `gh`/network can't verify |
| `gh pr merge` in any form except `--disable-auto` | Bash | **Merging is the human's action only** — Claude opens PRs and stops; `--auto` still means the agent caused the merge |
| Reading `.env*`, `*.pem`, `*.key` via shell | Bash | Secrets entering Claude's context |
| Reading `.env*` (non-example), `*.pem`, `*.key` | Read | Same |
| Writing to `.env*` (non-example) | Edit/Write | Only `.env.example` is committed |
| Embedding secret values | Edit/Write | Regex patterns for Anthropic keys, DB URLs with passwords, private key blocks, AWS keys, GitHub tokens, raw JWTs |

### Stack-specific guards (Supabase/Postgres — replace for your datastore)

| What it blocks | Why |
|---|---|
| `supabase db reset --linked` / `--db-url` | Wipes a **production** database — only the local (Docker) reset is allowed |
| `supabase projects delete` | Irreversible deletion of a hosted project |
| Remote `DROP`/`TRUNCATE`/`DELETE` SQL | Destructive SQL against a non-localhost `postgres://…@host`; run it only on the local DB via migrations |

Keep the *shape* when you swap datastores: local/disposable stays frictionless, remote/irreplaceable gets hard blocks.

> **v2 — guards match operations, not prose.** Quoted payloads of `-m/--message/--title/--body/-t/-b` are stripped before the danger patterns run, so `git commit -m "drop stale rows"` or a PR body *describing* `rm -rf` no longer false-positives. Message text is inert prose — never executed — so stripping loses no protection. `git commit -F <file>` / `--body-file` remain the norm for long text.

> Bash command-matching is scoped per shell command: the pattern gap excludes `;`, `&`, `|`, so a `.env` mentioned in a *later* command on the same line isn't a false positive — the real read (`cat .env`) still blocks.

---

## Stop — `stop-pr-check.py`

Runs when Claude tries to end a turn on a **pushed** branch ahead of the mainline;
blocks (once) with a reminder when any of these hold, so "open a PR and watch CI to
green" isn't just a written rule that gets skipped across parallel sessions:

| Blocks ending the turn when | Why |
|---|---|
| The branch has **no PR** yet | The workflow expects a PR once a task is done (`gh pr create`) |
| The open PR has **failing CI** (`FAILURE`/`CANCELLED`/`TIMED_OUT`/…) | CI must be watched to green before a task is "done" |
| The open PR is **`DIRTY`** (merge conflicts) | GitHub can't build the merge ref, so the required `pull_request` CI **never runs** — only side checks (CodeQL/Vercel) report and can look green. A conflicted PR must be rebased, not mistaken for passing (2026-07-03 near-miss). Fires only on explicit `DIRTY`, never the transient `UNKNOWN` right after a push |

"Ahead of the mainline" is measured against **`origin/main`** (the remote-tracking
base), not local `main` — in PR flow you branch off `origin/main` and rarely update
local `main`, so it lags; comparing against it would false-nag a zero-commit branch
(2026-07-04 fix). Dedups per (branch, reason, HEAD sha) in `.claude/.stop-pr-nag/`
(gitignored) so explaining instead of acting can't trap the session in a loop; fails
open when `git`/`gh`/network is unavailable. Ported from todoclaw PRs #59 + #77
(2026-07-03).

---

## SessionStart — `session-start.py`

**Advisory, not a guard.** At session start it injects a short orientation string
(current branch, dirty-tree status, this branch's open PR, and a one-line reminder of
the workflow) via the `hookSpecificOutput.additionalContext` output contract — so a
fresh session opens already knowing where it is. Always exits 0 (SessionStart has no
block semantics) and fails open silently. **Deliberately NOT in the self-protected set**
below: it informs rather than blocks, so there's no guard for the agent to edit away.

---

## PostToolUse — `audit.py`

Appends a one-line timestamped record of every `Bash`/`Edit`/`Write` call to `.claude/audit.log` (gitignored — local only). Review it to see what Claude did in a session, especially before a commit.

```
2026-06-22T14:03:11Z [Bash] npm install husky --save-dev
2026-06-22T14:03:15Z [Write] /Users/.../repo/.gitignore — write
```

---

## The battery — `test_hooks.py`

```
python3 .claude/hooks/test_hooks.py
```

92 block/allow cases covering every guard above — the self-protection cases (edit/mutate a hook → block; read/py_compile/stage → allow, incl. that a redirect must *target* a protected path so `... hook 2>&1` isn't a false positive), the v2 prose-stripping allows, sandboxed branch-guard/branch-naming cases (throwaway git repos pinned to `main` / `master` / a codename / a feature branch, so results don't depend on this repo's current branch or CI's detached HEAD), a real sibling-worktree sandbox for the cross-worktree guard, merged-PR and never-merge guard cases against a **mocked `gh`** (no network), and Stop-hook cases including the DIRTY-PR block (its exit-0 + JSON-decision protocol, plus the dedup that prevents nag loops). Runs in CI on every PR; also available as `npm run test:hooks`
(and the repo-wide secret scan as `npm run lint:secrets`). **If you edit a hook, add a
case — and keep docs/COLLABORATION.md's "What's automatic (enforcement)" section in
sync.**

---

## Defense in depth

(`.secretlintrc.json` — layer 2's config — is copied byte-for-byte from todoclaw's, in
production 2026-06-23 → 2026-07-03; strict JSON can't carry its own provenance header,
so it lives here.)

These hooks are **layer 1** of three (full model: `docs/SECURITY.md`):
1. **Claude Code hooks** (this) — guard Claude's actions in real time; the model cannot bypass.
2. **Git pre-commit hooks** (Husky + secretlint) — guard commit contents locally; bypassable via `--no-verify`.
3. **CI + branch protection** — the unbypassable server-side gate on every PR.
