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

Two gaps on that lane are **accepted in writing** rather than papered over: no
`wipLimit` (nothing in such a daemon can enforce one), and a workspace-scoped
tracker write token inside the session, covered by the `self-approval` guard
alone. Both vanish the moment the ticket is dispatched by something that writes a
pin, which is the standing recommendation.

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

### A wrapper, the other half of the same option

The option paired "a wrapper" with the setup hook, and the two are not the same surface.
The runner does expose the seams a wrapper would need — `pathToClaudeCodeExecutable`,
`extraArgs`, `spawnClaudeCodeProcess` (`packages/claude-runner/src/types.ts:76-84`,
threaded through at `ClaudeRunner.ts:739-742`) — and injecting `--max-turns` /
`--max-budget-usd` through any of them would be a **real ceiling**, applied outside the
session by something the session does not run.

It is rejected on availability, not on principle: **nothing in the daemon ever sets
them.** They are unset SDK-passthrough fields with no operator-facing config key — the
edge-worker never writes any of the three, and neither `RepositoryConfigSchema` nor
`EdgeConfigSchema` has a field that would. Reaching them means editing the daemon, which
is option 4 with extra steps.

That leaves the one wrapper that needs no cooperation: a `claude` shim earlier on the
**daemon's** `PATH`, appending the flags before exec'ing the real binary. It is the
strongest idea in the option set and it is deliberately not built here, for three
reasons. It intercepts *every* `claude` invocation on the machine, including the
operator's own interactive sessions, so its blast radius is far wider than the lane it
polices. It must resolve which repo it is dispatching for — and its cwd is the worktree,
which is agent-writable, so a naive implementation reads its limits from the thing being
limited. And none of it can be tested here: the daemon is not installed, and shipping an
untested privileged shim into a user's `PATH` is precisely the trade Step 2 says to
refuse. It is written up as the viable-but-unbuilt option rather than shipped blind.

### The remaining alternatives, and why each loses

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

**Enforcing inside the session**, via a mid-run checkpoint the agent consults. Rejected
for the reason the option itself gives: the thing being limited is doing the limiting.
The contract already settles this class — `fixIterations` is prompt material precisely
because a self-applied number is a thrift knob, not a safety one, and §4 makes a
session's own telemetry reporting that can never buy it more budget. A checkpoint is
useful for making a session *stop tidily*; it is not a ceiling.

**Contributing limits upstream.** The right long-term answer and no help now: bus factor
1, unpredictable timeline, and nothing to run in the meantime. It is also not mutually
exclusive with anything here — the decision below stands whether or not the daemon later
grows its own caps, because the rule it encodes is about pins, not about one tool.

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

- **The tracker token is injected unconditionally, and the bare server prefix is
  granted.** `McpConfigService.ts:107-113` writes `Authorization: Bearer
  ${linearToken}` into the session's MCP server set; `mcp__linear` — the *server*
  prefix, i.e. every tool on it — is in `LINEAR_DEFAULT_ALLOWED_TOOLS`
  (`allowed-tools-defaults.ts:88`) and in `SLACK_DEFAULT_ALLOWED_TOOLS` (`:133`),
  which is what the `"readOnly"` preset resolves to
  (`ToolPermissionResolver.ts:60`). The token reaches the SDK as an in-memory
  `mcpServers` object (`ClaudeRunner.ts:625`, `:717`), **not** as a file the
  daemon writes into the worktree — so "the token sits in a file the session can
  read" is not established from this source; whether the SDK materializes it is
  an SDK question this checkout cannot answer.
- **The guard covering it survives an unpinned session only in part** — and this
  corrects the assumption that `agent:*` ownership still applies. In
  `.claude/hooks/pre-tool-use.py:1328`, only the approval guard is ungated;
  `lifecycle-label` is behind `if pin:` (`:1338`), and own-ticket scoping and
  acceptance-criteria integrity both key off a pinned ticket ID. A daemon-started
  session therefore *can* set `agent:*` labels, write to a ticket that is not its
  own, and edit an acceptance criterion. That fail-open direction is §3's
  deliberate choice — an ad-hoc human session must not be bricked — so the fix is
  not to flip it wholesale. Making `lifecycle-label` unconditional is separable
  and worth its own ticket: §6 makes `agent:*` dispatcher-owned for *every*
  writer, and a human's ad-hoc session has no more business setting
  `agent:working` than an agent's does.
- **One unconditional ticket write.** `EdgeWorker.ts:4164` → `:5577` moves the
  issue to the lowest-ordered `started` state on session start, with no disabling
  flag and failures swallowed. Nothing is written on finish, so the safe-outputs
  validator is not made redundant. `NoopActivitySink` exists and is exported
  (`sinks/NoopActivitySink.ts:12`, `sinks/index.ts:14`) but is instantiated
  nowhere, tests included — it is not an off switch.
- **Per-PR serialization is real but irrelevant here.** `EdgeWorker.ts:235-237`
  keeps one in-flight session per GitHub PR with an unbounded FIFO behind it,
  because those sessions share a worktree. It is not a global limiter and does
  not touch the tracker-issue path.

**Not verified, and stated as such.** The daemon is not installed here, so nothing
above was observed on a running system — in particular whether the kit's own
PreToolUse hook fires inside a daemon-started session. `ClaudeRunner.ts:671` sets
`settingSources: ["user", "project", "local"]`, which is why the project's hooks
are *expected* to load, but that is an inference from source, not a test.
`CLAUDE_CODE_RETRY_WATCHDOG` — cited as making a rate-limited session wait rather
than die — is absent from Claude Code's documented environment variables and could
not be confirmed; no plan here rests on it.
