# Lessons

The gotcha catalog — every entry cost a failed run, a wedged PR, a deadlock, or a
debugging session during the todoclaw build (2026-06-22 → 2026-07-03) or this kit's own
construction. Format: what bites → the fix. Sources: todoclaw memory files, ADRs,
SERVICES.md, the v1 secure-bootstrap gotcha log, and this repo's build.

---

## Claude Code & hooks

**Hook scripts BEFORE settings wiring — the missing-script deadlock (two variants).**
`settings.json` hook config hot-loads the instant the file is written, and Claude Code
reads python's exit 2 as "block" — which is exactly what python exits when the script
file can't be opened. Result: **every tool call blocks, including the ones that would
fix it.** Variant 1 (todoclaw, 2026-06-23): relative hook path broke once the shell
`cd`'d out of the project root. Variant 2 (this kit, 2026-07-03): `settings.json`
written before the hook script existed. Fixes: always
`python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/…` (absolute, quoted; the var is set in the
hook environment — verified — though not in the Bash tool env), and **create the
scripts first, write settings.json last**. Recovery is human-only: create an exit-0
stub at the expected path, or fix/remove settings.json. Do NOT "harden" with a
`${CLAUDE_PROJECT_DIR:-.}` fallback (reintroduces the relative-path hole) — fail-closed
is correct for a security hook.

**Guards must match operations, not prose (v2 hook lesson).** Inline `-m "drop stale
rows"` commit messages and PR bodies describing `rm -rf` false-positive naive scanners.
The v2 hook strips quoted `-m/--title/--body` payloads before scanning; the remaining
habit — long text via `git commit -F <file>` / `--body-file <file>` — sidesteps the
issue entirely and is the kit norm. Note the stack-specific DB guards deliberately
match on *mention* (wider gap) — better to block a commit message than risk a prod wipe.

**On a hard environment block: explain the fix and HALT.** No sandbox-disabling, shim
files, symlink hacks, or workaround chains — a deadlock is a signal to surface, not
engineer around. (Standing rule born from the variant-1 deadlock above; encoded in
CLAUDE-template.md Hard Rule 6.)

**secretlint is format-strict; the hook regexes are the belt-and-suspenders.** The
Anthropic rule wants a realistic ~108-char key, so short fakes pass secretlint — while
real `ghp_…` PATs, private-key blocks, and DB URLs trip it. Use both layers; rely on
neither alone. Corollary for docs: `postgresql://user:password@host` trips secretlint
even with placeholder passwords — write the `user@host` form (omit `:password`).

**The `.env.example` allow-list trap.** A naive `\.env([^e]|$)` pattern still matches
`.env.example`. The proven pair:
`grep -E '(^|/)\.env' | grep -v -E '(^|/)\.env\.example$'` — and the exception must be
re-verified in **all three layers** whenever a pattern changes.

**Per-command regex scoping.** A Bash-guard gap of `[^#\n]*` spans `;`/`&`/`|`, so
`cat foo; grep x .env` wrongly blocks. Use `[^#\n;&|]*` — real reads still block.

**Test the hook the way the harness invokes it.** todoclaw's first pre-commit "looked
fine" and was silently broken. Hence the permanent battery (`test_hooks.py`, in CI) and
the ritual: stage a fake `ghp_…` token (must block), commit `.env.example` (must pass),
confirm the audit log grew.

## Git, GitHub & CI

**Merge THEN require (branch protection ordering).** A job's `name:` IS its
required-status-check context, and GitHub accepts a context that has never reported —
at which point **every open PR wedges** ("Expected — waiting for status"). Add CI jobs
in one step; flip them to required only after they've reported on main. Adding without
dropping existing: `gh api -X POST …/protection/required_status_checks/contexts -f 'contexts[]=Lint'`.

**`workflow_run` uses the workflow file on the DEFAULT branch.** Edits to a
`workflow_run`-triggered workflow take effect only after merging to main; you cannot
exercise that path from a feature branch. Test via `workflow_dispatch` (gated to main).
Also: `branches: [main]` filters on the *triggering run's* head_branch, and downstream
jobs must check out `workflow_run.head_sha` — `github.sha` is main's tip, not
necessarily what CI validated.

**Scheduled workflows also only run from the default branch** — merge first, then
`workflow_dispatch` once manually to verify.

**core.hooksPath is absolute → worktrees run the MAIN checkout's pre-commit.** Husky
points `core.hooksPath` at the main checkout's `.husky/_`; every worktree commit runs
the MAIN checkout's hook file with CWD = the worktree. Consequences: a hook fix merged
to main does nothing until the main checkout **pulls it**; hook tooling must resolve
binaries via `git rev-parse --git-common-dir` (its parent = main checkout), never a
literal `node_modules/.bin/...` path (that bug misreported as "found a potential
secret" and pushed people toward `--no-verify`). If a worktree commit is blocked by
hook tooling: run the exact scan manually with the main checkout's binary, confirm exit
0, THEN `--no-verify` — perform the check, don't skip it; CI re-runs it regardless.

**Husky resets `core.hooksPath` on every `npm install`** (the `prepare` script). Don't
fight it or repoint it; make the hook itself robust.

**Verify a PR merged before any follow-up.** Fast-merging owners mean the branch you
just pushed is probably already merged; a follow-up commit onto it is stranded. Check
`gh pr view <n> --json state` / `git fetch --prune` first; branch fresh from main.
Squash-merge caveat: stacked changes collapse into one commit — verify by checking
files in `origin/main`, not the log.

**Stage files explicitly — never `git add -A` / `git add .`.** Generated files appear
where you don't expect (todoclaw: a bogus root `/deno.lock` from running `deno` at the
repo root). Guard known generated paths in `.gitignore` AND stage by explicit path.

**ADR numbering collides under parallel sessions.** Numbers are claimed at
merge-to-main; two parallel branches drafted 0019/0020/0022/0023 collisions three times
in one build (one PR renumbered twice). Structural fix: one file per decision,
`YYYY-MM-DD-slug.md`, **no number** (docs/adr/README.md). Same medicine for any
append-only log with a shared counter or common tail.

**GitHub runners are detached-HEAD.** Anything branch-dependent (like the hook's branch
guard) must be tested in a sandbox repo pinned to a named branch — the kit's battery
does exactly this.

## Environment & tooling

**nvm doesn't apply in non-interactive shells.** Claude Code's shell (and git hooks)
get the nvm *default* Node — todoclaw's default was v16; Vite 8 and secretlint need
≥20. Fixes, layered: `.nvmrc` in-repo; export the absolute path when scripting
(`export PATH="$HOME/.nvm/versions/node/v22.23.0/bin:$PATH"`); settings.json `env.PATH`
with the resolved bin dir; the pre-commit hook self-heals by globbing
`~/.nvm/versions/node/v{24,22,20}*`; permanent fix `nvm alias default 22`.

**Git runs hooks under `sh -e`.** Any unguarded non-zero exit aborts the hook: guard
no-match greps with `|| true`; capture tool exits with `if ! cmd; then`.

**`.env.local` does not enter worktrees** — and hooks (correctly) block Claude from
copying it. Prefer tooling that resolves env **at runtime from the running stack**
(todoclaw's golden suite shells out to `supabase status -o env` — zero env files
needed); otherwise copying is a human step.

**macOS keychain re-prompts after `brew upgrade`.** Keychain ACLs are per binary
signature; an upgraded CLI is a "new" binary and asks again. Expected — not an attack.
General norms: know which command triggered a prompt (announce keychain-reading
commands before running), "Always Allow" only for tools you trust, never type your
password into a dialog you can't attribute.

## Supabase & backups from CI (the pooler saga)

**The direct DB host is IPv6-only; GitHub runners are IPv4-only.**
`db.<ref>.supabase.co:5432` → `pg_dump: … Network unreachable`. Use the **session
pooler**: `aws-<N>-<region>.pooler.supabase.com:5432` (session, NOT transaction 6543 —
transaction mode can't run pg_dump).

**The free pooler only accepts the built-in `postgres` user.** A custom least-privilege
role fails with `FATAL (ENOTFOUND) tenant/user <role>.<ref> not found` even though it
exists — Supavisor doesn't route custom roles. Least-privilege backup needs the paid
IPv4 add-on or a self-hosted runner; until then: dump as `postgres`, scope
`--schema=public`, treat the secret as full-DB access, record the accepted tradeoff +
upgrade path.

**The `aws-<N>-` prefix is project-specific.** Wrong instance → "tenant/user not
found"; wrong host → "Name does not resolve". Copy from dashboard → Connect → Session
pooler; narrow blind with `dig +short aws-{0,1,2}-<region>.pooler.supabase.com`.

**More from the same saga:** `pg_dump` must be ≥ the server major — run it from a
pinned `postgres:17-alpine` image, not the runner's client. Append `?sslmode=require`.
The DB password is shown **once** at creation (reset at Settings → Database if lost —
the app uses the anon key, not the DB password). New key format: `sb_publishable_…` /
`sb_secret_…` replace the `eyJ…` anon/service-role JWTs (publishable is still
public/RLS-gated). `supabase db push` has no `--yes` and can hang on a `[Y/n]` prompt
in CI (supabase/cli#2238) — pipe `yes |`. Gateway `verify_jwt` 401s the CORS OPTIONS
preflight before your function runs — set `verify_jwt = false` and verify the JWT
in-function. Local `supabase functions serve` injects a permissive
`Access-Control-Allow-Origin: *` at the gateway, so the origin-lock can only be
verified against the **deployed** function. A `SECURITY DEFINER` trigger on
`auth.users` that errors breaks signup entirely with an opaque error — create default
rows via app-side upsert instead.

## Cost & billing

**Map billing by failure mode, not vibes.** Todoclaw's 3-provider sweep: **Supabase
Free and Vercel Hobby cannot charge you** — they pause/read-only the resource at a
limit, never invoice. Only Anthropic bills per use — so external alerting targets only
the provider that can actually invoice, and the in-app budget kill-switch is the
authoritative bound (a console hard-cap below it is pointless).

**Anthropic $-threshold notifications attach only to a NAMED workspace** — a key in
the Default Workspace can't get them without minting a new key in a dedicated
workspace (keys can't move). Decide deliberately; the in-app kill-switch makes alerts
insurance, not load-bearing.

**Model IDs and pricing move faster than knowledge cutoffs.** Verify both against live
provider docs before hard-coding cost math; bias arithmetic in the conservative
direction (over-count spend so a kill-switch trips early, never late — todoclaw's
formula deliberately over-counted during an intro-pricing window, no dated code to
revert).

## Process

**Docs are scaffolding first, then right-sized.** Heavy ADR/docs discipline exists to
let cold sessions reconstruct a system under construction; once built and stable, new
ADRs only for architecture/security-boundary/external-service changes, and doc updates
only when a change makes one stale. The dial-down is an explicit, dated decision — not
drift.

**Three isolated secret stores** (local env / CI secrets / host env) — setting one does
nothing for the others; walk the list on every rotation. Full model: docs/SECURITY.md.

**Deferrals are managed loops, not shrugs.** Record a revisit trigger with every
deferral and write the dated re-decision when it fires (todoclaw deferred Realtime
twice, on schedule, with reasons).
