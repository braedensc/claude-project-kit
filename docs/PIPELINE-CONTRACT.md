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

Two rules shape all of it:

- **The pipeline is optional.** It is *configured* for a project **iff `delivery.json`
  exists**. Absent, every pipeline-scoped guard, skill and workflow no-ops entirely
  (§2).
- **Anything the session agent can write is *reporting*, never *authority*.** Config and
  pins are authority; telemetry, PR bodies and commit messages are reporting. A guard
  that reads a value the agent could have written is not a guard.

---

## 1. `delivery.json`

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
> survive renames; a *deleted* ID fails loudly at the API call, which is the behavior we
> want. Anywhere in the pipeline that compares a state or label, it compares IDs.
>
> Corollary: `labels.ids` values are **resolved**, not authored. They ship as `""`
> (unresolved) and a setup step fills them by looking each display name up once. The
> five `stateIds` are placeholder tokens instead — a closed, mandatory set of exactly
> five, where a missing value stalls the whole queue; labels are an open set
> (`track:*` grows per project) that no fixed token list can cover.

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
| `maxBounces` | integer | Max review→fix round trips on one ticket before `agent:needs-human`. |
| `totalAttempts` | integer | Max dispatches of one ticket, counting all stages and bounces. Exhausted → `agent:needs-human`. |
| `dailyUsd` | number | Rolling 24h spend cap for the whole pipeline. |
| `reviewSeverityThreshold` | enum `low\|medium\|high\|critical` | Lowest review severity that blocks progress and starts a bounce. Findings below it are posted as comments only. |

> **Spend is metered by the dispatcher, not by the agent's telemetry.** `dailyUsd` and
> `maxUsd` are enforced against the dispatcher's own accounting of the runs it started.
> The `cost_usd` a session self-reports is for dashboards; a session that under-reports
> must not be able to buy itself more budget.

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
| `autoMergeMaxLines` | integer | Diff-size ceiling under which an *out-of-session* automation may merge. `0` disables. **No Claude Code session may ever act on this** — the merge command is hook-blocked in every form, including `--auto`. If this is ever non-zero, the merge is performed by CI or a GitHub App, never by an agent. |
| `riskPaths` | string[] | Globs that force human review regardless of diff size or provenance. Ships with the guard machinery, CI, git hooks, `delivery.json`, and key material. |

### `dispatch`

| Field | Type | Notes |
|---|---|---|
| `backend` | string | Where sessions run: `github-actions`, `local-daemon`, `cloud`. |
| `labelTrigger` | string | Canonical label key (resolved through `linear.labels.ids`) whose presence queues a ticket. Default `agent:queued`. |
| `pauseOnCapacity` | boolean | On a provider capacity error, pause the queue and apply `blocked:capacity` instead of consuming a `totalAttempts` slot. Capacity is not the ticket's fault. |
| `pinsRoot` | string | Directory for pin files. Default `~/.claude/pipeline/pins`. Must resolve outside every worktree and outside the repo. |

### `monitoring`

| Field | Type | Notes |
|---|---|---|
| `provider` | string | `github-actions`, `external`, or `none`. |
| `stormPerHour` | integer | Max alerts emitted per hour; beyond it, alerts are coalesced. Prevents one broken cron from filing a hundred tickets. |

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
| `lifecycle-label` | configured | The session does not set its own `agent:*` or `blocked:capacity` labels (§6) |
| `self-approval` | configured | The session does not move a ticket `raw` → `ready`; only `epic/*` provenance auto-approves, and only out of session (§5) |
| `telemetry-required` | configured | A terminal run posts exactly one valid telemetry block before the turn ends (§4) |

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
- [ ] Any `auth.*` outside `subscription|api-key`
- [ ] `autonomy.autoApproveProvenance` not a subset of `["epic"]`
- [ ] `autonomy.riskPaths` missing `.claude/hooks/**`, `.claude/settings*.json`, or `delivery.json`
- [ ] `dispatch.pinsRoot` resolving inside the repo or inside any worktree

A validator MUST NOT fail — or emit anything at all — when `delivery.json` is absent.
That is *off*, not *broken*.
