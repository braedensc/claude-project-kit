# Stage A triggered from Linear — an idea ticket becomes a pending-approval epic tree

**Date:** 2026-09-06 · **Status:** Accepted (2026-09-08) · **Context:** KIT-105, branch
`docs/kit-105-linear-triggered-planning-adr`. Extends
[Stage E under a delegation-bound dispatcher](2026-09-05-stage-e-under-a-delegation-bound-dispatcher.md)
— the same transport, pointed at planning instead of review.

> **Accepted, and the kit-side mechanism is now built.** The owner authorised the build.
> The **source-agnostic mechanism** ships in the kit: contract §8's `ticket-create`
> extended to a tree, `schemas/safe-outputs.schema.json` in lockstep, and the deterministic
> executor `scripts/pipeline_plan_executor.py` — all CI-tested by `--selftest` and inert
> here (no `delivery.json`). **Activation is a separate operator step and is NOT done by
> this or any session**: the Planning team, its dispatcher entry (with the tool-fence
> resolved below), the owner-scoped executor credential and job. And the load-bearing
> security choice — item 1 of
> [What could not be settled](#what-could-not-be-settled-from-documents) — is now
> **resolved to fallback (b)** on evidence from the *built* Stage E installer, recorded
> there. What remains gated is only what always was: the end-to-end proof is a **later
> session's**, once KIT-102 lands and the gate is run by hand once (KIT-104), and the owner
> turns the gate on.

> **Section-number convention.** `§N` always cites `docs/PIPELINE-CONTRACT.md`. This ADR's
> own subsections are referred to by name (e.g. "the approval-gate section"), never by a
> bare number, so the two never blur.

## Decision

**Stage A becomes startable from Linear by the same move Stage E's review lane made: a
human delegates a ticket into a dedicated team, the dispatcher runs a sandboxed session
whose *identity is fixed by that team's dispatcher entry*, and an owner-scoped executor
reads the session's proposed output back and materialises it.** The end state is the owner
as a product manager who writes ideas and approves epics and never opens a Claude Code
session. Six decisions:

1. **Trigger** — the owner files an `idea` ticket, labels it, and **delegates it into a
   dedicated *Planning* team**. The dispatcher (Cyrus today) dispatches it like any other
   delegated ticket. **No poller and no new transport** — planning's trigger is a human
   act the dispatcher already handles natively, which review's (a PR opening) was not.
2. **Routing** — which session *kind* this is (plan, not code) is decided by **which
   dispatcher entry the ticket landed in** — the Planning team's entry, with its own
   tool-fence and its own brief — **not by a label the session reads.** This is the exact
   mechanism Stage E's pivot settled for the reviewer, and it is what makes the routing
   safe under KIT-41.
3. **Safe filing** — the planning session **holds no ticket-create capability.** It reads
   the codebase, runs the planning passes, and emits a **proposed tree** (epic + children,
   with dependency edges) as a structured safe-outputs request. A **credential-holding
   executor** — the direct analog of Stage E's poller-publisher — validates it and creates
   every ticket, forcing every field that carries authority. This needs contract §8's
   `ticket-create` kind **extended to express a tree** (the real design work below).
4. **The approval gate** — the executor files the epic in the **backlog/intake state**
   (`raw`) with executor-forced `provenance:agent`, and the children in `raw` with
   `provenance:epic` pointing at that *just-created, unapproved* epic. **Nothing becomes
   dispatchable until the owner moves the epic out of intake** — the one human gate.
5. **The gate on output** — the **executor** runs the Definition-of-Ready gate
   (`check_ticket_dor.py --strict`, staged from committed config) on the proposed children
   and **refuses to create any that fail.** This would be the DoR gate's first real consumer
   under a live dispatcher (KIT-104), and it depends on the gate being correct first
   (KIT-102).
6. **Coupling** — the design reads Linear and writes through the executor and reaches into
   the dispatcher's internals for nothing, so it survives the Phase-2 dispatcher
   replacement (KIT-10), exactly as Stage E does.

> **The single precondition the whole thing rests on — stated up front because the security
> argument is only as true as this.** Under the measured dispatcher (below), a session holds
> the tracker's *entire* write surface. The safe-outputs validator (§8) and the executor's
> forced fields bind only the **safe-outputs channel** — the path a *cooperative* session
> uses. A session that instead calls `mcp__linear__save_issue` directly never reaches them.
> So the security of this design reduces to one thing: **the planning session must hold no
> direct tracker-mutation tool** — `save_issue` (create *and* update), `save_issue_label`,
> `create_issue_label`. That is the *tool-fence*, and it is currently **unverified** (per-tool
> granularity, item 1 of [What could not be settled](#what-could-not-be-settled-from-documents))
> with **incomplete fallbacks**. **This design must not be built or activated until the
> tool-fence is confirmed to remove those tools from a planning session** (or an equivalent
> update-covering, planning-mode, runner-independent guard exists), plus the two kit-code
> fixes in [the security argument](#the-security-argument). Everything below assumes that
> precondition; where it is not yet met, the row is marked *contingent*, not *structural*.

**Mechanism would ship in the kit** as the extended safe-outputs contract, its schema, and
the executor's tree-validation and DoR-gating logic (source-agnostic, CI-tested `--selftest`
batteries, inert here with no `delivery.json`). **Activation** — the Planning team, its
dispatcher entry, the owner-scoped executor credential, the launchd job — is a
dispatcher-side operator step, handed to the owner, destined for the Phase-2 seed. No session
installs or edits any of it.

## Why

### The transport is already solved — but by option 4, not by the on-disk Stage E ADR

KIT-105's premise is right: Stage E built the transport this needs. But the
[Stage E ADR on disk (2026-09-05)](2026-09-05-stage-e-under-a-delegation-bound-dispatcher.md)
describes a design that was **superseded a day later**. Its trigger had a poller *launch its
own review session directly* ("a second, tiny dispatcher"). That was rejected on 2026-09-06
(KIT-89, "Pivot — option 4"; PRs #71/#72/#73, open, unmerged) because it ran the reviewer
and every fix session **as the owner, outside the sandbox** — no separate account, no
Seatbelt, no egress allowlist — and a prompt-injected diff could then read owner
credentials and get them posted to a public PR. **The pivot is the pattern this ADR
extends, and it is the safer one:**

- The poller **detects** but **never launches a session.** It **creates and delegates** a
  review ticket into a dedicated **Reviews team**, using an owner-scoped Linear key kept
  **outside the dispatcher's environment**, and the dispatcher's own delegation-bound
  dispatch starts the sandboxed session.
- The session's *identity and capability* come from the **dispatcher repository entry** the
  Reviews team maps to: `disallowedTools` fences the toolset and `appendInstruction` carries
  the reviewer brief. Verified from dispatcher source (v0.2.69): `allowedTools` restricts
  nothing, `disallowedTools` per entry is the fence, `promptTemplatePath` is stripped by the
  CLI so the brief must ride `appendInstruction`, and **app-actor delegation is blocked, so a
  session cannot trigger its own review.** Note what the reviewer entry did *not* do: it
  stripped `Bash`/`Edit`/`Write` and similar built-ins, but **left *all* of Linear MCP in
  "by owner decision."** So whether a `disallowedTools` entry can remove an *individual*
  tracker tool (`save_issue` while keeping `save_comment`) is **not demonstrated by the
  reviewer** — it is exactly the open question this ADR inherits, not a solved precedent.
- The poller **reads the session's response back from Linear**, validates the
  `pipeline-review/1` block whole, and publishes the one PR comment.

Planning maps onto this cleanly, and in two places is *simpler* than review.

### The constraints that are not negotiable here

- **A dispatched session holds the tracker's full write surface.** Measured (KIT-96 /
  KIT-97): a session sees all 61 `mcp__linear__*` tools including `save_issue` (create and
  update), `save_issue_label`, `create_issue_label` and state mutations; the dispatcher sets
  **no default `disallowedTools`, so it removes no tracker mutation.** "The session never
  holds the key" — true under the old CI dispatcher — **is false today** (build record §4
  counter-instance, §16 risk "fired on the dispatcher change"). This is *why* filing must be
  executor-mediated **and** why the tool-fence, not §8, is the load-bearing control: §8 only
  inspects the safe-outputs file, and a direct `save_issue` call never produces one.
- **`ticket-create` is single-ticket only, and not yet live.** Contract §8's kind takes
  `{source_ticket_id, title, body, labels[]}` — a flat finding ticket, capped at 3 per run,
  landing in `raw` with executor-forced `provenance:agent`. It has **no parent/child/tree
  shape.** And its executor is designed and built (PR #64) but **not deployed** on the live
  dispatcher, which "runs the local-daemon model with no credential-holding executor for
  `ticket-create`." A companion guard that blocks *direct* `save_issue` **creates** is
  prepared (`feat/kit-96-block-direct-create`, hooks battery green) but **not applied** — and,
  as the security argument notes, it is create-only and ticket-mode-only, so it does not by
  itself back the fence for a planning session.
- **Runner selection is attacker-reachable text (KIT-41, Urgent, open).** The dispatcher
  picks the *runner* (Claude / Codex / Gemini / …) from Linear labels **and from `[agent=…]`
  tags in the issue description, which outrank labels** — and only the Claude runner loads
  the kit's PreToolUse hooks. A session started under another runner has **no guard layer at
  all**, which also voids the PreToolUse fallback for the tool-fence. Addressed in the
  routing section below, not assumed away.
- **The DoR gate is wrong and unread (KIT-102, KIT-104).** Two copies drifted opposite ways
  (the kit's has the `provenance:agent` class but misreads Linear's mention markup; a
  downstream copy has the markup fix but lacks the class), and **nothing reads the gate's
  verdict under the live dispatcher.** Eight tickets already carry `provenance:epic` without
  passing it. An autonomous planner filing dozens of tickets makes that systematic unless the
  gate is (a) correct and (b) enforced on the output.
  _(Update, 2026-09-08 — KIT-102 reconciled the kit copy: it now carries both the
  `provenance:agent` class and a carve-out that tolerates the tracker's inline-mention
  markup, with battery cases for each. The **(a) correct** half is done; **(b) enforced**
  — nothing reads the verdict — is KIT-104, still open.)_

### 1. Trigger

**Decision: delegation into a dedicated Planning team. No poller.**

Review needed a poller because a *PR opening* is not an event the dispatcher emits. Planning
needs none: the trigger is **a human delegating a ticket**, which is precisely what the
dispatcher binds on. The owner files an `idea`/`epic-request` ticket and delegates it into the
Planning team; a sandboxed session starts, bound to that ticket by the delegation — the same
authority the whole build rests on, a value the session cannot write (build record §04).

**Rejected — a poller that files the idea ticket for the human.** A poller that watched for a
new `idea`-labelled ticket and delegated it would reintroduce a machine authoring the trigger.
The point of the PM seat is that **the human's delegation is the trigger**; a poller here
would automate away the one gesture that carries intent. (A poller remains the right answer
only where the trigger is a machine event with no human in it — review's PR-opened.)

### 2. Routing

**Decision: routing by the dispatcher entry the ticket was delegated into, not by a label the
session reads.**

What makes the session a *planner* is the Planning team's dispatcher entry, exactly as the
Reviews team's entry makes a *reviewer*:

- `appendInstruction` carries the **planning brief** — the `/plan-epic` procedure adapted for
  an unattended session: read the codebase, run the PRD/decomposition/rubric passes, then
  **emit a proposed tree as a safe-outputs request instead of filing.** (KIT-98 is where that
  brief text is authored; it is Backlog/not-started, so the brief is a dependency, not a
  thing to inline here.)
- `disallowedTools` **must remove the ticket-mutation tools that carry authority** —
  `save_issue`, `save_issue_label`, `create_issue_label` — while leaving the **read** tools
  the planner needs (Read/Grep/Glob to decompose against real code, the Linear read tools for
  the dedupe pass, and `save_comment`). This is the doctrine-4 form — *withhold the
  capability, don't forbid the action* — and it is the whole security argument's foundation.
  **But whether `disallowedTools` is name-granular for individual MCP tools is unverified**
  (see [What could not be settled](#what-could-not-be-settled-from-documents) item 1 — the
  reviewer left all of Linear MCP in). Three fallbacks, strongest first:
  - **(a) name-granular fence** — remove `save_issue`/label tools, keep read + `save_comment`.
    Structural. Needs per-tool MCP granularity (unverified).
  - **(b) no Linear MCP at all** — the planner holds Read/Grep/Glob + `Write` and emits its
    tree to a run-scoped safe-outputs artifact the executor reads (the `github-actions`
    safe-outputs shape). Also structural, and independent of MCP granularity — at the cost of
    the Linear-dedupe pass (the executor can run dedupe instead) and a way for the executor to
    read the sandboxed session's artifact.
  - **(c) fence coarse, Linear left in** — rely on a PreToolUse guard *extended to planning
    mode* to block create/write. Weakest: pattern-matched, config-gated, and void under a
    non-Claude runner (KIT-41). Only acceptable as a stopgap.

**On KIT-41 — the authority the label carries, stated rather than assumed.** KIT-105 asks
this be made explicit. Two different things could be "chosen by a ticket," and they are not
the same weight:

| What is chosen | By what | Weight | Why safe / where the risk is |
|---|---|---|---|
| **plan vs. code** (which *skill*) | the **team the human delegates into** → dispatcher entry | **light** | A planner has **no more authority than a coding session**: same sandbox, same account, same hooks, same no-merge, same executor-only writes. Its output is gated (DoR + human epic-approval). The worst a mis-*labelled* idea does is run the wrong brief, and the brief cannot escalate. And the *chooser* is a human delegation into an owner-created team, not text a stranger can write. |
| **which runner** (Claude vs. Codex vs. …) | labels **and `[agent=…]` in the description** | **heavy** | Choosing the runner is choosing whether *any* guard loads (KIT-41). This is **not** the planning-vs-coding choice and must not be conflated with it. |

So routing to the planner is a light authority **provided the runner is pinned by owner
config and not overridable by ticket text.** Under a human-authored idea ticket that is
usually fine, but an idea pasted from elsewhere could carry an `[agent=…]` tag. Therefore:
the Planning entry pins the Claude runner, and **the trigger is not safe against pasted idea
text until KIT-41 is closed** (namespace/ignore description-borne runner tags). Stage E's
poller sanitises copied strings for exactly this reason; a human-delegation trigger has no
poller in the path to sanitise, so the fix has to live in KIT-41. Named as a dependency, not
waved off.

**Rejected — a label the session reads chooses the skill.** If the *session* branched on an
`idea` label it read from the ticket, that label would be untrusted authority in the KIT-41
sense (a value in the fence deciding what the session does). Making the **dispatcher entry**
(owner config, outside the ticket) decide keeps the choice off the untrusted surface. This is
why decision 2 is "routing by entry," not "routing by label."

### 3. Safe filing — extending `ticket-create` to a tree

**Decision: the session proposes a tree; the executor materialises it. Extend contract §8
with a tree-shaped request; the executor holds the only create.**

This is the real design work KIT-105 names, and the honest answer to its question ("extend
the kind, or have the executor materialise a proposed tree?") is **both are the same move**:
extend the request shape to *describe* a tree, and let the executor *build* it. A new
`ticket-create` request that carries an epic with its children:

```json
{
  "schema": "pipeline-safe-outputs/1",
  "requests": [
    {
      "type": "ticket-create",
      "source_ticket_id": "KIT-777",
      "epic": { "title": "…one line…", "body": "…the PRD/epic markdown…" },
      "children": [
        { "title": "child A — the prerequisite", "body": "…§ Acceptance criteria …",
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
relations. The security properties every existing `ticket-create` rule already carries are
**kept verbatim**, and the tree shape adds these:

- **`source_ticket_id` still equals the pinned/delegated id** — the plan is provably *from
  this session*. The value is compared, never used to address anything (§8).
- **Everything lands in intake.** The epic and every child are created in
  `findingTicket.landing`, restricted to `raw` — never `ready`/`working`/`review`/`done`.
- **The executor sets every authority field, the session none.** State, provenance and
  assignee are the executor's. The session's `labels` may carry only `track:*`/`effort:*`;
  `provenance:*`, `agent:*`, `blocked:*` and `hooks-change` are refused in every field, add
  and remove (§8), so via the safe-outputs channel the session cannot mint `provenance:human`
  on the epic nor point a child's provenance wherever it likes.
- **The epic carries `provenance:agent`.** An epic drafted by a session is agent-authored,
  and honesty on the board matters: a human-written epic (manual `/plan-epic`) is
  `provenance:human`; an idea-triggered one is `provenance:agent`. By §5 rule 1
  `provenance:agent` **never auto-approves** — so the epic itself cannot approve itself; it
  waits for the human. (Reusing `provenance:agent` rather than inventing a class keeps §5's
  fixed vocabulary intact.)
- **The executor, not the session, sets each child's parent** — forced to the epic it created
  in this same batch. Via the safe-outputs channel the session cannot supply a parent epic id,
  which would otherwise let it name an *already-approved* epic and have its children
  auto-approve. **Create-time forcing is necessary but not sufficient:** a child sitting in
  `raw` is a live ticket whose `parentId` can be changed later by any holder of `save_issue`,
  so closing the pointer hole for the child's whole `raw` lifetime *also* depends on the
  tool-fence (item 1) — see the security argument.
- **The per-run cap changes shape.** The flat kind is capped at 3 (a session reports
  findings, not a backlog). A plan legitimately *is* a tree, so the cap becomes one epic and
  a bounded number of children per run (a `budgets.maxPlanChildren`-style ceiling), still
  all-or-nothing: one malformed child rejects the whole tree, nothing is created (§8).

**How the tree travels** reuses whatever safe-outputs transport the backend already provides
— the run-scoped artifact validated by the safe-outputs job on `github-actions` (which also
suits fallback (b) above), or the read-the-response-back-from-Linear channel Stage E's poller
established for the local-daemon model. The tree *kind* is transport-agnostic; the executor is
the credential holder either way. (Reading a large tree back from a Linear comment has a size
question — see [What could not be settled](#what-could-not-be-settled-from-documents).)

### 4. The approval gate — the crux

**Decision: the executor files the epic in intake as `provenance:agent` and children in
intake under that fresh epic; the human moving the epic out of intake is the intended single
release; and — given the tool-fence — no session-writable value sits on that path.**

This is the load-bearing part, because it is the *human-signal defect class* the whole build
is shaped against (build record §4): a session must never be able to produce the signal that
means "a person approved this." The honest statement has two halves — what the design intends,
and the exact conditions under which that intent actually holds.

**The intended flow:**

1. Executor creates the **epic** in `raw` with `provenance:agent`.
2. Executor creates each **child** in `raw` with `provenance:epic` and `parentId` = the epic
   from step 1. (Provenance value `epic/<EPIC-ID>` is reconstructed from the label class + the
   parent link, §5 rule 4.)
3. **The owner reads the plan** (a summary comment the executor posts on the original idea
   ticket — build record §15: "Epic plan ready → comment on your original idea → read the
   summary, approve") and **moves the epic out of intake.** The intended sole human gate.
4. The **approve tier** (`scripts/check_auto_approve.py`, §11) evaluates each child:
   provenance resolves to `epic/<EPIC-ID>` (§5 rule 4); that epic **exists and is out of
   intake** (§5 rule 2); the child is in `raw`; it carries no `agent:*`/`blocked:*`/
   `hooks-change`; DoR passes `--strict`; nothing matches `autonomy.riskPaths`. All pass ⇒ the
   child moves `raw` → `ready` and becomes dispatchable.

**Where that intent actually holds — and the two ways it does not, unless closed.** Through
the *safe-outputs channel*, every step above is enforced: §8 refuses `raw`/`ready`/`done` as
transition targets **even when a caller lists them in `allowed_to_states`** ("a belt the
caller cannot unbuckle"), and refuses the `provenance:*` class in session fields. But §8 binds
only that channel. Two exposures remain, and both are load-bearing:

- **The direct write surface (the primary exposure).** With `save_issue` in hand (measured), a
  session bypasses §8 entirely: `save_issue` an epic directly in a non-`raw` state, create
  children under it with `save_issue` + `save_issue_label` `provenance:epic`, and the approve
  tier — which reads only the epic's *resulting state* — releases the children. **No human
  acted.** The approve tier's "recomputed from an unwritable source" is true **only if the
  session cannot write that state**, i.e. only if the tool-fence (item 1) holds. It is *not*
  an intrinsic property of the state. So the crux's guarantee is exactly as strong as the
  fence, and no stronger.
- **The "out of intake" signal is too weak, in the current code.**
  `check_auto_approve.check_epic` treats **any** non-`raw` state as approval (`working`,
  `review`, `done` all pass), and the PreToolUse approval guard only fires on the `ready`
  state id — so even a stray forward move by a human, or a Linear board automation, releases
  the whole child tree, and a session writing `working` (not `ready`) evades the one state the
  guard watches. **This must be tightened for the design to mean what it says:** the approve
  tier should require a *specific* human-approval state (e.g. exactly `ready`, or a dedicated
  `approved` state), and ideally verify the transition's **actor** is not the agent identity,
  so the human signal is *verified* rather than *inferred from the fence*.

So the crux is real, but it is **contingent, not structural, until (1) the tool-fence removes
the direct write surface and (2) the approve tier gates on a specific, actor-checked approval
state.** Both are named as required build dependencies below. The design's contribution is to
make the human's epic-approval the *only intended* signal and to route every cooperative path
through gates that already have teeth; making it the only *possible* signal is the build's job,
and this ADR says exactly what that job is.

### 5. The gate on output — DoR, run by the executor

**Decision: the executor runs `check_ticket_dor.py --strict` on the proposed children,
staged from committed config, and creates only those that pass. A failing child is not
created; the executor reports it back on the idea ticket for a re-plan.**

The manual skill runs the gate too (its step 4), but a session gating *its own* output is
reporting, not authority — the same reason the review publisher, not the reviewing model,
computes severity (§14). Under autonomous filing the **executor's** run is the one that
counts, staged from the default branch like every other grader so a PR head cannot loosen the
gate that judges it (§8, §12). This *would give* KIT-104's finding its fix: the gate would
finally have a consumer under the live dispatcher, one that **blocks filing** rather than
decorating tickets after the fact.

It depends on **KIT-102** landing first — a gate that is wrong in either direction is worse
than none once a planner leans on it. Until then this decision is inert by construction (no
`delivery.json` here; the live dispatcher has no executor yet).

### 6. Coupling

**Decision: reads Linear, writes through the executor, reaches into the dispatcher for
nothing.** The trigger uses *delegation* — an abstraction any dispatcher provides, not a
Cyrus internal — and the routing uses a dispatcher *entry*, which the Phase-2 dispatcher will
have in some form too. The safe-filing contract (§8, extended) is dispatcher-agnostic. The
one Cyrus-specific fact the design must respect is the same one Stage E isolates — a session
holds the full tracker write surface, which is *why* the tool-fence and the executor exist —
and that fact only makes the design's precautions necessary; it is not a dependency on Cyrus
code. When Phase 2 (KIT-10) replaces the dispatcher, this path is unaffected.

## The security argument

KIT-105's second deliverable: written out, and mapped to the guard — never a prompt. **Read
the preamble first, because it changes how every row is scored.**

**The one control everything rests on.** The safe-outputs validator (§8) and the executor's
forced fields are enforced *only on the safe-outputs channel* — the path a cooperative session
uses. A session holding the measured full write surface can ignore that channel and call
`mcp__linear__save_issue` directly, which §8 never sees. So **the entire argument reduces to
one precondition: the planning session must hold no direct tracker-mutation tool** (`save_issue`
create *and* update, `save_issue_label`, `create_issue_label`). Rows tagged **structural**
below hold outright; rows tagged **contingent** hold *only while that fence holds* and are
therefore exactly as strong as item 1 of "What could not be settled." The table does **not**
present the fence-dependent rows as independent layers — they collapse onto the fence, and
saying so is the point.

| A planning session must not be able to… | Enforced by | Status |
|---|---|---|
| **Hold a direct tracker-mutation tool at all** (`save_issue`/label-create) | the Planning dispatcher entry's `disallowedTools` fence (or fallback (b): no Linear MCP + `Write`-to-artifact) | **contingent — this is the root control; per-tool granularity unverified (item 1). Every row below assumes it.** |
| **Approve its own epic / move any ticket to an approved state** | *safe-outputs channel:* §8 refuses `raw`/`ready`/`done` as transition targets even in `allowed_to_states`. *direct channel:* only the fence removes `save_issue`(update) | structural on-channel; **contingent** overall (direct `save_issue` bypasses §8) |
| **File the epic pre-approved** | executor forces `landing: raw`; §8 refuses non-intake targets on-channel; direct create needs the fence | structural on-channel; **contingent** overall |
| **Make children auto-approve without genuine approval** | §5 rule 2 + approve tier read the epic's real state — unwritable **only while the fence holds**; and the state check must be tightened (see build-fix A) | **contingent** on the fence **and** on build-fix A |
| **Point/re-parent children at an already-approved epic** | executor forces `parentId` at create; keeping it forced for the child's `raw` lifetime needs the fence (re-parent uses `save_issue` update) | structural at create; **contingent** for the lifetime |
| **Mint a `provenance` label** (`provenance:human` on the epic, any origin on a child) | §8 refuses the `provenance:*` class on-channel; directly needs the fence to remove `save_issue_label` | structural on-channel; **contingent** overall |
| **Set its own lifecycle / acknowledge a guard change** (`agent:*`, `blocked:*`, `hooks-change`) | §8 / §6 refuse these on-channel; directly needs the fence | structural on-channel; **contingent** overall |
| **Land a child that fails the DoR gate** | the executor runs `check_ticket_dor.py --strict` from committed config and refuses failures | structural (executor-side); depends on KIT-102 correctness |
| **Trigger its own planning session** | app-actor delegation is blocked (verified from dispatcher source); a `ticket-create` output lands in `raw`/`provenance:agent` and starts nothing. Residual label/team-move self-trigger needs the fence | structural (delegation route); contingent (label/team-move route) |
| **Escape the guard layer by switching runner** | *(gap)* runner is selectable from untrusted description text — **KIT-41, open.** The Planning entry pins Claude, but this is not closed until KIT-41 lands; it also voids the PreToolUse fallback for the fence | **dependency, not a guard** |

**The named fallback does not currently back the fence.** The KIT-96 direct-write guard
(`feat/kit-96-block-direct-create`) is (a) **create-only** — it does not cover a `save_issue`
*update* of an existing epic's state, i.e. the re-parent and approve-forging moves; (b)
**ticket-mode-only** — its create block is under `if mode == 'ticket':`, and a planning
session's pin is `session_mode: planning`, so it takes the branch that blocks only foreign-team
targets; (c) **not applied** on the live dispatcher; and (d) **void under a non-Claude runner**
(KIT-41). So today the fence is a *single* control, not a fence-plus-guard defence-in-depth.

**Two kit-code fixes this design requires (outside KIT-105's scope; flag for the build):**

- **Build-fix A — `scripts/check_auto_approve.py`:** gate epic approval on a *specific*
  human-approval state (not "any non-`raw`"), and ideally verify the out-of-intake transition's
  actor is not the agent identity. Without it, "out of intake" ≠ "human approved," and a stray
  move or board automation releases the tree.
- **Build-fix B — `.claude/hooks/pre-tool-use.py`:** extend the tracker-write guard to
  **planning mode** (block create; guard same-team write / re-parent / state-change) and cover
  `save_issue` **update**, not just create — so a residual create tool cannot forge the signal
  and the fence is not the only layer.

The through-line, honestly: **the owner approving the epic is the *intended* single human
signal; making it one the session cannot *forge* requires the tool-fence plus build-fixes A
and B. Until those hold, the signal is forgeable via the direct write surface, and the design
must not be activated.** Far from weakening the design, this makes KIT-105 a concrete forcing
function for closing "the rest of the write surface" open work the build record §4 names.

## An illustrative tree (described, not filed)

Per KIT-105's rule against creating real tickets to "test," here is the shape only. Idea
ticket `KIT-777` "Let a session file follow-up findings" (`provenance:human`, delegated into
Planning) →

- **Epic** `KIT-778` (created `raw`, `provenance:agent`) "Safe follow-up ticket filing"
  — summary comment posted back on `KIT-777`, awaiting the owner.
  - child `KIT-779` (`raw`, `provenance:epic`→`KIT-778`, `effort:M`) "Add `ticket-create`
    safe-output kind + schema"
  - child `KIT-780` (`raw`, `provenance:epic`→`KIT-778`, `effort:M`, depends on `KIT-779`)
    "Executor: create, force provenance, notify owner"
  - child `KIT-781` (`raw`, `provenance:epic`→`KIT-778`, `effort:S`, depends on `KIT-779`)
    "Refuse `ticket-create` unless configured"

Before the owner moves `KIT-778` out of intake: three children sit in `raw`, none dispatchable.
After: the approve tier re-evaluates each, and the ones that pass `--strict` DoR move to
`ready`. Nothing the session did **through the safe-outputs channel** could have skipped that
gesture — and nothing it did through the *direct* channel could either, **once the tool-fence
and build-fixes A/B hold.**

## What differs from the two things it resembles

**vs. Stage E (review):** planning is *human-triggered*, so it needs **no poller** and has
**no "reviewed party authors the trigger"** problem — the human authors it. It needs **no
delegation-time basis snapshot** (KIT-92): review's hard problem was "what did the ticket ask
when work started," because the reviewed party could edit the standard mid-flight; planning
reads the *current* codebase and the human approves the *concrete output*, so there is no
standard for the session to rewrite behind the reviewer's back. The two lanes share the
transport (delegate → sandboxed session by entry → executor reads back) and diverge on almost
everything else. One thing they share that this ADR does **not** inherit as solved: Stage E
left all of Linear MCP in the reviewer's toolset, so neither lane has yet demonstrated
per-tool tracker fencing.

**vs. manual `/plan-epic`:** the skill today does raw `mcp__linear` writes and creates the
epic as `provenance:human` (a person ran it). Autonomously, the writes move behind the
executor and the epic becomes `provenance:agent` (a session drafted it). The human gate is
identical — move the epic out of intake — so the skill's own doctrine ("never move anything to
`ready`; the human's single approving action is on the epic") transfers unchanged; only *who
holds the pen at filing time* changes.

## What is accepted as lost

- **Structural non-write via absence of a credential** is only recovered if the tool-fence is
  name-granular (fallback (a)) or the planner runs with no Linear MCP (fallback (b)). Where
  neither holds, the fallback is the KIT-96 PreToolUse guard — pattern-matched, config-gated,
  create-only, ticket-mode-only and runner-dependent, and so weaker than absence. Accepted,
  and named, but this is *the* thing the build must resolve, not a footnote.
- **The runner-selection hole is inherited, not solved here** (KIT-41). This ADR pins the
  runner in config and states the dependency; it does not close the description-tag vector,
  and while it is open the PreToolUse fallback for the fence is void.
- **The DoR gate's correctness is assumed, not delivered** (KIT-102). The design enforces the
  gate; it does not fix it.
- **The approve tier's "human-approved = out of intake"** is weaker than the design's language
  implies until build-fix A tightens it. Accepted only as a named build dependency.

## What could not be settled from documents

The honest list — where the build session should expect the real risk, because it needs Stage
E's *built* behaviour or an open decision to resolve. **Item 1 is the load-bearing one.**

1. **Per-tool tracker fencing — the linchpin. RESOLVED (2026-09-08): fallback (b);
   its IMPLEMENTATION was corrected 2026-09-17 — see the Update at the end of this
   file, which supersedes this item's account of how the fence is enforced.** Whether
   the dispatcher's `disallowedTools` can remove *individual* Linear tools (`save_issue` but
   keep `save_comment` and the read tools) was the open question. The build session read the
   **built** Stage E installer (`scripts/pipeline_stage_e_setup.py`) for the answer the running
   system actually gives, and it is decisive against name-granular fencing being *demonstrated*:
   the review entry's `disallowedTools` names **only built-in tool categories**
   (`Bash, Edit, Write, NotebookEdit, WebFetch, WebSearch, Task, EnterWorktree, ExitWorktree`),
   names **no `mcp__` tool at all**, and the installer's own `--selftest` **forbids any `mcp__`
   entry in the fence** ("the tracker's MCP tools were fenced — that is an owner decision, not
   this installer's"). So per-tool MCP fencing is not proven anywhere in the kit, and resting
   the whole no-self-approval guarantee on it would be resting it on an unverified capability.
   **Therefore the Planning lane takes fallback (b): the planning session is attached NO Linear
   MCP server at all** — it holds `Read`/`Grep`/`Glob` to decompose against real code and
   `Write` to emit its tree, and nothing that can reach the tracker. It reads the idea ticket
   from its **delegated/injected context** (the dispatcher binds the ticket; the session-start
   brief carries the title + acceptance criteria behind the untrusted-data fence), and emits its
   proposed epic+children tree as a **`Write` artifact** — a `pipeline-safe-outputs/1` request
   file — that the owner-scoped executor (`scripts/pipeline_plan_executor.py`) reads, validates,
   DoR-gates and materialises.

   **Why this is stronger than (a), not a retreat.** Fallback (b) is *structural and
   independent of MCP granularity*: a session that holds no tracker tool cannot call
   `save_issue` regardless of what `disallowedTools` can express, what the PreToolUse guard
   pattern-matches, or **which runner loaded** — so it closes the KIT-41 runner-selection hole
   for the tracker-write vector too (a non-Claude runner that loads no guards still has no
   tracker tool). The consequence the ADR named for (b) — losing the in-session Linear dedupe
   pass — is absorbed as designed: the executor is the deterministic party that can run dedupe,
   and the human reads the summary comment before approving. The build is designed and the
   installer configured around (b); the `disallowedTools` entry additionally names the tracker
   tools as belt-and-braces, but the guarantee does not depend on that naming working.
2. **The two kit-code fixes (A: specific/actor-checked approval state; B: planning-mode +
   update-covering tracker guard).** Both are stated above as required; neither exists yet, and
   whether A is done by gating on `ready` vs a dedicated `approved` state is an open call.
3. **The tree read-back channel at planning scale.** Stage E reads *one* review block back from
   Linear. A plan is an epic plus a dozen children with full bodies — potentially past a comment
   size limit, or needing multiple comments or the artifact channel. Untested at this size;
   fallback (b)'s artifact transport may be the better fit.
4. **Gate/create sequencing. RESOLVED (2026-09-08): gate against a pending ref, create nothing
   until every child passes.** The executor builds each child ticket object with a *pending*
   epic reference — parent `<TEAMKEY>-0` (a well-formed but impossible id; real Linear numbers
   start at 1), provenance `epic/<TEAMKEY>-0`, a placeholder project, and the real landing
   state — and runs `check_ticket_dor.py --strict` on the whole set **before creating anything**.
   This is sound because the DoR gate checks a child's *internal* consistency (parent link ↔
   provenance ↔ project ↔ state ↔ sections ↔ acceptance criteria), which does not depend on the
   epic's real id — it has no API and never checks that the epic *exists* in Linear. So gating
   against the sentinel is equivalent to gating against the real id, and it leaves **no orphan
   epic** on failure (the "create epic first" alternative would). If any child fails, the whole
   tree is rejected and reported back (all-or-nothing, §8). This holds against the gate on
   `main` at build time and does not fight KIT-102, which changes *what* the gate checks, not
   that it checks internal consistency.
5. **KIT-98's brief text.** The planning `appendInstruction` does not exist yet (KIT-98 is
   not-started). The routing mechanism is settled; the words it delivers are not.
6. **KIT-41's closure shape.** Until description-borne runner tags are namespaced or ignored,
   the trigger is only as safe as the idea text is trusted. Fine for hand-written ideas; not
   proven for pasted ones.
7. **The end-to-end proof is a later session's.** KIT-105's third deliverable (an idea →
   session → epic + a DoR-passing child through the executor, gate held) is explicitly gated on
   Stage E landing, KIT-102, the KIT-96 executor enabled, and Stage A run by hand once
   (KIT-104). None is done. This ADR is the design and the security argument only.

## Doc-impact list — build status

The mechanism landed 2026-09-08. Rows marked **✓ built** are done in this PR; the rest stay
forward-references until activation and the end-to-end proof (a later session's).

| Doc | Change | Status |
|---|---|---|
| `docs/PIPELINE-CONTRACT.md` §8 (+ `schemas/safe-outputs.schema.json`, §12 parity) | The tree-shaped `ticket-create`: `epic`/`children`/`depends_on` fields, the per-run child cap (an executor constant `MAX_PLAN_CHILDREN`, not a budget), the executor-sets-parent rule, `provenance:agent` on the epic. Prose + schema amended in lockstep; `check_schemas.py` parity + fixtures added. | **✓ built** |
| `scripts/pipeline_plan_executor.py` (+ `test:plan-executor` in CI) | The deterministic executor: validate whole → DoR-gate every child → materialise epic/children with forced fields → summary comment; all-or-nothing; `ok`/`rejected`/`errored` verdicts (§13). | **✓ built** |
| `.claude/skills/plan-epic/SKILL.md` | An unattended mode: emit a proposed tree as a safe-outputs request instead of raw `mcp__linear` writes; keep the interactive path. | **✓ built** |
| `scripts/check_auto_approve.py` | **Build-fix A:** gate epic approval on a specific approval state (`EPIC_APPROVAL_STATE = "ready"`), not merely "out of intake." | **✓ shipped earlier** (PR #77) |
| `.claude/hooks/pre-tool-use.py` (+ `test_hooks.py`) | **Build-fix B** (planning-mode + update-covering tracker guard). **Under fallback (b) this is no longer load-bearing** — the planning session holds no tracker tool at all, so there is nothing for a PreToolUse guard to catch on that path. It remains available as optional defence-in-depth for a *coding* session on a direct-credential backend (where the KIT-96 protected-label guard already lives, PR #78), but the idea gate does not depend on it. | **not needed for (b)** |
| Activation: Planning team, dispatcher entry (fence = no Linear MCP + brief), owner-scoped executor credential/job | An operator installer (`scripts/pipeline_stage_a_setup.py`) mirroring the Stage E installer, run by a person, refusing in an agent environment. | pending (Build 2/3) |
| The planning `appendInstruction` | The planning brief the Planning entry delivers — `PLANNING_BRIEF` in `scripts/pipeline_stage_a_setup.py`, every load-bearing phrase pinned by its selftest. KIT-98 closed without it; KIT-163 wrote it. | **✓ built** (2026-09-19 update) |
| End-to-end proof (idea → session → epic + DoR-passing child through the executor) | KIT-105's third deliverable, gated on KIT-102 + KIT-104 + the owner turning the gate on. | pending |
| KIT-98's `docs/SESSION-BRIEF.md` | The coding and reviewer briefs. The planning brief is the row above, not this file. |
| The pipeline field guide (`§12` model labels / session kinds) | A planning session is a distinct, executor-mediated session kind with a pinned Claude runner — forward-reference only. |
| Build record (artifact) §7A / §17 / §18 roadmap | Stage A moves from "built, never exercised" toward "startable from Linear"; the governance gap narrows. Forward-reference only until proven. |
| The Stage A guide + runbook (`stage-a-ticket-factory-{guide,runbook}.md`) | Today they document the manual, interactive path. Add: a Linear-triggered planning path is designed in this ADR, not yet built. |
| `docs/AUTONOMY.md` | The approve tier would gain a real upstream producer (the planner); note the epic-approval gesture is the tier's human input, and the tightened approval-state requirement (build-fix A). |
| `CLAUDE.md` / `docs/CLAUDE-template.md` | The dev-loop section: a planning session is a distinct session kind, executor-mediated, never files raw. |

## Verified

- **The design was adversarially reviewed before landing, and the review moved it.** A
  multi-lens review (three independent break-it security lenses plus source-fidelity, KIT-105
  coverage, and consistency) found a **critical overclaim** in an earlier draft: the security
  table attributed the approval guarantee to §8 and called it "structural," when §8 binds only
  the safe-outputs channel and the measured direct `save_issue` surface bypasses it. The
  argument was rebuilt around the tool-fence as the single contingent control, and build-fixes
  A (approval-state) and B (planning-mode guard) were added from the same review. Catching that
  the load-bearing claim was contingent, before it shipped, is the strongest evidence the
  argument now states what is true. (The review also corrected the "read-only reviewer" claim,
  a stray §7 citation, and the `depends_on` example.)
- **The pattern this extends is real and running-adjacent**: Stage E's option-4 transport
  (delegate into a dedicated team → sandboxed session by dispatcher entry → owner-scoped
  reader publishes) is built across PRs #71/#72/#73 (open) and its constraints were read from
  dispatcher source v0.2.69 (KIT-91/#72). This ADR reuses it; it does not re-verify it.
- **The write-surface reality is measured, not assumed** (KIT-96/KIT-97): a session holds all
  61 `mcp__linear__*` tools and the dispatcher removes none. This is *why* the tool-fence, not
  §8, is the load-bearing control.
- **The safe-outputs-channel invariants are the contract's, mechanically checked**: §8 refuses
  `raw`/`ready`/`done` and the `provenance:*` class in session-facing fields
  (`npm run test:safe-outputs`, `test:emit`); §5's `autoApproveProvenance ⊆ ["epic"]` and the
  epic-exists-and-approved rule are `check_auto_approve.py` + `test:approve`. The design adds
  no new trust on that channel; the open work is the *direct* channel and the approval-state
  strength, both named above.
- **Nothing here is built or run.** No `delivery.json` ships in the kit, so the extended kind
  and the executor logic would be inert here and proven by `--selftest` in CI, as every other
  pipeline script is. The gating dependencies (Stage E, KIT-102, KIT-96 enabled, KIT-104, and
  build-fixes A/B) are open, and this ADR does not pre-empt any of them.

---

## Update 2026-09-17 — fallback (b) was implemented as a key nothing reads

**What this corrects.** Item 1 above records the fence as *resolved to fallback (b)* and
describes the planning session as holding "no Linear MCP at all". The installer implemented
that sentence literally: the composed Planning entry carried `linearMcpAttached: False`, and
both this ADR and the installer treated that field as the structural control.

**The dispatcher never reads it.** Version 0.2.69 has zero references to the key across all
of its packages, while its MCP config service injects `linear`, `cyrus-tools` and `cyrus-docs`
into every tracker-triggered session. The entry's `disallowedTools` named six individual
`mcp__linear__*` tools, so a planning session would have held the tracker's whole write
surface apart from those six. Nothing was exposed: Stage A has never run on any machine.

**What is true now.** The fence is the shape
KIT-132 gave the reviewer. Every server the
dispatcher injects is named in `disallowedTools` in both documented rule forms,
`mcp__<server>` and `mcp__<server>__*`; `MCP_FENCE_RULE_RE` refuses any other shape, because
a rule the runtime does not honour is skipped in silence and reads as a closed fence. The
unread key is gone, and so is `allowedTools`, which narrows nothing and changes which servers
are injected. Twenty-nine built-in tools go with them — everything that runs, edits, fetches,
schedules, messages, publishes or reads an MCP resource by argument.

**Fallback (b) is still the decision, and two of its claims no longer hold.** The mechanism
is now a named deny list instead of a field nobody consumes. That keeps the claim that a
correctly routed planning session holds no tracker tool. It loses two things this ADR said
fallback (b) delivered:

- **Runner independence.** Fallback (b) was said to close the KIT-41 runner-selection hole
  because a session with no MCP server attached has no tracker tool whatever runner loads.
  The servers are attached now, and denied by name. Whether a non-Claude runner honours that
  deny list is unverified, so KIT-41 is not moot.
- **"Routing by entry, not by label."** The routing decision above says the job kind comes
  from the team's entry, never from text or labels on the ticket. The dispatcher reads a
  description routing tag before team keys, and a label-selected prompt type's tool list
  before the entry's own. An idea's own text or labels can therefore start a session outside
  the planner's fence (KIT-154). Until that closes, the trigger is safe only for ideas a
  person writes, not for pasted text.

**What the planner keeps, and the one thing that is not proven.** It keeps Read, Grep and
Glob to decompose against real code, and Task/Agent because the planning procedure's rubric
panel is five passes in fresh contexts. It does **not** keep `Write`, which fallback (b) above
relied on for its artifact: the dispatcher's OS sandbox confines only shell commands, and the
dispatcher hot-reloads its own config file and loads a working-directory `.mcp.json`, so a
planner that could write a file could rewrite its own fence. Its proposal therefore travels in
its final message, which the dispatcher posts to the idea ticket, and is read back from there —
the route the reviewer already uses (KIT-150). Whether a subagent
started by `Task` inherits its parent's `disallowedTools` is **unverified**, and if it does
not, one Task call reopens the fence. No selftest can answer it; the activation checklist
carries a live probe for a person to run before the gate goes on
(KIT-140).

**Two consequences of holding no tracker tool that this ADR still overstates.** The
decomposition section says the executor "can run dedupe instead" of the planner's Linear
search pass; the executor does not, and no ticket asked it to
(KIT-141). And "the executor reads the
Write artifact" has no reader behind it: nothing in the kit discovers a finished planning
session, fetches its tree, or invokes the executor with the delegated ticket pinned
(KIT-150). The installer now stops on that,
by name, **before** it hands over the Planning entry — applying the entry turns on a producer
whose output nothing would read.

**The installer's live path.** Until 2026-09-17 the installer was a selftest-only scaffold:
its ledger was never written, `verify` raised, and every tracker call failed by construction
(KIT-135). It now keeps a mode-600 ledger that
`status` replays, reports every step as done, already done, would-change, blocked on a person,
failed, or could-not-measure, and provisions the Planning team and labels over a transport
that refuses redirects. The installer and the executor still read different config files
(KIT-136).

---

## Update 2026-09-19 — the build that makes the gate runnable

This block records the decisions the 2026-09-19 round took. Each part landed in its own pull
request, and each supersedes the older text it names.

### The planning brief is written, and pinned (KIT-163)

KIT-98 closed having shipped the coding and reviewer briefs only, so the planning brief had no
author, and the placeholder told a planner things that were false: "do not ask questions" when
the executor has a question channel, and a "safe-outputs path" that does not exist. The brief
(`PLANNING_BRIEF` in the installer) now tells a planning session:

- **Its ticket** is the `<identifier>` in the prompt's `<linear_issue>` block — the only valid
  `source_ticket_id`. The dispatcher's issue prompt carries that block, and the brief is
  appended after it as the last `<repository-specific-instruction>` block.
- **The idea is data, not instructions.** This lane has no pin, so the session-start fence the
  routing section above relied on never runs here. The brief is the fence, and it names its own
  position so a description that fakes a brief reads as data.
- **Its one output** is a fenced json block at the end of its final message, with nothing after
  it. The dispatcher posts only the last text-only assistant message as the session's response;
  an earlier message becomes a "thought" nothing reads.
- **Which passes it runs and which the fence removes**: no census or config preflight and no
  readiness gate (no shell — the executor gates), no duplicate check (no tracker), no filing,
  no read-back and no telemetry block.
- **How to ask**: a `ticket-comment` naming its own ticket, in the same document.
- **How a child that changes a guard is flagged**, now that it cannot carry the guard-change
  label: a fixed first line in its Context and the path under Pointers. The executor's summary
  lists every such child for the owner (`GUARD_CHANGE_MARKER`, `guard_change_children`).

The skill's unattended section now lists every difference from the interactive path, step by
step, and describes the fence as the deny list it is.

### The executor is safe to retry and loud when it cannot report (KIT-170, part)

A reader that polls finished sessions will hand the executor the same proposal twice: after
an error, or after its own state is lost. Before this round that filed a second tree, and a
rejection or no-output note that failed to post exited 0 or 3 with nothing on the board. Now
the epic carries a plain-text receipt over the pinned ticket and the proposal as filed; the
executor asks for it before creating anything, and files nothing a second time. A run that
fails partway lists what it created on the ticket. A report that cannot be posted is
`errored`. Session text is scanned with the review publisher's credential scan before it is
filed or quoted, and a telemetry block is never delivered as a question. KIT-170's other
items (the `notify` modes, advisory `depends_on`, dependency positions in the summary) stay
open on that ticket.

### The idea is never the ticket the dispatcher sees (KIT-154, option A; KIT-150)

**The decision.** The owner chose the pre-delegation lane on 2026-09-19, relayed through the
orchestrating session. The routing section above says the job kind comes from the team and
never from the ticket's own text; that was false for three routes, and no dispatcher setting
closes the first of them. So the gate stops showing an idea to the router at all.

The owner's gesture is a **state move**, which starts nothing by itself. The planner job
(`scripts/pipeline_plan_poller.py`) reads the idea's history for the move, checks the owner
made it, and writes a **planning ticket**: the idea quoted inside `<untrusted-idea-data>`
with every routing and runner directive neutralized, no labels, no project, and exactly one
routing tag — its own, naming the Planning entry. That ticket is created and delegated in a
single call, so no session's identity is in the loop, and `assert_one_directive` refuses to
file a description carrying anything else.

What this closes, and what it leaves:

| Route | Before | Now |
|---|---|---|
| A `[repo=…]` tag in the idea's text | starts a coding session in every matching entry | the tag never reaches a delegated ticket |
| A label selecting a prompt type | replaces the entry's fence | the planning ticket carries no labels |
| A runner label or `[agent=…]` tag (KIT-41) | picks a runner that may load no guard | neither reaches the delegated ticket; the residual is a person re-delegating a planning ticket they labelled, and only the owner may start a session |
| Anyone delegating an idea | a paid session, attributed to the owner | only the owner's own move starts a run, checked against the ticket's history |

An idea handed to the agent directly still starts a session — nothing can stop that from
inside the tracker — so the job warns on that ticket, once, and **files nothing** from it.

**The read-back (KIT-150).** The route is the reviewer's, proven live: the session's final
message is posted as its one `response` activity, and the job reads it back, hands it to the
executor with `--message`, and pins the planning ticket's identifier — a value the session
never chose. The executor decides what counts as a proposal: the LAST fenced
`pipeline-safe-outputs/1` block wins, none is an empty run with a visible note, and one that
does not parse is a rejection, because a proposal cut off mid-block must not read as silence.

**The size question, measured rather than assumed.** A twenty-child plan with realistic
bodies is **50,429 characters** as a final message end to end, through the real executor and
the real readiness gate (`npm run test:plan-poller`). The schema's own caps allow roughly
340,000. The tracker's limit on an activity body is **not documented and not measured**; the
dispatcher neither truncates nor splits, and a body the tracker refuses is never posted at
all — which arrives here as a session that finished with no plan, and therefore as the
executor's no-output note rather than a partial tree. The first real plan's size is recorded
at activation.

**Where the job runs, and why not beside the review poller.** As the executor's own macOS
account, under a system LaunchDaemon the installer writes and never loads. Every coding
session can read any file the dispatcher's account can read through the dispatcher's own
tool server (KIT-162, accepted for coding sessions); the key this job holds files tickets
carrying provenance labels, so it must not live in that home. The job's key must be the
owner's own, because the dispatcher checks the DELEGATOR against the entry's allowed users —
a pass whose key belongs to anyone else stops with a config error rather than filing
planning tickets the dispatcher would refuse in silence.

### The Planning entry is one the dispatcher can load (KIT-155)

The composed entry carried a name, a team key, a never-used routing label, the fence and
the brief — and nothing that makes an entry work. It now carries `repositoryPath` and
`baseBranch` from the entry that already manages the planned repository (identity is that
clone's `origin`, with the entry's own `githubUrl` as a second authority), the
`workspaceBaseDir` and `linearWorkspaceId` the dispatcher uses, `userAccessControl.allowedUsers`
set to the owner alone, and an `id` equal to its `name`. `entry_problems` names every one
that is missing, and a selftest mutant drops each.

The installer reads the dispatcher's config the way the review installer does — through
that installer's own reader program, run as the dispatcher's account, which prints FACTS
and never the file, because the file holds the dispatcher's tracker tokens. Four refusals
come with it: no clone of the planned repository, entries that disagree about the workspace
base or id, another entry already claiming the Planning team key (team routing takes the
first claimant, so which entry ran a planning ticket would depend on file order), and a
routing tag naming the Planning entry that another entry would also answer.

**The fence now covers what this machine injects**, not only the four servers every machine
has: every server the dispatcher's `linearMcpConfigs` files name, and every server the
planned repository's own committed `.mcp.json` would add from the session's working
directory. A server that cannot be named is a refusal, never a smaller fence. One more
source was found while building it: an agent definition under the planned repository's
`.claude/agents/` can declare `mcpServers` of its own, and those tools reach a HELPER
session; the deny list still applies to them by name, so the installer refuses to compose
an entry while such a definition exists rather than fence over a name it was never given.

A prompt type that sets a tool list in the dispatcher's `promptDefaults` is reported rather
than refused: the planning ticket the job writes carries no labels, so none is selected —
but a label added to one by hand would select it, and that type's list would replace this
fence.

### A label cannot swap the fence, and the runner residual is written down (KIT-154, routes 2 and 3)

**Route 2 is closed in the entry itself.** The dispatcher resolves a session's tool list as
the entry's `labelPrompts[type]`, then the global `promptDefaults[type]`, then the entry's
own `disallowedTools`; the type comes from the ticket's labels, and `orchestrator` selects
one on every entry whether any `labelPrompts` exists or not. The planning ticket the job
writes carries no labels, which closes this at the source. The entry now closes it as well:
it defines every prompt type this dispatcher version can select — `debugger`, `builder`,
`scoper`, `orchestrator`, with `graphite-orchestrator` resolving to the last — each with
the same fence and a label no ticket holds. Whichever type a label selects, the list that
wins is the planner's. A type a later dispatcher adds would not be covered, so the
installer refuses to compose an entry while `promptDefaults` names a type outside that set.

**Route 3 is not closed, and this is the wording of the residual.** The owner has not yet
confirmed it, and it rests on a fact about the machine that only the owner can check.

> A runner label (`codex`, `openai`, `gemini`, `cursor`, `opencode`, or a
> `<provider>/<model>` label) or an `[agent=…]` / `[model=…]` tag picks the runner, and no
> entry setting overrides that (`RunnerSelectionService.determineRunnerSelection`, 0.2.69).
> Whether a non-Claude runner honours `disallowedTools` is unverified (KIT-41). Under the
> pre-delegation lane, neither a runner label nor a runner tag reaches the ticket the
> dispatcher sees: the planning ticket carries no labels and its text is stripped of both
> tag shapes, and a session's runner is fixed when it starts. The residual is a person
> adding a runner label to a planning ticket and delegating it again, and only the owner
> may start a session in the Planning entry. Accepted on the additional ground that no
> runner other than Claude has credentials in this dispatcher's environment — a fact to
> re-check whenever another provider's key is added.

### The duplicate-check pass has a home again (KIT-141)

The decomposition section said the executor "absorbs" the planner's tracker-searching pass.
It did not, and nothing did, so a plain reading of the shipped documents described a check
that was not running.

This section, with the KIT-150 one above it, also supersedes the 2026-09-17 update's **"Two
consequences of holding no tracker tool that this ADR still overstates"** — both of them.
A ticket did ask for the dedupe pass (this one), and the reader that paragraph said nothing
in the kit provided is `scripts/pipeline_plan_poller.py`. That paragraph is left standing as
written, as every superseded passage here is; read it as a record of 2026-09-17, not of now. The executor now runs it: before it creates anything it reads the work
team's most recently updated tickets and compares their titles with the proposed children's,
by shared significant words over the shorter title — deterministic, model-free, like the rest
of the file.

It is a **report, never a verdict**. It lists what looks alike in the summary the owner reads
when approving, and never rejects a plan: whether two tickets are the same piece of work is a
judgement about intent, and the person approving the epic is the one who can make it. All
three outcomes are said out loud — matches, nothing alike, and a lookup that could not run —
because an absent section would read as the second, which is the one thing it must not mean.

### A helper session inherits the fence (KIT-140)

The planner keeps `Task`/`Agent`, because the rubric passes are independent contexts. That
made one question load-bearing: if a helper session were handed a fresh tool set, one call
would reopen everything the deny list closes. It was recorded as unverifiable from the kit,
and the activation checklist carried a live probe for it.

It is answerable from source. The chain is dispatcher 0.2.69 → `cyrus-claude-runner` 0.2.69
→ `@anthropic-ai/claude-agent-sdk` 0.3.245 → the CLI binary it runs, 2.1.245 (the version
and its build are named in the SDK's own manifest). The SDK passes an entry's
`disallowedTools` as `--disallowedTools`; the CLI turns that into deny rules on the
session's permission context. In 2.1.245 the tool pool a session is offered is filtered by
those rules, and a helper's permission context is derived from its parent's — the derivation
changes the mode, the prompt behaviour, the allow rules and the working directories, and
never the deny rules. So a denied tool is neither offered to a helper nor callable by one.

**One exception, and it is why the entry composer reads the planned repository.** An agent
definition's own front-matter `mcpServers` are connected for the helper without that pool
filter. A call to one is still refused when a deny rule NAMES that server, which is what
fencing every server the machine and the repository inject is for (KIT-155) — and why a
definition naming servers the installer cannot name is refused outright.

The live probe stays, and its card now says what to expect: the two tool lists should match.
Source is what the runtime should do; the probe is what it did.

## Update 2026-09-23 — planning built into each work team (KIT-184)

**The decision.** The owner decided on 2026-09-21, and approved the design on 2026-09-23,
that there is **no Planning team**: Linear caps teams, and each extra team is more setup per
project. Planning now lives inside each project's own work team. The as-built lane (the
2026-09-19 block) was never installed, so nothing is migrated.

**What this block supersedes.** Every passage below stays as written; read each as a record
of its date, not of now.

- **The Planning-team trigger** — decision 1, "delegation into a dedicated Planning team", as
  already narrowed by the 2026-09-19 block's "the owner moves an idea into the Plan it state on
  the Planning team". The owner still moves an idea into **Plan it**, but on the project's own
  work team. The team the idea is on says which repository it is for.
- **Option A's Planning team** — the 2026-09-19 block "The idea is never the ticket the
  dispatcher sees", where the planning ticket carries "no labels" and "exactly one routing tag
  … naming the Planning entry". The planning ticket is now filed on the idea's own team and
  carries its tag **and** one routing label.
- **The routing decision** — decision 2, "routing by the dispatcher entry the ticket was
  delegated into", which relied on the Planning team's entry, and the 2026-09-19 KIT-155 refusal
  "another entry already claiming the Planning team key". A planning entry now claims **no team
  key** at all.
- The 2026-09-19 route-2 and route-3 paragraphs, where they say the planning ticket "carries no
  labels". It carries one: its routing label, which names no prompt type, runner or model.

**The planning entry deliberately has no team key.** The team key is the **only fetch-free
routing input** the dispatcher has (Cyrus 0.2.69, `RepositoryRouter`): the `[repo=…]` tag, the
routing label and the project are each read by a separate fetch, and a fetch that fails counts
silently as "none". The team is read from the webhook itself. On a dedicated team, a routing
failure landed in a fenced entry. On a work team it lands in the **coding** entry, which claims
that team's key. A planning entry that claimed the key too would compete with the coding entry
for every ticket on the team. So it claims none, and three things replace the net the team used
to be:

1. **A routing label unique to the repository** (`stage-a-planning-<repository name>`, team-scoped,
   exact case). It catches a tag that failed to fetch or match, and a one-off blip. It cannot
   catch a failure that hits both fetches, which share one client and one key. It must be the
   only entry's label: several matching entries make **one** session whose disallowed tools are
   only what every matched entry denies (`ToolPermissionResolver`), so a shared label would empty
   the fence. The installer refuses a label another entry routes on or reads as a prompt type.
2. **The routing check, in the same pass.** The dispatcher posts a `**Routing**` thought naming
   the entry that answered, after the worktree exists and **before the runner starts**. Seconds
   after filing, the planner job reads the session's **earliest** routing-shaped thought (a model
   could imitate a later one). A note naming any other entry, a route other than the tag or the
   label, or **no note** within the wait is a failure: the note is best-effort, so absence proves
   nothing. The wait defaults to 120 seconds and stretches to 330 while a repository setup hook
   runs, because the note comes after the hook.
3. **A circuit breaker.** On a failure the job writes the evidence first — the session id, the
   note, the times — to a stop file and its own record, because cancelling deletes the session's
   worktree. Then it cancels the ticket, pages the owner with an `agent:needs-human` mark on the
   dead planning ticket (a mark the notifier already reads), and notes the idea. **All planning
   stops** until a person signs the probe again; the stop survives restarts, an unreadable stop
   counts as stopped, and only a probe signed after it clears it. A planning ticket is never
   reused, because the dispatcher caches a ticket's first route for good.

**The residual.** A planning ticket whose tag and label fetches both fail runs in the coding
entry for the seconds between the routing note and the cancel. It works from the clean copy of
the idea, not the idea itself; cancelling deletes its working copy; it cannot merge. This is the
accepted price of no Planning team.

**A dispatcher upgrade is the larger risk, and it is pinned.** One upgrade that changed tag or
label routing would send every planning ticket to the coding entry. The executor's account
cannot read the dispatcher's install directory (it is mode 700 under the dispatcher's account,
measured), but the dispatcher serves its own version on `GET /version` with no key
(`EdgeWorker.registerVersionEndpoint`), and every `cyrus-*` package pins its siblings exactly, so
that version fixes the routing code. **The job reads it over loopback on every pass** and starts
no run when it differs from the version recorded at the probe, or cannot be read. The installer
records the version at the probe sign-off; when `DISPATCHER_ROUTER_FILE` names the routing code,
it also records its fingerprint, read as the dispatcher's account, and `verify` re-checks both.
The runbook's upgrade step is: upgrade, re-probe, sign.

**The probe proves which entry answered.** For every planned repository, a person files one
probe ticket with the tag and the label, and one with the label alone. `attest CA-PROBE
--ticket …` reads the dispatcher's routing notes back and refuses a sign-off they do not
support: every ticket routed to its own repository's planning entry, and both routes seen.

**Everything else the design page found.**

- **Live work dragged into Plan it is refused**, with one note and no run: a ticket with a
  delegate, an agent session, a `provenance:*` or `agent:*` label, a parent or children, or a
  pull request attached. A daily cap across every team (default 10) bounds a bulk move.
- **Plan it must be unstarted**, created so by the installer, which refuses an existing state of
  that name of any other type; the job re-checks the type every pass (KIT-183). A started Plan it
  would catch every coding ticket, since the dispatcher moves each session's ticket to the
  lowest-position started state. The installer reports which state that is, per team.
- **The trigger reads the whole history**, paged and bounded (KIT-183). Linear's history takes an
  order field but no direction, and the direction is not documented.
- **The direct-delegation warning** watches only ideas in Plan it, and only sessions created after
  the move, plus sessions on the job's own tickets that it did not start. It never fires on the
  team's real work. And the read-back reads the session whose routing was checked, never a later
  one.
- **Other jobs skip planning tickets**, by their routing label or their opening tag and never by
  title, through one shared definition (`scripts/pipeline_machine_tickets.py`): the review
  poller's discovery, the criteria snapshot, the finding poller, and the executor's duplicate
  check, which also leaves out the idea itself.
- **The executor refuses a plan whose pinned ticket is on another team** than the repository's
  `delivery.json` names, and the job gives up on such a run at once, with a note, rather than
  retrying it.
- **Two repositories naming one team are refused**: the dispatcher routes a team key to one
  repository, so one team plans one repository. The settings are a list, `PLANNED_REPOS`; each
  repository's team is read from its own committed `delivery.json`.
- **Team agent guidance reaches every session on the team**, planners included, with
  "takes precedence" wording. Linear's public API does not expose it (it exists only in webhook
  payloads), so the probe card asks a person to read it and record it in the sign-off.

**What the pre-merge review changed.** An adversarial review of the build found seven real
defects, all fixed before merge:

- **A routing read that failed ended the pass**, so later passes filed new planning tickets
  before re-checking the old one. Now every open routing check is finished first, and no
  new run starts while any route is unknown.
- **One substitution in the sanitizer could fold an idea's text into a directive**, and the
  refusal ended the whole pass. The sanitizer now runs until nothing changes, and an idea it
  still cannot clean is refused on its own.
- **The Stage E skips read marks a coding session can write.** A session could put the
  planning label on its own ticket and take itself out of review. The review poller's
  discovery and the criteria snapshot now skip a ticket only when the dispatcher's own
  routing note confirms a planning session. The finding poller and the duplicate check still
  skip by the marks alone: a session that skips them only hides its own reports.
- **A repository added after the sign-offs** was planned under the old probe, and its entry
  was never printed. Both sign-offs now re-open until they cover every planned repository.
- **A re-sign read the same probe tickets again.** It now needs tickets handed over after the
  last sign-off, because a re-sign is what clears a stop.
- Executor runs the pass's clock killed were never counted, and a closing note that failed
  once was lost. Both are retried and bounded now.

**KIT-182's criterion "each fenced entry keeps its team key" does not hold for a planning
entry**, by this decision. It still holds for the review entry, which keeps its team.

**Not proven — nothing here has run live.** The routing note's rendered shape and its delay after
delegation; whether the tracker escapes it; `Issue.history`'s page order; whether `Team.labels`
returns every team label a pass needs; that the installed dispatcher matches the 0.2.69 source
this was read from. The probe measures the first three on a real dispatcher before the gate is
switched on. One residual is known and not closed: if the dispatcher's own note fails to post
AND the ticket falls to the coding entry, a later thought the model writes could imitate the
note and be read as it.
