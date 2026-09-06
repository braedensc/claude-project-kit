# Stage A triggered from Linear — an idea ticket becomes a pending-approval epic tree

**Date:** 2026-09-06 · **Status:** Proposed · **Context:** KIT-105, branch
`docs/kit-105-linear-triggered-planning-adr`. Extends
[Stage E under a delegation-bound dispatcher](2026-09-05-stage-e-under-a-delegation-bound-dispatcher.md)
— the same transport, pointed at planning instead of review.

> **Why Proposed, not Accepted.** This ADR records a *direction*. Its build is gated on
> four things that do not exist yet (Stage E landing, KIT-102, KIT-96's executor enabled
> on the dispatcher, and Stage A proven by hand once — KIT-104), and several mechanism
> choices below can only be settled against Stage E's *built* behaviour, not against
> documents. The end-to-end proof KIT-105 also asks for is a **later session's**, once the
> dependencies are met. What is settled here is the shape and the security *argument*; what
> is not — including the one control the whole security argument rests on — is called out in
> [What could not be settled from documents](#what-could-not-be-settled-from-documents).

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

1. **Per-tool tracker fencing — the linchpin.** Whether the dispatcher's `disallowedTools` can
   remove *individual* Linear tools (`save_issue` but keep `save_comment` and the read tools)
   is unverified — the reviewer left *all* of Linear MCP in "by owner decision," so nothing has
   demonstrated it. The entire security argument's "contingent" rows stand or fall on this. If
   it is not name-granular, take fallback (b) (no Linear MCP; `Write`-to-artifact) rather than
   the weak PreToolUse-only stopgap. **Measure this first.**
2. **The two kit-code fixes (A: specific/actor-checked approval state; B: planning-mode +
   update-covering tracker guard).** Both are stated above as required; neither exists yet, and
   whether A is done by gating on `ready` vs a dedicated `approved` state is an open call.
3. **The tree read-back channel at planning scale.** Stage E reads *one* review block back from
   Linear. A plan is an epic plus a dozen children with full bodies — potentially past a comment
   size limit, or needing multiple comments or the artifact channel. Untested at this size;
   fallback (b)'s artifact transport may be the better fit.
4. **Gate/create sequencing.** The DoR gate checks parent existence, but the children reference
   an epic that does not exist until the executor creates it. Whether to create the epic first
   then gate children against the real id (leaving a childless epic in `raw` on failure), or to
   gate the whole tree against a pending ref, needs the *fixed* gate (KIT-102) in hand.
5. **KIT-98's brief text.** The planning `appendInstruction` does not exist yet (KIT-98 is
   not-started). The routing mechanism is settled; the words it delivers are not.
6. **KIT-41's closure shape.** Until description-borne runner tags are namespaced or ignored,
   the trigger is only as safe as the idea text is trusted. Fine for hand-written ideas; not
   proven for pasted ones.
7. **The end-to-end proof is a later session's.** KIT-105's third deliverable (an idea →
   session → epic + a DoR-passing child through the executor, gate held) is explicitly gated on
   Stage E landing, KIT-102, the KIT-96 executor enabled, and Stage A run by hand once
   (KIT-104). None is done. This ADR is the design and the security argument only.

## Doc-impact list — when the feature is built (not now)

Describing unbuilt behaviour as real is the failure this project keeps catching, so **none of
these is rewritten now.** At most each gets a one-line forward-reference to this ADR; the
substantive edits land with the build.

| Doc | Change, when built |
|---|---|
| `docs/PIPELINE-CONTRACT.md` §8 (+ `schemas/safe-outputs.schema.json`, §12 parity) | The tree-shaped `ticket-create`: `epic`/`children`/`depends_on` fields, the new per-run child cap, the executor-sets-parent rule, `provenance:agent` on the epic. Amend prose + schema in lockstep. |
| `docs/PIPELINE-CONTRACT.md` §5 / §11 | Note that an idea-triggered epic is `provenance:agent` and gates the tree via rule 2; **and that the approve tier must gate on a *specific* human-approval state (build-fix A), not merely "out of intake."** |
| `scripts/check_auto_approve.py` | **Build-fix A:** gate epic approval on a specific approval state + ideally an actor check on the transition. Add the case to `test:approve`. |
| `.claude/hooks/pre-tool-use.py` (+ `test_hooks.py`) | **Build-fix B:** extend the tracker-write guard to planning mode and to `save_issue` update. Guard-change → `hooks-change` label + the CI job. |
| `.claude/skills/plan-epic/SKILL.md` | An unattended mode: emit a proposed tree as a safe-outputs request instead of raw `mcp__linear` writes; keep the interactive path. |
| KIT-98's `docs/SESSION-BRIEF.md` / the planning `appendInstruction` | The planning brief itself. |
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
