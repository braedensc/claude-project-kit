# Where the review session runs

**Date:** 2026-08-26 · **Status:** Accepted · **Context:** `docs/kit-20-where-review-runs`, KIT-20. Reverses the review half of [Pipeline guards, dispatcher-anchored](2026-08-24-pipeline-guards-dispatcher-anchored.md) for the `local-daemon` backend, and extends [Budgets belong to whatever writes the pin](2026-08-25-external-daemon-budget-enforcement.md), which established what a daemon-started session does and does not inherit.

## Decision

**Review moves to the machine the dispatcher runs on.** When `dispatch.backend` is
`local-daemon`, the review pass is a local Claude Code session, not a GitHub Actions
run. The cloud templates stay in the kit for the `github-actions` backend; they stop
being *this* project's path.

**Nothing is built in this PR.** The investigation below shows review-on-Cyrus is
**construction, not configuration** — Cyrus has no PR-opened trigger and its GitHub
system prompt is hardcoded to *make changes and push*, which is the wrong shape for a
reviewer. Per KIT-20's own instruction, that stops the work at this ADR. What building
it would involve is recorded in [Building it](#building-it-what-is-actually-missing)
so the next decision starts from evidence rather than from scratch.

The isolation concern does **not** clear the bar for staying in the cloud. Reading the
rubric from the committed default branch closes the vector KIT-20 named, but it does
not close three others, and those three need their own mechanisms —
[enumerated below](#the-isolation-question).

## Why

The kit put review in GitHub Actions when the dispatcher was also in GitHub Actions.
That was coherent: one trust domain, one credential model, one place to look. Choosing
a local daemon for dispatch split that domain in half, and a review lane in the other
half now pays a coordination cost for an independence benefit that
[turns out to be smaller than it looks](#accepted-downsides).

### What Cyrus actually does with GitHub PRs

Read from source at `cyrusagents/cyrus` @ `85aeaaa` — the same commit
[AUTONOMY.md](../AUTONOMY.md) and the KIT-17 ADR pin — cloned to a scratch directory,
not added to this repo.

**It starts sessions from GitHub events, but not the event review needs.** The
transport accepts exactly four types and hard-rejects everything else
(`packages/github-event-transport/src/types.ts:60-66`, enforced at
`GitHubEventTransport.ts:243-246`):

| Event | Starts a session? | Gate |
|---|---|---|
| `issue_comment` | yes | must contain `@$GITHUB_BOT_USERNAME` (`EdgeWorker.ts:1273-1278`) |
| `pull_request_review_comment` | yes | same mention gate |
| `pull_request_review` | yes | no mention needed; off when `prReviewTrigger: false` (`EdgeWorker.ts:1263`) |
| `push` | **no** | routed to a rebase-notification handler for *already running* sessions (`EdgeWorker.ts:934`, `:1670-1700`) |

There is no `pull_request` subscription at all — every `pull_request` string in the
package is a *payload field* inside a comment or review event, never a subscribed type.
**A PR opening starts nothing.** Something has to speak first.

**The prompt is the triggering comment.** `taskInstructions` is the comment body with
the bot mention stripped (`EdgeWorker.ts:1432-1436`). It is wrapped in a system prompt
built by two hardcoded string builders (`:1909-1941`, `:1942-1987`) that both instruct:

> - Make changes directly to the code on this branch
> - After making changes, commit and push them to the branch

`promptTemplatePath` exists on the repo config (`config-schemas.ts:345`) but the GitHub
path never reads it — it is wired only into the Linear assemblies (`EdgeWorker.ts:509`,
`:3061`, `:3104`). So a rubric can only arrive as the comment body, fighting a system
prompt that tells the session to push. Prompt-versus-prompt is not a boundary.

**The worktree is not guaranteed fresh, and the reuse is silent.** `createGitHubWorkspace`
derives the branch from the PR head ref (`EdgeWorker.ts:1853-1902`) and calls
`GitService.createGitWorktree`, which — when that branch is already checked out
somewhere — **returns the existing worktree** (`GitService.ts:811-827`):

```js
if (existingWorktreePath && existingWorktreePath !== workspacePath) {
    this.logger.info(`Branch "${branchName}" is already checked out in worktree at ${existingWorktreePath}, reusing existing worktree`);
    return { path: existingWorktreePath, ... };
}
```

The coding session that opened the PR is on exactly that branch. Unless its worktree is
gone, **the reviewer runs inside it** — an `info` log, not an error.

**It can post a PR comment, on a credential separate from the model's.** The reply is
the session's last assistant text block, posted by a deterministic EdgeWorker path, not
by the model (`EdgeWorker.ts:1988-2065`), using installation token → GitHub App token →
`GITHUB_TOKEN` (`:1197-1212`). It posts a **plain comment** — never an approval or a
check status. That the poster is deterministic is worth keeping; it is the one piece of
the cloud design's structure that survives unchanged.

**GitHub sessions are full-write by default.** `GITHUB_DEFAULT_ALLOWED_TOOLS` grants
`Read`, `Edit`, `Write`, `Bash`, `Task` and more, with a comment stating the intent
plainly: *"GitHub sessions are full engineering sessions like Linear"*
(`packages/core/src/allowed-tools-defaults.ts:140-185`). This is overridable per
deployment via `githubAllowedTools` (`config-schemas.ts:473`, applied at
`ToolPermissionResolver.buildGithubAllowedTools`), which matters a great deal below.

### The isolation question

KIT-20 proposed the `delivery.json` doctrine: the reviewer reads its rubric and the
ticket's acceptance criteria from the committed default branch, never the working tree.
That is correct and necessary. It is not sufficient. Enumerating what a coding session
could do to influence a later local review:

| # | Vector | Closed by read-from-default-branch? |
|---|---|---|
| 1 | Edit the rubric in its worktree | **Yes.** Never read. |
| 2 | Commit a modified rubric on its branch | **Yes** — `git show origin/main:<path>`, provided the reviewer fetches first rather than trusting a stale local ref. |
| 3 | Commit a modified `CLAUDE.md` on the PR branch | **No.** Claude Code auto-loads `CLAUDE.md` from the working directory, and the reviewer is checked out on PR head. The reviewed diff becomes reviewer instructions. |
| 4 | Leave a dirty worktree the reviewer inherits | **No.** `GitService.ts:811-827`. Uncommitted and untracked files never appear in the PR diff, so this is invisible to the thing being reviewed *and* to a human reading the PR. |
| 5 | Author the trigger comment that becomes the reviewer's prompt | **No.** The comment body *is* `taskInstructions`. A session that posts `@bot review me` writes its own reviewer's prompt. |
| 6 | Self-approve from inside the review session | **No.** `gh pr merge` is hook-blocked; `gh pr review --approve` is **not**, and the default toolset includes `Bash` with the human's `gh` credential. |
| 7 | Edit the hooks or `.claude/settings.json` | **Yes**, independently — self-protection plus the `hooks-change` label gate. |

Four vectors survive. None of them meets KIT-20's bar of *"cannot be meaningfully
isolated,"* because each has a concrete closure that lives in our control:

- **#3** is not local-specific — a cloud reviewer checking out PR head has the identical
  exposure — so it is not a discriminator between the options. It wants `CLAUDE.md` on
  `autonomy.riskPaths`, and a reviewer that reads its *own* operating instructions from
  `origin/main` and treats the worktree strictly as data.
- **#4** is the sharpest finding and the only genuinely Cyrus-specific one. It closes if
  the dispatcher tears down the coding worktree before review, and it is worth an
  assertion in the reviewer's own preamble — *fail if the working tree is dirty* — rather
  than trusting teardown, because the failure mode is silent.
- **#5** closes by rule and by guard: the trigger comment is authored by the watcher,
  never by a session. This is the pin doctrine in a second place — the party under review
  does not get to write the terms — and it deserves the same treatment the protected
  labels got, a hook that blocks a session from `@`-mentioning the bot.
- **#6** closes by setting `githubAllowedTools` to a read-only list so no approval path
  exists inside the session at all. Because the *only* GitHub-triggered sessions in this
  design are reviews, that narrowing costs nothing — a consequence being that **bounce
  cannot share the GitHub lane.** A bounce stays a Linear dev dispatch, which is what
  contract §7 already calls it: one bounce = one fix session.

So the answer to KIT-20's question is *yes, but not for the reason offered.*
Read-from-default-branch closes the rubric vector; the ambient-context and
trigger-authorship vectors need mechanisms of their own, and all four are buildable.

### Accepted downsides

**Reviewer independence is weaker, and in more ways than one machine.** Same host, same
`gh` credential, same Claude credential, same filesystem. The cloud design bought
non-approval *structurally* — a `pull-requests: read` token, a closed `--allowedTools`
allowlist with no Bash, and a separate deterministic job to post the comment. Locally
only the third survives for free. The first two become configuration
(`githubAllowedTools`) and a hook, which is a real boundary but a weaker one: config can
be edited by a human in a hurry, where a token scope cannot.

Two further reductions, both inherited from the KIT-17 finding: a Cyrus-started session
is **unpinned**, so the pin-dependent guards stand down for the reviewer exactly as they
do for a coding session (`pre-tool-use.py:1423`, `:1433`, `:981`) — coherent for a
read-only reviewer, but it means the reviewer is bounded by its toolset, not by the pin.
And no budget applies: Cyrus does hold GitHub sessions to `maxTurns: 200`
(`EdgeWorker.ts:1560`) where Linear issue sessions get none (`:4665`), so the review lane
is in fact the *better*-bounded of the two, but 200 turns is a runaway ceiling, not a
review budget.

**An unattended session can consume the subscription window.** Braeden accepted this
explicitly, and the reasoning is recorded because it is what makes the trade acceptable
and it would otherwise be lost:

> *"if I hit the limit the answer is just to wait, I can pace my work a little slower
> since this is working around the clock"*

The trade is throughput-for-thrift with a human-absorbable failure mode: the pipeline
runs unattended for far more hours than the human works, so the window is better spent
by the pipeline than reserved for a person who is asleep. Contract §`auth` exists to
split these credentials — `auth.review: api-key` remains available to anyone who would
rather pay than wait, and this decision does not remove that lever, it declines to pull
it.

### What was rejected

**Staying in the cloud.** Nothing found meets the critical bar. Subscription auth in
particular does **not** break in a review context: `CLAUDE_CODE_OAUTH_TOKEN` is a
first-class option (`packages/claude-runner/src/session-env.ts:13-15`, and Cyrus's own
`docs/SELF_HOSTING.md:97`), forwarded identically to every session with no per-context
branching, and rate limits surface as a `rate_limit_event` error activity rather than a
corrupt run. The credential is contended, not broken — a cost, not a disqualifier.

**Having the coding session trigger its own review** at the end of `/ship`. This is
vector #5 with the safety filed off: the reviewed party authoring the reviewer's prompt.
Rejected on the same grounds the pin exists.

**A GitHub Actions workflow that posts the trigger comment** so a local daemon does the
reviewing. It works, and it is honestly the cheapest path — but it keeps a cloud
dependency in the critical path for a one-line side effect, which is the incoherence
this ADR is resolving. Worth revisiting only if the local watcher proves unreliable.

## Building it: what is actually missing

Recorded, not built — this is a separate decision.

1. **A local watcher.** Polls `gh pr list --json number,createdAt,headRefName` on a
   launchd interval, with a durable seen-set **outside every worktree** (the doctrine
   `pinsRoot` and `dispatch.statePath` already follow). Polling is fine; the daemon is
   already running.
2. **It posts a fixed trigger comment** naming the rubric path on `origin/main`. Fixed
   text, authored by the watcher, never by a session.
3. **Cyrus config:** `githubAllowedTools` narrowed to a read-only set; a decision on
   `prReviewTrigger` (leaving it on means a human's *changes-requested* review spawns a
   Cyrus fix session, which may or may not be wanted alongside the Linear bounce path).
4. **A dirty-worktree assertion** in the reviewer's preamble, per vector #4.
5. **A hook guard** blocking a session from `@`-mentioning the bot, per vector #5, and
   one blocking `gh pr review --approve`, per vector #6.
6. **Findings still emit the `pipeline-review/1` shape** in the final assistant message,
   since that message is what EdgeWorker posts and what the telemetry sweep parses. The
   contract needs no change here.

The unavoidable ugliness: the hardcoded *"commit and push"* system prompt cannot be
turned off. The answer is not to argue with it in the task text but to make it
unactionable — a read-only toolset means the instruction has no path to execute.

## Fate of the dependent work

| Work | Fate |
|---|---|
| **KIT-18** — the pin bridge | **Still needed, re-scoped.** The reviewer needs the dispatch-time ticket snapshot for the same reason it always did: scope judged against the live ticket lets scope drift retroactively. What changes is the direction — no longer "carry the pin into a GitHub Actions run" but "let a local review session locate the pin for the PR's ticket." A Cyrus session is unpinned, so this does not come for free. |
| **KIT-19** — startup-failure + trigger port | **Split.** The *trigger port* half is **cancelled** — there is no cloud trigger left to port. The *startup-failure* half is **still needed and more urgent**: a daemon that fails to start is at least as silent as a workflow that fails to start, and unlike a workflow it has no run history a human passes by. Re-scope to daemon health. |
| `pipeline-review.yml` | **Retained for `github-actions`, superseded here.** Not deleted — the kit is a template and the cloud backend is still a supported choice. |
| `pipeline-bounce.yml` | **Still needed, re-scoped.** It triggers on `workflow_run` of *Pipeline review*; with review local there is no such run. Bounce re-homes to the dispatcher as a fix dispatch. |
| `pipeline-auto-merge.yml` | **Still needed, re-scoped.** Same `workflow_run` dependency; must key off the review's PR comment or a check instead. |
| `pipeline-dispatch.yml` | **Retained for `github-actions`.** Already superseded locally by the daemon; that predates this ADR. |
| `pipeline-telemetry.yml` | **Unaffected.** It sweeps PR comments, and the review still posts one. |
| `pipeline-auto-approve.yml`, `pipeline-safe-outputs.yml` | **Unaffected.** Ticket intake and a `workflow_call` helper; neither depends on where review runs. |

## The lane-naming defect — follow-up, not this PR

Contract §`auth` names its lanes for *what kind of work they do* — `devSessions`,
`scheduled`, `review` — when what determines the credential is *where the session runs*.
This ADR is the proof: `auth.review` was written assuming review is unattended CI, and
this decision makes review attended-machine/unattended-time, which the vocabulary cannot
express. `dispatch.backend` already names the axis that matters.

This is the same defect KIT-17 recorded on `auth.*`, now observed a second time from a
different direction — which is evidence it is structural rather than incidental.

**It belongs in a follow-up, not here.** Renaming a contract field is a breaking change
to a frozen format (§ preamble: amend the contract in its own PR, never invent a second
shape), it touches the validator, the selftests and every template that reads `auth.*`,
and bundling it with a decision record would put a mechanical rename inside a PR whose
value is the argument. **No contract shape is amended in this PR.**

## Verified

- Every Cyrus claim is `file:line` against `cyrusagents/cyrus` @ `85aeaaa`, cloned to a
  scratch directory outside this repo. The commit matches the one AUTONOMY.md and the
  KIT-17 ADR already pin, so the two readings are comparable.
- The absence claim — no `pull_request` trigger — was checked positively rather than by
  failing to find one: the type union is closed (`types.ts:60-66`) and the transport
  rejects anything outside it before dispatch (`GitHubEventTransport.ts:243-246`).
- The `maxTurns` asymmetry from the prior research was re-confirmed at both sites:
  `EdgeWorker.ts:1560` (GitHub, 200) and `:4665` (Linear, `undefined`).
- The isolation enumeration is adversarial by construction — it asked what a coding
  session *could* do, and reports four vectors that survive the proposed answer rather
  than ratifying it.
- No code changed, so the gate is unchanged. The kit ships no `delivery.json`, so every
  pipeline script remains inert here regardless.
