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
booleans and enums carry real defaults instead. `~` in a path value is expanded to
`$HOME` by readers.

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
| `stateIds.review` | string (UUID) | dispatcher, reviewer | PR open, awaiting review/CI. |
| `stateIds.done` | string (UUID) | dispatcher | Merged/closed. |
| `labels.ids` | object → string (UUID) | dispatcher, guards | Map of **canonical key → Linear label ID**. The key is the stable name used in code; the Linear display name may drift from it. |
| `labels.required` | string[] | validator | Subset of `labels.ids` keys that must resolve before the pipeline may dispatch. |

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
> open-ended and project-named so it can never be in `required`. One implementation —
> `scripts/pipeline_labels.py` — imported by every read path, never re-derived.
>
> Residual gap, named so nobody assumes otherwise: a label **renamed and then
> recreated** matches by neither ID nor name, so it reads as a label the project added
> for its own reasons. Closing it needs a liveness check against the API, which belongs
> in `/setup-board`.
>
> Corollary: `labels.ids` values are **resolved**, not authored. They ship as `""`
> (unresolved) and a setup step fills them by looking each display name up once. The
> five `stateIds` are placeholder tokens instead — a closed, mandatory set of exactly
> five, where a missing value stalls the whole queue; labels are an open set
> (`track:*` grows per project) that no fixed token list can cover. §6 fixes the scope
> those labels are created at.

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
| `autoMergeMaxLines` | integer | Diff-size ceiling under which an *out-of-session* automation may merge. `0` disables. **No Claude Code session may ever act on this** — the merge command is hook-blocked in every form, including `--auto`. If this is ever non-zero, the merge is performed by CI or a GitHub App, never by an agent. The full tier is §11. |
| `autoMergeMethod` | string (optional) | `squash` \| `merge` \| `rebase`. How the platform merges a qualifying PR. Default `squash`. Cosmetic — it decides nothing about *whether* a PR merges. |
| `riskPaths` | string[] | Globs that force human review regardless of diff size or provenance. Ships with the guard machinery, CI, git hooks, skills (prompts and rubrics are graders), `delivery.json`, and key material. |

### `dispatch`

| Field | Type | Notes |
|---|---|---|
| `backend` | string | Where sessions run: `github-actions`, `local-daemon`, `cloud`. |
| `labelTrigger` | string | Canonical label key (resolved through `linear.labels.ids`) whose presence queues a ticket. Default `agent:queued`. |
| `pauseOnCapacity` | boolean | On a provider capacity error, pause the queue and apply `blocked:capacity` instead of consuming a `totalAttempts` slot. Capacity is not the ticket's fault. |
| `pinsRoot` | string | Directory for pin files. Default `~/.claude/pipeline/pins`. Must resolve outside every worktree and outside the repo. Pins are **short-lived** — written per dispatch, deleted at session end, expiring in hours — so nothing durable may be stored here (§9). |
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

### Three states — and *off* is not *broken*

| State | Condition | Behavior |
|---|---|---|
| **Off** | `delivery.json` absent | Every pipeline-scoped guard, skill and workflow **no-ops immediately**: exit 0, no output, no diagnostics, no network, no git. Indistinguishable from a kit checkout without the pipeline at all. |
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
   Nothing that can fail may run ahead of this test.
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
| `self-approval` | configured | The session does not move a ticket `raw` → `ready`; only `epic/*` provenance auto-approves, and only out of session (§5). Mechanically enforced by the safe-outputs validator, which refuses `raw`, `ready` and `done` as transition targets however the caller is configured (§8) |
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

pin_key  = sha256(realpath(<session root>)).hexdigest()[:16]
pinsRoot = delivery.json → dispatch.pinsRoot   (default ~/.claude/pipeline/pins)
```

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
| Heading match | The heading text equals `Acceptance criteria` / `Out of scope`, compared case-insensitively after trimming. Not a substring test — `## Acceptance criteria (draft)` is a different section and is ignored. |
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
2. Derive the session root; compute `pin_key`; read the pin.
3. Verify `pin_version`, `expires_at`, and `worktree` == derived session root.
4. **Absence of a pin never grants autonomy.** In `ticket` mode a missing pin is
   *broken* and fails closed. In every other mode, checks that would *withhold*
   something fail open (a human's ad-hoc session in a configured repo must not be
   bricked) and checks that would *grant* extra autonomy fail closed.

> **Tamper-evident, not tamper-proof.** The session's shell runs as the same user, so
> `0444` and a path outside the repo raise the cost of tampering — they do not make it
> impossible. The ledger makes divergence detectable, and the real guarantee is the one
> the rest of the kit already rests on: nothing lands without a reviewed PR and CI. The
> pin's job is to make the honest path deterministic and the dishonest path visible.

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
| `model` | string | Exact model ID as used. |
| `auth_mode` | enum `subscription\|api-key` | |
| `started_at` / `ended_at` | ISO-8601 UTC | `ended_at` null only for an in-flight row; a posted block should be terminal. |
| `tokens_in` / `tokens_out` | integer | |
| `tokens_cache_read` / `tokens_cache_write` | integer | `0` when caching was not used — never null. |
| `cost_usd` | number | Best-effort self-report. Dashboards only. |
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
the review workflow already publishes, so there is one finding shape in the system and
not two.

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

Rules:

1. **Only `epic/*` may ever auto-approve.** Everything else waits in `raw` for a person.
   The agent-facing consequence: an agent cannot widen its own mandate by filing tickets
   for itself, because nothing it files carries `epic/*` provenance.
2. Auto-approval additionally requires that the referenced epic **exists and is itself in
   a human-approved state**. Without that check, `epic/<anything>` is a self-serve
   approval — a fabricated ID would mint autonomy.
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
| `agent:needs-human` | dispatcher | `maxBounces` or `totalAttempts` exhausted, or a `riskPaths` change. Terminal until a person acts. |
| `blocked:capacity` | dispatcher | Provider capacity, paired with `agent:blocked`. Cleared on retry; does **not** consume an attempt. |
| `provenance:epic` \| `provenance:monitor` \| `provenance:review` \| `provenance:retro-proposal` \| `provenance:human` | dispatcher / human | Origin class (§5). Exactly one per ticket. |
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
    { "type": "ticket-label",   "ticket_id": "ENG-123", "add": ["needs-design"], "remove": [] }
  ]
}
```

| Type | Fields | Notes |
|---|---|---|
| `ticket-comment` | `ticket_id`, `body` | `body` is non-empty markdown. The telemetry block (§4) travels as one of these. |
| `ticket-state` | `ticket_id`, `to` | `to` is a **canonical state key** from `linear.stateIds` (`working`, `review`, …), never a UUID and never a display name. |
| `ticket-label` | `ticket_id`, `add[]`, `remove[]` | Canonical label keys from `linear.labels.ids` (§6). Either list may be empty. |

An unrecognized `schema`, an unrecognized `type`, or `requests` that is not a list
rejects the batch. A reader that does not recognize the schema refuses; it does not
guess.

### Validation rules

Every rule is a MUST, and all of them run **before any request executes**.

| Rule | Rationale |
|---|---|
| Every `ticket_id` equals the **dispatcher-supplied** pinned ID | The central check. The agent *names* a ticket and the validator *compares* it — the value is never used to address anything. A mismatch is an attempted retarget, not a typo. |
| At most **one** `ticket-state` per run | A run advances the ticket once. Several transitions in one batch is either confusion or an attempt to land somewhere by way of somewhere else. |
| `raw`, `ready` and `done` are **never** valid targets | `ready` would be self-approval (§5); `done` would be a session claiming its own merge (§5, §6). Refused **even when a caller passes them in `allowed_to_states`** — a belt the caller cannot unbuckle. |
| Targets are otherwise limited to the caller's `allowed_to_states` | Default `review`. The stage decides what a session may do, not the session. |
| `agent:*`, `blocked:capacity`, `provenance:*` and `hooks-change` labels are refused, in `add` **and** `remove` | Dispatcher- and human-owned (§5, §6). A session setting `agent:needs-human` — or clearing `agent:blocked` — is a session editing its own supervision. `remove` matters as much as `add`. |
| Unknown label keys are refused | Only keys present in `linear.labels.ids` resolve to an ID. |
| Exactly **one** telemetry block across all comments, when the caller requires it | §4. Counted by the `"schema": "pipeline-telemetry/1"` marker, matched with whitespace tolerance so nothing hinges on a JSON writer's spacing. Zero blocks is a `telemetry-required` violation (§2); two is a double-count. |
| A missing requests file with telemetry required is a **rejection**, not a skip | A session that reports nothing is indistinguishable from one that silently failed, and silence must not be the quiet path. |

### Caps

| Cap | Value | Why |
|---|---|---|
| Requests per batch | 20 | A runaway or hostile session must not spray the tracker. |
| Comment body | 16 000 chars | Bounded cost, bounded blast radius. |
| Label ops per request | 10 | `add` + `remove` combined. |

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

The job reports a single verdict: `ok` (validated, executed), `rejected` (batch
refused, nothing applied), or `skipped` (nothing to apply and none required).

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
provenance resolves to `epic/<ID>` (§5 rule 4); that epic exists and is itself out of
intake (§5 rule 2); the ticket is in `raw`; it carries no dispatcher-owned `agent:*` /
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

Eight conditions, all required:

| Condition | Read from (never from) |
|---|---|
| Auto-merge enabled: `autoMergeMaxLines > 0` **and** the `PIPELINE_AUTO_MERGE_ENABLED` repo variable is `true` | a repo variable (a config value alone — a PR can propose one) |
| The ticket still passes the approve tier, **recomputed** | a live re-run (a stored "was approved" flag) |
| **Zero bounces** | Actions run history (`pipeline:bounce-N` PR labels — the fix session's token can edit PR labels) |
| Review findings usable, none at or above `reviewSeverityThreshold` | the review artifact (a PR comment — the author can edit it) |
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

Every structure above is prose, and prose is what agents *read and try to follow*. Four
of them are also **JSON Schema (draft 2020-12) files under `schemas/`**, and those files
are not a second source of truth — they are this document in a form a machine can
enforce. `scripts/check_schemas.py` fails CI if the two disagree about which fields
exist.

| Section | Schema | Parity check |
|---|---|---|
| §1 `delivery.json` | `schemas/delivery.schema.json` | **exact, bidirectional, by path** — every field in a §1 table must be a schema property and vice versa |
| §3 pin file | `schemas/pin.schema.json` | bidirectional by field name |
| §4 telemetry block | `schemas/telemetry-block.schema.json` | bidirectional by field name |
| §8 safe-outputs request file | `schemas/safe-outputs.schema.json` | bidirectional by field name |

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

Where a **model** produces one of these documents, the schema is passed at generation:
Claude Code headless takes `--json-schema <file>`, and the Agent SDK takes
`outputFormat: { type: 'json_schema', schema }`. Where a **shell step or workflow**
produces one, its output is validated before anything consumes it:

```bash
python3 scripts/check_schemas.py --instance "$FILE" --schema safe-outputs || exit 1
```

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
