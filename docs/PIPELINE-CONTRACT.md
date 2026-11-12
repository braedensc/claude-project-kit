# Pipeline Contract

**Frozen shared formats for the agentic delivery pipeline** (tickets → Claude Code
sessions → PRs). Every stream — dispatcher, hooks, skills, workflows, collectors —
reads and writes these structures. **Change them only by amending this file in a PR**;
inventing a second shape for the same structure is the failure mode this document
exists to prevent.

| Artifact | Lives | Written by | Read by |
|---|---|---|---|
| [`delivery.json`](#1-deliveryjson) | repo root, committed | human (bootstrap) | dispatcher, hooks, skills, CI, collectors |
| [Pin file](#3-pin-file) | outside every worktree, uncommitted | dispatcher | hooks, validators |
| [Telemetry block](#4-telemetry-block) | ticket comment | the session agent | collector / dashboards |
| [Provenance + labels](#5-provenance-values) | ticket fields | dispatcher, human | approval gate, dispatcher |
| [Safe-outputs request file](#8-safe-outputs-request-file) | run-scoped artifact, uncommitted | the session agent | the safe-outputs validator |
| [Dispatcher state](#9-dispatcher-state-record) | outside every worktree, backend-durable | dispatcher | dispatcher |
| [Telemetry store](#10-telemetry-store) | a Postgres schema, outside the repo | the collector | dashboards, the weekly review |
| [Autonomy tiers](#11-autonomy-tiers) | — | — | the approve and merge gates |
| [Machine-readable schemas](#12-machine-readable-schemas) | `schemas/`, committed | this document (amend both together) | producers, validators, CI |
| [Absence is not failure](#13-absence-is-not-failure) | — | — | every pipeline job that can legitimately do nothing |
| [Status file](#16-status-file) | the role account's home, outside every worktree | the status job | the tech lead, the daily summaries, the heartbeat monitor |
| [Usage file](#17-usage-file) | the role account's home, outside every worktree | the usage job | a later queue, which decides when new work may start |
| [Day plan file](#18-day-plan-file) | the role account's home, outside every worktree | the morning planner, through `scripts/pipeline_day_plan.py` | the stand-up, the tech lead, a later app |
| [Watcher status file](#19-watcher-status-file) | the watcher account's home, outside every worktree | the watcher | the health watch, the stand-up, the validator (later) |
| [Hold file](#20-hold-file) | the watcher account's home, in a folder no worker can write | the watcher | every job that starts work |
| [Acceptance result block](#21-acceptance-result-block) | the acceptance session's result | the acceptance session | the acceptance poller |
| [Acceptance verdict comment](#22-acceptance-verdict-comment) | a comment on the plan's epic | the acceptance poller | the bridge, before it closes a plan (later) |
| [Validator settings file](#23-validator-settings-file) | the user's own home, outside every worktree | the owner, by hand | the validator |
| [Validator ledger](#24-validator-ledger) | the user's own home, outside every worktree | the validator, by one writer function | the stand-up, the recap, the watcher |
| [Acceptance follow-up request](#25-acceptance-follow-up-request) | a comment on the plan's epic | the acceptance poller's live pass | the finding poller (later) |
| [Acceptance check request](#26-acceptance-check-request) | a comment on the plan's epic | the Slack bridge, on the owner's yes | the acceptance poller's live pass |

> **§8 onwards are appended rather than slotted in beside their topical neighbours.**
> These section numbers are cited by name from `.claude/hooks/session-start.py`, the
> `templates/workflows/pipeline-*.yml` files, the skills and `scripts/check_ticket_dor.py`.
> Renumbering §5–§7 to make room would silently repoint every one of those citations,
> which is the same class of desync this document exists to prevent. Numbers are
> append-only; the cross-links below carry the topical ordering instead.

Two rules shape all of it:

- **The pipeline is optional.** It is *configured* for a project **iff `delivery.json`
  exists**. Absent, every pipeline-scoped guard, skill and workflow no-ops entirely
  (§2).
- **Anything the session agent can write is *reporting*, never *authority*.** Config and
  pins are authority; telemetry, PR bodies and commit messages are reporting. A guard
  that reads a value the agent could have written is not a guard.
- **Absence is not failure.** A component that can legitimately do nothing must be able
  to tell *nothing to do* from *could not do it*, and must say which. Otherwise the
  pipeline's most common green outcome is also its best hiding place (§13).

---

## 1. `delivery.json`

> **The machine-readable form of this section is
> [`schemas/delivery.schema.json`](../schemas/delivery.schema.json)** (§12). The tables
> below and that file are one definition in two renderings; CI fails if a field appears
> in one and not the other. Amend both in the same PR.

**Location: repo root.** Not `.claude/` — that directory is Claude-Code harness wiring
(settings, hooks, skills), part of which is hook-self-protected. `delivery.json` is
tool-agnostic pipeline config read by services that never run Claude Code (a webhook
handler fetching it by raw URL from the default branch), so it sits where
`package.json` sits: discoverable at the root.

**The kit ships `delivery.example.json`, never a live `delivery.json`.** A project opts
in by copying the example and filling it in — the same inert-by-default convention as
`.mcp.json.example`, `.env.example` and `templates/workflows/`. Never commit a live
`delivery.json` to the kit itself; that would switch the pipeline on for every fresh
copy of the template (§2).

At bootstrap a project does exactly one of two things: copy the example to
`delivery.json` and fill its `{{…}}` tokens, **or delete the example**. Leaving it in
place unfilled fails `scripts/check_placeholders.py --bootstrapped` — the zero-tokens
assertion is the nudge that forces the choice to be made rather than drifted past.

> Why `delivery.example.json` and not `delivery.json.example`: CI's *Validate all tracked
> JSON/YAML* step globs `*.json`, so this spelling keeps the template inside parse
> validation and a malformed template cannot ship. `.mcp.json.example` predates that
> reasoning and sits outside the glob.

**Resolution, for anything that gates behavior:**

| Question | Source | Why |
|---|---|---|
| Is the pipeline on? (§2) | **Working-tree stat** of `<repo root>/delivery.json` | Must be cheap, offline-safe, and impossible to fail before it answers. Deleting the file to disable a guard is loud: it is a tracked file, so removal lands in the diff and in `autonomy.riskPaths`. |
| What are the values? (budgets, autonomy, states) | **The committed copy on `github.defaultBranch`** — `git show origin/main:delivery.json` | The working-tree copy sits inside the agent's worktree and the agent can edit it; reading it would let a session raise its own budgets. Falls back to the working tree only when the default branch has no copy yet — the adoption PR — where nothing is dispatching anyway. |

- **No environment-variable override** for guard-relevant reads. Env is model-mutable
  inside a session; an override path is a hole in every guard that uses it.
- Dispatchers and other out-of-session tooling may point elsewhere (they are not the
  thing being guarded).
- `delivery.json` is in `autonomy.riskPaths` by default: a PR touching it always needs a
  human. CI should additionally assert that **if the base branch has `delivery.json`, the
  head branch still does** — deletion is a removal of supervision, not a routine edit.

**Template constraint:** CI validates that every tracked `.json` file parses. So in the
shipped template, **placeholder tokens appear only inside JSON strings**; numbers,
booleans and enums carry real defaults instead. `~` in a path value is the home the
account database gives the OS account the reader runs as, never `$HOME` (§3 says why). A
path that is not absolute once `~` is read that way is refused, never read from wherever
the reader happens to run.

### `version`

| Field | Type | Notes |
|---|---|---|
| `version` | integer | Contract version. `1` today. A reader that does not recognize the value must refuse to run, not guess. |

### `linear`

| Field | Type | Read by | Notes |
|---|---|---|---|
| `teamKey` | string | dispatcher, branch validator | Ticket prefix, e.g. `ENG` in `ENG-123`. Uppercase. |
| `workspace` | string | dispatcher, comment poster | Workspace slug; builds ticket URLs. |
| `stateIds.raw` | string (UUID) | approval gate | Intake: proposals land here, human-gated. |
| `stateIds.ready` | string (UUID) | dispatcher | Approved and dispatchable. |
| `stateIds.working` | string (UUID) | dispatcher | A session holds it. Counts against `budgets.wipLimit`. |
| `stateIds.review` | string (UUID) | dispatcher, reviewer | PR open, awaiting review/CI — the "waiting on a human" lane that keeps it distinguishable from `working`. Reached by the tracker's *pull request opened* git automation and/or a session's §8 `ticket-state` request; same target, so the two do not race, and on a lane with no safe-outputs executor the automation is the only mechanism. Neither authorises anything — approval is `ready` and is read specifically (§5). |
| `stateIds.needsApproval` | string (UUID) | Stage E bounce driver | **Provisioned as type `unstarted` — never `started`, never `completed`** (see below). AI review has concluded and a person should accept the work: the findings were clean or below `budgets.reviewSeverityThreshold`, or the bounce budget is spent (then `agent:needs-human` is on the ticket too, and that label is what tells the two apart). Reached ONLY by the bounce driver's own move — no git automation, and never a session: the `ticket-state` kind (§8) excludes it, because the conclusion of a review is not something the reviewed party may report. It authorises nothing; approval is `ready` and is read specifically (§5). |
| `stateIds.done` | string (UUID) | dispatcher | Merged/closed. Reached by the tracker's *pull request merged* git automation, seconds after the merge, or by a person. Never by a session: the `ticket-state` kind (§8) refuses `done`. **Decided (KIT-51): the automation keeps this move.** It closes tickets and starts the dispatcher's worktree clean-up with no person involved; turned off, tickets would close only by hand and worktrees would pile up. The accepted cost: Done makes the dispatcher force-remove the ticket's worktree, so a merge while that ticket's session still runs loses its uncommitted work, and any ticket id in a PR's title, branch or body moves that ticket too (`docs/OPERATIONS.md`). The tracker sets these moves per team, so each team with a repository needs them. |
| `labels.ids` | object → string (UUID) | dispatcher, guards | Map of **canonical key → Linear label ID**. The key is the stable name used in code; the Linear display name may drift from it. |
| `labels.required` | string[] | validator | Subset of `labels.ids` keys that must resolve before the pipeline may dispatch. **Floored at §6's dispatcher-owned set** (`agent:queued`, `agent:working`, `agent:blocked`, `agent:needs-human`, `blocked:capacity`) — a project may require more, never fewer. |
| `findingTicket` | object \| absent | safe-outputs executor | **Opt-in.** Present ⇒ the `ticket-create` kind (§8) is available: a session may *request* a follow-up finding ticket. Absent ⇒ that kind is refused and findings stay `ticket-comment`s. |
| `findingTicket.landing` | enum `raw` | safe-outputs executor | The state a filed finding lands in — the backlog/intake state only. Restricted to `raw`: never `ready` (self-approval), never `working`/`review`/`done` (§5, §8). |
| `findingTicket.notify` | enum `subscribe`\|`assign`\|`both` | safe-outputs executor | How `ownerUserId` is notified. The new ticket is never assigned to the session's own identity. |
| `findingTicket.ownerUserId` | string | safe-outputs executor | Linear **user ID** notified on each filed finding. A person, never the session. |

> **States and labels are referenced by ID, never by display name.** A rename in the
> Linear UI must not silently desync a guard — with names, a renamed "Ready" state stops
> matching and the queue quietly stops dispatching (or worse, a guard fails open). IDs
> survive renames. Anywhere in the pipeline that compares a state or label, it compares
> IDs.
>
> **A deleted ID fails loudly on a WRITE, and silently on a READ.** Linear rejects
> `issueAddLabel` with a dead label ID, so every key → ID → mutation path is
> self-checking. A read is a local dict lookup against `labels.ids`, and a miss is just
> a miss: the label vanishes from the ticket's key set, `effort:*` falls back to `M`,
> `provenance:*` to `human`, and an `agent:needs-human` hold a person applied to park
> the ticket evaporates. **So every read path must detect staleness explicitly.**
> Resolution stays by ID; the display name the queries already return is the
> *diagnostic* — an unmapped label whose name matches a canonical key means the recorded
> ID is wrong, not that the label is somebody else's. Severity follows `labels.required`
> (§7's dial): fatal for a required key, a warning otherwise, because `track:*` is
> open-ended and project-named so it can never be in `required`. **That dial has a
> floor** — membership in `labels.required` is what buys a key the fatal tier, so an
> empty list would demote every hold to a warning and re-arm dispatch on parked
> tickets. §7 therefore requires the dispatcher-owned set to be in it.
>
> One implementation —
> `scripts/pipeline_labels.py` — imported by every read path, never re-derived.
>
> Residual gap, named so nobody assumes otherwise: a label **renamed and then
> recreated** matches by neither ID nor name, so it reads as a label the project added
> for its own reasons. Closing it needs a liveness check against the API, which belongs
> in `/setup-board`.
>
> Corollary: `labels.ids` values are **resolved**, not authored. They ship as `""`
> (unresolved) and a setup step fills them by looking each display name up once. The
> six `stateIds` are placeholder tokens instead — a closed, mandatory set of exactly
> six, where a missing value stalls the whole queue; labels are an open set
> (`track:*` grows per project) that no fixed token list can cover. §6 fixes the scope
> those labels are created at.
>
> **`needsApproval` is provisioned `unstarted`. Never `started`, never `completed`.**
> This is not cosmetic, and it is the reason the key carries a type at all:
>
> - The dispatcher moves every new session's issue to the **lowest-ordered `started`-type
>   state**, unconditionally — no flag disables it and its failures are swallowed. A
>   second `started` lane beside `working` and `review` adds a third contender to an
>   ordering nothing in the kit pins, records or checks, and when it goes wrong there is
>   no error anywhere to bring someone to look.
> - A `completed` lane is worse. The dispatcher deletes a worktree when its issue reaches
>   a terminal state, and the bounce driver skips any ticket already in one, so every
>   subsequent bounce would become a silent no-op with the worktree already gone.
> - `unstarted` removes the lane from that contest entirely, and matches how the kit
>   already models `ready` — its other "waiting on a human" state.

### `github`

| Field | Type | Notes |
|---|---|---|
| `owner` | string | Org or user. |
| `repo` | string | Repo name. |
| `defaultBranch` | string | Base for PRs **and the ref guards read config values from**. Default `main`. |

### `branch`

| Field | Type | Notes |
|---|---|---|
| `types` | string[] | Allowed branch type prefixes. Must stay a subset of what the live PreToolUse branch-naming guard accepts: `feat`, `fix`, `chore`, `refactor`, `docs`. |
| `requireTicketId` | boolean | When true, the slug must begin with the ticket ID. |

Derived pattern when `requireTicketId` is true:

```
^(feat|fix|chore|refactor|docs)/<teamkey-lowercased>-<number>-[a-z0-9][a-z0-9-]*$
e.g.  feat/eng-123-token-refresh
```

**The ticket ID is lowercased in the branch name.** The kit's existing branch guard is
`^(feat|fix|chore|refactor|docs)/[a-z0-9][a-z0-9-]*$` — uppercase is rejected, so
`feat/ENG-123-…` is blocked before the first edit. Any stream generating branch names
must lowercase.

### `stack`

| Field | Type | Notes |
|---|---|---|
| `kind` | string | Stack identifier used to pick command defaults and prompt fragments, e.g. `node-ts`, `python`, `go`, `mixed`. |
| `securityNotes` | string[] | Stack-specific cautions injected into session and review prompts (e.g. "all AI calls are server-side only"). Prompt material, never a guard. |
| `graderPaths` | string[] | Globs a reviewer/grader must always inspect when a change touches them — the "look here first" list. Advisory to reviewers; distinct from `autonomy.riskPaths`, which is enforced. |

### `commands`

| Field | Type | Notes |
|---|---|---|
| `lint` | string \| null | |
| `typecheck` | string \| null | |
| `test` | string \| null | |
| `e2e` | string \| null | |
| `preview` | string \| null | Starts the app for a human/visual check. |

`null` means the project has no such step; a runner skips it rather than failing. An
empty string is invalid — it hides a misconfiguration behind a no-op.

### `budgets`

| Field | Type | Notes |
|---|---|---|
| `perEffort` | object keyed `S`/`M`/`L` | Each value: `{ maxTurns, maxUsd, maxMinutes }`. Selected by the ticket's `effort:*` label. |
| `maxTurns` | integer | Hard ceiling. **Effective turns = `min(perEffort[e].maxTurns, maxTurns)`** — a per-effort value can lower the cap, never raise it. |
| `wipLimit` | integer | Max tickets in `working` at once, across the whole team. |
| `maxBounces` | integer | Max review→fix round trips on one ticket before `agent:needs-human`. **One bounce = one fix session**, counted out of session (§9). |
| `fixIterations` | integer | Max read→fix→push→re-watch cycles **inside** one fix session. Default `3`, matching `/fix-ci`'s own bound. Must be ≥ 1. |
| `totalAttempts` | integer | Max dispatches of one ticket, counting all stages and bounces. Exhausted → `agent:needs-human`. The count lives in the dispatcher state record (§9). |
| `dailyUsd` | number | Rolling 24h spend cap for the whole pipeline. |
| `reviewSeverityThreshold` | enum `low\|medium\|high\|critical` | Lowest review severity that blocks progress and starts a bounce. Findings below it are posted as comments only. |

> **Spend is metered by the dispatcher, not by the agent's telemetry.** `dailyUsd` and
> `maxUsd` are enforced against the dispatcher's own accounting of the runs it started.
> The `cost_usd` a session self-reports is for dashboards; a session that under-reports
> must not be able to buy itself more budget.

> **`maxBounces` and `fixIterations` are two different numbers at two different
> levels, and one field cannot serve both.** `maxBounces` bounds how many times the
> pipeline is willing to pay for a *fresh fix session*; `fixIterations` bounds how many
> push-and-re-watch cycles *one such session* makes before it gives up and reports.
> Reusing one value for both couples them at exactly the wrong place: `maxBounces: 1`
> is a reasonable one-shot policy, and under the reuse it also tells the fix session to
> make a single push — usually not enough to clear a red build, so the one bounce burns
> for nothing.
>
> **Only `maxBounces` is enforced.** It is counted out of session, keyed on CI run IDs
> the session cannot write (§9). `fixIterations` is **prompt material**: it is injected
> into the fix session's instructions and the session could exceed it. That is
> tolerable because it is a *thrift* knob, not a safety one — the caps that actually
> stop a runaway session are `budget.maxTurns`, `maxUsd` and `maxMinutes` from the pin,
> and every push still lands on a branch that no agent may merge. Do not build a guard
> on `fixIterations`; it is reporting-grade, per the doctrine at the top of this file.

### `auth`

| Field | Type | Values | Notes |
|---|---|---|---|
| `devSessions` | string | `subscription` \| `api-key` | Interactive, human-present sessions. |
| `scheduled` | string | `subscription` \| `api-key` | Unattended/cron dispatches. |
| `review` | string | `subscription` \| `api-key` | Automated review passes. |

Declares which credential each context uses so cost attribution and rate-limit blast
radius are predictable — unattended lanes are normally kept off the interactive
credential so a runaway queue cannot exhaust a human's session capacity.

### `autonomy`

| Field | Type | Notes |
|---|---|---|
| `autoApproveProvenance` | string[] | Provenance **classes** allowed to move `raw` → `ready` without a human. Must be a subset of `["epic"]`; a validator hard-fails any other value. See §5. |
| `epicApprovers` | string[] (optional) | KIT-161. Tracker user ids of the people whose move of an epic into `ready` approves its tree (§5 rule 2). The approve tier reads who moved the epic from the tracker's history (`readyBy`) and holds every child unless that id is on this list. Empty or absent: nothing is approved. Never the agent user or a machine account. |
| `autoMergeMaxLines` | integer | Diff-size ceiling under which an *out-of-session* automation may merge. `0` disables. **No Claude Code session may ever act on this** — the merge command is hook-blocked in every form, including `--auto`. If this is ever non-zero, the merge is performed by CI or a GitHub App, never by an agent. The full tier is §11. |
| `autoMergeMethod` | string (optional) | `squash` \| `merge` \| `rebase`. How the platform merges a qualifying PR. Default `squash`. Cosmetic — it decides nothing about *whether* a PR merges. |
| `riskPaths` | string[] | Globs that force human review regardless of diff size or provenance. Ships with the guard machinery, CI, git hooks, skills (prompts and rubrics are graders), `delivery.json`, and key material. **What it means today: paths that protect the pipeline from the agent, not paths where a mistake is expensive.** The kit's floor (`REQUIRED_RISK_PATHS` in `scripts/check_delivery_config.py`) lists only guard machinery. A database migration, an auth module or a billing file is covered only if the project adds it. The validator requires the floor and allows more, so a project extends its own list; the kit does not widen the floor for everyone. Whether to add the data layer is the project's decision, and it must be made before the auto-merge tier is turned on. |

### `dispatch`

| Field | Type | Notes |
|---|---|---|
| `backend` | string | Where sessions run: `github-actions`, `local-daemon`, `cloud`. |
| `labelTrigger` | string | Canonical label key (resolved through `linear.labels.ids`) whose presence queues a ticket. Default `agent:queued`. |
| `pauseOnCapacity` | boolean | On a provider capacity error, pause the queue and apply `blocked:capacity` instead of consuming a `totalAttempts` slot. Capacity is not the ticket's fault. |
| `pinsRoot` | string | Directory for pin files. Default `~/.claude/pipeline/pins`. `~` is the home the account database gives the OS account, never `$HOME` (§3). Must resolve outside every worktree and outside the repo. Pins are **short-lived** — written per dispatch, deleted at session end, expiring in hours — so nothing durable may be stored here (§9). **One exception:** `dispatched-lane/<OS account>`, the OLDER place of the root-owned lane marker a person writes once for a dispatcher that writes no pins (§3; it now lives in `/etc/claude-pipeline`). Where it is still there it is durable on purpose, and a cleanup of stale pins leaves that folder alone. |
| `statePath` | string \| null | Where the dispatcher state record (§9) is kept, for backends that need a location named. `null` when the backend supplies its own durable store — which is the case for `github-actions`, where the store is the `pipeline-state` artifact. Must resolve outside every worktree and outside the repo. |

### `monitoring`

| Field | Type | Notes |
|---|---|---|
| `provider` | string | `github-actions`, `external`, or `none`. |
| `stormPerHour` | integer | Max alerts emitted per hour; beyond it, alerts are coalesced. Prevents one broken cron from filing a hundred tickets. |

### `telemetry` (optional)

Absent ⇒ **collection is off**, in exactly the sense §2 gives the word: the collector
exits 0 and emits nothing. A project can run the whole pipeline and never persist a row.

| Field | Type | Notes |
|---|---|---|
| `store` | string | `postgres` today. A collector that does not recognize the value refuses to run rather than guessing at a dialect. |
| `dsnEnv` | string | **The NAME of an environment variable holding the connection string — never the connection string.** A DSN carries a password, and `delivery.json` is a tracked file. |
| `schema` | string | SQL schema the tables live in. Default `pipeline`. Must be a plain identifier. |
| `lookbackDays` | integer | How far back a sweep reads. Default `30`. Generous on purpose: the sweep is idempotent (§10), so overlap is free and a gap is not. |

> **Not in the §7 validator, and deliberately so.** Every §7 row gates *autonomy* —
> what may dispatch, approve, or merge. Telemetry gates nothing: it is a sink for
> reporting (§4), and a project with a misconfigured sink loses dashboards, not
> supervision. It is validated by its own consumer, `scripts/telemetry_scrape.py`,
> at the point of use. Adding it to the guard validator would imply the guards
> depend on it, which is the opposite of true.

### `acceptance` (optional)

Absent ⇒ **the acceptance check is off**, in exactly the sense §2 gives the word: a finished
plan closes as it does today, and the readiness gate skips its browser-check rules and names
them as skipped. Present ⇒ the project says how a session runs its app for a browser check
**with no real services**, and planning must write checks a machine can run (§15). The
decision record is `docs/adr/2026-11-02-acceptance-check-before-the-last-merge.md`.

| Field | Type | Notes |
|---|---|---|
| `setup` | string \| null | Run once on the fresh checkout before `build`, e.g. `npm ci`. Default `null`: the checkout needs nothing. |
| `build` | string \| null | Run from the repo root on a fresh checkout of the pull request being checked. Produces what `serve` serves. Its environment holds no secret: dummy service settings belong in the project's own script, never in an env file (the kit's secret guards will not let a session read one). `null` only when `serve.mode` is `command` and that command builds as it starts. |
| `serve.mode` | enum `files`\|`command` | `files`: the browser's requests to `serve.origin` are answered from `serve.dir`. No port is opened and no process runs. `command`: `serve.command` starts a local server at `serve.url`. Prefer `files`; `command` is for an app that renders on a server. |
| `serve.dir` | string | `files` only. The repo-relative folder `build` writes. Gitignored. |
| `serve.fallback` | string \| null | `files` only. The file served for a path that has no file. Default `index.html` (a single-page app); `null` answers 404. |
| `serve.origin` | string | `files` only. The origin the app loads at. Default `http://127.0.0.1:9`: a loopback origin is a secure context, and Chromium refuses port 9 outright, so a request that escapes interception reaches nothing. |
| `serve.command` | string | `command` only. Starts the app with its own fakes for every service. The runner stops its whole process group when the check ends. |
| `serve.url` | string | `command` only. Where that server answers. Loopback only: `127.0.0.1`, `localhost` or `[::1]`. |
| `serve.readySeconds` | integer | `command` only. How long to wait for `serve.url` before the run is *could not run*. Default `120`. |
| `fakeBackend` | string \| null | Repo-relative path of the project's own fake-backend module (§15). It answers every request the app makes to a service. `null`: no module, and every request to anything but the app's origin is aborted. Either way each aborted request is listed in the result. A coding session can edit this file in the pull request it is judged on, so list it in `autonomy.riskPaths`, which puts it behind a person's review. |
| `scenarios` | string[] | The data sets the fake backend serves, by name: `signed-in`, `signed-out`, `empty`. A check names one; the first is the default. Required when `fakeBackend` is set. |
| `viewports` | object → `{width, height, mobile?}` | Named screen sizes a check may name. Default `desktop` (1280×800) and `phone` (390×844, `mobile`: touch, a mobile user agent, device pixel ratio 2). |
| `modes` | object → `{colorScheme?, reducedMotion?, locale?, timezoneId?, localStorage?}` | Named app states a check may name: a theme, a locale, reduced motion. Data only. `default` is built in (no emulation, empty storage) and cannot be redefined. A ticket may declare more in its own block (§15). |
| `uiPaths` | string[] | Globs, in `autonomy.riskPaths` syntax. A ticket whose Pointers or acceptance criteria name a matching path is a **UI ticket**. Under `--strict` a UI ticket must carry a browser check, or say `No browser check:` and why (§15). |

> **Judged at the warning tier, never the error tier.** The section gates no autonomy: an
> acceptance check can only hold a plan's close or file a follow-up. So a typo here never
> makes `delivery.json` broken (§2). Its consumers refuse a malformed one at the point of
> use, loudly: the readiness gate exits 2 naming the field, and the acceptance run reports
> *could not run* (§13).

---

## 2. The pipeline is optional

The kit is a template for **any** project. Most projects that adopt it will never run an
agentic pipeline, and for them the kit must behave exactly as it does today — no new
prompts, no new blocks, no new output.

### One discriminator

> **The pipeline is *configured* for a project if and only if `delivery.json` exists at
> the repo root.**

Nothing else decides it. No environment variable, no label, no settings flag, no
`enabled` field in the config, no "is the dispatcher installed" probe. One question,
asked one way, by every pipeline-scoped guard, skill and workflow. A second
discriminator is a second thing to desync.

**Moving the file aside does not turn the pipeline off.** The PreToolUse hook is what a
session would most want switched off, so it asks the same question more carefully: when
the working tree has no `delivery.json`, a copy committed at `HEAD`, or on the
default-branch refs the config is read from (§1), still counts. That is one local
`git cat-file`, with no network. Without it, a plain `mv delivery.json x` switched every
pipeline guard off, and `git checkout -- delivery.json` put the file back with nothing
in the diff (KIT-241). When only `HEAD` holds it (the adoption PR, moved aside), the hook
reads that copy.

### Three states — and *off* is not *broken*

| State | Condition | Behavior |
|---|---|---|
| **Off** | `delivery.json` absent | Every pipeline-scoped guard, skill and workflow **no-ops immediately**: exit 0, no output, no diagnostics, no network, no git — except the hook's one local read of whether a commit holds the file (above). Indistinguishable from a kit checkout without the pipeline at all. |
| **Configured** | `delivery.json` present and valid | Pipeline-scoped guards active per this contract. |
| **Broken** | `delivery.json` present but unreadable, unparseable, or failing the §7 validator — or `session_mode: ticket` with a missing, expired, or mismatched pin | **Fails closed.** Block with a reason naming the file and the fix, per the kit's fail-closed doctrine. |

**Conflating *off* with *broken* would brick every manual project.** A pipeline guard
that fails closed on "no config found" blocks every `Edit`/`Write` in an ordinary project
that simply never adopted the pipeline. And because the guard machinery is
self-protected, the agent cannot repair it — recovery needs a human at a terminal. That
is the exact failure the kit already learned once from hook bootstrap order
(`docs/LESSONS.md`): a fail-closed guard whose *precondition* is missing takes the
project hostage.

So the check order is fixed, and the existence test comes first:

1. **Does `<repo root>/delivery.json` exist?** No → **exit 0 immediately.** Before
   parsing anything, before resolving a pin, before shelling out to git or the network.
   Nothing that can fail may run ahead of this test. (The hook's one exception is the
   local check above for a committed copy; it fails to *off*, never to a block.)
2. Yes → parse and validate it. Failure here is **broken** → block with a reason.
3. Mode-specific checks (pin, ticket, budget) → block on failure.

**Absence is never an error; presence is a promise.** A project that has opted in has
accepted that a misconfiguration stops work — that is the point. A project that has not
opted in must never be able to reach step 2.

### Universal vs pipeline-scoped guards

**Universal — always on, every project, pipeline or not.** These are the kit's existing
guarantees and none of them may be made conditional on `delivery.json`:

| Guard | What it protects |
|---|---|
| Branch guard (+ branch naming, merged-PR) | No `Edit`/`Write`/`git commit` on `main`; `<type>/<slug>` naming; no commits onto a merged PR's branch |
| Never-merge | Merging is the human's action only, in every form including `--auto` |
| Secrets + destructive ops | No command naming a secret file, no embedded secret values, no `rm -rf`, no `curl \| sh`, no push to `main`, no bare `--force` |
| Cross-worktree | No writes into a worktree other than the acting session's |
| Self-protection | Hook scripts and `settings*.json` are human-only |
| Egress | No exfil-shaped network call to a non-allowlisted host |

**Pipeline-scoped — the six new ones.** Each no-ops entirely when the discriminator says
*off*. The names are reserved here so streams do not invent divergent ones; the
mechanics belong to the stream that implements each guard.

| Guard | Active when | Enforces |
|---|---|---|
| `pin-binding` | configured **and** `session_mode: ticket` | A valid, unexpired pin exists and its `worktree` matches the derived session root (§3) |
| `ticket-branch` | configured **and** `branch.requireTicketId` | The branch embeds the pinned ticket ID, lowercased (§1) |
| `scope-fence` | configured | Writes stay inside the pinned ticket's scope; a change touching `autonomy.riskPaths` forces human review |
| `lifecycle-label` | configured | The session does not set its own `agent:*` or `blocked:capacity` labels (§6). Mechanically enforced by the safe-outputs validator, which refuses dispatcher- and human-owned label keys (§8) |
| `self-approval` | configured | The session does not move a ticket `raw` → `ready`; only `epic/*` provenance auto-approves, and only out of session (§5). Mechanically enforced by the safe-outputs validator, which refuses `raw`, `ready` and `done` as transition targets however the caller is configured (§8). On a direct tracker write the hook matches `ready` by its ID, whatever the case of its letters; so a pinned or dispatched session sets a state **by ID only** (`linear.stateIds`), and a write setting one by name or by state type is refused — the guard cannot tell `Ready` from `ready` |
| `telemetry-required` | configured | A terminal run posts exactly one valid telemetry block before the turn ends (§4), carried as a `ticket-comment` request and counted by the safe-outputs validator (§8) |

---

## 3. Pin file

**The problem:** a session must be bound to exactly one ticket, and hooks and validators
must be able to check that binding. If the binding lives anywhere the session can write,
the session can rewrite it — retarget itself at a different ticket, widen its own scope,
or claim a budget it was not given.

**The rule:** the dispatcher writes the binding **outside the agent's worktree, before
the session starts**.

### Not valid pin transports

| Transport | Why not |
|---|---|
| PR body / PR title | The agent authors and edits them. |
| Ticket comments | The agent posts them. |
| Commit messages, trailers | The agent writes them. |
| Environment variables | Model-mutable inside the session; a `Bash` call can re-export anything. |
| Any file inside the worktree — `CLAUDE.md`, `.claude/**`, a dotfile, a scratch file | The agent's Edit/Write and shell reach all of it; the cross-worktree guard permits writes *inside* its own worktree by design. |
| The branch name | The agent chooses it. `branch.requireTicketId` is a consistency convention, not a trust anchor. |
| The session transcript | Agent-authored content. |

### Path convention

```
<pinsRoot>/<pin_key>.json
<pinsRoot>/ledger.jsonl        # append-only, one row per pin ever written
<pinsRoot>/dispatched-lane/<OS account>   # the OLDER lane marker (below); still counts
/etc/claude-pipeline/dispatched-lane/<OS account>   # the lane marker (below); root's

pin_key  = sha256(realpath(<session root>)).hexdigest()[:16]
pinsRoot = delivery.json → dispatch.pinsRoot   (default ~/.claude/pipeline/pins)
```

**`~` is the account's home, never `$HOME`.** A reader and a writer resolve `~` in
`pinsRoot` to the home the account database gives the OS account they run as. A
delegation-bound dispatcher loads a worktree's env file into the next session it starts
there (KIT-240), so a session could move `$HOME` for its successor and plant a pin under
it. A pin found there would bind nothing a dispatcher wrote. (On an account that carries
the lane marker no pin is read at all, below.) The OS account's name comes from the same
database, never `$USER` or `$LOGNAME`. A `pinsRoot` that is not absolute once `~` is read that way (a relative
path, `~name` for no such account) is refused: a relative one would be read from the
reader's cwd, which a session moves with `cd`.

**Nor is git's environment the session's.** A reader that asks git — whether
`delivery.json` is committed, what the committed copy says, which branch and worktrees
these are — runs it with none of the session's `GIT_*` variables and with no global
git config at all (`GIT_CONFIG_GLOBAL` is the null device, so a `$HOME` or
`$XDG_CONFIG_HOME` the session moved names nothing git reads). A `GIT_DIR`,
`GIT_OBJECT_DIRECTORY` or broken global git config planted in that env file would
otherwise answer from another repository, or fail every read, and a `delivery.json`
moved aside would then read as *off*. Not the account's own global config instead: a
dispatcher's sandbox denies reading home folders, and git stops on a config it is not
permitted to read.

`<session root>` is resolved **exactly as `.claude/hooks/pre-tool-use.py` resolves it**:
anchor on `CLAUDE_PROJECT_DIR` (falling back to the hook file's location), widened to
the hook process's cwd only when the payload carries `agent_id` *and* that cwd is a
sibling worktree of the same repo. Keying on the session root — not on a session ID —
lets a hook find its own pin with no handshake, and reuses the one anchor the kit
already establishes as not model-mutable.

### Shape

```json
{
  "pin_version": 1,
  "dispatch_id": "d_01JAV8Q2S6R7X0M4KDNP3YHTZ9",
  "session_mode": "ticket",
  "worktree": "/abs/path/to/worktree",
  "branch": "feat/eng-123-token-refresh",
  "base_branch": "main",
  "auth_mode": "api-key",
  "budget": { "maxTurns": 60, "maxUsd": 6.0, "maxMinutes": 45, "attempt": 1, "of": 3 },
  "ticket": {
    "id": "ENG-123",
    "team_key": "ENG",
    "url": "https://linear.app/<workspace>/issue/ENG-123",
    "state_id": "<uuid>",
    "effort": "M",
    "track": "track:platform",
    "provenance": "epic/ENG-100",
    "title": "Refresh tokens before expiry",
    "acceptance_criteria": ["...", "..."],
    "out_of_scope": ["...", "..."],
    "snapshot_at": "2026-08-24T15:04:05Z"
  },
  "subject": null,
  "pinned_at": "2026-08-24T15:04:05Z",
  "pinned_by": "dispatcher:runner-01",
  "expires_at": "2026-08-24T17:04:05Z"
}
```

| Field | Type | Notes |
|---|---|---|
| `pin_version` | integer | `1`. Unrecognized → reader refuses, does not guess. |
| `dispatch_id` | string | Opaque, unique per dispatch. Joins the pin to `runs.run_id` and to the ledger. |
| `session_mode` | enum `ticket\|planning\|diagnosis\|maintenance` | What this session is allowed to be. |
| `worktree` | string (abs path) | The worktree this pin governs. A reader MUST verify it matches the session root it derived; a mismatch is a hard stop, not a warning. |
| `branch`, `base_branch` | string | Expected branch and PR base. |
| `auth_mode` | `subscription\|api-key` | Copied from `auth` for the lane that dispatched. |
| `budget` | object | The resolved caps for this run — already clamped against `budgets.maxTurns`. `attempt`/`of` carry the `totalAttempts` position. |
| `ticket` | object \| null | Required when `session_mode` is `ticket`; may be null otherwise. |
| `ticket.acceptance_criteria` | string[] | The definition of done, snapshotted at dispatch. |
| `ticket.out_of_scope` | string[] | Explicit non-goals — the scope fence. |
| `ticket.snapshot_at` | ISO-8601 UTC | When the ticket was read. The pin is a **snapshot**: later Linear edits do not reach a running session. |
| `subject` | string \| null | Free-text subject for non-ticket modes. Null for `ticket` mode. |
| `pinned_at`, `pinned_by` | ISO-8601 UTC, string | Provenance of the pin itself. |
| `expires_at` | ISO-8601 UTC | Past it, the pin is stale; readers treat it as absent and a sweeper deletes it. |

### Ticket → pin field mapping

The shape above pins `ticket.acceptance_criteria` and `ticket.out_of_scope` as string
arrays but says nothing about how a *ticket* expresses them. It is one convention, and
this is it.

**Everything else comes from Linear's structured fields, not from prose.** `id`,
`team_key`, `url`, `state_id` and `title` are read from the issue; `effort` and `track`
from labels; `provenance` from the `provenance:*` label class plus the parent link
(§5 rule 4). Only these two lists are parsed out of the description, because Linear has
nowhere else to put them.

**Both are read from level-2 markdown headings in the issue description:**

```markdown
## Acceptance criteria

- [ ] A token within 5 minutes of expiry is refreshed before the request is sent
- [ ] `npm test` covers the near-expiry path and passes

## Out of scope

- Refresh-token rotation and reuse detection (separate ticket)
```

The parsing rule, which every reader of a ticket description MUST share:

| Rule | Behavior |
|---|---|
| Heading level | Exactly `## `. A `#` or `###` line does not open a section. |
| Heading match | Compared case-insensitively after trimming. `Out of scope` matches in full: `## Out of scope (draft)` is a different section and is ignored. `Acceptance criteria`: the exact heading wins; with none, the first heading that starts with the words `Acceptance criteria` is read, such as `## Acceptance criteria (draft)` or `## Acceptance criteria:`, never `## Acceptance criteriaX` (KIT-441). Two such headings and no exact one is a DoR error. |
| Fenced blocks | A ```` ``` ```` or `~~~` fence suspends heading detection. A `## Acceptance criteria` line *inside* a code fence is sample text, not a section. |
| Section end | The next `## ` heading outside a fence. |
| Items | Lines matching `- ` or `* ` at the start (after optional indent). Acceptance criteria are task-list items (`- [ ] …` / `- [x] …`) and **the `[ ]` / `[x]` marker is stripped** — it is list syntax, not part of the criterion. Out-of-scope items are plain bullets. |
| Continuation lines | Not joined. A wrapped item keeps only its first line, so **each item must fit on one line.** |
| Repeated sections | Each section appears exactly once. A second occurrence is a malformed ticket, not more items. |
| Every other `## ` section | **Ignored entirely.** `Context`, `Test plan` and `Pointers` are for the human reading the ticket and for the brief's verbatim description block; nothing in them reaches a pin field. |

**A missing or empty section yields an empty list — never an inferred one.** The
dispatcher does not guess a definition of done from the description prose, and it does
not silently substitute one. An empty `out_of_scope` means the ticket shipped without a
scope fence; an empty `acceptance_criteria` means it shipped without a definition of
done. Both are Definition-of-Ready failures that `scripts/check_ticket_dor.py` is
supposed to have caught at approval, and the session is told plainly that the list was
empty rather than handed something invented.

> **One description, one parser.** `scripts/check_ticket_dor.py` (the gate that admits
> a ticket) and the dispatcher (which snapshots it into a pin) read the same five
> sections of the same text. The table above is the DoR gate's rule, and it is
> canonical **because the gate is what certifies the ticket parses at all** — a
> dispatcher that reads the text differently from the gate that approved it can pin a
> criteria list no human ever reviewed. Any second reader that diverges is the
> "second shape for the same structure" failure, not a local convenience.

### Write protocol (dispatcher)

1. Create the worktree and branch.
2. Write the pin to a temp file in `pinsRoot`, `fsync`, `chmod 0444`, then **atomically
   `rename()`** into `<pin_key>.json` — a reader never sees a half-written pin.
3. Append one row to `ledger.jsonl`.
4. Spawn the session with cwd = worktree.
5. On session end, the **dispatcher** deletes the pin. The agent never does.

### Read protocol (hooks, validators)

1. Confirm the pipeline is configured (§2). Not configured → **exit 0, do nothing.**
2. Read the **lane marker** (below). With one, stop here: no pin is read, and the lane
   rules apply.
3. Derive the session root; compute `pin_key`; read the pin.
4. Verify `pin_version`, `expires_at`, and `worktree` == derived session root.
5. **Absence of a pin never grants autonomy.** In `ticket` mode a missing pin is
   *broken* and fails closed. With no pin and no marker, checks that would *withhold*
   something fail open (a human's ad-hoc session in a configured repo must not be
   bricked) and checks that would *grant* extra autonomy fail closed.

> **Tamper-evident, not tamper-proof.** The session's shell runs as the same user, so
> `0444` and a path outside the repo raise the cost of tampering — they do not make it
> impossible. The ledger makes divergence detectable, and the real guarantee is the one
> the rest of the kit already rests on: nothing lands without a reviewed PR and CI. The
> pin's job is to make the honest path deterministic and the dishonest path visible.

### The lane marker (a dispatcher that writes no pin)

A dispatcher that binds a session by a person's delegation writes no pin, so without
more its sessions would read as a human's ad-hoc ones. Its OS account carries a marker
instead: `/etc/claude-pipeline/dispatched-lane/<OS account>`, owned by root, in folders
root owns all the way down to `/`, written once by a person (the Stage E installer's
card CK-10, measured by its `lane-marker` row, which checks every one of those folders).
A session cannot create, delete or move it.

The marker used to live at `<pinsRoot>/dispatched-lane/<OS account>`, and that place
still counts: **either one makes the lane.** It is not enough alone, because the role
account created the pins root and can rename the folders under it, which takes the
marker out of sight; it cannot rename root's `/etc`. With the system marker in place,
removing the old one gains nothing **for a reader that knows the system marker.** A project
made from the kit carries its own copy of the hook, and a copy older than the system marker
reads only the old one. While a served repository's committed hook is such a copy, the old
marker under that repository's pins root is still required; the installer's `lane-marker`
row reads each served hook and stops on CK-10 while one has no marker it reads.

**The marker wins over a pin.** No dispatcher writes a pin for an account that carries
the marker, so a pin found for one was forged — the pins root is that account's to write
whenever its sandbox lets a write leave the worktree. A reader that finds the marker does
not read the pin at all: not as a binding, and not as a broken one.

A reader looks for each place with `lstat`, so the entry counts, not what it points at:

- **No such file** (`ENOENT`, or `ENOTDIR` on the way) in both places: not the lane.
  Read the pin (step 3 above).
- **There**, in either place: the dispatcher's lane. Tracker writes get the pinned
  `ticket` rules: no protected label, no direct create, no state set by anything but its
  ID, no rewrite of the own ticket's acceptance criteria (a partial `patch` included),
  and writes only to the session's **own ticket** — the one its worktree folder is named
  after (`<base>/<ISSUE-ID>`), or the folder above it (`<base>/<ISSUE-ID>/<repo>`). Any
  team's key counts. The folder is the session's root, never a subagent's widened one.
  When neither folder names a ticket, an issue write and a write naming any ticket fail
  closed; a comment with no resolvable target stays allowed (§4 reporting).
- **Could not check** — any other error in either place, or an account the database
  cannot name: the lane. Reading "could not look" as "a person's session" would let an
  error open every guard the marker turns on.

---

## 4. Telemetry block

A session reports itself by posting **one fenced JSON block** as a ticket comment.

> **The session does not post it directly.** It has no tracker credential; the comment
> is *requested* as a `ticket-comment` in the safe-outputs file (§8) and posted by the
> validator job. §4 is the block's shape; §8 is how it travels.

````markdown
```json
{
  "schema": "pipeline-telemetry/1",
  "runs": [ { "...": "one row" } ],
  "ticket_events": [ { "...": "zero or more rows" } ]
}
```
````

- The fence info string is plain `json` so it renders everywhere; the **`schema` key is
  the marker**. A collector scans every `json` fence in a comment and keeps the objects
  carrying `"schema": "pipeline-telemetry/1"`.
- **One telemetry block per comment.** Rows are idempotent on `run_id` — re-posting the
  same block must not double-count.
- All timestamps are **ISO-8601 UTC with `Z`**. All counters are non-negative integers;
  `cost_usd` is a number with up to 4 decimal places.
- **Agent-authored ⇒ reporting only.** Never gate a budget, an approval, or a merge on a
  value from this block.

### `runs` row

| Field | Type | Notes |
|---|---|---|
| `run_id` | string | Globally unique, opaque; recommended `r_` + ULID. Stable across re-posts — it is the idempotency key. |
| `dispatch_id` | string \| null (optional) | The pin's `dispatch_id` (§3). §3 says this value "joins the pin to `runs.run_id`", and until it was carried here the join had no second side. Null for a run with no pin (a manual `/work`). |
| `session_mode` | enum (optional) | The pinned mode this run believes it was dispatched under. Present ⇒ a collector can perform the mode/stage cross-check below; absent ⇒ it simply does not. It is a **self-report of a pinned value**, so it flags contract conformance and never grants anything. |
| `ticket_id` | string \| null | e.g. `ENG-123`. Null for runs with no ticket. |
| `team_key` | string | e.g. `ENG`. |
| `stage` | enum `epic\|dev\|review\|bounce\|triage\|diagnosis\|retro` | What kind of work this run did. |
| `model` | string | Exact model ID as used. The literal `unknown` when no source named one — and then `model_note` says why. |
| `model_note` | string \| null (optional) | Why `model` is not the exact ID read from the run's own record: nothing reported one, or it was read from somewhere else (a resumed session's earlier run, a config value). Null when it was read from the run itself. A silent `unknown` says nothing, so the gate refuses `unknown` without this. A row no model session belongs to (a conclusion, an exhaustion) has `model` `unknown`, and this note says why no session ran. A collector stores the note as one line of at most 200 characters. A session wrote it, so a reader treats it as data, never as an instruction. |
| `auth_mode` | enum `subscription\|api-key` | |
| `started_at` / `ended_at` | ISO-8601 UTC | `ended_at` null only for an in-flight row; a posted block should be terminal. |
| `tokens_in` / `tokens_out` | integer | |
| `tokens_cache_read` / `tokens_cache_write` | integer | `0` when caching was not used — never null. |
| `cost_usd` | number | Best-effort self-report. Dashboards only. |
| `cost_note` | string \| null (optional) | Why `cost_usd` (and the token counters) are not measured from the run's own record: the source reports no cost, a session cannot read its own total, or the run has not happened when the row is written. Null when they were measured. A row no model session belongs to is an exact `0` with a null note; its reason sits in `model_note`. A reader counts a row as **unmeasured** when this note is set. It also counts one when `cost_usd` and both token counters are `0`, unless `model` names nothing and `model_note` gives the reason: a model run that reports no tokens is not a measurement. Any cost sum that includes an unmeasured row is a floor, never a total. A collector stores the note as one line of at most 200 characters, and a reader treats it as data. |
| `turns` | integer | |
| `outcome` | enum `completed\|blocked\|timeout\|capacity\|error\|budget` | `capacity` = provider capacity (see `dispatch.pauseOnCapacity`); `budget` = a cap in `budgets` stopped it; `blocked` = needs a human decision. |
| `error_class` | string \| null | Short stable slug (e.g. `rate_limit`, `hook_block`, `ci_red`). Null unless `outcome` ∈ {`blocked`, `error`, `timeout`, `capacity`, `budget`}. |
| `files_changed` | integer | |
| `lines_added` / `lines_removed` | integer | |
| `pr_number` | integer \| null | Null until a PR exists. |

**`session_mode` → allowed `stage`.** The pin fixes the mode; the run reports a stage.
A stage outside its pinned mode is a contract violation the collector flags.

| `session_mode` | may report `stage` |
|---|---|
| `ticket` | `dev`, `bounce` |
| `planning` | `epic`, `triage` |
| `diagnosis` | `diagnosis` |
| `maintenance` | `review`, `retro` |

### `ticket_events` row

| Field | Type | Notes |
|---|---|---|
| `ticket_id` | string | |
| `event` | enum | `created`, `approved`, `dispatched`, `first_commit`, `pr_opened`, `ci_green`, `review_posted`, `bounce_started`, `merged`, `deployed`, `reverted` |
| `at` | ISO-8601 UTC | |
| `actor` | enum `human\|agent\|system` | `system` = CI, a webhook, a cron. `merged` is always `human` or `system` — never `agent`. |

Events are append-only facts. The same `(ticket_id, event, at)` posted twice is one
event; a genuinely repeated event (a second `bounce_started`) carries a distinct `at`.

### `review_findings` row (optional array)

A `review`-stage run may carry a third array, so what the reviewer found is queryable
alongside what the run cost. Every field mirrors the `pipeline-review/1` finding shape
the review lane already publishes (§14 — the review workflow on `github-actions`, the
local publisher on a dispatcher-side lane), so there is one finding shape in the system
and not two.

| Field | Type | Notes |
|---|---|---|
| `severity` | enum `low\|medium\|high\|critical` | Required. |
| `category` | string | `correctness`, `security`, `tests`, `scope`. Free-form beyond those, and grouped as-is by dashboards. |
| `file` / `line` | string \| null, integer \| null | Where, when the reviewer could say. |
| `summary` | string | One sentence. |
| `pr_number` | integer \| null | Defaults to the PR named by the block's `runs` row. |
| `at` | ISO-8601 UTC \| null | Defaults to the comment's own timestamp. |

**Findings carry no ID.** A collector keys them on a digest of their own content
(§10), so a re-posted block collapses onto one row while two genuinely different
findings on the same line stay two.

> **Why this lives in telemetry rather than only in the review artifact.** The
> artifact is per-run and expires with the Actions retention window; "which category
> of finding keeps recurring" is a question about *many* runs over *months*, and it is
> the question that turns review output into a rubric change. An answer that expires
> in 90 days cannot be the input to a quarterly habit.

---

## 5. Provenance values

Every ticket carries exactly one provenance value — where the work came from.

| Value | Meaning | May auto-approve `raw` → `ready`? |
|---|---|---|
| `epic/<ID>` | Decomposed from an epic a human already approved, e.g. `epic/ENG-100` | **Yes — the only one** |
| `monitor` | Filed by a standing monitor (uptime, drift, cron health, PR conflict) | No |
| `review` | Raised by an automated review pass | No |
| `retro-proposal` | Proposed by a retrospective run | No |
| `human` | A person wrote it | No — a human already decided; it enters `ready` directly |
| `agent` | Filed by a working session as a follow-up finding it met (§8 `ticket-create`) | No — a session filing its own next work is the loop §11 bars |

Rules:

1. **Only `epic/*` may ever auto-approve.** Everything else waits in `raw` for a person.
   The agent-facing consequence: an agent cannot widen its own mandate by filing tickets
   for itself, because nothing it files carries `epic/*` provenance.
2. Auto-approval additionally requires that the referenced epic **exists and sits in the
   human-approval state `ready`** — the same state its children are released into, reached
   by a person's deliberate move. Any *other* out-of-intake state (`working`, `review`,
   `done`) is **not** approval: a human parking an epic in `working` ("I'm looking at
   this"), or a Linear board automation advancing it, must not release the child tree.
   Without the existence check, `epic/<anything>` is a self-serve approval — a fabricated
   ID would mint autonomy; without the *state* being specific, any forward nudge would.
   **The move is checked by actor, not by state alone (KIT-161).** The caller reads who moved
   the epic into `ready` from the tracker's history and supplies it as the epic's `readyBy`.
   The epic approves its tree only when that id is on `autonomy.epicApprovers`: people, never
   the dispatcher's agent user or a machine account's key. No list, no `readyBy`, or an id not
   on the list is *not approved*, and says which (§13).
   (The PreToolUse approval guard denies a session writing `ready`; this reads it. A
   session forging the signal by writing some *other* state buys nothing, because no
   other state approves — see `docs/adr/2026-09-06-approve-tier-epic-approval-state.md`.)
3. `autonomy.autoApproveProvenance` must be a subset of `["epic"]`. A validator hard-fails
   any other value, so the rule is mechanically checked and not merely documented.
4. **Two representations, one value.** Linear labels are a fixed vocabulary and cannot
   carry a per-epic ID, so the label records the *class*
   (`provenance:epic`, `provenance:monitor`, …) while the full value including the epic
   ID lives in the ticket's parent link and in `pin.ticket.provenance`. Guards match the
   full value; the label exists for humans filtering a board.

---

## 6. Label taxonomy

Canonical keys — the keys of `linear.labels.ids`. Guards resolve a key to its ID and
compare IDs; nothing compares display text.

| Key | Set by | Meaning |
|---|---|---|
| `track:*` | human / epic | Workstream routing, e.g. `track:platform`. Open-ended: one row per track. At least one must exist. |
| `effort:S` \| `effort:M` \| `effort:L` | human at approval (agent may propose) | Selects `budgets.perEffort`. Exactly one per ticket. |
| `agent:queued` | dispatcher | Ready to dispatch. Default `dispatch.labelTrigger`. |
| `agent:working` | dispatcher | A session holds it; counts against `wipLimit`. |
| `agent:blocked` | dispatcher | Stopped on something external. |
| `agent:needs-human` | dispatcher | `maxBounces` or `totalAttempts` exhausted, or a `riskPaths` change. Terminal until a person acts. **Also: a bounce that was delivered and never answered with a push.** The Stage E bounce driver applies it when a `delivered` ledger row exists for the last bounce, the pull request's head is still the commit that bounce was delivered against, and more than `blocked_after_seconds` (default 21600) have passed — once per bounce, alongside one top-level notice on the coding ticket (the §4 telemetry row is posted there too, as for every driver action), spending no bounce and moving the ticket nowhere. The notice opens with the `fix-not-pushed` mark, which the notifier pages from `bounce_actor_ids` alone (KIT-336). Without it a session that stops mid-bounce is the pipeline's quietest state: the trigger is gone until a re-review, a re-review needs a push, and only a *spent* budget ever called a person. The condition reads the ledger, the head and the clock and never the session's reply, because a reply is a string the counted party writes. |
| `agent:blocked` \| `agent:needs-human` | the **human-action notifier**, on a pin-less dispatcher | Same two labels, same rule, one more writer. A dispatcher that binds by delegation writes no pin and applies no lifecycle label, so a session's escalation is *requested* and nothing ever grants it — a blocked ticket reads as any other in-progress one, and the tracker's own view routing never fires. `scripts/pipeline_notify_local.py` is the dispatcher-side component that applies it, from a mark the session only ever asks for. A session asks for it with the `needs-human-session` mark (the older `agent:needs-human` mark is still read and paged, in general words); the notifier also reads `needs-human-session` (KIT-282), which the session brief tells a session to write (KIT-419), once every notifier that reads its sessions knows it. The planner job's `needs-human-routing` and the bounce driver's `fixes-spent` ask for the same label, and each names its cause only from its own key's author ids: from anyone else it still pages and still gets the label, in `agent:needs-human`'s general words. The notifier ADR's marks table has every mark. The §6 invariant is unchanged: the session still never applies its own supervision label. Its label ids come from the notifier's own config and must be re-resolved whenever `delivery.json`'s are (§1's delete-and-recreate case), or the two records drift silently. |
| `blocked:capacity` | dispatcher | Provider capacity, paired with `agent:blocked`. Cleared on retry; does **not** consume an attempt. |
| `provenance:epic` \| `provenance:monitor` \| `provenance:review` \| `provenance:retro-proposal` \| `provenance:human` | dispatcher / human | Origin class (§5). Exactly one per ticket. |
| `provenance:agent` | the safe-outputs **executor** (§8) | Origin class (§5): a working session filed this as a finding. Applied by the executor on `ticket-create`, never by the session — `provenance:*` is refused in every session-facing label field. Never auto-approves. |
| `hooks-change` | human | The change touches guard machinery. |
| `meta` | human | The pipeline working on itself. Excluded from throughput metrics so pipeline overhead never reads as delivery. |

**Every label in that table is WORKSPACE-scoped — created with `teamId` omitted.** Not
just the machinery (`agent:*`, `provenance:*`, `blocked:capacity`, `hooks-change`): the
project taxonomy (`track:*`, `effort:*`) too. One workspace serves many teams sharing
one taxonomy, and a team-scoped label cannot be applied to another team's tickets, so
the second team onboarded is where the wrong choice surfaces.

**Scope cannot be converted after creation.** There is no rescope; the fix is delete and
recreate, which mints new IDs and strands every one `delivery.json` had recorded — the
labels look fine on the board while the config points at nothing. That is a read-path
failure §1 now requires each read to detect, not a reason to relax it. Re-run the
resolution step (`/setup-board`) after any delete-and-recreate.

**`agent:*` and `blocked:capacity` are dispatcher-owned.** A session must not set its own
lifecycle labels — self-labelling `agent:needs-human` or clearing `agent:blocked` is a
session editing its own supervision.

**`hooks-change` exists in two systems and the names must match exactly.** In GitHub it is
the label the *Hooks change guard* CI job requires on any PR touching
`.claude/hooks/**` or `.claude/settings*.json`; the Linear label mirrors it so a ticket
is marked before the PR is opened. A ticket whose change touches those paths carries it
in both places — and the GitHub label must exist in the repo before the job's first run
on a guarded PR (`gh label create hooks-change …`).

---

## 7. Validator checklist

Runs only when the pipeline is configured (§2). A `delivery.json` validator MUST fail on:

- [ ] `version` unrecognized
- [ ] Any `linear.stateIds.*` empty or still a `{{…}}` token
- [ ] `linear.labels.required` missing any of `agent:queued`, `agent:working`, `agent:blocked`, `agent:needs-human`, `blocked:capacity` — the §6 dispatcher-owned floor; membership is what makes a stale ID for a hold **fail** a read rather than warn
- [ ] Any key in `linear.labels.required` missing from `linear.labels.ids`, or resolving to `""`
- [ ] No `track:*` key present in `linear.labels.ids`
- [ ] `branch.types` containing a type the live branch guard rejects
- [ ] Any `commands.*` set to `""` (use `null`)
- [ ] `perEffort[e].maxTurns > budgets.maxTurns` for any `e` (a per-effort value may only lower the cap)
- [ ] `budgets.reviewSeverityThreshold` outside `low|medium|high|critical`
- [ ] `budgets.fixIterations` missing, or less than `1`
- [ ] `dispatch.statePath` non-null and resolving inside the repo or inside any worktree
- [ ] `dispatch.statePath` null for a `dispatch.backend` that has no durable store of its own (anything other than `github-actions`) — the attempt counter would have nowhere to live (§9)
- [ ] Any `auth.*` outside `subscription|api-key`
- [ ] `autonomy.autoApproveProvenance` not a subset of `["epic"]`
- [ ] `autonomy.riskPaths` missing `.claude/hooks/**`, `.claude/settings*.json`, or `delivery.json`
- [ ] `dispatch.pinsRoot` resolving inside the repo or inside any worktree

A validator MUST NOT fail — or emit anything at all — when `delivery.json` is absent.
That is *off*, not *broken*.

**Implemented by `scripts/check_delivery_config.py`** (`npm run test:delivery` for its
selftest; both run in CI's *Kit checks*). Every row above is a case in that selftest,
checked against synthetic configs — the kit ships no live `delivery.json`, so the
validator is inert here and the selftest is what has teeth. Amending a row means adding
the case in the same PR.

**It runs in two layers, and the split is the point (§12).** SHAPE comes from
`schemas/delivery.schema.json` — the validator no longer re-implements "`stateIds` is an
object with exactly five string values" in Python, and the schema's own `x-rule` /
`x-tier` / `x-fix` annotations carry each violation's rule name, tier and remediation
prose. SEMANTICS stay in the script, because no schema keyword reaches them: whether an
ID is *resolved* rather than merely a string, whether a path lands outside every worktree
**on this disk**, `perEffort[e].maxTurns` against `budgets.maxTurns`, `statePath` against
`backend`, the exact `riskPaths` floor, and `branch.types` against the **live** guard's
own regex. A schema-valid config can still be wrong, so neither layer replaces the other.

---

## 8. Safe-outputs request file

**The problem:** a session must be able to comment on its ticket and move it to review.
Hand it a tracker credential and every "the agent must not …" rule in this document
degrades to a prompt instruction — an agent holding the API key can call the GraphQL
endpoint directly and no wording prevents it.

**The rule:** the session holds **no tracker credential at all.** It writes structured
*write-requests* to a file; a separate job that does hold the credential validates them
against the **dispatcher-supplied** pinned ticket ID and executes only the survivors.
The constraint stops being a request and becomes structural: no key in the job, no
write path.

### Shape

```json
{
  "schema": "pipeline-safe-outputs/1",
  "requests": [
    { "type": "ticket-comment", "ticket_id": "ENG-123", "body": "…markdown…" },
    { "type": "ticket-state",   "ticket_id": "ENG-123", "to": "review" },
    { "type": "ticket-label",   "ticket_id": "ENG-123", "add": ["needs-design"], "remove": [] },
    { "type": "ticket-create",  "source_ticket_id": "ENG-123", "title": "…one line…", "body": "…markdown…", "labels": ["track:infra"] }
  ]
}
```

| Type | Fields | Notes |
|---|---|---|
| `ticket-comment` | `ticket_id`, `body` | `body` is non-empty markdown. The telemetry block (§4) travels as one of these. |
| `ticket-state` | `ticket_id`, `to` | `to` is a **canonical state key** from `linear.stateIds` — only `working` or `review`, never a UUID and never a display name. `raw`, `ready`, `done` and `needsApproval` are all refused: the last of those is the conclusion of the review, and a session reporting it would be issuing a verdict on its own work (§5). |
| `ticket-label` | `ticket_id`, `add[]`, `remove[]` | Canonical label keys from `linear.labels.ids` (§6). Either list may be empty. |
| `ticket-create` (finding) | `source_ticket_id`, `title`, `body`, `labels[]` | **Files a follow-up finding ticket.** `source_ticket_id` is the session's OWN pinned ID (compared to the pin, never used to address anything); `title` is one line; `body` is non-empty markdown; `labels[]` is optional. The new ticket's identity, state, provenance and assignee are the **executor's** to set, never the session's — see *Filing a finding* below. Available only when `linear.findingTicket` is configured. |
| `ticket-create` (plan) | `source_ticket_id`, `epic`, `children[]` | **Proposes an epic tree** (`epic` = `{title, body}`; each `children[]` entry = `{title, body, labels[]?, depends_on[]?}`). Same `type` as the finding, discriminated by carrying `epic`+`children` rather than `title`+`body`. The executor files the epic and every child in the backlog, forcing every authority field, and turns `depends_on` ordinals into blockedBy relations — see *Filing a plan* below. This is the idea-gate's output: a planning session holds no tracker tool, so a proposed tree is the only way it reaches the board. |

An unrecognized `schema`, an unrecognized `type`, or `requests` that is not a list
rejects the batch. A reader that does not recognize the schema refuses; it does not
guess.

### Filing a finding — the one kind that creates a ticket, and how it stays safe

A session that meets a real bug outside its ticket used to have nowhere durable to put
it: the PR body dies at merge, and a session filing its own ticket is the *agent
producing a human's signal* defect this document is built against (§5). `ticket-create`
resolves that without reopening it, by the same move every other write here makes — **the
session requests, a credential-holding executor decides.** The session never holds the
create: it names its own pinned ticket as `source_ticket_id` and writes a `title` and
`body`; the executor creates the new ticket and **forces every field that could carry
authority**:

- **Lands in the backlog only.** `linear.findingTicket.landing` — restricted to `raw`
  (intake). Never `ready` (that would be self-approval, §5), never `working`/`review`/`done`.
- **Carries `provenance:agent`** (§5), applied by the executor. The session cannot request
  a provenance label — `labelList` excludes the whole `provenance:*` class — so it cannot
  mint `provenance:human` or any other origin. `provenance:agent` never auto-approves (§5,
  §11): a filed finding waits in the backlog for a person exactly like `monitor` and
  `review` do.
- **Notifies the owner, never the session.** `linear.findingTicket.notify` subscribes
  and/or assigns `linear.findingTicket.ownerUserId`. The new ticket is **never** assigned
  to the session's own identity.
- **Records where it came from.** The executor prepends a provenance line naming the
  filing session's `source_ticket_id` to the body, so the finding always carries its origin.
- **A finding cannot authorise the session's own next step.** A new backlog ticket moves
  nothing; it starts no session (it is not `ready`, and `provenance:agent` bars
  auto-approval). "File a ticket asking to approve my PR" produces a backlog item a human
  must still act on, which is the whole point.
- **The three-per-run cap is a hard limit, and the session must be told how to live
  within it.** A fourth `ticket-create` fails the batch, and because §8 is all-or-nothing
  that failure also drops the run's telemetry and its state move — a silent, confusing
  loss for a session that did not know the cap was there. So the session-facing guidance
  (the work skill, the kit's `CLAUDE.md`) tells a session to file the three most important
  findings and report any others in its **summary comment**, where a person triages them.
  The cap is a flood guard, not a limit to raise: many findings at once is itself a signal
  a person should look, and the graceful path is the comment, never a fourth request.
  **On a local-daemon backend the cap is per source ticket, across passes** (KIT-234):
  `pipeline_finding_poller.py` counts what it already filed from a ticket, declines a
  fourth request for good, and says so once on that ticket. It cannot fail a batch, since
  each finding is its own comment.

**The kind is off unless a project turns it on.** Absent `linear.findingTicket`, a
`ticket-create` request is refused and a session reports findings as `ticket-comment`s as
before (§5b in the kit's `CLAUDE.md`, the work skill). And it rides whatever executor holds
the credential: the GitHub-Actions safe-outputs job here, or a local-daemon backend's
equivalent once one ships. No executor for a backend ⇒ the kind is unavailable there ⇒
findings stay comments. A session holding a *direct* tracker credential (a local-daemon
MCP session) must still never create a ticket itself; that path is closed by the guard
(§8's companion in `.claude/hooks/pre-tool-use.py`), not by this file.

### Filing a plan — the tree shape of `ticket-create`

A **planning session** (the idea gate: the owner moves an idea to Plan it on the project's
own work team, and a planner job files a clean planning ticket on that team and delegates it;
the dispatcher runs it sandboxed) does not file findings — it proposes a whole **epic tree**.
It holds no tracker tool and no tool that writes a file — its entry's deny list removes
both — so it cannot write to the board or to its own supervision; its only output is a
proposed tree, in its final message, which a credential-holding executor validates and
materialises. The executor neutralizes every dispatcher routing directive (`[repo=…]`,
`repo=`, `[model=…]`, `[agent=…]`) in every title and body the session wrote before the
readiness gate reads them, because a filed child is text a person may later delegate. A
directive spelt with a backslash escape or a character reference counts too: Linear decodes
those when it stores the text (`scripts/pipeline_routing_tags.py`). The
tree is the *same* `ticket-create` type, carrying `epic` and `children` instead of a flat
`title`/`body`:

```json
{
  "schema": "pipeline-safe-outputs/1",
  "requests": [
    {
      "type": "ticket-create",
      "source_ticket_id": "KIT-777",
      "epic": { "title": "…one line…", "body": "…the PRD/epic markdown…" },
      "children": [
        { "title": "child A — the prerequisite", "body": "…## Acceptance criteria …",
          "labels": ["track:meta", "effort:M"] },
        { "title": "child B — depends on A", "body": "…",
          "labels": ["track:meta", "effort:S"], "depends_on": [0] }
      ]
    }
  ]
}
```

`depends_on` holds **0-indexed ordinals into this request's `children` array** (child B at
index 1 depends on child A at index 0); the executor turns them into Linear `blockedBy`
relations. Every finding-shape guarantee above is **kept verbatim** — `source_ticket_id`
equals the pinned ID, the session sets no authority field, nothing lands outside the
backlog — and the tree shape adds these:

- **The epic carries `provenance:agent`.** An epic a session drafted is agent-authored, and
  by §5 rule 1 `provenance:agent` never auto-approves, so the epic cannot approve itself; it
  waits for the human to move it to exactly `ready` (§5 rule 2 — any other out-of-intake
  state releases nothing). A human-written epic (manual
  `/plan-epic`) stays `provenance:human`; an idea-triggered one is `provenance:agent`.
- **The executor sets each child's parent**, forced to the epic it created in this same
  batch. The session cannot supply a parent id — which would otherwise let it name an
  *already-approved* epic and have its children auto-approve. Each child carries
  `provenance:epic` pointing at that just-created, unapproved epic (§5 rule 4).
- **The executor runs the Definition-of-Ready gate** (`check_ticket_dor.py --strict`) on
  every proposed child and **refuses the whole tree if any child fails** — a partial epic
  looks decomposed and is not. Nothing is created; the executor reports each failing child
  and its errors back on the idea ticket for a re-plan.
- **The per-run cap is one epic and a bounded number of children** (`MAX_PLAN_CHILDREN`, an
  executor constant like the flat kind's cap-of-3 — not a `delivery.json` budget), still
  all-or-nothing: one malformed or DoR-failing child rejects the whole tree.
- **The executor posts a summary comment** on the idea ticket so the owner can read the plan
  and approve the epic — the one human gate that releases the tree. Approval makes children
  *eligible*; each reaches `ready` only through the approve tier (§11, which re-runs the
  gate and is off unless a project switches it on) or a person, and starts only when
  delegated.
- **It compares the proposed children with the work team's recent tickets** and lists what
  looks alike in its summary, deterministically and by title — the rubric pass the fence
  took away from the planner. It never refuses a plan for a likeness: that judgement
  belongs to the person approving the epic. Nothing alike, and a lookup that failed, are
  both said out loud, because an absent section would read as "nothing looked alike" (§13).
- **It is safe to retry, and never silent about a report it could not post** (KIT-170).
  The epic carries a plain-text receipt, `Plan id: <pinned>/<digest>`, over the pinned
  ticket and the proposal as filed. Before creating anything the executor asks the work
  team for that receipt; a second run on the same proposal files no second tree. When the
  tree is whole, it makes what an earlier attempt left out (KIT-393): each blocked-by
  relation the plan's `depends_on` needs and the tracker lacks, then the summary if none for
  that receipt reached the ticket, then each note not already there, each judged against what
  the tracker holds so nothing is made or posted twice. A filed child is matched to the plan by the place the
  executor wrote in its opening lines ("child 2 of 5 in its plan"), else by a unique title. One
  that cannot be matched is never linked on a guess: the summary and notes are still posted, the
  run ends `filed`, and its reasons say the links could not be checked. A link that fails to be
  made is `errored`. A search that fails is
  `errored`, never "nothing there". A run that fails partway posts exactly what it created.
  A rejection or no-output note that cannot be posted is `errored`, so the caller retries.
- **A retry reuses the project an earlier attempt made** (KIT-395). The project's description
  carries the same plan id. Before making one the executor looks for a project of that name
  holding the receipt and files into it; a search that fails is `errored`, never "none there".
  A newer answer has a new plan id, so it starts over with a project of its own.
- **A pointer a sibling creates is excused, and only that** (KIT-319). The executor tells the
  readiness gate, for each child, the paths the children it depends on (directly or through
  others) name in backticks in their Context or Acceptance criteria (`sibling_paths`, at most
  100, relative and inside the repo). The gate excuses a missing Pointers path only if it is
  exactly one of them. A path nobody names, a typo, and a path a
  sibling only points at (under its own Pointers) are still refused.
- **Session text is scanned for credential shapes before anything is filed or quoted**,
  with the review publisher's own scan (`pipeline_review_local.secret_hits`). A hit rejects
  the whole batch with a fixed reason and quotes nothing. A §4 telemetry block in a
  planning batch is set aside and never delivered as a question.
- **Where its inputs come from.** The executor's only config is the **planned repository's
  committed `delivery.json`**: the work team the tree is filed into (`linear.teamKey`), that
  team's state and label ids, and `linear.findingTicket`. It resolves the pinned ticket **in its
  own team** — the prefix of the pinned id — and **refuses when that is not the work team the
  config names**: a plan is filed on the team its idea is on (KIT-184). Its duplicate check
  leaves out the pinned ticket, the idea a planning run was made from (`--idea`), and every
  planning ticket. The pinned id comes from whatever started the session,
  never from the tree: a run that can write refuses without it, because a pin defaulted from
  `source_ticket_id` would compare the session's claim with itself. The **planner job**
  (`scripts/pipeline_plan_poller.py`) supplies it: it writes the planning ticket the session is
  delegated, so the pinned id is a value the session never chose.

**The planning session's only channel is this document, so a QUESTION rides it too.** Because the
session holds no tracker tool, every way it reaches the owner travels through the executor. A
session that needs a design decision it cannot resolve from the code emits a `ticket-comment`
(§8's ordinary kind) in the same batch, naming its own pinned ticket. The executor posts it on
the idea ticket:

- **A plan plus questions** — the plan is filed and the questions ride along as notes (the
  session's open questions also belong in the epic PRD; these are top-level asides).
- **Questions and no plan** — the executor surfaces them as a **needs-input escalation**: the
  planner could not decompose the idea and is asking. A legitimate terminal, not a failure —
  but a question the executor *cannot deliver* (no credential, tracker down) is a loud
  `errored`, because a question nobody sees is worse than none.
- **Nothing at all** — a session that produces no file is not left **silent** on the board
  (§13): when the pinned ticket is known, the executor leaves a visible *no-output* note so the
  owner sees the miss rather than a plan that never arrives.

Every comment the executor posts — summary, rejection, escalation, no-output — carries an
invisible `<!-- pipeline-escalation: <label> -->` mark, the same shape a stopped session's
comment uses, so the **human-action notifier** (`docs/adr/2026-09-06-human-action-notifier-and-reply-relay.md`)
can grep the tracker and page the owner. One exception: with `--defer-mark`, a no-output or
a rejection report is posted without its mark, so the job that started the run can page
after it decides what to do (KIT-373). A summary, a question and a partial filing's report
always keep theirs. The planner job passes the flag on every run, with automatic re-runs on
or off (KIT-338), and pages for itself: once, when a move ends without a plan, on the last
run's planning ticket, naming every run of the move and how each ended; and once, on the
idea, for each refusal before a run. Each such page carries the planner job's own
`planning-failed` mark, and its line 2 is the exact-shape line
`Ended: <how>[; runs: <n>]; filed: nothing|not certain[; replan: <idea>]` the notifier
builds its page from. A plan's summary carries the executor's own exact-shape line on its
line 2, under its `epic-awaiting-approval` mark: `Plan: <epic>; pieces: <n>[; open
questions: <m>]`, where `m` counts the questions filed with the plan as its notes, and a
refiled summary leaves it out (KIT-274). The notifier builds the plan-ready page from it,
and keeps it in its DM ledger for the Slack bridge's approve question. The idea gate is the
*producer* of those marks for the planning lane;
the notifier is the *consumer*. The executor **neutralizes any `<!--` sequence
in the session's own text** (question bodies, notes, titles) before embedding it, so a session
cannot forge a mark inside content it supplies — only executor-authored marks survive the grep.
A `ticket-comment` in a planning batch obeys the same §8 rules as anywhere else (its
`ticket_id` is compared to the pin — §6/§8 still bar it from touching a `provenance:*`/`agent:*`
label or state) and is capped at a few per run — a planner asks the questions that block it,
not a wall.

The executor is `scripts/pipeline_plan_executor.py` — deterministic, model-free, the direct
analog of the review publisher (§14). It runs on the dispatcher host as an owner-scoped role
account holding the tracker credential; the planning session never does. Reuses
`linear.findingTicket` for its landing state, owner and notification, so the plan kind is
off on the same discriminator the finding kind is: absent `linear.findingTicket`, a
`ticket-create` (plan) request is refused exactly as the flat kind is.

### Validation rules

Every rule is a MUST, and all of them run **before any request executes**.

| Rule | Rationale |
|---|---|
| Every `ticket_id` — and a `ticket-create`'s `source_ticket_id` — equals the **dispatcher-supplied** pinned ID | The central check. The agent *names* a ticket and the validator *compares* it — the value is never used to address anything. A mismatch is an attempted retarget, not a typo. A finding is provably *from this session* before it is filed. |
| At most **one** `ticket-state` per run | A run advances the ticket once. Several transitions in one batch is either confusion or an attempt to land somewhere by way of somewhere else. |
| At most **3** flat `ticket-create` (finding) per run, and only if `linear.findingTicket` is configured | A session reports the findings it happened to meet, not a backlog. The cap bounds a runaway or hostile session; the config gate keeps the kind off until a project opts in. |
| A `ticket-create` lands in `linear.findingTicket.landing` (backlog only), carries executor-applied `provenance:agent`, and is refused any `provenance:*`/`agent:*`/`blocked:*`/`hooks-change` in its `labels` | The session sets none of the fields that carry authority — state, provenance, assignee. The executor forces them, so the invariant is structural, not a prompt (see *Filing a finding*). |
| At most **one** `ticket-create` (plan) tree per run; its `children` are capped at `MAX_PLAN_CHILDREN` and each child's `labels` are refused any `provenance:*`/`agent:*`/`blocked:*`/`hooks-change` | A run proposes one decomposition, not many. The executor forces the epic's `provenance:agent`, each child's `provenance:epic`, and each child's parent — see *Filing a plan*. |
| Every `depends_on` ordinal is in range for the tree's `children`, not self-referential, and forms no cycle | The executor turns them into blockedBy relations; a dangling or circular edge is a malformed tree, not a relation. |
| A `ticket-create` (plan) child that fails `check_ticket_dor.py --strict` rejects the **whole tree** | A partial epic reads as decomposed and is not; the executor creates nothing and reports the failing children back for a re-plan (§5, §13). |
| `raw`, `ready` and `done` are **never** valid targets | `ready` would be self-approval (§5); `done` would be a session claiming its own merge (§5, §6). Refused **even when a caller passes them in `allowed_to_states`** — a belt the caller cannot unbuckle. |
| Targets are otherwise limited to the caller's `allowed_to_states` | Default `review`. The stage decides what a session may do, not the session. |
| `agent:*`, `blocked:capacity`, `provenance:*` and `hooks-change` labels are refused, in `add` **and** `remove` | Dispatcher- and human-owned (§5, §6). A session setting `agent:needs-human` — or clearing `agent:blocked` — is a session editing its own supervision. `remove` matters as much as `add`. |
| Unknown label keys are refused | Only keys present in `linear.labels.ids` resolve to an ID. |
| Exactly **one** telemetry block across all comments, when the caller requires it | §4. Counted by the `"schema": "pipeline-telemetry/1"` marker, matched with whitespace tolerance so nothing hinges on a JSON writer's spacing. Zero blocks is a `telemetry-required` violation (§2); two is a double-count. |
| A missing requests file with telemetry required is a **rejection**, not a skip | A session that reports nothing is indistinguishable from one that silently failed, and silence must not be the quiet path (§13). |
| An artifact that downloads but holds no `requests.json` is a **rejection**, not a skip | The artifact *was* read, so this is a malformed batch rather than an absent one — a different fact about a different party, and it must not report as the session having written nothing. |

### Caps

| Cap | Value | Why |
|---|---|---|
| Requests per batch | 20 | A runaway or hostile session must not spray the tracker. |
| Comment body, and a finding's `body` | 16 000 chars | Bounded cost, bounded blast radius. |
| Finding `title`, an epic's or child's `title` | 200 chars (schema); the DoR gate enforces its own tighter title limit on plan children | A title is one line, not a report. |
| flat `ticket-create` (finding) per run | 3 | A session reports what it met; it does not generate a backlog. |
| `ticket-create` (plan) trees per run | 1 | A run proposes one decomposition. |
| Plan `children` per tree | `MAX_PLAN_CHILDREN` (executor constant; schema hard-ceiling 20) | A plan is a tree, not a backlog; the executor's constant is the operative flood guard. |
| Label ops per request | 10 | `add` + `remove` combined, and a finding's or child's `labels`. |

### All-or-nothing

**One invalid request rejects the entire batch and nothing is applied.** Two reasons,
and both matter:

1. **An escape attempt earns no partial credit.** If nineteen valid requests applied
   and only the twentieth were dropped, probing for the edge of the validator would be
   free — a session could smuggle its legitimate work through while testing what else
   gets past.
2. **A half-applied batch leaves the tracker in a state nobody designed** — a state
   moved but the explaining comment missing, or labels changed with no telemetry to say
   why.

The job reports a single verdict: `ok` (validated, executed), `rejected` (batch read and
refused, nothing applied), `skipped` (nothing to apply and none required), or `errored`
(**this job** failed — it could not read the requests, or could not reach the tracker to
apply them).

**`skipped` is the only clean way to have applied nothing, and it is only reachable by
asking.** The validator queries the artifacts API for the collected artifact name
*before* downloading it, because `download-artifact` fails on absence as well as on
failure and so its own outcome can never tell the two apart. No artifact ⇒ `skipped`.
An artifact that exists ⇒ the download must succeed, and anything else is `errored`.

**`errored` is deliberately wider than "could not read".** A missing credential and a
tracker that stops answering halfway through a plan are the same fact to a caller: the
requests may exist and have gone unapplied, or been applied only in part, and none of
that is a statement about what the session wrote. One token that covers the class is
what stops a fifth being invented for the next cause. A caller MUST treat `errored` as a
failure, never as an empty batch, and never as a verdict on the batch's contents (§13).

**All-or-nothing covers validation, not the network.** Nothing executes until every
request has passed, so a *rejected* batch applies nothing. An `errored` batch may be
partly applied, and the job says how far it got rather than implying it got nowhere.

### What is authority here, and what is not

| | |
|---|---|
| **Authority** | `delivery.json` on the default branch; the pin; the pinned ticket ID and dispatch ID passed in by the dispatcher. The validator checks out the **default branch**, never the PR head — reading `delivery.json` from the head would let a session widen its own allowed transitions in the same PR it is asking to be approved. |
| **Reporting** | Every field of the requests file. Each one is a *request*, never a fact. |

> **The file path may be named by an environment variable, and that is not a §3
> violation.** §3 rules env vars out as *pin* transports because a pin is authority. The
> path here points at agent-authored output: repointing it only costs the session its
> own reporting channel. What the agent cannot influence is the artifact name the
> dispatcher collects and the pinned ID the validator compares against.

---

## 9. Dispatcher state record

`budgets.totalAttempts` bounds how many times one ticket may be dispatched, and
`budget.attempt` / `budget.of` are pinned into every pin (§3). Both require the count to
be known **before** dispatch — which makes it authority, not reporting, and gives it
nowhere to live in any structure written during a session.

**The count lives in a dispatcher-owned state record**, written outside every worktree,
by the dispatcher only.

### Shape

```json
{
  "schema": "pipeline-dispatcher-state/1",
  "attempts": { "ENG-123": 2 },
  "capacity": {
    "paused_until": "2026-08-24T18:00:00Z",
    "resets_at": "2026-08-24T18:00:00Z",
    "used_percentage": null,
    "noted_at": "2026-08-24T15:04:05Z"
  },
  "spend": [
    { "at": "2026-08-24T15:04:05Z", "usd": 6.0, "ticket": "ENG-123", "dispatch_id": "d_01JAV8…" }
  ]
}
```

| Field | Type | Notes |
|---|---|---|
| `attempts` | object → integer | Ticket identifier → dispatches so far. `attempts[t] >= totalAttempts` ⇒ the ticket is skipped and `agent:needs-human` is applied. |
| `capacity` | object | Queue pause after a provider capacity error (`dispatch.pauseOnCapacity`). Time-bounded: a pause in the past is simply over. |
| `spend` | array | The dispatcher's own accounting behind `budgets.dailyUsd` — the ledger referenced by "spend is metered by the dispatcher" in §1. |

### Rules

1. **Exactly one writer per run.** The record is written once, at the end of a dispatch
   run, by one job. Concurrent writers would lose updates on the one number that decides
   whether more money gets spent.
2. **Never read from inside a worktree, never written by a session.** Same reason the
   pin is not: a counter the agent can edit is not a counter.
3. **A capacity failure refunds the attempt and the reserved spend.** Provider capacity
   is not the ticket's fault (§1 `dispatch.pauseOnCapacity`).
4. **A missing or unreadable record starts from zero. It never blocks dispatch.** Fail-
   closed on a missing *precondition* is the hostage failure §2 exists to prevent.
5. **The store's retention must exceed the longest a ticket can stay in flight.** On
   `github-actions` that is the artifact's retention window; on a filesystem store it is
   whatever prunes the directory.

### Where it lives, per backend

The contract owns the **record and its invariants**; each `dispatch.backend` binds them
to a concrete durable store, declared by `dispatch.statePath` (§1):

| `dispatch.backend` | Store | `statePath` |
|---|---|---|
| `github-actions` | The `pipeline-state` Actions artifact, read newest-first at the start of a run and republished at the end | `null` — the artifact name is fixed by this contract, so there is nothing to configure |
| `local-daemon` | A file at the configured path | Required |
| `cloud` | A file or object at the configured path in the service's own durable storage | Required |

Naming the artifact *here* is the point: a well-known name written into the contract is
one shape, whereas the same name chosen inside one workflow is a local convention the
next backend cannot see.

### The trade-off, stated plainly

**The counter is bounded, not exact.** If the store is lost or expires, every in-flight
ticket gets its attempts back and may be dispatched up to `totalAttempts` more times.
That is accepted, because the **durable terminal signal is not the counter** — it is the
`agent:needs-human` label, which lives on the ticket, is dispatcher-owned, and is never
cleared by the dispatcher. A ticket carrying it is skipped before the count is even
consulted. So losing the state costs **at most one extra attempt per ticket**, not an
unbounded loop.

The property that actually holds is: *a ticket never dispatches while carrying
`agent:needs-human`*, and the attempt count is the mechanism that **applies** that
label. A project that needs exact attempt accounting must supply a store that does not
expire; the contract does not require a database to run a queue.

### Rejected: a dispatcher-authored ticket comment

A marker comment on the ticket itself is tempting — it is durable, backend-independent,
and needs no new storage. It is **rejected**, and the reasoning is recorded here because
it will be proposed again.

§3 already lists ticket comments as **not a valid pin transport** — "the agent posts
them." The tempting rescue is that in this architecture the agent has *no tracker
credential at all* (§8), so it cannot post one. That rescue does not hold:

1. **It is a property of one backend's implementation, not of the contract.** The
   credential split is a `github-actions` workflow design. `dispatch.backend` also
   admits `local-daemon` and `cloud`, and a session there may well run with a tracker
   MCP server attached. A trust boundary that holds only until someone configures a
   different backend is not a trust boundary.
2. **Even with no credential, the agent has a validated write channel into ticket
   comments.** §8 executes `ticket-comment` requests using the *dispatcher's* credential.
   The validator bounds their count and size and refuses dispatcher-owned labels and
   states — it does not inspect comment *bodies* for a state marker. A session could
   request a comment containing a forged marker, and it would be posted under the
   dispatcher's own identity, indistinguishable from the real thing. Defending that
   needs a further validator rule whose whole security rests on a marker string staying
   obscure.
3. **It would make a frozen rule conditional.** Amending §3 to "ticket comments are
   fine when the backend happens to withhold a credential" is exactly the second shape
   for the same structure this document exists to prevent.

Also rejected: **storing the counter in `pinsRoot`.** It reuses a directory that already
exists, but it conflates two lifetimes — pins are per-dispatch scratch with a sweeper
deleting stale ones, while the counter must outlive every session on the ticket. Long-
lived authority does not belong in a directory something is designed to prune.

---

## 10. Telemetry store

§4 defines the **block a session posts**. This section defines **where those rows come
to rest**, so that a dashboard, a retrospective and an ad-hoc query are all reading the
same three tables rather than three private re-derivations of the same comments.

Configured by `delivery.json` → `telemetry` (§1). Absent ⇒ collection is off: the
collector exits 0 and emits nothing, the same way every other pipeline-scoped tool
treats an absent discriminator (§2).

### The three tables

| Table | One row per | Fed by |
|---|---|---|
| `runs` | agent invocation | §4 `runs` |
| `ticket_events` | lifecycle milestone | §4 `ticket_events` |
| `review_findings` | finding from a review pass | §4 `review_findings` (optional) |

Columns mirror the §4 row fields one-for-one, plus two the collector adds:
`source_comment_id` (which comment a row came from — the audit trail back to the
original text) and `ingested_at`.

### Idempotency is by natural key, never by a cursor

Each table has a key the contract already froze, and re-inserting the same row is
defined to be a no-op:

| Table | Key | On conflict |
|---|---|---|
| `runs` | `run_id` | **Update.** §4 makes `run_id` stable across re-posts, and a re-post may carry a corrected `ended_at` or a PR number the first post did not have yet. |
| `ticket_events` | `(ticket_id, event, at)` | **Nothing.** §4: "the same `(ticket_id, event, at)` posted twice is one event." An event is a fact, not a record that gets amended. |
| `review_findings` | `sha256` digest of `(ticket_id, pr_number, severity, category, file, line, summary)`, truncated | **Nothing.** Findings have no ID in §4, so identity is content. |

**No "last scraped" cursor, by design.** A cursor is state that can be lost, skipped
past, or rewound, and every one of those failures is silent: lose it and you either
double-count or leave a hole nobody notices. Natural keys make an overlapping sweep
free, so the collector reads a generous window every time and lets the store deduplicate.
The trade is a little wasted read for the removal of an entire class of silent
corruption.

### A malformed row is skipped, never fatal

The blocks most worth collecting come from runs that ended badly, which are also the
blocks most likely to be malformed. So a collector MUST fault-isolate at every level —
a comment that is not JSON, a fence that is not telemetry, a row missing a required
field — count and name what it stepped over, and exit non-zero **only** when the store
itself was unreachable. A sweep that dies on the first bad row stops collecting
everything behind it.

One row is refused outright rather than skipped-and-counted: a `merged` event with
`actor: agent`. §4 forbids it, and recording it anyway would corrupt every autonomy
metric computed downstream.

### Still reporting, not authority

Nothing read out of these tables may gate a budget, an approval, or a merge. `dailyUsd`
is metered against the dispatcher's own ledger (§9); `runs.cost_usd` is a self-report
that exists so humans can see the shape of spend. **Implemented by
`scripts/telemetry_scrape.py`** (`npm run test:telemetry`), which owns the DDL.

---

## 11. Autonomy tiers

Three questions the pipeline answers without a human, each narrower than the last, each
with its own gate. The ladder is the point: a project adopts one rung at a time, and
every rung above the first is off by default.

| Tier | Question | Gate | Default |
|---|---|---|---|
| **Dispatch** | may this *approved* ticket start a session? | the dispatcher's WIP, budget and attempt gates (§9) | on |
| **Approve** | may this ticket move `raw` → `ready` without a person? | `scripts/check_auto_approve.py` | on, but `epic/*` only (§5) |
| **Merge** | may this PR merge without a person? | `scripts/check_auto_merge.py` + the platform's ruleset | **off** (`autoMergeMaxLines: 0`) |

### The approve tier

Every gate must pass, and each is recomputed from a source the session cannot write:
provenance resolves to `epic/<ID>` (§5 rule 4); that epic exists and sits in the
human-approval state `ready` — not merely any state out of intake (§5 rule 2); the ticket
is in `raw`; it carries no dispatcher-owned `agent:*` /
`blocked:*` label and no human-applied `hooks-change`; the Definition-of-Ready gate
passes in `--strict`; and nothing the ticket names matches `autonomy.riskPaths`.

**`monitor`, `review`, `retro-proposal` and `human` never auto-approve**, and the reason
is adversarial rather than stylistic. Those four are the classes an agent — or anything
that can trip a probe or influence a diff — can cause to be filed. If any of them could
approve itself, the pipeline could widen its own mandate by writing a ticket, and
"write a ticket asking for X" is a capability every one of those paths has. `epic/*` is
the only class whose approval traces back to something a person did.

### The merge tier — the platform merges, never the agent

**Nothing in the kit merges.** The never-merge guard blocks the CLI merge command in
every form including `--auto`, and that is unconditional. The merge tier works by asking
**GitHub** to enable its own auto-merge, so the platform performs the merge under branch
protection rules that live in repository settings — outside the repo tree, outside any
diff, unreachable from a session. The capability is not held and then restrained; it is
never held.

That makes the required ruleset part of the tier, not an operational footnote: required
status checks on the default branch, at least one approving review, and "Allow
auto-merge" enabled. **Without required checks, auto-merge merges the instant it is
enabled** and the gates below become the only gates, which is exactly what they are not
designed to be. `docs/AUTONOMY.md` carries the copy-paste setup.

Nine conditions, all required:

| Condition | Read from (never from) |
|---|---|
| Auto-merge enabled: `autoMergeMaxLines > 0` **and** the `PIPELINE_AUTO_MERGE_ENABLED` repo variable is `true` | a repo variable (a config value alone — a PR can propose one) |
| The ticket still passes the approve tier, **recomputed** | a live re-run (a stored "was approved" flag) |
| **Zero bounces** | Actions run history (`pipeline:bounce-N` PR labels — the fix session's token can edit PR labels) |
| Review findings usable, none at or above `reviewSeverityThreshold` | the review artifact (a PR comment — the author can edit it) |
| The review covered the **whole** change — `coverage` is not `partial` | the review artifact's own coverage (the findings list's length: a partial review is clean about the files it saw) |
| Every check run terminal and green | the check-runs API ("CI passed" asserted in a commit message) |
| `mergeStateStatus` is not `DIRTY` / `UNSTABLE` / `UNKNOWN` | the PR API |
| The diff touches no `riskPaths` | `git diff base...head` (the PR body's description of its own size) |
| Changed lines ≤ `autoMergeMaxLines` | `git diff --numstat` |

**Zero bounces is the condition to leave alone.** A bounce means the first attempt was
wrong in a way review or CI caught; the fix may well be right, but the evidence that the
pipeline understood the ticket is now mixed — and mixed evidence is precisely the case
worth a human's thirty seconds. It is also the state carrying the most machine-authored
churn, so it is where a human read is worth the most. This does not conflict with
`maxBounces > 0`: bounces exist to get a PR ready **for a person**, not ready to merge
itself.

**A push revokes the request.** Every gate above described one head sha. GitHub keeps
auto-merge enabled across subsequent pushes, so a PR that qualified at 40 lines could be
amended to 400 and still merge on the strength of a stale verdict. A new push therefore
disables auto-merge unconditionally and the PR must qualify again. Revoking is cheap;
the reverse mistake is not recoverable.

### What no tier ever grants

No tier lets anything raise its own limits. A retrospective may *propose* a budget,
rubric or cap change and may never apply one: proposals ship as ordinary reviewed PRs,
invented work carries `retro-proposal` provenance (which §5 bars from auto-approval),
and a proposed loosening of any cap is rejected mechanically by
`scripts/check_weekly_review.py` (`npm run test:review`). A system that concludes it
should be allowed to spend more, and then allows itself, does not have a budget.

---

## 12. Machine-readable schemas

Every structure in this document is prose, and prose is what agents *read and try to follow*.
Nine of them are also **JSON Schema (draft 2020-12) files under `schemas/`**, and those files
are not a second source of truth — they are this document in a form a machine can
enforce. `scripts/check_schemas.py` fails CI if the two disagree about which fields
exist.

| Section | Schema | Parity check |
|---|---|---|
| §1 `delivery.json` | `schemas/delivery.schema.json` | **exact, bidirectional, by path** — every field in a §1 table must be a schema property and vice versa |
| §3 pin file | `schemas/pin.schema.json` | bidirectional by field name |
| §4 telemetry block | `schemas/telemetry-block.schema.json` | bidirectional by field name |
| §8 safe-outputs request file | `schemas/safe-outputs.schema.json` | bidirectional by field name |
| §14 review findings file | `schemas/review-findings.schema.json` | bidirectional by field name |
| §15 browser check block | `schemas/browser-check.schema.json` | bidirectional by field name |
| §16 status file | `schemas/pipeline-status.schema.json` | bidirectional by field name |
| §17 usage file | `schemas/pipeline-usage.schema.json` | bidirectional by field name |
| §18 day plan file | `schemas/pipeline-day-plan.schema.json` | bidirectional by field name |

**Amending one of those sections means amending its schema in the same PR.** That is not
a convention here — CI names the missing half.

### Why this exists

`/setup-board` once emitted a `delivery.json` sharing **zero field names** with §1. That
config would have bricked the project it was setting up: a version-less config is
*broken*, not *off* (§2), and a fail-closed guard blocks every tool call until a human
repairs the file by hand. Fixing the emitter fixed one instance. Two independent
implementations of one truth — the prose here and the rules each consumer hand-rolled —
were free to drift, and that is the class.

### Shape is not semantics, and the semantic layer does not move

**A schema constrains shape. It says nothing about meaning.** A schema-valid config can
still carry a UUID that resolves to nothing, a `pinsRoot` inside a worktree, or a
`perEffort` band above the global cap; a schema-valid safe-outputs file can still name a
ticket the session was never pinned to. So every consumer-side rule in this document
stays exactly where it is — the hook's BROKEN classification (§2), the §7 validator's
semantic rules, and §8's validation rules, which are the ones that matter and which run
against the **dispatcher-supplied** pinned values.

Conforming to a schema earns a document nothing. This is defense in depth, and the
doctrine at the top of this file is unchanged: **anything the session agent can write is
reporting, never authority** — including a document that validates.

### Generating, rather than requesting, the right shape

**A document is validated before it is written, never after.** A check that runs after
the write is a diagnosis: the malformed `delivery.json` is already on disk and already
failing the hook closed; the malformed request is already in the batch that §8 will
reject whole. `scripts/emit_document.py` is the gate — it validates a candidate and
writes the destination **only if the candidate conforms**, so a refused document is not
a file anyone has to find.

```bash
# a whole document: validated, then installed — or nothing is written at all
python3 scripts/emit_document.py --schema delivery \
  --candidate cand.json --install delivery.json

# one §8 request: the RESULTING batch is validated before it replaces the old one,
# so a malformed request costs that request and not the batch around it
python3 scripts/emit_document.py --append "$REQ"
```

Which mechanism fits depends on what produces the document, and they are not
interchangeable:

| Producer | Mechanism |
|---|---|
| A skill or script that writes a file | `emit_document.py`, above. |
| A step that builds a document in memory | `check_schemas.document_problems(doc, name)` before the write — one definition of "conforms", shared by both dispatchers and the review emitter. |
| A model whose **final message** is the document | The schema at generation: Claude Code headless takes `--json-schema <file>`, the Agent SDK takes `outputFormat: { type: 'json_schema', schema }`. |
| A model that writes a **file** with a tool | Neither of those constrains a tool call, so the file is refused at the boundary that reads it — see §14. |

Where the boundary reads a document produced under a ref an agent could have written,
**the schema and the validator are staged from the default branch**, exactly as a rubric
is: a PR that could loosen the schema its own review is held to has not been reviewed.

Asking for a shape in a prompt is a request. Constraining it at generation, or refusing
it at the boundary, is a guarantee — of shape only, which is the whole point of the
paragraph above.

### The validator is vendored on purpose

`scripts/jsonschema_mini.py` implements the draft 2020-12 subset these schemas use, in
stdlib only. The kit installs no Python packages for its guards, and the validator that
decides whether the pipeline may run is the last place to add a dependency. Its
guarantee: **an unrecognized keyword is an error, never a silent no-op** — a schema
keyword that nothing enforces reads as protection while giving none, so `check_schema()`
rejects any keyword the validator cannot enforce, over every shipped schema, in CI.

---

## 13. Absence is not failure

**A component that can legitimately do nothing MUST be able to distinguish "nothing to
do" from "could not do it" — and MUST say which.**

Most of this pipeline is built out of jobs whose ordinary answer is *nothing happened*.
A session dies before writing its requests. A review finds no findings. A poll finds an
empty queue. Every one of those is a correct, quiet, green outcome — and every one of
them is one bad day away from being indistinguishable from a denied API call, an expired
artifact, or a script that crashed before it reached its first line of work.

The two states have opposite meanings and identical symptoms, which is why they get
conflated: **no output, no error, no red check.** And the conflation is self-concealing.
A component that reports "nothing to do" when it actually failed removes the only signal
that would have brought anyone to look at it.

### The three states, and what each owes the reader

| State | What happened | How it must report |
|---|---|---|
| **Did the work** | The component ran and acted | Its normal success output |
| **Nothing to do** | The component ran, *asked*, and the answer was genuinely nothing | Green — and it **says what it asked and what the answer was**. A silent green is not this state, it is an unlabelled one |
| **Could not do it** | The component could not establish the answer, or could not act on it | **Loud.** Non-zero, or a distinct verdict a caller must treat as failure. Never the same token as *nothing to do* |

### The test to apply

> **If this component failed completely right now, would anything anywhere go red?**

If the answer is no, the component is not reporting — it is being trusted. Ask the
follow-up: *what tells absence from failure here, and could the agent or the failure
itself have written that thing?* An inference drawn from the failure ("the download
failed, so there must be no artifact") is not an answer, because the failure is exactly
what makes it unreliable. Ask something that can answer independently.

### Where this shows up in a workflow

Three mechanical shapes, all fine in the right place and all worth a second look:

- `continue-on-error: true` — asks the reader to believe every way this step can fail is
  benign. Legitimate when a **later step evaluates the outcome** (`steps.<id>.outcome` is
  computed *before* `continue-on-error` is applied) and fails on it. A defect when nothing
  downstream reads it.
- `|| true` — collapses "the command said no" into "the command could not run". Fine for
  `grep` (exit 1 *is* the answer) and for cleanup. A defect when the empty result then
  flows into a gate, where empty reads as *permitted*.
- an early `exit 0` — correct for §2's *off* discriminator, which is a real answer to a
  real question. A defect when it is reachable by something **going wrong** rather than by
  something being **absent**.

**A deliberate no-op that announces itself is not a defect.** The rule is not "never do
nothing quietly"; it is "never let *doing nothing* and *failing* look the same".

### Instances of this rule elsewhere in this document

It is not a new idea here — it is the general form of rules this contract already states
one case at a time, which is precisely why it earns a section of its own:

- §2 — `delivery.json` absent is **off**; present-but-unreadable is **broken**. One
  discriminator, three states, and *off* is never allowed to mean *broken*.
- §8 — a missing requests file with telemetry required is a **rejection, not a skip**,
  because "a session that reports nothing is indistinguishable from one that silently
  failed".
- §8 — the safe-outputs verdicts are `ok`, `skipped`, `rejected` and **`errored`**. The
  fourth exists because collapsing it into `skipped` reads as a clean run, and collapsing
  it into `rejected` asserts two things that are false: that the batch was refused, and
  that nothing was applied.
- §12 — an unrecognized schema keyword is **an error, never a silent no-op**.

### What enforces it

Partly a check, mostly a convention — stated rather than implied.

`scripts/pipeline_safe_outputs_probe.py --selftest` (`npm run test:safe-outputs`, in CI's *Kit
checks*) pins the §8 instance: the classification rows, the exit codes, and the step
graph that carries them — that the probe runs **before** the download, that the download
no longer wears `continue-on-error`, and that the verdict is computed from both outcomes.
A perfect classifier wired behind `continue-on-error` would still report success for a
failure, so the wiring is asserted, not assumed.

The general rule is a **convention**, in the same sense as the source-agnostic rule in
`CLAUDE.md`: no check can tell a benign `|| true` from a load-bearing one. What is
mechanical is the reviewer's question, and it is short enough to actually get asked —
*if this failed, what would go red?*

---

## 14. Review findings file

A review session's **entire deliverable is one file**, `REVIEW-FINDINGS.json` in the
repository root. The reviewer has no tools to comment, approve, push or reach the
tracker; everything that gates money downstream — whether the review is usable, what its
worst severity was, whether that meets the rubric's threshold — is computed **from** this
file by a deterministic publisher, never reported **in** it.

> **Which publisher depends on the backend, and the separation is what matters, not the
> location.** On `github-actions` it is the review workflow's normalize step; on a
> dispatcher-side lane it is `scripts/pipeline_review_local.py`'s publisher. Both are
> deterministic code rather than the reviewing model, which is the one piece of the cloud
> design that survives the move unchanged
> ([ADR 2026-09-05](adr/2026-09-05-stage-e-under-a-delegation-bound-dispatcher.md) § 4).
> The shape below is unchanged by either.

### Shape

```json
{
  "schema": "pipeline-review/1",
  "summary": "two or three sentences a human can read in ten seconds",
  "findings": [
    {
      "severity": "high",
      "category": "security",
      "file": "src/x.ts",
      "line": 42,
      "summary": "one line — the claim",
      "detail": "why it is wrong and what would fix it"
    }
  ],
  "blocked": null
}
```

| Field | Type | Notes |
|---|---|---|
| `schema` | const `pipeline-review/1` | The marker. An unrecognized value makes the review **unusable**, which is reported as *treat this PR as unreviewed* — never as clean. |
| `summary` | string | Two or three sentences. May be empty: an empty findings list with an empty summary is still a verdict. |
| `findings` | array | Empty means the change is clean, and saying so with an empty list is the correct output — **unless `blocked` is set**. |
| `blocked` | string \| boolean \| null, optional | Omitted unless the reviewer could not judge the change at all. One line saying what was missing. A set `blocked` means *could not review*, and the publisher declines: the PR is reported unreviewed, never clean. Absent, `null`, `false` and the negative spellings a model reaches for (`no`, `none`, `n/a`, `no blockers`, and any of them followed by a dash and an explanation) all mean not blocked — tolerant in that direction only, because a false decline discards a review that was done. A **partial** review (§11, files withheld over the size cap) is not a blocker and must not set it. Findings listed beside a set `blocked` are **not acted on**, so both publishers say how many there were: silence about them reads as *there were none* (§13). |

Each finding:

| Field | Type | Notes |
|---|---|---|
| `severity` | enum `low\|medium\|high\|critical` | The field that spends money — everything at or above the rubric's threshold starts a paid fix session. |
| `category` | string | `correctness`, `security`, `tests`, `scope`. Free-form beyond those. |
| `file` / `line` | string \| null, integer \| null | Where, when the reviewer could say. `line` is omitted when it does not apply. |
| `summary` | string | One line: the claim. |
| `detail` | string | Why it is wrong and what would fix it. Read by the humans on the PR and by the fix session that may follow. |

The first five mirror §4's `review_findings` row exactly, so there is **one finding shape
in the system, not two**. `detail` is the one field §4 does not carry: it is prose for a
human, not a column to group by.

### Malformed is unusable — never quietly smaller

**A finding this document cannot express is a finding the review did not report.** The
normalize step used to keep the findings whose `severity` it recognized and drop the
rest, so a reviewer that wrote `Critical` produced a run that had found something and
reported that it had not — the one failure mode a review must never have. The whole
document is now held to the schema: it conforms and every finding survives, or the
review is **unusable** and the PR is reported as unreviewed. Fail-closed on shape,
fail-open on outcome — an unusable review must not read as clean, and must not wedge the
PR either.

**And a well-formed document can still be a non-review.** A reviewer handed a description
with no diff in it conforms perfectly and finds nothing, because there was nothing to
find. Read as a clean bill that is a lie, and it was one that nothing downstream could
detect: the runbook asked the reviewer to explain itself in `summary`, which is prose no
consumer parses. `blocked` is the machine-readable half of that sentence. Set, it makes
the publisher decline exactly as a malformed document does — same loud comment, same
`usable: false` — so the two ways of not-reviewing arrive as one. It rests on the reviewer
being honest about being stuck, which is reporting and not authority (§14's `$comment`); a
reviewer that is blocked and stays silent is unchanged by this field.

### Why the boundary, and not `--json-schema`

The reviewer writes a **file**, and neither `--json-schema` nor `outputFormat` constrains
a tool call — both bind the final assistant message. So this document is refused at the
boundary that reads it instead, in the workflow step that normalizes it, before anything
downstream treats it as a review. The schema and the vendored validator are staged from
the **default branch**, like the rubric beside them: read out of the checked-out head,
they would let a PR loosen the schema its own review is held to.

The published artifact is this document **enriched by CI** with the computed fields the
bounce tier reads. Those fields are workflow-authored; none of them is read from the
model's file, and a model that writes one gains nothing by it.

### Shape is not a review

Conforming here earns a review nothing. A schema-valid findings file can be wrong about
every one of them, and no tier treats "the reviewer produced a well-formed document" as
"the change is good". **Agent-authored ⇒ reporting, never authority**, exactly as
everywhere else in this document.

### The coverage marker on Stage E's comment

Every comment Stage E posts on a pull request says what it did with it, for a script to
read. The comment's **second line**, under its heading, is one HTML comment:

```
<!-- stage-e-coverage {"coverage": "partial", "head": "<sha>", "withheld": ["path"]} -->
```

| `coverage` | Meaning | Extra key |
|---|---|---|
| `full` | the reviewer saw the whole change | — |
| `partial` | whole files, or pieces of one, were withheld over the size cap | `withheld`: the paths or pieces, up to a fixed budget, in order (one name is cut at 300 characters); `withheld_more`: how many more there were, only when the list was cut |
| `declined` | no review was made | `reason` |
| `skipped` | Stage E will not review it: its branch names no ticket | `reason` |

`head` is the commit the comment speaks for. The JSON never holds `--`: each `--` is
written `-\u002d`, so no path can close the comment early. **Read the second line only.**
The rest of the comment carries reviewer text, and a marker there is not Stage E's.
`pipeline_review_local.coverage_marker()` is the reader. Only `full` says the change was
reviewed, and only at `head`. A `withheld` list that was cut is still `partial`: a reader
takes `coverage`, never the list, and the PR's outcome file holds every name (KIT-618).

---

## 15. Browser check block

A plan's checks that a machine can run in a browser (KIT-302). One fenced `json` block,
`"schema": "pipeline-browser-check/1"`, in a ticket's `## Test plan`, or in an epic's
`## How we will know it works` for checks that span the whole feature. The acceptance session
runs each check through the look tool, in the kit's own sandbox, against the app built the
way `delivery.json` `acceptance` says (§1). Machine-readable: `schemas/browser-check.schema.json`.

**Data only.** Every step is one verb from a closed list, read by the acceptance runner.
Nothing in a block is evaluated as code, a path is never a URL, and a verb outside the list
refuses the whole block. Conforming earns a plan nothing: a block says what to look at, and
the session and its skeptic pass decide what they saw.

### The block

| Field | Type | Notes |
|---|---|---|
| `schema` | `"pipeline-browser-check/1"` | An unrecognized value refuses the block. |
| `modes` | object → mode | App states this ticket's checks may name, beyond `acceptance.modes`. A name may not repeat one `delivery.json` declares. At most 6. |
| `checks` | check[] | 1 to 12. |

### A check

| Field | Type | Notes |
|---|---|---|
| `id` | string | Lowercase, digits and `-`. Unique in the block. |
| `criteria` | integer[] | 1-based positions of the ticket's acceptance criteria this check proves. Child tickets only. |
| `scenario` | string | A name from `acceptance.scenarios`. Default: its first. |
| `viewports` | string[] | Names from `acceptance.viewports` (default `desktop`, `phone`). Every check runs once per viewport and mode. |
| `modes` | string[] | `default`, a name from `acceptance.modes`, or one this block declares. |
| `open` | string | A path on the app's own origin, starting `/`. Never a URL, never `//`. |
| `steps` | step[] | 1 to 25, run in order. |
| `look` | string | What a person would judge in this check's screenshots, in plain words. The acceptance session judges it; the runner never does. |

### A target

What a step acts on, found the way a person would find it. At least one field.

| Field | Type | Notes |
|---|---|---|
| `role` | string | An accessibility role: `button`, `heading`, `tab`, `switch`. |
| `name` | string | The accessible name, with `role`. |
| `text` | string | Visible text. |
| `label` | string | A form field's label. |
| `placeholder` | string | A form field's placeholder. |
| `testId` | string | A `data-testid`, when nothing a person sees is unique. |
| `css` | string | A CSS selector, the last resort. |
| `exact` | boolean | Match `name` or `text` exactly. |
| `nth` | integer | Which match, from 0; `-1` is the last. |
| `within` | target | Search inside this target only. |

### Steps

Each step is `{"do": <verb>, …}`. The verbs: `click`, `hover`, `fill`, `press`, `reload`,
`goto`, `wait`, `emulate`, `addClass`, `removeClass`, `setStorage`, `screenshot` and
`expect`. Any step may carry `only`.

| Field | Type | Notes |
|---|---|---|
| `do` | string | The verb. |
| `on` | target | What `click`, `hover`, `fill`, `addClass`, `removeClass`, `screenshot` and `expect` act on. Required for all but `screenshot`. |
| `only` | string[] | Run this step only on these viewports: a phone menu a desktop does not have. |
| `text` | string | `fill`: what to type. `expect`: the text the target holds. |
| `key` | string | `press`: a key, such as `Enter` or `Shift+Tab`. |
| `path` | string | `goto`: a path on the app's origin, as `open`. |
| `for` | target | `wait`: until this target is visible. A `wait` names `for` or `ms`, not both. |
| `ms` | integer | `wait`: a pause, at most 2000. |
| `colorScheme` | string | `emulate`: `light`, `dark` or `no-preference`. An `emulate` names this, `reducedMotion`, or both. |
| `reducedMotion` | string | `emulate`: `reduce` or `no-preference`. |
| `class` | string | `addClass`, `removeClass`: one class name. |
| `values` | object | `setStorage`: localStorage keys and string values, then a reload. |
| `name` | string | `screenshot`: its name in the result. |
| `full` | boolean | `screenshot`: the full page, not the viewport. |
| `at` | string | `expect`: `first-paint` judges the page before its scripts run, the moment a flash would show. |
| `part` | string | `expect`: `placeholder`, `before`, `after` or `marker`, a part of the target. |
| `visible` | boolean | `expect`: shown or not. |
| `count` | integer | `expect`: how many match. |
| `hasClass` | string | `expect`: the target carries this class. |
| `lacksClass` | string | `expect`: it does not. |
| `attr` | object | `expect`: attribute values, at most 4. |
| `css` | object | `expect`: computed style values, at most 4, such as `{"background-color": "rgb(18 18 18)"}`. |
| `contrast` | object | `expect`: `{"min": <ratio>}`, the text-to-background contrast ratio, 1 to 21. |
| `min` | number | The ratio `contrast` asks for. |

### A mode

The same shape as `acceptance.modes` in §1, and `scripts/check_schemas.py` holds the two
equal: `colorScheme` (`light`, `dark` or `no-preference`), `reducedMotion` (`reduce` or
`no-preference`), `locale` (such as `en-GB`), `timezoneId` (such as `Europe/London`), and
`localStorage` (keys and string values set before the page loads). Every field is optional.

An example, the shape the dark-mode plan's epic would have carried:

```json
{"schema": "pipeline-browser-check/1", "checks": [
  {"id": "home", "scenario": "signed-in", "viewports": ["desktop", "phone"],
   "modes": ["light", "dark"], "open": "/",
   "steps": [{"do": "expect", "on": {"css": "body"}, "at": "first-paint",
              "css": {"background-color": "rgb(var(--c-bg))"}},
             {"do": "screenshot", "name": "home"}],
   "look": "Dark: no white or cream surface; cards lift off the board."}
]}
```

Which names exist (viewports, modes, scenarios), which criteria a check may point at, and when
a UI ticket must carry a block are the readiness gate's rules (`scripts/check_ticket_dor.py`),
not shape.

---

## 16. Status file

One file says where every plan stands: its pieces, their pull requests, each pull request's
merge state, the required checks named in the job's config, and the newest Stage E review
(KIT-335). The status job, `scripts/pipeline_status_local.py`, writes it each pass. It runs
with no AI, as the role account, like the Stage E jobs. The tech lead, the daily summaries
and the GitHub-only pages read this file. None of them holds a GitHub token (owner decision
5, KIT-306 path A). Machine-readable: `schemas/pipeline-status.schema.json`. The job checks
its document against that schema before it writes, and writes nothing that does not conform.

**Fields only.** Every value is an id, a cleaned title, a state from a closed list, a count,
a time or a link. No pull request, comment or review text reaches the file, and no pull
request title: the ticket's own title names the piece. A cleaned title is one line, at most
120 characters, with no angle bracket, ampersand, pipe or backtick, and no control, format
or other hidden character (Unicode categories Cc, Cf, Co and Cs, and variation selectors):
a title a session wrote cannot carry text only a model would read. A reader can quote it as
data.

**Reporting, never authority.** A session can write a ticket's title and labels and a pull
request's branch. Nothing may read this file to decide what to run, merge or approve.

### Where the facts come from

| Source | What it gives | Read how |
|---|---|---|
| `tracker` | Each plan the idea gate filed: an issue labelled `provenance:agent`, with no parent and at least one child. Its children are the pieces. Each piece's pull requests are the links the GitHub integration attached, the bridge's own rule (KIT-299). A link of any other source is passed over, since a session can attach any link to its own ticket. | Linear, with the role account's key |
| `github` | Each linked pull request's state, merge state, labels and head commit, and the check runs on that commit. Then the open pull requests of each repository `required_checks` names by key, two pages of 100 at most, each read the same way (KIT-487). | GitHub REST, with the role account's token |
| `reviews` | The review poller's outcome record for each pull request: `<review_state_dir>/outcomes/<owner>__<repo>__pr-<n>.json`. | The file system, as the role account |

A plan is listed while it is open, and for `lookback_hours` (default 72) after it finishes.
So a summary can say what shipped since the day before.

### Shape

```json
{
  "schema": "pipeline-status/1",
  "written_at": "2026-11-03T18:05:00Z",
  "started_at": "2026-11-03T18:04:31Z",
  "result": "partial",
  "error": null,
  "lookback_hours": 72,
  "sources": {
    "tracker": {"state": "read", "asked": 3, "answered": 3, "reason": null, "read_at": "2026-11-03T18:04:40Z"},
    "github": {"state": "partly-read", "asked": 4, "answered": 3, "reason": "acme/app#41: GitHub answered HTTP 404", "read_at": "2026-11-03T18:04:52Z"},
    "reviews": {"state": "read", "asked": 2, "answered": 2, "reason": null, "read_at": "2026-11-03T18:04:52Z"}
  },
  "counts": {"plans": 1, "pieces": 2, "pull_requests": 2, "open": 1, "merged": 1, "closed": 0, "unknown": 0, "conflicting": 1},
  "plans": [{
    "id": "ENG-300", "title": "Dark mode", "url": "https://linear.app/acme/issue/ENG-300",
    "team": "ENG", "state": "In Progress", "state_type": "started",
    "created_at": "2026-11-01T09:00:00Z", "updated_at": "2026-11-03T17:00:00Z",
    "completed_at": null, "canceled_at": null, "pieces_complete": true,
    "pieces": [{
      "id": "ENG-301", "title": "Back the palette with CSS variables",
      "url": "https://linear.app/acme/issue/ENG-301", "state": "Needs Approval",
      "state_type": "unstarted", "started_at": "2026-11-02T10:00:00Z", "completed_at": null,
      "canceled_at": null, "updated_at": "2026-11-03T16:30:00Z", "flags": [],
      "prs_complete": true,
      "prs": [{
        "repo": "acme/app", "number": 40, "url": "https://github.com/acme/app/pull/40",
        "source": "github", "error": null, "state": "open", "draft": false,
        "opened_at": "2026-11-02T11:00:00Z", "updated_at": "2026-11-03T16:00:00Z",
        "merged_at": null, "closed_at": null, "head_sha": "0123456789ab", "base": "main",
        "mergeable": "conflicting", "merge_state": "dirty", "labels": ["conflict"],
        "checks": {"result": "waiting", "required": [
          {"name": "Kit checks", "state": "passing", "waits_on_person": false},
          {"name": "Hooks change guard", "state": "failing", "waits_on_person": true}]},
        "review": {"state": "notes", "at": "2026-11-03T15:00:00Z", "max_severity": "low",
                   "findings": 2, "current": true}
      }]
    }]
  }],
  "findings": [{"kind": "github", "state": "unknown", "summary": "partly-read: acme/app#41: GitHub answered HTTP 404"}],
  "firing": []
}
```

The example leaves out the plan's second piece for room. Its pull request, #41, is the one
GitHub answered 404 for, and the tracker's link says it is merged. `counts` and `sources` cover
both pieces.

### The file

| Field | Type | Notes |
|---|---|---|
| `schema` | `"pipeline-status/1"` | A reader that does not know the value refuses the file. |
| `written_at` | time | When this pass wrote it. Its age is the answer to "how old is this?". The heartbeat monitor judges the job stale by it. |
| `started_at` | time | When this pass began reading. |
| `result` | `ok` \| `partial` \| `failed` | `ok`: every source was read. `partial`: at least one was not read whole (exit 3). `failed`: no source that is on could be read (exit 1). See *Exit codes and the heartbeat* for a pass with no network. |
| `error` | string \| null | Null, unless the pass stopped on an unexpected error: then one line saying why, with both credentials scrubbed, and every source that is on is `could-not-read`. A pass cut short by its clock or a cap is `partial`, and that source's `reason` says so. |
| `lookback_hours` | integer | How long a finished plan stays listed. |
| `sources` | object | `tracker`, `github` and `reviews`, each a source below. |
| `counts` | object | `plans`, `pieces`, `pull_requests`, and the pull requests that are `open`, `merged`, `closed`, `unknown` and `conflicting`. A quiet day says what was checked from these. |
| `plans` | plan[] | At most 100. Empty when the tracker could not be read: `sources.tracker` says so. |
| `other_pull_requests` | pull request[] (optional) | KIT-487. Each open pull request in a repository `required_checks` names by key that no listed plan's piece links to, read the same way, in repository and number order. At most 200. A file from before KIT-487 has none. |
| `other_pull_requests_complete` | boolean \| null (optional) | KIT-487. `true`: every such repository's open pull requests were listed. `false`: at least one was not, or there were more than 200, and `sources.github` says why. `null`: `required_checks` is a list, so it names no repository and none was looked for. An empty list means "none open" only when this is `true`. |
| `queue_room` | object \| null (optional) | KIT-474. The queue's room hold, from the bridge's record (the `room_file` setting; empty turns it off). `null`: the job is told not to look, or the queue has not yet read the machine. See *The queue's room*. |
| `kit_clone` | object \| null (optional) | KIT-581. Whether the checkout these jobs run from, the role account's copy of the kit, is behind main. `null`: the job is not told a repository (`kit_repo`). See *The kit copy*. |
| `findings` | finding[] | The heartbeat monitor's view of `sources`, in the health watch's words. |
| `firing` | string[] | Empty unless `result` is `failed`: then the sources that failed, which the monitor pages on. |

Times are UTC to the second (`2026-11-03T18:05:00Z`), or null where the source gave none.

### A source

| Field | Type | Notes |
|---|---|---|
| `state` | `read` \| `partly-read` \| `could-not-read` \| `off` | `read`: every question was answered. `partly-read`: some were not. `could-not-read`: none was, or none could be asked. `off`: the config turns it off (`reviews` only). |
| `asked` | integer | Questions put to it this pass: tracker queries; GitHub requests (a probe, then each pull request, each check-run listing, each comment page and each page of a repository's open pull requests); review records looked for. |
| `answered` | integer | How many it answered. A review record that does not exist is an answer: no review yet. |
| `reason` | string \| null | Why it was not read whole: a missing key, an HTTP status, a timeout. Null when it was, or when it is off. |
| `read_at` | time \| null | When this pass last had an answer from it. |

A reader says "I couldn't check" for a source that is not `read`. It never guesses (KIT-348).

### The kit copy

A merged fix runs nowhere until the role account's clone moves, and only the one command moves it (KIT-309). The status job runs from that clone, so it can say when the clone is behind. It reads the clone's head from the files in `.git` (no process), reads which files the jobs run out of the clone's own copy of the installer's lists (`REQUIRED_SCRIPTS`, the sibling jobs' files and `RUNTIME_DATA_DIRS`, with `ast.literal_eval` and no import), and asks GitHub one read-only question: compare the head with the branch (`kit_repo`, `kit_branch`; `kit_clone` names another checkout). It does not count against `sources`, and a question it cannot answer never fails the pass.

| Field | Type | Notes |
|---|---|---|
| `state` | `current` \| `behind` \| `unknown` | `behind`: a file the jobs run, or a schema they read, changed between the head and the branch. `current`: none did (a document or a test may have): the same rule `verify` in the Stage E installer uses, so it does not cry wolf on every merge. `unknown`: the question could not be answered, or GitHub listed too few of the changed files to say none was a job file; never read as current. |
| `head` | string \| null | The checkout's head commit, twelve characters. |
| `merges_behind` | integer \| null | How many commits the branch is ahead. |
| `files` | string[] | At most ten of the changed job files. A name is kept only when it is one of the jobs' own files under `scripts/` or `schemas/`. |
| `files_total` | integer \| null | How many changed job files there were. Null when GitHub listed too few to say. |
| `checked_at` | time \| null | When this pass asked. |
| `reason` | string \| null | Why it is `unknown`, in fixed words (no .git, a HEAD it does not read, an installer it cannot read, GitHub not knowing the head, no token, a checkout holding commits the branch lacks, more files changed than GitHub lists); also on a `behind` that GitHub's cut-off list shows is true but whose count is only a floor (`files_total` is then null). |

The stand-up says `behind` in one plain line in both rundowns while it stands, and `unknown` as not known. It says nothing when the copy is current or the field is absent. A clone with local edits, and a job not installed on this machine, are not seen here: the line counts every job's files.

### The queue's room

While the machine is short of memory, free disk or swap, the queue starts nothing new (KIT-457). The bridge writes what its queue pass found as a small record beside its heartbeat (`queue-room.json`, `pipeline-queue-room/1`), and the status job copies it into `queue_room` as fields only, so the file and the stand-up can say it in their own fixed words.

| Field | Type | Notes |
|---|---|---|
| `state` | `held` \| `clear` \| `unread` | `held`: nothing new starts for want of room. `clear`: the machine was read and has room. `unread`: the record is there and cannot be read or is not a room record, with `reason`; never taken as room. |
| `since` | time \| null | When this hold began (`held` only). |
| `resumed_at` | time \| null | When the last hold ended (`clear` only; null until one has). |
| `checked_at` | time \| null | When the bridge last read the machine. A closed working window or the watcher's hold leaves it as it was, so a reader judges its age. |
| `lines` | line[] | `held` only: each line the machine is over, at most three. |
| `reason` | string \| null | `unread` only, in fixed words. |

A line is `{"line", "reading", "limit", "unit"}`: `line` is `pressure`, `disk` or `swap`; `reading` is the number read, or `null` when it could not be taken (a line that could not be read is over, never room); `limit` is the line it is held to; `unit` is `level` (memory pressure: 1 normal, 2 warn, 4 critical) or `GB`.

### A plan

| Field | Type | Notes |
|---|---|---|
| `id` | string | The epic's ticket id. |
| `title` | string | Cleaned. |
| `url` | string \| null | The ticket's link in the tracker. |
| `team` | string | The team key. |
| `state` | string | The state's name, cleaned. |
| `state_type` | enum | The tracker's own kind: `triage`, `backlog`, `unstarted`, `started`, `completed` or `canceled`; `unknown` for any other. |
| `created_at`, `updated_at`, `completed_at`, `canceled_at` | time \| null | From the tracker. |
| `pieces_complete` | boolean | False when the plan has more children than one read holds (50). |
| `pieces` | piece[] | In ticket-number order. |

### A piece

| Field | Type | Notes |
|---|---|---|
| `id`, `title`, `url`, `state`, `state_type` | as a plan's | |
| `started_at`, `completed_at`, `canceled_at`, `updated_at` | time \| null | A piece's start and finish, for the evening wrap (KIT-349). |
| `flags` | string[] | The lifecycle labels on it, from a closed list: `agent:blocked`, `agent:needs-human`, `blocked:capacity`, `hooks-change` (§6). |
| `prs_complete` | boolean | False when it has more links than one read holds (25). |
| `prs` | pull request[] | Empty when no pull request is linked yet. |
| `session` | object \| null (optional) | KIT-449, open pieces only. The piece's newest agent session: `id`, `status` (`pending`, `active`, `awaitingInput`, `error`, `complete`, `stale`), `created_at`, `started_at`, `ended_at`. Null when it has none. One listing a pass, capped at 500 sessions; a listing it could not finish makes a piece it did not reach `unknown` (id null), never null, and the tracker `partly-read`. |

### A pull request

| Field | Type | Notes |
|---|---|---|
| `repo` | string | `owner/name`. |
| `number` | integer | |
| `url` | string | Its GitHub link. |
| `source` | `github` \| `tracker` | `github`: GitHub answered this pass. `tracker`: it did not, so `state` is what the tracker's link says, and `error` says why. |
| `error` | string \| null | Why GitHub was not read for it, or for its check runs. Null when both were. |
| `state` | `open` \| `merged` \| `closed` \| `unknown` | |
| `draft` | boolean \| null | |
| `opened_at`, `updated_at`, `merged_at`, `closed_at` | time \| null | From GitHub. |
| `head_sha` | string \| null | The head commit, cut to 12 characters. |
| `base` | string \| null | The branch it merges into. |
| `mergeable` | `mergeable` \| `conflicting` \| `unknown` | A conflict that waits on a person (KIT-350). `unknown` until GitHub has worked it out. |
| `merge_state` | string \| null | GitHub's own word: `clean`, `dirty`, `blocked`, `behind`, `unstable`, `draft`, `has_hooks` or `unknown`. |
| `labels` | string[] | Pipeline labels from a closed list: `conflict` (the conflict loop's) and `hooks-change`. |
| `checks` | object | `result`, and `required`: one check below per name the config lists for this repository, in its order. |
| `review` | object | The newest Stage E review, below. |
| `conflict_gave_up` | object \| null (optional) | KIT-448. The `episode` the conflict loop gave up on, and `at` when, read from its `escalated` or `page` marker; only while the PR still conflicts. Null otherwise. |
| `union_red` | object \| null (optional) | KIT-448. The `key` of the newest red union verdict that names this PR and judged its current head, and `at` when, read from its `pr-union:red` marker and the `pr-union:head` line under it (KIT-350). A verdict on an earlier head, or one that names no head, is not it, so a push that fixed the PR leaves no red behind. A later green verdict posts nothing, so at the same head this can outlive the red. Null when none. |

Both markers count only when `github-actions[bot]` wrote them. Sessions post as the owner, so a
look-alike under any other login is never read. Comments that cannot be read leave both null
and make the GitHub source `partly-read`.

`checks.result` is `passing` when every named check passed, and `failing` when one that does
not wait on a person failed. It is `pending` when one that does not wait on a person is
running or has not started, and `waiting` when only checks that wait on a person are not
passing. `none` means the config
names no check for this repository, and `unknown` means the checks were not read.

### A check

| Field | Type | Notes |
|---|---|---|
| `name` | string | The check's name from `required_checks` in the job's config, cleaned like a title. Runs are matched on the name as configured. |
| `state` | `passing` \| `failing` \| `pending` \| `missing` \| `unknown` | The newest run of that name on the head commit. `missing`: no run of that name. `unknown`: the runs were not read, or GitHub listed only some and this name was not among them; the pull request's `error` says which. |
| `waits_on_person` | boolean | True for a check only a person turns green, such as the `hooks-change` label's guard (`human_pending_checks` in the config). |

### A review

| Field | Type | Notes |
|---|---|---|
| `state` | enum | `none`: no review on record. `clean`: reviewed, nothing found. `notes`: findings below the threshold. `findings`: findings at or above it, so Stage E sends it back. `partial`: whole files were withheld from the reviewer and nothing it saw met the threshold, so a person reviews the rest; never `clean` (KIT-138). `not-reviewed`: the reviewer could not judge it. `unknown`: the record could not be read, or reviews are off. |
| `at` | time \| null | When the review was recorded. |
| `max_severity` | `low` \| `medium` \| `high` \| `critical` \| null | |
| `findings` | integer \| null | How many findings. Never what they say. |
| `current` | boolean \| null | True when the review judged the pull request's current head. Null when either head is unknown. |

### A finding

| Field | Type | Notes |
|---|---|---|
| `kind` | `tracker` \| `github` \| `reviews` | The source. |
| `state` | `unknown` \| `firing` | `unknown` never pages. `firing` pages, and only on a `failed` pass. |
| `summary` | string | The source's state and reason, on one line. |

### Exit codes and the heartbeat

The file is the job's heartbeat, in the health watch's words (`findings`, `firing`), so the
heartbeat monitor can read it the way it reads the health watch's file. Its row is KIT-411's,
with the step that installs the job. `ok` and `partial` are good results, and a source not
read whole is a finding that does not page: one source down for a pass is a network blip or a
dark wake, and the jobs that use that source page it if it stays down. `failed` fires its
sources, a pass that stopped on an error carries `error`, and a file that stops being written
is stale.

**A pass with no network is `partial`, not `failed`, when reviews are on.** The tracker and
GitHub are `could-not-read`, and the review records are still `read`, with nothing to look
up. That is deliberate: a laptop makes dark wakes with no network all night, and a page must
mean something is broken. The review poller, the bounce driver and the catch-up poller use the
same tracker key and GitHub token, and they page an outage that lasts. `failed` is reached
when the review records cannot be read either, or reviews are off, or the pass crashes.

**A source unread for two awake hours fires (KIT-415).** Relying on the other jobs to page a long
outage left this job quiet for a day with no network, or after an env file stopped loading. So the
job keeps a count, in `blind_state_file` (default `~/.stage-e/state/status-blind.json`, mode 600,
outside every git working tree). For each of the tracker and GitHub, a pass that could not read it
adds that pass's awake time to the count, and a pass that read it clears the count. Once a source has
been unread for two awake hours over at least two passes, its finding is `firing`, with a summary
naming how many awake minutes, so the heartbeat monitor pages it once. The time is awake time, summed
pass by pass from a clock that stops while the Mac sleeps, as the catch-up poller counts it, so a
night of dark wakes adds up to nothing and a reboot restarts the count. A dry run counts nothing.
The result stays `partial` and the exit stays 3. An empty `blind_state_file` turns the count off.

| Exit | Meaning |
|---|---|
| 0 | Every source was read, and the file was written. |
| 3 | The file was written, and at least one source was not read whole. It names which. A pass with no network lands here when reviews are on. |
| 1 | No source that is on could be read, the pass crashed, or the file could not be written or did not conform. A file that did not conform is not written at all. |
| 2 | Usage or config: nothing was read or written. |

A `--dry-run` reads everything, checks the document, prints its summary and where it would
write, and writes nothing. `--print` also prints the document.

---

## 17. Usage file

One file says what the pipeline spent: the dollars each local day, the dollars since the plan's
weekly reset by kind of work and by piece, how long its sessions ran, Anthropic's newest word on
each usage window, how each session signed in, the runs that never reached the model, and
the runs the plan's limit cut off (KIT-422, KIT-470). The usage job, `scripts/pipeline_usage_local.py`, writes it each pass. It runs with
no AI, no network and no credential, as the role account, like the status job. It reads files
and writes one file. It starts nothing and stops nothing. Machine-readable:
`schemas/pipeline-usage.schema.json`. The job checks its document against that schema before it
writes, and writes nothing that does not conform.

**Fields only.** Every value is a number, a ticket id, a model name that matches a strict
pattern, a word from a closed list, a local date, or a time to the second in UTC. No prompt,
reply, comment or ticket title reaches the file, and nothing the job prints quotes one. A model
name starts with a lower-case letter or digit, has at most 64 characters, and uses only
letters, digits and `.`, `_`, `-`, `[` and `]`. Any other name is `other`, and its spend still
counts. An `error` line names the error's class and the line it came from, never the error's own
words, because a log's text can ride in one.

**An estimate, not a bill.** A dollar figure is the SDK's own estimate at API prices, as its
`result` lines report it. Under a plan sign-in no money moves: the figure says how much work was
done. A session that signs in with a key is billed by the token, and `sign_in` lists those runs.

**Reporting, never authority.** A later queue reads this file to decide when new work may start.
Nothing reads it to approve a ticket or to merge. The files the job reads belong to the
dispatcher and the role account and sit outside every worktree, so a session cannot write them.

### Where the facts come from

| Source | What it gives | Read how |
|---|---|---|
| `logs` | Every run: `<dispatcher_home>/logs/<workspace>/session-<session id>-<UTC start>.jsonl`, for each file modified within `lookback_days` plus one day. From its `sdk-message` lines: the last `result` line's running totals, the first and last timestamped line, the `init` line's sign-in source, the kind of the first prompt, and the `rate_limit_event` lines. From its first `session-metadata` line: when it started. A `session-pending-<UTC start>.jsonl` file is a run's placeholder. | The file system, as the role account |
| `ledger` | Stage E's `<stage_e_state_dir>/bounce-ledger.jsonl`, the rows whose outcome is `delivered` or `conflict-delivered`, and `<stage_e_state_dir>/outcomes/*.json`, which say each pull request's ticket. | The file system, as the role account |
| `planner` | The idea gate's `<stage_a_state_dir>/seen.json`. Each trigger's `run_ticket` is a planning run. | The file system, as the role account |

A line that is not JSON, or is longer than 8 MiB, is passed over. A workspace is a folder under
`logs/`: a ticket's id, or `slack-` and a thread's id for a chat. A *run* is one log file in it:
a session's start, or one of its resumes.

### What a run was

A run is the first of these that fits:

| Kind | A run is this when | Told from |
|---|---|---|
| `tech_lead` | its folder starts with `slack-` | the folder |
| `review` | its ticket is in the review poller's own record (`<stage_e_state_dir>/review-tickets.json`), or, for an old Reviews team, its folder starts with `reviews_team_key` and a `-` (KIT-596) | `record`, or the folder |
| `planner` | its folder is a planning run's ticket in the idea gate's record | the idea gate's record |
| `coding` | it is the first run in its folder, by the log file names: an older log outside the window still counts as the first | the file names |
| `bounce_fix`, `conflict_fix` | it starts from 60 seconds before to 30 minutes after a `delivered` (or `conflict-delivered`) ledger row for that ticket; failing that, its first prompt carries the bounce driver's fixed line for a bounce (or a conflict fix) | the ledger, then the prompt |
| `catch_up` | its first prompt carries the catch-up job's fixed line | the prompt |
| `other_later` | any other later run: a person's reply, or a mention | nothing marks it |
| `judge` | not a dispatcher run at all: a model call the watcher's judge made (KIT-569) | the judge's calls file, `judge-calls.json` (`judge_file`) |

**The judge's calls (KIT-569).** The dispatcher's logs hold no judge call, so the judge keeps its own record: each call's time and
cost, fields only. When the usage job's config names it (`judge_file`), every call counts as one run of kind `judge`, in the local
day it was made and, when the week is known, in the week from the plan's reset: its dollars are in the day's and the week's `usd`
and in `by_kind.judge`, so the watcher's day limit sees them. A call before the lookback is not counted. The top-level `judge` says
whether the record was read: `read`; `off` (none is named); or `could-not-read`, when the file is not there (a `judge_file` naming a file the judge has not written yet), or is unreadable, or not a record
the job knows, or held a call row it does not know. Then **no call is counted, and the file says so, never zero**, and `result` is
`partial`. The job reads only the times and the dollars; it never reads the judge's words.

The record is `{"schema": "pipeline-watcher-judge-calls/1", "calls": [{"at": "<UTC, to the second>", "usd": <dollars, 0 or more, or null when
the call could not be priced>}, ...], ...}`. A call row holds those two keys and no others; a null cost counts as an unpriced run,
never as zero dollars. More than 20000 rows, or a record over 4 MiB, is `could-not-read`.

The fixed lines are `Stage E bounce`, `Stage E conflict fix` and `Catch-up`, as the bounce driver
and the catch-up job write them; the selftest reads their source to keep this true. The kind of
a first prompt is kept, and its words never are. A conflict fix's own record line says "sent by
the Stage E bounce driver", so the conflict marks are looked for before the bounce marks. A
ledger row outranks a prompt. A source that is `off` or was not read leaves its kinds to be
told from what is left: a planning run then shows as `coding`, and a bounce fix as what its
prompt says. The file says so in `sources`.

### What a run cost

A `result` line reports the session's running totals: `total_cost_usd`, and for each model in
`modelUsage` its cost and its input, output, cache read and cache write tokens. A run's spend is
its own share of those totals:

- It is the last `result` line in the run's file, less the same figure from the session's
  previous run: the log file of the same session id that started before it.
- A figure smaller than the previous one means the session started again, so the whole figure is
  the share.
- A crash's `result` (`is_error` true, and a total of zero or none) and a run with no `result`
  yet have no usable total. They are *unpriced*: counted in `runs` and `unpriced`, left out of
  `usd`, never counted as zero. They are never the next run's base, so their spend lands in the
  next priced run. An error `result` that does carry a total is priced like any other.
- A session's total holds every turn before the window. So for the first run of a session that
  the window holds, the job reads the nearest older logs of that session in its folder (at most
  3, newest first) for their last total only, and uses it as the base. They are not counted as
  runs. If none gives a total, the run's share is its own total. A long chat thread would
  otherwise count the whole thread again each day.
- A reply's own `usage` line is never used: it is logged as the reply starts, and it undercounts
  the output.

A run's duration is the seconds from its first to its last timestamped line.

### Days, the week and the pieces

A run counts on the local date of its last line, on the clock of the machine that ran the job.
`days` has one entry for each local date in the lookback, today last: `lookback_days` (default
8, from 1 to 31) dates. Eight is the least that always reaches back to a week's start.

The plan's week starts at the reset the newest `seven_day` event names, less seven days. The
week is *unknown* (`known` is false and every other field of `week` is null) when no `seven_day`
event names a reset, when the newest one's week is over or has not begun, or when the lookback
stops short of the week's start. In the last case `sources.logs` says so, and the exit code is 3.
An unknown week is never zero and never a guess.

`pieces` lists each ticket that spent anything since the week began. A chat run counts in
`tech_lead` and in the totals, and is never listed one by one.

### Runs the plan's limit cut off

When the plan's five-hour or weekly window is at its limit, a running session is cut off. The
log shows it as a `rate_limit_event` whose status is `rejected`, then a `result` with `is_error`
true. A run is *limit-stopped* when it heard a `rejected` event and the first `result` after the
newest one is an error, or no `result` follows at all. A rejection followed by a success is a
run that went on. A warning (`allowed_warning`) is not a rejection. An event with no timestamp
is not counted, as no event without one is.

`limit_stops` lists each limit-stopped run in the lookback, newest first, by its last line:
`ticket` (its id; null for a chat thread), `kind`, `ended_at`, `window` (the rejected window's
word, `other` for one the list lacks) and `resets_at` (null when the rejection named no reset,
or one that is not a time). The queue's pass reads it (KIT-472) to hand a stopped coding piece over once
more after `resets_at`, once for each stop. It is a runaway cap, not a limit: at most 200 are listed, and
`limit_stops_over_cap` says how many the cap left out (0 when none). When the logs could not be
read both are unknown: `limit_stops` is empty and `limit_stops_over_cap` is null, never 0.
Words from the closed lists and numbers only: nothing a run said is kept.

### Shape

```json
{
  "schema": "pipeline-usage/1",
  "written_at": "2026-11-03T18:05:00Z",
  "started_at": "2026-11-03T18:04:31Z",
  "result": "partial",
  "error": null,
  "sources": {
    "logs": {"state": "read", "asked": 212, "answered": 212, "reason": null, "read_at": "2026-11-03T18:04:58Z"},
    "ledger": {"state": "partly-read", "asked": 7, "answered": 6, "reason": "a review record could not be read", "read_at": "2026-11-03T18:04:59Z"},
    "planner": {"state": "read", "asked": 1, "answered": 1, "reason": null, "read_at": "2026-11-03T18:04:59Z"}
  },
  "judge": {"state": "read", "reason": null},
  "week": {
    "known": true,
    "starts_at": "2026-10-30T09:00:00Z",
    "resets_at": "2026-11-06T09:00:00Z",
    "usd": 41.337,
    "runs": 130,
    "unpriced": 2,
    "seconds": 51234,
    "by_kind": {
      "tech_lead": {"runs": 20, "usd": 3.1}, "review": {"runs": 18, "usd": 6.5},
      "planner": {"runs": 4, "usd": 2.4}, "coding": {"runs": 52, "usd": 21.3},
      "bounce_fix": {"runs": 14, "usd": 4.2}, "conflict_fix": {"runs": 3, "usd": 0.9},
      "catch_up": {"runs": 1, "usd": 0.15}, "other_later": {"runs": 18, "usd": 2.787},
      "judge": {"runs": 0, "usd": 0}
    },
    "by_model": {"claude-opus-5-5": 30.2, "claude-sonnet-5-5": 11.137}
  },
  "days": [
    {"date": "2026-11-02", "usd": 9.5, "runs": 31, "unpriced": 0, "seconds": 11800, "by_kind": {
      "tech_lead": {"runs": 4, "usd": 0.8}, "review": {"runs": 5, "usd": 1.9},
      "planner": {"runs": 1, "usd": 0.6}, "coding": {"runs": 10, "usd": 4.1},
      "bounce_fix": {"runs": 4, "usd": 1.2}, "conflict_fix": {"runs": 1, "usd": 0.3},
      "catch_up": {"runs": 0, "usd": 0}, "other_later": {"runs": 6, "usd": 0.6},
      "judge": {"runs": 0, "usd": 0}}},
    {"date": "2026-11-03", "usd": 7.25, "runs": 24, "unpriced": 2, "seconds": 9100, "by_kind": {
      "tech_lead": {"runs": 6, "usd": 0.9}, "review": {"runs": 4, "usd": 1.4},
      "planner": {"runs": 0, "usd": 0}, "coding": {"runs": 9, "usd": 3.55},
      "bounce_fix": {"runs": 2, "usd": 0.5}, "conflict_fix": {"runs": 0, "usd": 0},
      "catch_up": {"runs": 1, "usd": 0.15}, "other_later": {"runs": 2, "usd": 0.75},
      "judge": {"runs": 0, "usd": 0}}}
  ],
  "pieces": [
    {"id": "ENG-301", "kind": "coding", "usd": 7.25, "runs": 6, "last_at": "2026-11-03T16:40:12Z"},
    {"id": "REV-88", "kind": "review", "usd": 1.1, "runs": 2, "last_at": "2026-11-03T15:20:41Z"}
  ],
  "status": {
    "windows": {
      "five_hour": {"status": "allowed", "utilization": 0.42, "surpassed": null, "resets_at": "2026-11-03T21:00:00Z", "seen_at": "2026-11-03T18:03:12Z"},
      "seven_day": {"status": "allowed_warning", "utilization": 0.77, "surpassed": 0.75, "resets_at": "2026-11-06T09:00:00Z", "seen_at": "2026-11-03T18:03:12Z"}
    },
    "events_per_day": 38.5
  },
  "sign_in": {
    "counts": {"none": 150, "ANTHROPIC_API_KEY": 2},
    "api_key_runs": [{"id": "ENG-307", "at": "2026-11-03T09:12:00Z"}]
  },
  "never_reached_model": 1,
  "limit_stops": [
    {"ticket": "ENG-312", "kind": "coding", "ended_at": "2026-11-03T17:41:09Z", "window": "five_hour", "resets_at": "2026-11-03T19:00:00Z"}
  ],
  "limit_stops_over_cap": 0
}
```

The example leaves out six of the lookback's eight days, and five of the week's pieces, for
room. The ledger's review record that could not be read makes the pass `partial`. Two runs
signed in with a key, and one of them was a chat, so one is listed.

### The file

| Field | Type | Notes |
|---|---|---|
| `schema` | `"pipeline-usage/1"` | A reader that does not know the value refuses the file. |
| `written_at` | time | When this pass wrote it. Its age is the answer to "how old is this?". |
| `started_at` | time | When this pass began reading. |
| `result` | `ok` \| `partial` \| `failed` | `ok`: every source that is on was read whole. `partial`: at least one was not (exit 3), and `sources` says which and why. `failed`: the logs could not be read, no source could be, or the pass stopped on an error (exit 1). |
| `error` | string \| null | Null, unless the pass stopped on an unexpected error: then one line with the error's class and where it came from, and every source that is on is `could-not-read`. |
| `sources` | object | `logs`, `ledger` and `planner`, each a source below. |
| `week` | object | Since the plan's weekly reset, below. |
| `days` | day[] | One for each local date in the lookback, oldest first. Empty when the logs could not be read. |
| `pieces` | piece[] | At most 1000, the costliest first. Empty when the week is unknown. |
| `status` | object | What Anthropic last said about each usage window, below. |
| `sign_in` | object | How each run signed in, below. |
| `never_reached_model` | integer \| null | Runs in the lookback that left only a placeholder log: they started and never heard from the model. A placeholder counts only when no run's start matches it. Null when the logs could not be read. |

Times are UTC to the second (`2026-11-03T18:05:00Z`), or null where the source gave none.

### A source

| Field | Type | Notes |
|---|---|---|
| `state` | `read` \| `partly-read` \| `could-not-read` \| `off` | `read`: every question was answered. `partly-read`: some were not. `could-not-read`: none was, or none could be asked. `off`: the config turns it off (`ledger` and `planner` only). |
| `asked` | integer | Questions put to it this pass: run files, placeholders and older logs read, and folders that could not be listed; the ledger, the review records' folder and each record; the idea gate's record. |
| `answered` | integer | How many it answered. A ledger or record that does not exist yet is an answer: nothing yet. |
| `reason` | string \| null | Why it was not read whole, in the job's own words. Null when it was, or when it is off. |
| `read_at` | time \| null | When this pass last had an answer from it. |

A reader says "I couldn't check" for a source that is not `read`. It never guesses. A state
folder that does not exist is `could-not-read` and says which config key to check, or to set to
an empty string to turn the source off.

### The week

| Field | Type | Notes |
|---|---|---|
| `known` | boolean | True when the newest weekly event names a reset still ahead and the lookback reaches the week's start. False otherwise, and then every field below is null. |
| `starts_at` | time \| null | The weekly reset less seven days. |
| `resets_at` | time \| null | The reset the newest `seven_day` event that names one gives. |
| `usd` | number \| null | The week's spend, to a millionth of a dollar. |
| `runs` | integer \| null | Runs in the week, priced or not. |
| `unpriced` | integer \| null | Runs with no usable total, counted in `runs` and never in `usd`. |
| `seconds` | integer \| null | How long the week's sessions ran, summed over runs. |
| `by_kind` | object \| null | Runs and spend for each kind in the table above. Every kind is present, with zeros for one that did not run. |
| `by_model` | object \| null | Spend by the model that ran it, from `modelUsage`. It can fall short of `usd` when a result named no model. At most 64 models: past that the cheapest are folded into `other`, and the spend still adds up. |

### A day

| Field | Type | Notes |
|---|---|---|
| `date` | string | A local date, `YYYY-MM-DD`. |
| `usd`, `runs`, `unpriced`, `seconds` | | As the week's. |
| `by_kind` | object | As the week's. |

### A piece

| Field | Type | Notes |
|---|---|---|
| `id` | string | The ticket's id: the workspace's folder name, when it is one. |
| `kind` | `review` \| `planner` \| `coding` | The kind of the ticket's workspace. Every run in it counts toward the piece, whatever its own kind. |
| `usd` | number | The sum of its priced runs this week. |
| `runs` | integer | All its runs this week. |
| `last_at` | time | The last line of its newest run. |

### The usage windows

`status` is what Anthropic last said about the plan's usage windows, as the dispatcher heard it.

| Field | Type | Notes |
|---|---|---|
| `windows` | object | The newest event for each window heard, below. A window never heard is absent. |
| `events_per_day` | number \| null | Usage events heard in the lookback, divided by its days: a sign of how fresh the newest word is. An event that names no window counts here and is not listed. Null when the logs could not be read. |

The windows are named as the SDK names them:

| Window | What it is |
|---|---|
| `five_hour` | The five-hour window. |
| `seven_day` | The weekly window. Its reset sets the week. |
| `seven_day_opus`, `seven_day_sonnet` | The weekly windows kept for one model. |
| `seven_day_overage_included`, `overage` | The SDK's words for the plan's extra usage. |
| `other` | An event whose window is not one of these. |

### A window's entry

| Field | Type | Notes |
|---|---|---|
| `status` | `allowed` \| `allowed_warning` \| `rejected` \| `other` | Anthropic's word for the window. `other`: a word this list does not have. |
| `utilization` | number \| null | How much of the window is used, as a fraction, or null when the event gave none. |
| `surpassed` | number \| null | The warning threshold the window has passed, or null. |
| `resets_at` | time \| null | When the window resets, or null. |
| `seen_at` | time | When the dispatcher heard it. |

### Sign-in

| Field | Type | Notes |
|---|---|---|
| `counts` | object | Runs in the lookback by the SDK's own word for how they signed in: `ANTHROPIC_API_KEY`, `apiKeyHelper`, `/login managed key`, `none`, `user`, `project`, `org`, `temporary` or `oauth`. `other`: a word that list does not have. `not seen`: a run with no `init` line, or one that names no source. |
| `api_key_runs` | object[] | Each run on a ticket that signed in any way but the plan's own (`none` or `oauth`): a key, a key helper, another source the SDK names, or `other`. The newest first, at most 500: its ticket `id` and the time it started, `at`. A chat run is counted in `counts` and never listed. A run whose source was `not seen` is counted, never listed. |

A plan sign-in draws on the plan's windows. A key is billed by the token, and a worktree `.env`
can put one in a session without anyone choosing it (`docs/AUTONOMY.md`). `api_key_runs` is how
that shows.

### The job's config

| Key | Default | Meaning |
|---|---|---|
| `dispatcher_home` | `~/.cyrus` | The dispatcher's folder, whose `logs/` holds every session's log. |
| `stage_e_state_dir` | `~/.stage-e/state` | Stage E's state folder. An empty string turns the `ledger` source off. |
| `stage_a_state_dir` | `~/.stage-a/state` | The idea gate's state folder. An empty string turns the `planner` source off. |
| `judge_file` | empty: off | The watcher judge's `judge-calls.json` (KIT-569): each call's time and dollars, no words. Empty turns the judge's cost off, and the file's `judge` says `off`. |
| `reviews_team_key` | empty | Optional (KIT-596): the key of an old separate Reviews team, such as `REV`. A review on a work team is told by the review poller's record, and a record that cannot be read is said in the Stage E source. |
| `out` | `~/.stage-e/usage/usage.json` | Where the file is written, outside every git working tree. |
| `lookback_days` | 8 | How many local dates the file covers, from 1 to 31. |

`~` is the account's home from the account database, never `$HOME`. An unknown key is a usage
error.

### Exit codes and the heartbeat

The file's `written_at` is the job's heartbeat. Installing the job and watching that heartbeat
are not part of this section. A pass that crashes still writes a `failed` file whose `error` says
where, and a file that stops being written is stale.

| Exit | Meaning |
|---|---|
| 0 | Every source that is on was read whole, and the file was written. |
| 3 | The file was written, and at least one source was not read whole. It names which. |
| 1 | The logs could not be read, no source that is on could be, the pass crashed, or the file could not be written or did not conform. A file that did not conform is not written at all. |
| 2 | Usage or config: nothing was read or written. |

A `--dry-run` reads everything, checks the document, prints its summary and where it would
write, and writes nothing. `--print` also prints the document.

### Limits worth knowing

- A restart of a session is seen only when its new total is smaller than the old one.
- A run still going has no `result` yet, so it is unpriced until a later pass.
- `coding` is told by the log file names. A folder whose oldest logs were deleted shows the
  oldest one left as its first run.
- A piece is a folder named as a ticket id. A folder with any other name counts in the totals and
  is never listed.

---

## 18. Day plan file

One file says what is planned for today: who planned the day, the working window, the day's budget,
each lane and its pieces, what waits on the owner and the exact reply that clears it, the
agent-filed tickets that start by themselves, the picks the checks dropped, and what holds new
starts (KIT-438). The morning planner writes it from the queue's decision
(`scripts/pipeline_queue_order.py`), through `scripts/pipeline_day_plan.py`, and again as the day
goes. The stand-up's morning message reads today's plan and its "needs your OK" items from it, its
afternoon message reads the day's spend, and the tech lead and a later app read it the same way,
with no model between. Machine-readable: `schemas/pipeline-day-plan.schema.json`. The writer checks
its document against that schema before it writes, and writes nothing that does not conform.

**Fields only.** Every value is an id, a cleaned title, a number, a word from a closed list, a time,
or a reply built by code from one of two fixed patterns. A cleaned title is the status file's
(§16): one line, at most 120 characters, with no angle bracket, ampersand, pipe or backtick, and no
hidden character. It is the only text from a ticket that reaches the file. A reply is built, never
copied: `reply` is null, or exactly `start <ID>` or `approve <ID>`, built from the item's kind and
ref, and only when the queue named a reply for that same ticket.

**Reporting, never authority.** Nothing reads this file to decide what to start, approve or merge.
The writer refuses a folder inside a git working tree.

### Shape

```json
{
  "schema": "pipeline-day-plan/1",
  "written_at": "2026-11-04T14:05:00Z",
  "date": "2026-11-04",
  "planned_by": {"who": "agent", "why": null},
  "window": {"state": "open", "opens_at": "2026-11-04T14:00:00Z", "closes_at": "2026-11-04T20:00:00Z"},
  "budget": {"planned": 40, "stop": 60, "spent": 6.5, "estimated": 4},
  "lanes": [
    {"id": "acme/app:1", "repo": "acme/app", "pieces": [
      {"id": "ENG-301", "title": "Back the palette with CSS variables", "estimate": 2, "state": "started", "held": null},
      {"id": "ENG-302", "title": "Add the dark theme", "estimate": 4, "state": "held", "held": "blocked"}]},
    {"id": "acme/app:2", "repo": "acme/app", "pieces": [
      {"id": "ENG-311", "title": "Fix the login redirect", "estimate": 2, "state": "planned", "held": null}]}
  ],
  "needs_ok": [
    {"kind": "plan", "ref": "ENG-320", "title": "Offline mode", "holds_up": {"pieces": 3, "usd": 14}, "reply": "approve ENG-320", "urgent": true},
    {"kind": "merge", "ref": "acme/app#40", "title": null, "holds_up": {"pieces": 1, "usd": 4}, "reply": null, "urgent": false}
  ],
  "agent_filed": [{"id": "ENG-311", "source": "ENG-290"}],
  "dropped": [{"id": "ENG-330", "reason": "over-load"}],
  "stops": [],
  "counts": {"lanes": 2, "pieces": 3, "planned": 1, "started": 1, "done": 0, "held": 1, "unplaced": 0, "needs_ok": 2, "agent_filed": 1, "stops": 0, "dropped": 1}
}
```

### The file

| Field | Type | Notes |
|---|---|---|
| `schema` | `"pipeline-day-plan/1"` | A reader that does not know the value refuses the file. |
| `written_at` | time | When this write was made. |
| `date` | string | The local date the plan is for, `YYYY-MM-DD`: the usage file's day. |
| `planned_by` | object | Who planned the day, below. |
| `window` | object | The working window the plan is for, below. |
| `budget` | object | The day's load, its hard stop and the spend, below. |
| `lanes` | lane[] | At most 100, by repository and then slot. |
| `needs_ok` | item[] | What only the owner can clear: the most urgent first, then the most that waits on it. At most 200. |
| `agent_filed` | start[] | The agent-filed tickets in today's plan, each with the ticket it came from. At most 200. |
| `dropped` | drop[] | The picks the checks dropped from the planner's answer. At most 500. |
| `stops` | stop[] | What holds every new start, as the queue names it. Empty when nothing does. |
| `counts` | object | How many of each, below. |

Times are UTC to the second, or null where the source gave none. Dollars are the SDK's own estimate
at API prices (§17): a measure of plan use, never a bill.

### Planned by, the window and the budget

| Field | Type | Notes |
|---|---|---|
| `who` | `agent` \| `queue` | `agent`: its picks, checked by code, made the lanes. `queue`: the plain queue planned the day, by priority. |
| `why` | word \| null | Null when the agent planned. Else why not: its answer was not used (`no-answer`, `too-long`, `no-block` (no fenced json block), `several-blocks`, `unreadable` (the block is not the answer's shape) or `nothing-valid` (every pick failed a check)), or it was not asked (`nothing-admitted`: no piece was admitted; `off`: the planner is switched off). |
| `state` | `open` \| `closed` \| `unknown` | `closed`: `opens_at` is the next opening. `unknown`: the window could not be read, and nothing is guessed. |
| `opens_at`, `closes_at` | time \| null | The window the state speaks of. |
| `planned`, `stop` | number | The day's planned load, and its hard stop: spend at or over it holds every new start. |
| `spent` | number \| null | Today's spend from the usage file. Null when it could not be read, never zero. |
| `estimated` | number | The estimates of today's pieces that are planned or started. A piece with no effort counts at the largest. |

### A lane and a piece

| Field | Type | Notes |
|---|---|---|
| `id` | string | A lane's id is its repository and a slot number, `owner/name:1`, the queue's own: the same tomorrow as today. A piece's is its ticket. |
| `repo` | string | `owner/name`. |
| `pieces` | piece[] | In queue order, finished and started pieces first. At most 500. |
| `title` | string \| null | Cleaned. Null when the ticket has none. |
| `estimate` | number \| null | The queue's estimate for its effort, or null when it names none. |
| `state` | enum | `planned`: the queue may start it now. `held`: it cannot. `started` and `done`: the caller said so. |
| `held` | word \| null | Why, when `state` is `held`, in the queue's words: `malformed`, `no-repo`, `no-estimate`, `blockers-unreadable`, `blocked`, `stopped`, `no-lane`, `running-cap`, `starts-cap`, `agent-filed-cap` or `over-load`. Null otherwise. |

A held piece sits in its repository's first lane, because the queue gives a piece a slot only when
it starts it. The queue's decision alone says `planned` and `held`: `started` and `done` come from
what the caller knows of the day's sessions.

### Needs your OK

| Field | Type | Notes |
|---|---|---|
| `kind` | enum | `plan`: a plan to approve. `agent_filed`: an agent-filed ticket over its limits. `changed`: a ticket someone else edited. `approval`: a ticket that needs the owner's yes. `merge`: a pull request that holds a lane or a piece. `question`: a piece stuck on a question. |
| `ref` | string | What waits: a ticket, a plan's ticket, or a pull request as `owner/name#N`. |
| `title` | string \| null | Cleaned. Null for a pull request. |
| `holds_up.pieces`, `holds_up.usd` | integer, number | How many of today's pieces wait on it, and their estimates in dollars. |
| `reply` | string \| null | `approve <plan>` for a `plan`; `start <ticket>` for `agent_filed`, `changed` and `approval`. Null for `merge` and `question`: a merge is done on GitHub, a question is answered in its thread. |
| `urgent` | boolean | True when the top of the queue is Urgent or High and waits on it. |

### Agent-filed starts, drops, stops and counts

| Field | Type | Notes |
|---|---|---|
| `id`, `source` | string, string \| null | An agent-filed ticket in today's plan, and the ticket its finding came from. |
| `id`, `reason` | string \| null, word | A piece the checks dropped, or null for something that was not a ticket id. The reason is `not-in-pool`, `duplicate`, `wrong-repo`, `blocker-order` (a blocker neither done nor earlier in the same lane), `lane-cap`, `overlap` (footprints that share files where the merge gate cannot yet test pull requests together), `bad-footprint` or `over-load`. |
| `stop`, `ref`, `value`, `limit` | word, string \| null, number \| null, number \| null | A runaway stop: `usage-missing`, `usage-failed`, `usage-stale`, `ledger-unknown`, `plan-near-limit`, `plan-at-limit`, `day-hard-stop`, `piece-cost`, `too-many-running`, `too-many-starts` or `api-key`; what it is about, what the queue saw, and the limit it was held to. |
| `lanes`, `needs_ok`, `agent_filed`, `stops`, `dropped` | integer | The length of each list. |
| `pieces` | integer | The pieces in the lanes: `planned`, `started`, `done` and `held` add up to it. |
| `unplaced` | integer | Held pieces that name no repository, so no lane holds them. |

A word the writer does not know, a held reason, a kind or a stop, is refused, not guessed: a queue
newer than the writer is an error, not a quiet gap in the file.


---

### The sprint plan file

`pipeline-sprint-plan/1`, written by the morning job (the proposal) and by the bridge (the owner's yes), whole by a rename (KIT-575). One JSON object with exactly these keys: `schema`; `sprint` (the usage file's `week.starts_at`, the sprint's id); `first_day` (its first working day, a local date, which the owner's reply names); `resets_at`; `proposed_at`; `announced_at` (null until the stand-up has sent it); `budget_usd`, `spent_usd` and `planned_usd`; `planned` (pieces: `ticket`, `effort`, `usd`, `title`); `not_planned` (`ticket`, `why`, `reply`); and `plan` (null, or `recorded_at` and `tickets`). A file that is not exactly this is never used and never written over. The queue reads only `plan.tickets`, for the sprint that is current, to put those pieces first; it is not read as an approval to start anything.

## 19. Watcher status file

One file says what the watcher saw this pass: for each of its ten monitors, whether it was clear, flagged, blind or off, what it asked and what the answer was, and each finding as fields (KIT-514). The watcher, `scripts/pipeline_watcher_local.py`, writes it each pass. It runs with no AI, as its own account, from files the workers cannot edit (`docs/adr/2026-11-05-the-watcher-an-independent-auditor.md`, status Proposed). It reads files and GitHub, sends nothing but a GET, and writes one file. It stops nothing and changes nothing: the hold that would stop the system is a later piece (KIT-515). Machine-readable: `schemas/pipeline-watcher.schema.json`. The job checks its document against that schema before it writes, and writes nothing that does not conform.

**Fields only.** Every value is an id, a count, a number, a word from a closed list, a time, a GitHub login, or a GitHub link the job builds from a repository and a number. No ticket, comment, pull request or review text reaches the file, and no excerpt of a match: a finding says which shape matched, in what, how many times, and nothing it said.

**Reporting, never authority.** The file says what a hold would be for. Nothing reads it to approve, merge or start anything. A reader that does not know `pipeline-watcher/1` refuses the file.

**Off by default.** With no config file, or `"on": false` in a safe one, a pass reads nothing, writes nothing and says so. A watcher that is on but has nothing to watch is a usage error, never a quiet pass. A config file that exists and is unsafe (below) is never off: it is judged first and obeyed never.

### The monitors

Ten, in this order. The file always lists all ten, so it never claims cover it lacks.

| Monitor | What it watches | Kind |
|---|---|---|
| `spend` | Today's dollars, each piece's dollars, and any run that signed in with a billed key, against limits in the watcher's own rules. From the usage file (§17). | second-hand |
| `runs` | Runs today and runs per piece against the watcher's own caps. The same file. | second-hand |
| `labels` | A protected label (`hooks-change`, and `agent:`, `blocked:` and `provenance:` labels) applied or removed by a login not on the watcher's list. GitHub's issue-events feed. | first-hand |
| `markers` | A pipeline marker on a comment's first line, by an author its readers would refuse: a marker only the workflow bot writes, from anyone else, or any other marker from an author whose association is not trusted. The workflow bot's own comments are always real. GitHub's comment feed. | first-hand |
| `injection` | Prompt-injection shapes in pull request and comment text: an attempt to close the kit's fence, an instruction to ignore earlier instructions, a system-role tag, a request to print a secret, and characters a reader cannot see. A tripwire, not a filter. | first-hand |
| `tracker-text` | The same shapes as `injection`, in ticket and comment text, and a pipeline marker on a comment's first line from an author who is not trusted. The tracker's newest 100 tickets and 100 comments, by one fixed read-only query in `scripts/pipeline_watcher_tracker.py` (KIT-556): the one request the watcher sends that is not a GET. A page that is all inside the lookback, in the wrong order, or a query that fails, makes it `blind`. A ticket's description is judged by who created it, not by who last edited it. | first-hand |
| `guard-blocks`, `egress`, `outside-files`, `approval-rate` | Off, each with its reason: a producer does not exist yet (KIT-518). | none |

A *first-hand* signal comes from a platform the watcher reads with its own key. A *second-hand* signal is a file a worker-side job wrote: whoever controls that job could falsify it, so it can only under-report.

### The four states, and the three levels

| State | What happened | How it reports |
|---|---|---|
| `clear` | It asked, read the answer, and found nothing at `page` or `pause`. | `said` says what it asked and what the answer was. A silent green is not this. |
| `flagged` | At least one finding at `page` or `pause`. | The findings, and `firing`. A reading that was also incomplete keeps its `reason`. |
| `blind` | It could not establish the answer: a missing key, a stale or unreadable file, a feed cut short, a cap reached, an unsafe rules file. | `reason`, and a non-zero exit. A blind monitor is never `clear`. |
| `off` | Not configured, or nothing writes its signal yet. | `reason` says which. Off is not broken. |

| Level | Meaning |
|---|---|
| `pause` | Something got through, or money is moving: a protected label by a login not trusted, a billed run, spend or runs over the watcher's own limits. |
| `page` | An attempt the readers ignored: a forged marker, a shape in text from an author not trusted. |
| `record` | Noted, never paged: a shape in text from a trusted author. |

This piece writes the level and nothing acts on it. A finding at `record` alone leaves its monitor `clear`.

### The rules file

The watcher's limits and lists live only in the rules file its config names. Nothing else sets them. A pass judges the rules file before it obeys anything in it. A rules file that is inside a git working tree, is a link, is writable by group or other, is owned by another account, or sits in a folder others can write is not trusted, and nothing in it is obeyed: not its `on`, not what it names to watch, not whether it can be read. The six monitors that could be on are `blind`, by name, and the pass exits 4, so the cheapest way to switch the watcher off is not to edit its rules. Such a file also names where the output goes, so it may only replace a status file the watcher already wrote. A monitor whose limits the owner has not set is `off`, not `blind`: the limits are the owner's decision.

### The file

| Field | Type | Notes |
|---|---|---|
| `schema` | `"pipeline-watcher/1"` | A reader that does not know the value refuses the file. |
| `written_at` | time | When this pass wrote it. A file that stops being written is the dead-man signal. |
| `started_at` | time | When this pass began reading. |
| `result` | `clear` \| `flagged` \| `blind` | `flagged` if any monitor is, whatever else is blind. `blind` if none is flagged and one that is on is blind. A pass with nothing on writes no file. |
| `error` | string \| null | Null, unless the pass stopped on an unexpected error: then the error's class and nothing of its words, and every monitor that is on is `blind`. |
| `lookback_hours` | integer | How far back the first-hand feeds were read. |
| `monitors` | monitor[] | All ten, in order. |
| `counts` | object | Totals over `monitors`, below. |
| `findings` | heartbeat finding[] | The heartbeat monitor's view, in the health watch's words: one per monitor that is flagged or blind. |
| `firing` | string[] | The monitors flagged at `page` or `pause`. |

Times are UTC to the second, or null where the source gave none.

### The install

With `install_manifest` named, every pass first checks the watcher's own files (KIT-517): `manifest.json`, `pipeline-watcher-manifest/1`, lists each file under `scripts/` and `schemas/` by the SHA-256 of its content, and the commit it came from. A file that differs, is a link, is not a plain file, is writable by group or other, or is owned by an account but root and the one running; a module in those folders the manifest does not list; and a manifest that is itself any of these, makes every monitor that could be on `blind`, by name, and the pass exits 4. The installer is `scripts/pipeline_watcher_setup.py`.

### The copy

The usage file is written by the workers' account, so the watcher reads a copy of it that only root writes (KIT-573). With `usage_copy_status` named, `usage_file` is that copy, and the usage file counts only if the copier's status says so. `scripts/pipeline_watcher_copy.py` runs as root from the install folder, reads its own rules (`pipeline-watcher-copier/1`: `status_file` and up to eight `copies` of `source`, `owner`, `dest` and `max_bytes`) and nothing else, and for each copy opens the source's last name without following a link, then requires a plain file owned by `owner`, with one link, no larger than the cap. It writes the copy `root:wheel` 0644 to a temp file and renames it over the old one, and writes a status file beside it, `pipeline-watcher-copy-status/1`:

- `schema`: `"pipeline-watcher-copy-status/1"`. A reader that does not know the value refuses the file.
- `written_at`: when the copier last ran. Older than `usage_max_age_minutes` makes the monitor `blind`.
- `result`: `copied`, or `refused` if any copy was.
- `copies`: one row per copy, holding `dest`, `state` (`copied` or `refused`), `reason` (null, or fixed words, when refused), `bytes`, `sha256` and `copied_at` (null when refused).

A refused source removes the old copy, so a reader sees it missing and never stale good figures. The watcher is `blind`, in the copier's own words, when the status is missing, old, unreadable, not this schema, has no row for the usage file, says `refused`, carries no hash, or when the copy it reads does not have the row's hash. Exit 4 when any copy was refused; 2 when its rules are wrong; 1 when it could not write its status. It cannot tell a true figure from a false one the workers' account wrote on purpose.

### The page

The watcher sends nothing but GETs, so it does not page. `scripts/pipeline_watcher_page.py` (KIT-519) reads this file and tells the owner by its own routes, a Slack app to his DM and a GitHub issue in a private alert repository, never through the bridge or the notifier. It pages once per change of answer, from fields only: which monitor, how many findings, since when, which ids, and a closed sentence of what the owner can do. A page that could not be sent is sent again and is not recorded as sent (§13); its heartbeat file, `pipeline-watcher-page-heartbeat/1`, says `could-not-page` and which route. A status file that is missing, unreadable, not a status file, stale or written by a failed pass is itself a page. With `judge_file` named, a judgment from the judge (`scripts/pipeline_watcher_judge.py`, KIT-516, `pipeline-watcher-judgments/1`) for the findings a page was sent about is sent once as a note after the page, and a judgment that failed is a note that says so. Nothing reads a judgment to set a level, clear a flag or clear a hold.

### A monitor

| Field | Type | Notes |
|---|---|---|
| `name` | enum | One of the ten above. |
| `state` | `clear` \| `flagged` \| `blind` \| `off` | As above. |
| `kind` | `first-hand` \| `second-hand` \| `none` | Where its signal comes from. |
| `level` | `record` \| `page` \| `pause` \| null | The highest level among its findings. |
| `asked` | integer | Questions its feeds put to the source this pass: file reads, or GitHub requests. A feed two monitors share is asked once and counts for each. |
| `answered` | integer | How many it answered. |
| `said` | string | What it asked and what the answer was, in fixed words and numbers, as one plain line: no angle bracket, ampersand, bar, backtick or line break. |
| `reason` | string \| null | Why it is blind or off, or why a flagged reading was also incomplete. |
| `read_at` | time \| null | When this pass last had an answer from its source. |
| `findings` | finding[] | At most 50. More is said in `said`, never dropped silently. |

### A finding

| Field | Type | Notes |
|---|---|---|
| `level` | `record` \| `page` \| `pause` | As above. |
| `what` | enum | A word from a closed list: `spend-day`, `spend-piece`, `key-run`, `runs-day`, `runs-piece`, `protected-label`, `forged-marker`, or a shape: `shape-fence-tag`, `shape-ignore-instructions`, `shape-system-tag`, `shape-secret-ask`, `shape-hidden-char`. |
| `ref` | string | What it is about: a local date, a ticket id, or `owner/name#N`. |
| `actor` | string \| null | A GitHub login: who applied the label or wrote the text. |
| `at` | time \| null | When. |
| `url` | string \| null | A GitHub link the job built from a repository and a number, never one taken from text. |
| `count` | integer | How many times a shape matched in that text, or 1. |
| `value`, `limit` | number \| null | For a spend or runs finding: the figure the watcher saw and the limit from its own rules. |

### Counts, and a heartbeat finding

| Field | Type | Notes |
|---|---|---|
| `monitors`, `on`, `clear`, `flagged`, `blind`, `off` | integer | How many monitors there are, are on, and ended in each state. A quiet pass says `on` of `monitors`. |
| `findings`, `pause`, `page`, `record` | integer | How many findings in all, and at each level. |
| `kind`, `state`, `summary` | string | A heartbeat finding: the monitor's name; `firing` if it is flagged and `unknown` if it is blind; and its state and reason on one line. |

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Every monitor that is on is clear, or the watcher is off (it says which). |
| 4 | None is flagged, and at least one that is on is blind. |
| 10 | At least one monitor is flagged at `page` or `pause`, whatever else is blind. |
| 1 | The pass crashed, or the file could not be written or did not conform. |
| 2 | Usage or config, including "on" with nothing to watch: nothing was read or written. |


---

## 20. Hold file

One small file says that new starts are held, and why (KIT-515). The watcher (§19) sets it when a monitor flags something at level `pause`. While it exists, nothing that starts work may start: each job that does calls `scripts/pipeline_hold.py`'s `gate()` before it starts anything. It holds new starts and nothing else. Nothing running is touched, and nothing is reverted, closed, relabelled or merged. Only the owner clears it. Machine-readable: `schemas/pipeline-hold.schema.json`.

**Absent is no hold. Anything else that is not a valid hold holds.** A file that does not parse, names another schema, lists no hold, is larger than a hold, or cannot be read is a hold, with that reason said. A reader that cannot tell never starts work.

**No expiry.** Nothing clears a hold by the passage of time, by a later clean pass, or by the watcher: the watcher only adds to it and never empties it. The owner clears it with `scripts/pipeline_hold.py clear`, in a terminal. It is refused inside an agent's environment, shows what is held, asks for a typed `yes`, removes the file and reads it back.

**Fields only.** A hold names a monitor, a word from a closed list, a ticket or `owner/name#N`, and a time. No ticket, comment or pull request text is ever in it.

**Whose file it is.** The watcher account's, in a folder no worker can write: a worker that could delete it could lift its own pause. That is the install's job (KIT-517). The writer refuses a path that is not absolute or is inside a git working tree.

### The file

| Field | Type | Notes |
|---|---|---|
| `schema` | `"pipeline-hold/1"` | A reader that does not know the value holds. |
| `set_at` | time | When the watcher last wrote it. |
| `holds` | hold[] | At least one and at most 50. Never empty: clearing is removing the file. |

### A hold

| Field | Type | Notes |
|---|---|---|
| `monitor` | `spend` \| `runs` \| `labels` | The monitor that flagged it at level `pause`. |
| `what` | enum | The watcher's closed word: `spend-day`, `spend-piece`, `key-run`, `runs-day`, `runs-piece` or `protected-label`. |
| `ref` | string | What it is about: a local date, a ticket id, or `owner/name#N`. |
| `since` | time | When this hold was first set. A later pass never moves it. |

### Who reads it

A reader calls `gate(path)`, which returns whether to block and why, in words and numbers. Today: the queue's starter (`scripts/pipeline_queue_start.py`), the idea gate's planner (`scripts/pipeline_plan_poller.py`: no planning run starts, new or again), the bounce driver (`scripts/pipeline_bounce_local.py`: no bounce and no conflict fix is sent; a conclusion, a notice and an escalation still are, since they only say something) and the catch-up job (`scripts/pipeline_catchup_poller.py`: no re-prompt; a delegation held back stays owed), each by a `hold` setting, `{"file": "/path/to/hold.json"}`, which `pipeline_hold.setting()` reads. With no file named a job says so on every pass. The bridge's direct asks follow, with their own case that fails if they stop reading the hold.

---

## 21. Acceptance result block

When every piece of an approved plan is coded and reviewed, an acceptance session builds the app at the last piece's pull-request head and looks at it through the look tool (§15's checks, `scripts/pipeline_look.py`). It ends by reporting what it found as one JSON block (KIT-531). The acceptance poller reads the block (KIT-324). Machine-readable: `schemas/pipeline-acceptance.schema.json`.

**Data, never authority.** A model writes the block. It can name a problem. It cannot grant a pass, a budget, an approval or a merge, and nothing in it is run. The poller trusts the look tool's own record for what ran and what failed mechanically, and this block only for what the session judged on screen. A check the block says it `ran` that the record does not hold is a disagreement, said aloud.

**The result and the problems must agree.** The block says one of three things, and each is held to its list:

- `found-nothing`: it ran at least one check, every check it ran `passed`, and it lists no problem.
- `found-problems`: it lists at least one.
- `could-not-check`: it lists none and gives a `reason`. It is never read as a pass.

A block that says `found-nothing` and lists a problem or a check that did not pass, `found-problems` and lists none, or `could-not-check` with no `reason`, or carries a `reason` on any other outcome, is refused. A block that is missing, does not parse, or names another marker is `could-not-check` too, said so: the plan stays held, and nothing reads it as passed.

**Bounded text.** Every free-text field is one line of at most 300 characters. The poller still neutralizes markers, fences and @-names before it writes any of it into a comment, a page or a ticket.

### The block

| Field | Type | Notes |
|---|---|---|
| `schema` | `"pipeline-acceptance/1"` | A reader that does not know the value holds the plan. |
| `plan` | ticket id | The epic this is about. |
| `head` | 40 hex characters | The pull-request head the session built, in full. |
| `outcome` | `found-nothing` \| `found-problems` \| `could-not-check` | Held to `problems` and `reason`, above. |
| `ran` | run[] | At most 60. Each check, viewport and mode the session says it ran. |
| `problems` | problem[] | At most 20. |
| `reason` | text | Why it could not check. Present for `could-not-check`, and only then. |

### A run

| Field | Type | Notes |
|---|---|---|
| `check` | name | A check `id` from §15's block. |
| `viewport` | name | A viewport the project's `acceptance` section names. |
| `mode` | name | A mode it names, or `default`. |
| `result` | `passed` \| `failed` \| `could-not-run` | As the look tool reported it. `could-not-run` is never a pass. |

### A problem

| Field | Type | Notes |
|---|---|---|
| `id` | name | A short name. The poller refuses or merges a repeated one; the schema cannot check uniqueness. |
| `severity` | `high` \| `medium` \| `low` | Only `high` holds the plan open. Every confirmed problem is filed as a follow-up. |
| `check` | name | The check that showed it. |
| `viewport` | name | Where. |
| `mode` | name | In which mode. |
| `screenshot` | name | The screenshot that shows it, by the name its `screenshot` step gave. |
| `what` | text | One plain sentence. |

### Who reads it

The acceptance poller (KIT-324) reads the block, runs a second pass that tries to disprove each problem, and files what survives as follow-ups. It decides what is `high` for the hold only after that pass; the session's word on severity is a proposal.

---

## 22. Acceptance verdict comment

After a check, the acceptance poller writes **one comment on the plan's epic** that says what it concluded from §21's block, the look tool's record and its second pass (KIT-554). It is for people, and for the bridge, which reads it before it closes a plan (that gate is a later piece). The comment's **first line** is the mark, and nothing else:

```
<!-- pipeline-acceptance:verdict <verdict> plan=<ticket id> head=<40 hex> mechanical=<yes|no> -->
```

| Field | Values | Notes |
|---|---|---|
| *(the word)* | `passed` \| `held` \| `could-not-check` | Only `passed` is a pass. |
| `plan` | ticket id | The epic the comment is on. A reader that finds another id refuses the mark. |
| `head` | 40 lower-case hex characters | The pull-request head the check judged. The verdict speaks for that head only. |
| `mechanical` | `yes` \| `no` | `yes` only when every look-tool record the poller read is the runner's alone or a base copy (the look tool's `outcome_trust`, `docs/LOOK-TOOL.md`). `no` otherwise, including when it read none: the outcome then rests partly on code the session could change. |

The verdict is the first of these that matches:

1. `could-not-check`: the result block or the look tool's record could not be read or trusted, or a problem is not §21's shape.
2. `held`: a problem the second pass did not disprove is still `high`, or a run failed in the look tool's record.
3. `could-not-check`: a run could not run, or the session and the record disagree about what ran.
4. `passed`: anything else, including problems that are all filed as follow-ups and none still `high`.

**How to read it.**

- Read the **first line only**, and match the whole line exactly (spaces around it may be trimmed). The rest of the comment is for people. It carries the session's words, neutralized: no `<!--` but the mark's own, no line break, no fence, no @-name. Session text never starts a line.
- Accept the mark only from the poller's tracker author. The same mark from anyone else is forged: ignore it, and say so. The watcher's `tracker-text` monitor (§19) already flags a `<!-- pipeline-` first line from an author it does not trust.
- One verdict per plan and head. The poller records it before it posts and never writes a second one for the same head, whatever a later pass concludes. A push is a new head, so compare the mark's `head` with the head you are about to act on: a verdict on another head is not this one's.
- No verdict is not a pass. Neither is `held` or `could-not-check`.

`scripts/pipeline_acceptance_poller.py`'s `read_verdict_mark()` is the reference reader, and its selftest holds the shape.

---

## 23. Validator settings file

What the validator may approve, as plain on/off switches (KIT-533). The owner decided there is no trust ladder: nothing gains or loses trust by itself, and a switch changes only when a person changes it. Every part of the validator reads the file through `scripts/pipeline_validator_settings.py`'s `load()`, which has no write path. Machine-readable: `schemas/pipeline-validator-settings.schema.json`. Design: `docs/adr/2026-11-05-the-validator-approves-inside-limits-that-code-enforces.md`. The operator guide is `docs/VALIDATOR-OPERATOR.md`.

**Absent is every switch off. So is anything that is not a valid, trusted settings file.** A file that does not parse, names another schema or the first version (`/1`, from before `merge_repos`), gives a key twice, misses a key or adds one, lists a repository that is not `owner/name` in lower case, names one twice or more than 100, is larger than a settings file, or cannot be read means every switch off, every limit 0, no login and no repository, with that reason said. So does a file a session could have written: one inside a git working tree, a link, one writable by group or other, one owned by another account, or one in or under a folder others can write without the sticky bit. It never means on. Absent, invalid and unreadable are decided the same way, toward the safe side, as the hold's are (§20).

**No switch for the safety system.** Changes to hooks, guards, CI and workflows, branch protection and rulesets, settings, the watcher and the validator's own limits stay human, fixed in code. A file that names one as a key, anywhere, on or off, is refused: every switch off, said in words.

**Fields only.** Switches, two whole numbers, one bot login and a list of repository names, each of a fixed shape. No free text.

**Whose file it is.** The owner's, under the user's own login, outside every git working tree. Only a person edits it. The example, `validator-settings.example.json`, has every switch off, every limit 0, no login and no repository listed.

### The file

| Field | Type | Notes |
|---|---|---|
| `schema` | `"pipeline-validator-settings/2"` | A reader that does not know the value treats every switch as off. The first version, `/1`, has no `merge_repos`, and is read as every switch off, said. |
| `approve` | approve | What it may approve in the owner's place. |
| `limits` | limits | How big a change it may approve. |
| `allow` | allow | Which human-only classes the owner lets it approve. |
| `stage_e_login` | bot login, or `null` | Stage E's own bot login: the one login whose reviews the evidence trusts. `null`: none yet, so no review is trusted. |
| `merge_repos` | list of `owner/name` | The repositories where a merge may be approved. Empty: none. |

### Approve

| Field | Type | Notes |
|---|---|---|
| `plans` | boolean | A plan, in place of the owner's `approve <plan>`. |
| `pieces` | boolean | A ready piece of an approved plan starting. |
| `merges` | boolean | A pull request merging. A job asks GitHub to enable its own auto-merge; no agent merges. |

### Limits

| Field | Type | Notes |
|---|---|---|
| `max_changed_lines` | 0 to 100000 | Most lines added plus removed. 0: nothing passes. |
| `max_files` | 0 to 10000 | Most files changed. 0: nothing passes. |

### Allow

Off keeps a change in that class for the owner, whatever else is on.

| Field | Type | Notes |
|---|---|---|
| `credentials` | boolean | Env files, keys, tokens, secrets stores. |
| `billing_and_accounts` | boolean | Billing, accounts, the plan or subscription. |
| `production_deploy` | boolean | The deploy workflow and its config. |
| `deleting_data` | boolean | Migrations that drop, bulk deletes, branch or release removal. |

### Stage E's login

`stage_e_login` is the login Stage E's own GitHub App posts its reviews under: the app's slug, then `[bot]`, as GitHub's REST API and its pages name it and as `scripts/pipeline_github_app.py`'s `bot_login()` makes it (`^[a-z0-9][a-z0-9-]{0,62}\[bot\]$`). No person's login has the `[bot]` suffix, so no person's login fits. A value of any other shape is a file that is not a settings file: every switch off.

The validator's evidence (`scripts/pipeline_validator_evidence.py`, KIT-580) takes the login from what `load()` returns, never from its own code or a flag, and counts a review comment only when its author is exactly that login and a bot. GitHub's GraphQL API names a bot without the suffix, so the evidence gives a bot's login the suffix before it compares. No login, or settings that were not read, means no review is trusted (`review-login-unset`).

### Repositories for merges

`merge_repos` makes the merges switch per project (KIT-604). A merge may be approved only when `approve.merges` is on **and** the pull request's repository is listed. Any other combination is a closed reason, never a pass: the switch off is `switch-off`, and a repository not listed is `repo-not-listed`.

Each entry is `owner/name` as GitHub shapes it, in lower case: an owner of 1 to 39 letters, digits and hyphens that starts with a letter or digit, then a name of 1 to 100 letters, digits, `.`, `_` and `-` that is not `.` or `..` (`^[a-z0-9][a-z0-9-]{0,38}/(?!\.\.?$)[a-z0-9._-]{1,100}$`). Each is listed once, and at most 100. A list that breaks any of these is a file that is not a settings file: every switch off, said in words, and the entry is never quoted back.

One helper gives the answer: `scripts/pipeline_validator_settings.py`'s `merge_allowed(settings, repo)`. It is true only for settings `load()` read, with the merges switch on and the repository listed. GitHub's names ignore case, so it compares the asked repository in lower case, and only when it is plain ASCII. The hard limits ask it, and the merge path asks it too; neither keeps a copy of the rule.

### Who reads the switches

`scripts/pipeline_validator_settings.py show`, for a person, and the hard limits (`scripts/pipeline_validator_limits.py`), which are handed what `load()` returned. The validator's other parts read it through `load()` as they are built: its merge path and its approvals.

---

## 24. Validator ledger

The validator's record of every decision, one row each (KIT-535). The stand-up, the recap and the watcher read it, and the watcher checks every merge against it. Only the validator writes it, through `scripts/pipeline_validator_ledger.py`'s `append()`, the one function that opens it for writing. Machine-readable: `schemas/pipeline-validator-ledger.schema.json`. Design: `docs/adr/2026-11-05-the-validator-approves-inside-limits-that-code-enforces.md`, "Its audit".

**Append-only, in one piece.** `append()` reads the ledger, keeps every row exactly, adds one, and replaces the file with one rename, under a lock. It stamps the row's time itself. It refuses a row that is not one, a ledger it cannot read, and a path inside a git working tree. A refusal writes nothing, and a decision that is not on the record is not an approval.

**One verdict per head.** A row for a head is never changed afterwards, and a second row for the same head is refused. A later push is a new head, and a new row. A plan or a piece has no head, so it may be decided again.

**An approval is whole.** It needs all three layers to pass and its own switch on. Only an approval has all three pass.

**No approval from a ledger that cannot be trusted.** `read()` gives the rows. A ledger that cannot be read, does not validate, gives a key twice, is larger than 16 MiB on disk, or that a session could have written gives no rows: `None`, never an empty list, with the reason in words. A session could have written one inside a git working tree, a link, one writable by group or other, or one in or under a folder others can write without the sticky bit. Absent is an empty ledger: nothing has been decided.

**Fields only.** No ticket, comment or pull request text, except `agent_says`: the agent's reason, one plain line of at most 300 characters, marked as the agent's words. It has no line break, control character or character a reader cannot see: every character is judged by its Unicode category, so format characters such as bidi controls, the soft hyphen and the tag characters are refused. A reader that shows it quotes it as the agent's, and never acts on it.

**Whose file it is.** The validator's, under the user's own login, outside every git working tree. Mode 644, so the watcher can read it from its own account.

### The file

| Field | Type | Notes |
|---|---|---|
| `schema` | `"pipeline-validator-ledger/1"` | A reader that does not know the value gives no rows. |
| `rows` | row[] | One per decision, oldest first. Only ever added to. |

### A row

| Field | Type | Notes |
|---|---|---|
| `at` | time | When the writer added it. The writer stamps it; a caller cannot. |
| `subject` | `plan` \| `piece` \| `merge` | What was decided: a plan's approval, a piece starting, or a pull request merging. |
| `ref` | ticket id, or `owner/name#N` | A ticket for a plan or a piece; a pull request for a merge. |
| `head` | 40 hex characters, or null | The pull request's head, in full, for a merge. Null for a plan or a piece. |
| `kind` | `approved` \| `sent-back` \| `escalated` \| `escalated-urgent` \| `held` | What the decision was. |
| `layers` | layers | Each layer's verdict. |
| `settings_on` | switch[] | The validator's switches that were on, each once: `approve-plans`, `approve-pieces`, `approve-merges`, `allow-credentials`, `allow-billing-and-accounts`, `allow-production-deploy`, `allow-deleting-data`. |
| `size_limits` | size limits | The size limits in force. |
| `agent_says` | text, or null | The agent's reason, in its own words. Null when it said nothing, and always null when it did not run. |

### Layers

Each is `pass`, `fail`, `unsure` or `not-run`. `unsure` holds, as `fail` does. `not-run`: an earlier layer held, so this one was not asked.

| Field | Type | Notes |
|---|---|---|
| `hard_limits` | verdict | Layer 1: the limits code enforces. |
| `evidence` | verdict | Layer 2: the checks, the review, the acceptance check, the merge state. |
| `agent` | verdict | Layer 3: the independent read-only agent. |

### Size limits

| Field | Type | Notes |
|---|---|---|
| `max_changed_lines` | 0 to 100000 | Most lines added plus removed. |
| `max_files` | 0 to 10000 | Most files changed. |

### Who reads it

Nothing yet. The validator's own pieces write rows, and the stand-up, the recap and the watcher read them, each in its own piece.

---

## 25. Acceptance follow-up request

After a check, the acceptance poller's live pass asks the finding path to file each problem that survived the second
pass (§21, KIT-553), at most three a check. It posts **one comment per request on the plan's epic** (KIT-571):

````
<!-- pipeline-acceptance:finding plan=<ticket id> head=<40 hex> -->
A follow-up the acceptance check asks the finding path to file.

```json
{"schema": "pipeline-finding/1", "title": "...", "body": "..."}
```
````

| Part | Notes |
|---|---|
| The mark | Line 1, alone. `plan` is the epic the comment is on; `head` the pull-request head the check judged. |
| The block | One `pipeline-finding/1` request, the shape the finding poller reads (`docs/FINDING-POLLER.md`): a `title` and a `body`, nothing else, in a three-backtick `json` fence. It asks for no size, no track and no label, so the ticket it becomes waits for a person. |

**How to read it.**

- Read the mark from the **first line only**, whole, and only from the acceptance poller's tracker author: the same key
  as the finding poller's own on a one-key install. The same mark from anyone else is forged.
- The plan in the mark must be the ticket the comment is on.
- Every word in the request that came from a session was neutralized first (§21): no comment opener, no fence, no
  @-name, no routing tag. The mark's own `<!--` is the only one in the comment.
- At most three requests per plan and head. Each is posted once: the pass records it before it posts, and one it may
  have posted is never posted again, but said.

The finding poller does not read this yet: it files only what the dispatcher's agent writes. Until it does, the requests
stay on the epic for a person to file.

---

## 26. Acceptance check request

A request to check a plan again. The Slack bridge posts it on the plan's epic when the owner sends `check <epic>` and
answers yes (KIT-325). The acceptance poller's live pass reads it (KIT-571).

```
<!-- pipeline-acceptance:check-request plan=<ticket id> -->
```

The rest of the comment is for people.

**How to read it.**

- Read the **first line only**, whole. The plan it names must be the epic the comment is on.
- Accept it only from `request_author_ids` in the live pass's config. Absent, that is the user of the pass's own
  tracker key, which the bridge shares on a one-key install. The same mark from anyone else is ignored.
- The newest request, if it is newer than the plan's newest check, starts **one** check, even at a head already
  checked. After the plan's pieces merged, it checks the repository's default branch at the head it is at now. Before,
  it checks the last piece's pull-request head again, once the poller's own rule has checked that head; a head not
  checked yet starts by that rule, and the request starts nothing of its own.
- A check run again takes the place of the earlier check's ledger row, once that check has its verdict (or its start
  failed) and its ticket is closed. So the ledger still holds one row per plan and head: the newest check of it.
- A request is acted on once: when its check starts, or its start fails. One that has to wait (the starts a pass may
  make, the ledger's lock, a head GitHub can't name, an earlier ticket not closed yet) is acted on at a later pass.
- Never while one of the plan's checks is running.
- A plan whose pieces all merged before any check of it ran is checked the same way, with no request: the bridge waits
  for a verdict before it closes a plan, so one must come.

