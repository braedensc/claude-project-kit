# Lessons

The gotcha catalog — every entry cost a failed run, a wedged PR, a deadlock, or a
debugging session during the production build this kit came from (2026-06-22 →
2026-07-03), this kit's own construction, or the first ticket a kit-derived project
ran end to end through the delivery pipeline (2026-08-25). Format: what bites → the
fix. Sources: that build's records (ADRs, runbooks, session notes — cited as
provenance, not links into this repo), this repo's own build incidents, and that first
pipeline run.

---

## Claude Code & hooks

**A delimiter fence only works if the payload can't close it.** The SessionStart hook
injects a ticket's title and acceptance criteria wrapped in `<untrusted-ticket-data>`,
because tracker text is third-party content and anyone who can file a ticket can write
it. The first pattern that neutralized the tag inside the payload anchored the optional
slash directly after `<`, so `</untrusted-ticket-data>` was caught but
`< /untrusted-ticket-data>` sailed through — one space, and everything after it is
promoted back to instruction level. Tolerate whitespace on **both** sides of the slash,
swallow attributes and the trailing `>`, and keep a case per escape shape. Corollary:
because that hook is deliberately *not* self-protected and its root falls back to the
model-mutable cwd, its `additionalContext` is context, never authority — a guard that
needs the pinned ticket reads the pin itself (docs/adr/2026-08-24-untrusted-ticket-data-fence.md).

**Hook scripts BEFORE settings wiring — the missing-script deadlock (two variants).**
`settings.json` hook config hot-loads the instant the file is written, and Claude Code
reads python's exit 2 as "block" — which is exactly what python exits when the script
file can't be opened. Result: **every tool call blocks, including the ones that would
fix it.** Variant 1 (production, 2026-06-23): relative hook path broke once the shell
`cd`'d out of the project root. Variant 2 (this kit, 2026-07-03): `settings.json`
written before the hook script existed. Fixes: always
`python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/…` (absolute, quoted; the var is set in the
hook environment — verified — though not in the Bash tool env), and **create the
scripts first, write settings.json last**. Recovery is human-only: create an exit-0
stub at the expected path, fix/remove settings.json — or, for the relative-path
variant only, restart the session (a fresh shell resets cwd to the project root).
Asymmetry worth knowing: a missing *interpreter* is the opposite failure — `python3`
absent means shell exit 127, which the harness treats as non-blocking, i.e. **fail
open**: every guard silently gone while bypassPermissions stays on. The kit's
PreToolUse wiring closes this with `command -v python3 … || exit 2`. Do
NOT "harden" with a
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

**Test the hook the way the harness invokes it.** The first pre-commit hook of the
production build "looked fine" and was silently broken. Hence the permanent battery
(`test_hooks.py`, in CI) and the ritual: stage a fake `ghp_…` token (must block),
commit `.env.example` (must pass), confirm the audit log grew.

## Git, GitHub & CI

**Merge THEN require (branch protection ordering).** A job's `name:` IS its
required-status-check context, and GitHub accepts a context that has never reported —
at which point **every open PR wedges** ("Expected — waiting for status"). Add CI jobs
in one step; flip them to required only after they've reported on main. Adding without
dropping existing: `gh api -X POST …/protection/required_status_checks/contexts -f 'contexts[]=Lint'`.

**Two green PRs can still make `main` red (semantic conflicts).** GitHub tests each
PR as *main + that PR* — never *main + the other open PRs*. So a break that only exists
in the **combination** is invisible to every PR's CI. Real case: one PR added a check
that rejects absolute home paths; a sibling PR added a test fixture containing one.
Each was green alone (the check without the fixture; the fixture without the check),
and `main` went red the moment the second merged — with no PR to blame, because git
reports no conflict: they touched different files. It is a *semantic* conflict, not a
textual one, so `git merge` will never warn you.

Cheap check before merging a batch — merge them together somewhere disposable and run
CI's checks against *that* tree:
```bash
git worktree add -f --detach /tmp/union origin/main
git -C /tmp/union merge --no-edit branch-a branch-b branch-c
cd /tmp/union && <your CI checks>        # then: git worktree remove --force /tmp/union
```
The built-in prevention is branch protection's **"require branches to be up to date"**
(`"strict": true`), but it forces every open PR to re-sync and re-run CI after each
merge — real friction at any volume, and often removed for that reason. GitHub's
**merge queue** buys the same guarantee without the churn (it tests the batch as one
tree) at the cost of a `merge_group` trigger in CI. Otherwise: keep CI's `push: [main]`
run — it turns this from silent to *loud within a minute* — and reach for the union
check when landing several PRs at once.

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
To *test* a hook fix from a worktree before it merges, commit once with
`git -c core.hooksPath=<dir> commit …` pointing `<dir>/pre-commit` at the fixed hook.

**Husky resets `core.hooksPath` on every `npm install`** (the `prepare` script). Don't
fight it or repoint it; make the hook itself robust.

**Cross-worktree writes bypass the branch guard — so a dedicated guard blocks them.**
The branch guard only fires for paths inside the session's own checkout, so an
absolute `Write`/`Edit` into a *different* worktree (classically the main checkout on
`main`, reached via a stray `cd`) lands there silently — tests/typecheck here still
pass against the unmodified files, so a whole session's edits can go to the wrong
checkout unnoticed. The one way a session writes to `main` despite the hook. Fix: a
cross-worktree guard resolves the target's owning worktree via `git worktree list` and
blocks any write outside the session's own root, printing the corrected in-worktree
path. Fails open; same-worktree and out-of-repo writes are untouched. Prefer
`git -C <dir>` over a persisted `cd`.

**A DIRTY (conflicted) PR looks green but never ran the real CI.** When a PR has merge
conflicts with its base, GitHub can't build the merge ref, so the `pull_request`-
triggered required checks (Lint/Typecheck/Test/E2E) **never run** — only side
workflows (CodeQL, Vercel) report, and those can be SUCCESS. `gh pr checks` then shows
passing while the gate never executed (a real 2026-07-03 near-miss). Never treat a
DIRTY PR as done: rebase onto latest main, resolve, force-push, re-watch CI. The kit's
Stop hook now blocks turn-end on `mergeStateStatus == DIRTY` (only explicit DIRTY, not
the transient UNKNOWN right after a push).

**A turn-end gate can't see a PR that goes DIRTY *after* the turn.** The Stop hook
above reads merge state at turn-end, in the session that owns the branch — so the PR
that was clean when its session ended and conflicted later, when a *sibling* merged,
is exactly the one it misses. With several parallel PRs off one `main` that is the
common case, not the edge: PR #27 opened clean and was conflicted a minute later,
when a sibling touching the same two files merged, and nothing flagged it
(2026-08-24). A gate inside the session cannot cover a state change outside it — that
needs a standing watcher, so the kit now runs
`.github/workflows/pr-conflict-monitor.yml` on every push to `main` (the merge IS the
event that conflicts the siblings) plus an hourly backstop. The `conflict` label is
the dedupe key: one comment per episode, cleared when the PR is mergeable again so a
later conflict re-alerts. Event-driven costs one wrinkle — GitHub computes
`mergeable` lazily, so a run right after a push reads `UNKNOWN` and must re-query a
*bounded* number of times before giving up.

**A hook that compares to *local* `main` false-nags — use `origin/main`.** In PR flow
you branch off `origin/main` and rarely update local `main`, so it lags (often several
merged PRs behind). A check like `git merge-base --is-ancestor HEAD main` against
*local* main then reads a zero-commit branch as "ahead of main" and nags for a PR that
isn't needed — the kit's own Stop hook did exactly this (2026-07-04). Compare against
the remote-tracking base (`origin/main`, no fetch needed); it's strictly fresher than
local main. Battery-cased so it can't regress.

**Verify a PR merged before any follow-up.** Fast-merging owners mean the branch you
just pushed is probably already merged; a follow-up commit onto it is stranded —
GitHub stops syncing a merged PR's head and stops running CI on pushes to it (this
burned real debugging time being misread as "CI is broken"). Check
`gh pr view <n> --json state` / `git fetch --prune` first; branch fresh from main.
Squash-merge caveat: stacked changes collapse into one commit — verify by checking
files in `origin/main`, not the log. Now **hook-enforced**: the PreToolUse guard
blocks commit/push on a MERGED-PR branch outright, failing open when `gh`/network
can't verify.

**Claude never merges — opening the PR is the end of its involvement.** A real
near-miss (2026-07-03): `gh pr merge --auto` was used on agent-opened PRs —
auto-merge still means the agent caused the merge, and the owner corrected it
immediately. Hook-enforced now: `gh pr merge` blocks in every form except
`--disable-auto` (which only *undoes* an auto-merge).

**GitHub Merge Queue is org-only — don't attempt it on a personal account.** The
rulesets API rejects any `merge_queue` rule with an unhelpfully empty
`"Invalid rule 'merge_queue'"`; the actual cause is that Merge Queue requires an
Organization-owned repo (Team/Enterprise), regardless of visibility. The applied
fallback: turn OFF `required_status_checks.strict` ("require branches to be up to
date") so a green PR merges without the "Update branch" click —
`gh api repos/<o>/<r>/branches/main/protection/required_status_checks -X PATCH
--input <json with strict:false + your exact check contexts>`. Accepted tradeoff: a
merge can land without CI having run on the literal post-merge state (the safety
Merge Queue would have added). Real conflicts still block regardless.

**Stage files explicitly — never `git add -A` / `git add .`.** Generated files appear
where you don't expect (in one build, a bogus root `/deno.lock` from running `deno` at
the repo root). Guard known generated paths in `.gitignore` AND stage by explicit path.

**ADR numbering collides under parallel sessions.** Numbers are claimed at
merge-to-main; two parallel branches drafted 0019/0020/0022/0023 collisions three times
in one build (one PR renumbered twice). Structural fix: one file per decision,
`YYYY-MM-DD-slug.md`, **no number** (docs/adr/README.md). Same medicine for any
append-only log with a shared counter or common tail.

**GitHub runners are detached-HEAD.** Anything branch-dependent (like the hook's branch
guard) must be tested in a sandbox repo pinned to a named branch — the kit's battery
does exactly this.

## Deploys, alerting & scheduled jobs (production, 2026-07-09 → 2026-07-22)

**Post-merge failures are silent by default — PR-green is SEPARATE from deployed.**
A prod deploy failed in the Actions tab for hours with nothing surfacing it
(2026-07-09): every PR check was green, the `workflow_run` deploy after merge was not.
Fix: a `workflow_run`-triggered alert that opens/comments ONE deduped, stable-titled
issue when a watched workflow fails on main (`templates/workflows/pipeline-failure-alert.yml`;
the stable title IS the dedupe key). And **detection without delivery is still
silence**: an auto-opened issue a solo owner doesn't watch is a dead-end — @mention +
assign the owner on create AND on every comment, so the notification reason becomes
mention/assign = email + GitHub Mobile phone push, zero new secrets. One manual step:
install GitHub Mobile and enable push.

**Skip-green preflights become a silent half-deploy hole once secrets exist — and they
blind the alerting layer.** Skip-green (a fork is never red) is right pre-configuration,
and stays right for low-stakes crons (backup/keepalive). For the deploy it is
lifecycle-bound: after the first successful deploy, flip preflights to fail-loud
(variants commented inline in deploy-on-green.yml). A rotated secret once shipped
functions against an un-migrated schema on a GREEN run (2026-07-13) — and a
skipped deploy produces no `failure` event, so pipeline-failure-alert can never fire.

**Hardcoded deploy lists rot — derive targets from the tree, smoke everything with an
absence classifier.** A hardcoded function list silently omitted a newly added function;
its cron POST 404'd every minute while every run stayed green.
Glob the deploy list from the repo, REFUSE to deploy an empty list, and probe EVERY
target. Don't assert one expected status (auth differs per target): classify only
absence signals as failure (404/000/5xx) and pass anything actually answered
(401/403/400/2xx). Retry with linear backoff for cold starts — retry absorbs
cold-start jitter, it does not paper over a real outage.

**Actions cron silently drops ticks, and schedulers self-report success.** GitHub
Actions `schedule` is best-effort — ticks at busy times (:00 especially) simply vanish
(one build lost the user-facing morning push two days running). For time-critical
work: the platform's own scheduler is primary, an idempotent Actions cron is the
redundant backup, and every cron runs off the top of the hour. Separately, a scheduler
saying "succeeded" only covers the trigger: pg_cron reported success all day while the
HTTP call its SQL made 403'd/404'd. Monitor the downstream EFFECT with a blip
threshold (e.g. ≥3 failures in 30 min) so a transient never pages but a broken cron
always does.

**Ordered-file collisions from parallel branches need a PR-time guard plus deploy-time
tolerance.** Two branches took the same migration timestamp and another sorted before
remote's latest — `db push` hard-stops on it, and prod fell 5 migrations behind for
hours (2026-07-09; the written serialization rule existed and was violated
anyway — see "write a hook" below). Deterministic PR-time check of only the files the
branch ADDS (`templates/scripts/check-migrations.mjs`: duplicate key, or not sorting
after base's latest), paired with `--include-all` on deploy-time `db push` so
legitimately out-of-order merges still apply. Tolerate out-of-order at deploy time,
enforce ordering at PR time.

## The delivery pipeline's first real run (2026-08-25)

The four entries below came out of the first ticket a template-derived project took end
to end — `/work` → the smallest diff → `/ship` → a PR. **Four rounds of static review
over the same pipeline had found none of them.** Every one is environmental: it lives in
the shape of the checkout, the order the config was committed, or the name a connector
happened to be given — none of it visible in a diff, and none of it reachable by reading
harder. One ticket through the loop buys what another review round cannot. The guards
themselves came out well: where they refused they were right, including the refusal in
the first entry that looked like a bug and was not.

**The pin binds a *path*, so dispatch from the worktree you will actually work in.** The
pin key is `sha256(realpath(session root))[:16]` (PIPELINE-CONTRACT §3) — the binding is
to a directory, not to a ticket and not to a branch. Task chips and `claude --worktree`
create a *fresh* worktree, so the sequence that feels natural — dispatch from the main
checkout, then start the session — writes the pin under the main checkout's key and
leaves nothing at the key the session computes. `/work` then fails closed, correctly, and
the message reads as though the dispatcher never ran. The rule is one line: **`cd` into
the worktree before dispatching**, so the dispatcher's default `--session-root` resolves
to the same path the session will run from; `--show` from inside the worktree is the
fastest confirmation. Two message fixes are worth making in any project that hits this —
the dispatcher should print the pinned path prominently (*"start your session here"*),
and `/work`'s failure should say *"a pin exists for `<other path>` — you are probably in
the wrong directory"* rather than merely reporting absence. It had that information and
buried it in prose, and wrong-directory beats never-dispatched by a wide margin as a
first guess.

**`delivery.json` is a one-way switch — build the machinery first, flip it last.** §2
makes the file's existence the single discriminator, so the instant it lands the project
is *configured*: every pipeline guard goes live, `/work` stops being inert, and a missing
pin becomes broken rather than off. Committing it early — while the scripts, workflows
and board conventions it presupposes are still being ported — produces the worst of the
three states, a project whose config claims enforcement while nothing enforces, because
it reads as on. Port or build the pieces first and add `delivery.json` as the last
commit of the bootstrap. Ordering is the whole fix; there is no partial-configuration
mode to reach for, on purpose.

**Nested worktrees destroy every tool that walks the tree — and a gate that can never be
green is not a gate.** Claude Code sites worktrees inside the repo by default
(`.claude/worktrees/`), so each sibling's full checkout sits under the repo root and
every glob picks it up. In the run above, `npm run lint` returned **4117 errors, 3794 of
them from sibling worktrees** — not one of them real. Noise is the small half of the
problem. That lint command is the local gate a pipeline session runs before it ships
(`delivery.json` → `commands`), and an unattended session has nobody to do the forensics
a human did here: it sees red, and its honest options are to bounce or to escalate, both
of which burn an attempt on nothing. Every tool that globs the tree needs the exclusion —
ESLint, `tsconfig`, Prettier, vitest, secretlint — or the worktrees belong outside the
repo entirely. Fixing one tool only relocates the problem; the next glob finds it again.

**MCP server names are per-installation, so granting a tool by name is not portable.**
MCP tool names are `mcp__<server-key>__<tool>`, and the key comes from whatever wrote the
server's config — so `/work`, `/ship` and `/weekly-review` granting `mcp__linear__*`
matches only where the server is literally keyed `linear`. A project with no `.mcp.json`,
reaching Linear through a connector configured elsewhere, gets an opaque UUID key and the
grant matches nothing. The failure is silent: nothing reports "this grant matched no
tool" — the session simply does not have the tool and routes around it. Two sessions hit
it independently and each improvised its own workaround, which is exactly the divergence
that costs a debugging session later. **The fix is genuinely open as of 2026-08-25:**
either the project ships a project-scoped `.mcp.json` naming the server `linear` (which
depends on existing connector auth carrying over — under investigation), or the skills
resolve the server by capability instead of by literal name. docs/AUTONOMY.md states the
requirement as it stands today; treat that as the current constraint, not the decided
answer.

## Environment & tooling

**nvm doesn't apply in non-interactive shells.** Claude Code's shell (and git hooks)
get the nvm *default* Node — one build's default was v16; Vite 8 and secretlint need
≥20. Fixes, layered: `.nvmrc` in-repo; export the absolute path when scripting
(`export PATH="$HOME/.nvm/versions/node/v22.23.0/bin:$PATH"`); settings.json `env.PATH`
with the resolved bin dir; the pre-commit hook self-heals by globbing
`~/.nvm/versions/node/v{24,22,20}*`; permanent fix `nvm alias default 22`.

**Git runs hooks under `sh -e`.** Any unguarded non-zero exit aborts the hook: guard
no-match greps with `|| true`; capture tool exits with `if ! cmd; then`.

**A fallback or status read attached to a pipeline measures the last stage, not the
command you meant to guard — and the last stage is usually a filter that succeeds on
empty input.** `cmd | sed 's/^/  /' || echo "(none found)"` — the `||` binds to the
whole pipeline, but the pipeline's exit status is `sed`'s, and `sed` exits 0 on empty
input same as on real input, so the fallback is unreachable: an absent file, an empty
result, and a working result all print nothing, and all three look identical. Same
family, same cause: `cmd | head -5` then `RC=$?` reads `head`'s exit status, not
`cmd`'s — `head` also succeeds on empty input. Both shapes fail in the direction that
looks like success, which is what let one of them ship five times in one session, twice
after being identified and fixed — and a review written specifically to catch this
family missed the fifth instance because it searched for `$?` after a pipe and not
`||` after a pipe; check for both shapes, not one. Fix: capture the real command's own
exit status before piping (`cmd; RC=$?; printf '%s\n' "$out" | sed …`), or test the
real command separately from the filter that formats its output — never attach a
fallback or a status read to a pipeline whose last stage is a filter.

**`.env.local` does not enter worktrees** — and hooks (correctly) block Claude from
copying it. Prefer tooling that resolves env **at runtime from the running stack**
(one build's golden suite shells out to `supabase status -o env` — zero env files
needed); otherwise copying is a human step.

**Never guess which backend a `.env.local` points at.** Verify with an unambiguous
single-pattern grep per candidate host — e.g. `grep -oE 'supabase\.co' .env.local`
then `grep -oE '127\.0\.0\.1' .env.local` — and never print the values (grep of a
pattern is hook-compatible; `cat` is not). A combined-pattern grep produced a wrong
prod-vs-local claim in a real session (2026-07-03).

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

**Map billing by failure mode, not vibes.** A production 3-provider sweep: **Supabase
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
direction (over-count spend so a kill-switch trips early, never late — one build's
formula deliberately over-counted during an intro-pricing window, no dated code to
revert).

## Process

**A guard the agent can edit is theater — the hooks must protect themselves.** A
PreToolUse hook that blocks something is worthless if Claude can just Edit the hook to
delete the block, or edit `settings.json` to unwire it — unwiring is the *easiest*
bypass. So the hook scripts + `settings.json` are human-only: Edit/Write and Bash
mutations (`>`, `sed -i`, `cp`/`mv`/`rm`, `git checkout/restore`, inline `-c`/`-e`
interpreters) targeting them are blocked; changing one means Claude prints a terminal
command for the human to run. Caveats worth stating honestly: (1) a shell can't be
perfectly fenced by regex, so this is a first line, not a sandbox — the real backstop
is git + branch protection + CI (the committed hook is what runs after merge, and CI
re-runs the battery against it); (2) it self-references (the running hook forbids
editing itself), so **compose and validate the guard before the lock lands** — once the
Edit/Write block is live, the hook is un-editable by the agent (and a syntax error would
fail *closed* — every tool blocked), so build it in a candidate and test the Bash-regex
half before adding the tool-level lock as the final edit.

**Written workflow rules are not reliably followed — back them with hooks.** The core
process lesson from the production retro: CLAUDE.md said "open a PR when done," "watch
CI to green," "don't push to merged branches" — and each was violated anyway across
parallel sessions until made *structurally impossible* (branch guard, branch-naming
guard, cross-worktree guard, merged-PR guard, `gh pr merge` block, Stop-hook no-PR /
failing-CI / DIRTY nags). When a written rule gets violated twice, stop rewording it
and write a hook. (Even the branch *name* convention: a fresh worktree's auto-generated
`claude/<codename>` branch landed unrenamed in a real PR, so the naming guard now
blocks work until it's renamed.)

**Docs are scaffolding first, then right-sized.** Heavy ADR/docs discipline exists to
let cold sessions reconstruct a system under construction; once built and stable, new
ADRs only for architecture/security-boundary/external-service changes, and doc updates
only when a change makes one stale. The dial-down is an explicit, dated decision — not
drift.

**Three isolated secret stores** (local env / CI secrets / host env) — setting one does
nothing for the others; walk the list on every rotation. Full model: docs/SECURITY.md.

**Deferrals are managed loops, not shrugs.** Record a revisit trigger with every
deferral and write the dated re-decision when it fires (the production build deferred
Realtime twice, on schedule, with reasons).
