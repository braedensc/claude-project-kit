# Budgets belong to whatever writes the pin, and an external daemon's setup hook is not that

**Date:** 2026-08-25 · **Status:** Accepted · **Context:** `fix/kit-17-cyrus-budget-ceiling`, KIT-17. Extends [Pipeline guards, dispatcher-anchored](2026-08-24-pipeline-guards-dispatcher-anchored.md), which established the pin as the only authority.

## Decision

The kit places **no enforcement script in an external agent daemon's pre-session
hook**, and ships nothing that tries to bound a session such a daemon started.
`docs/AUTONOMY.md` instead states, per limit and per dispatcher, which budgets
actually hold — including the column where none of them do — and names an
out-of-band spend cap as the backstop.

The rule this generalizes: **`delivery.json`'s `budgets` are enforced by whatever
writes the pin.** A dispatcher that does not read the config enforces nothing in
it, and no amount of documentation inside the repo changes that.

## Why

The specific surface offered was `cyrus-setup.sh`: a per-repo hook that an
agent daemon runs after creating the worktree and before starting the session.
Verified against `cyrusagents/cyrus` @ `85aeaaa`, on a machine where the daemon
is **not installed** — so everything below is read from source, not observed on a
running system.

**It cannot refuse to start a session.** `runHookScript` wraps the invocation in
a `try`/`catch` that logs `Continuing despite <hook> script failure...` and
returns normally (`packages/edge-worker/src/GitService.ts:1517-1527`). A non-zero
exit is caught; a hang is `SIGTERM`ed at five minutes
(`GitService.ts:56`) and then caught the same way. The only observable effect of
failure is a "failed" activity event posted to the tracker. Both the per-repo and
the global setup script route through that one function
(`GitService.ts:1219`, `:1591`), so neither has a veto.

That single fact disqualifies the option on its own. But the surface is worse
than merely useless:

**It runs with the daemon's privileges, and it lives where the agent writes.**
The script is resolved inside the *worktree*
(`GitService.ts:1277-1304`), which contract §3 lists by name as an invalid
transport for anything load-bearing — "the agent's Edit/Write and shell reach all
of it". So an enforcement script there would be a file the policed party can edit,
executing outside every agent guard. Zero enforcement value, real escalation
value. Step 2's constraint — *must not be writable by a session, must not read
anything a session can author* — is not merely unmet; it is inverted.

### The three alternatives, and why each loses

**Teardown as an observer.** `cyrus-teardown.sh` is handed only
`LINEAR_ISSUE_IDENTIFIER` (`GitService.ts:1248-1256`) — no cost, no turn count —
and it fires from `deleteWorktree`, at cleanup, not at session end. It cannot see
what a run spent, so it cannot even report a breach, let alone stop one. And a
post-hoc observer was never the preferred shape: refusing to start is a
precondition check; stopping a running session needs process supervision.

**Concurrency control from daemon state.** The daemon's session map is durable
and readable (`~/.cyrus/state/edge-worker-state.json`,
`packages/core/src/PersistenceManager.ts:93`), so *counting* in-flight sessions is
possible. Acting on the count is not — there is no veto to attach it to. This
would also have needed `dispatch.statePath`, which the contract reserves for
exactly this and which is currently `null`; the point is moot, and the field stays
unclaimed.

**Bounding it from inside the session.** The kit's own guard machinery is the one
thing here that *is* self-protected and does have a veto. It cannot help: it has
no view of turns or dollars, and §3's read protocol deliberately makes an **absent**
pin fail *open* for withholding checks (`pre-tool-use.py:981`, `:1423`) so a
human's ad-hoc session in a configured repo is never bricked. Making an unpinned
session fail closed to catch this case would brick every manual session in every
project that ever adopts the pipeline — the hook-bootstrap-order failure
(`docs/LESSONS.md`) with a wider blast radius.

So: no defensible enforcement point exists, and a documented accepted gap is the
correct outcome. The preference stated in the ticket applies — **prefer the weaker
option to a privileged script an agent can influence.**

### What replaces it

An out-of-band ceiling, chosen because it survives this system's bugs by not
being part of it. The Anthropic Console supports a monthly spend limit per
**workspace**; API keys are "tied to the Workspace they're created in and cannot
be moved between Workspaces", and a workspace limit may only be set lower than the
organization's. A key minted in a capped workspace holds its cap whatever
`delivery.json` says and whatever any dispatcher does.

## Verified

- **No veto, all paths.** `runHookScript`'s catch (`GitService.ts:1517-1527`) is
  the sole terminus for repo setup, global setup and teardown; both the `spawn`
  variant, which rejects on a non-zero close (`GitService.ts:1482-1503`), and the
  `execSync` variant, which throws (`GitService.ts:1560-1583`), land in it.
- **No config field bounds a session.** `RepositoryConfigSchema` and
  `EdgeConfigSchema` (`packages/core/src/config-schemas.ts`) carry no turn, cost,
  wall-clock or concurrency key. Re-verified rather than taken from the research
  summary; a case-insensitive sweep for `budget|quota|throttle|ratelimit|maxusd|
  maxsessions|maxminutes` across `packages/*/src` and `apps/` returns only
  unrelated SDK error strings.
- **No turn cap on tracker-issue sessions.** `EdgeWorker.ts:4665` passes
  `undefined` for `maxTurns` from `initializeAgentRunner` (`:4499`), the shared
  helper behind issue-session creation; `RunnerConfigBuilder.ts:517-518` therefore
  omits the key, and `ClaudeRunner.ts:734` omits it from the query. GitHub
  (`EdgeWorker.ts:1560`) and GitLab (`:2296`) pass `200`; chat sessions get `200`
  hardcoded (`RunnerConfigBuilder.ts:314`).
- **No concurrency control.** `maxConcurrent`, `pLimit`, `p-limit`, `semaphore`,
  `p-queue`, `bullmq`, `bottleneck`: zero hits across `packages/`, `apps/` and
  every `package.json`. **No retry/backoff and no wall clock.** `EdgeWorker.ts`
  (7614 lines) contains zero occurrences of `setTimeout` or `retry`.
- **The `.env` override is real and documented on both ends.** Session env is
  `buildBaseSessionEnv()` → `dotenv.parse(<worktree>/.env)` → runner additions,
  in that order, re-read every session (`ClaudeRunner.ts:673-680`, `:496`,
  `:1200-1210`); the first forwards the daemon's `CLAUDE_CODE_OAUTH_TOKEN`
  (`session-env.ts:12-16`). Claude Code's env-var reference states that when
  `ANTHROPIC_API_KEY` is set it "is used instead of your Claude Pro, Max, Team, or
  Enterprise subscription even if you are logged in", and that "in non-interactive
  mode (`-p`), the key is always used when present".
- **The agent cannot author that file.** Writing or editing a dotenv path, and
  any Bash command naming one, are already blocked, as is an `sk-ant-…` value in
  file content — four cases in the battery, green. The realistic path is an
  operator's own file copied in via the daemon's `.worktreeinclude`
  (`WorktreeIncludeService.ts:35-47`), which is why `AUTONOMY.md` tells operators
  to check that list rather than telling agents anything.

**Not verified, and stated as such.** The daemon is not installed here, so nothing
above was observed on a running system — in particular whether the kit's own
PreToolUse hook fires inside a daemon-started session. `ClaudeRunner.ts:671` sets
`settingSources: ["user", "project", "local"]`, which is why the project's hooks
are *expected* to load, but that is an inference from source, not a test.
`CLAUDE_CODE_RETRY_WATCHDOG` — cited as making a rate-limited session wait rather
than die — is absent from Claude Code's documented environment variables and could
not be confirmed; no plan here rests on it.
