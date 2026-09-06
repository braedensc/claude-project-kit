# Approve tier gates epic approval on the `ready` state

**Date:** 2026-09-06 · **Status:** Accepted · **Context:** build-fix A of
[Stage A triggered from Linear](2026-09-06-stage-a-triggered-from-linear.md) (KIT-105,
PR #76), surfaced in that ADR's adversarial review. Branch
`fix/approve-tier-epic-approval-state`. Independent of KIT-105's own build, which is
design-only and gated on other dependencies.

## Decision

The approve tier's epic gate (`scripts/check_auto_approve.py` `check_epic`) now requires
the referenced epic to sit in **exactly the `ready` state** — `EPIC_APPROVAL_STATE` — to
read as human-approved. Previously it approved on **any** classifiable state other than
`raw`, so `working`, `review` and `done` all counted. Contract §5 rule 2 and §11, and
`docs/AUTONOMY.md`, are amended to say `ready`, not "out of intake."

We keep `ready` rather than adding a dedicated `approved` state, and we **defer** the
actor check (that the person who moved the epic is not the agent identity) as named
follow-up work — it needs data this offline script does not receive.

## Why

**The old signal was too weak, and it was the whole child tree at stake.** `check_epic`
treated "not `raw`" as "a human approved this," on the reasoning that every state except
intake means *someone acted*. But "someone acted" is not "a human deliberately approved
this decomposition." A human dragging an epic to `working` to mean *"I'm looking at
this,"* or a Linear board automation advancing it on a rule, would silently release every
`epic/*` child underneath it to auto-approval. The gate trusted epic *state* with no
check on **who** set it or **why** — and approval is the largest grant in the pipeline.

**`ready`, not a dedicated `approved` state.** The kit ships a fixed five-state
vocabulary (`raw`/`ready`/`working`/`review`/`done`). Adding a sixth `approved` state
would be cleaner semantically but forces every adopting project to provision, place and
reference it — real surface for a template — to buy a distinction `ready` already makes.
`ready` is defined in §1's `stateIds` table as *"Approved and dispatchable,"* and it is
the exact state the children are released **into**. So the epic reaching `ready` is the
same approval gesture, one level up: the parent is approved, its children may follow. A
project that later wants a bespoke approval state is a contract change (a new `stateIds`
key + a config knob for the approval-state key), recorded then, not pre-built now.

**This also closes the guard's practical gap without touching the guard.** The PreToolUse
`_approval_guard` fires only when a session writes the `ready` state ID, so a session
writing some *other* state onto its own epic evaded the one state the guard watches. That
evasion is now inert: after this fix, no state but `ready` approves anything, so writing
`working` onto an epic buys an attacker nothing. Fully closing the *write* surface — the
tool-fence plus extending the tracker-write guard to planning mode and `save_issue`
update — is build-fix B, deliberately separate (it edits a self-protected hook, needs the
`hooks-change` acknowledgement + CI job, and belongs with KIT-105's activation).

**The actor check is deferred, not dropped — and deferred honestly.** Verifying the
transition's actor requires the epic's state-**history** (who moved it, into what) and a
configured set of agent identities to compare against. This script holds no credential
and makes no network call by design (so it runs in CI, in a selftest, and on a laptop
against a pasted ticket), and the caller
(`templates/workflows/pipeline-auto-approve.yml`) fetches only the epic's current
`state.id`, not its history. Bolting on an actor check that is silently inert until a
caller is upgraded is exactly the absence-vs-failure defect contract §13 warns against, so
we do **not** ship a half-built one. It is recorded as the next increment: the caller
resolves the actor of the transition into `ready`, the config names the pipeline's agent
identity, and a match becomes a HOLD — gated on that config so "off" and "could not
check" stay distinguishable.

**Exposure when written: latent, not live.** This repo ships no `delivery.json`, so the
whole pipeline — this gate included — is inert here; only its `--selftest` runs in CI.
The caller workflow ships as an inert template that must be `git mv`'d into
`.github/workflows/` and have `PIPELINE_AUTO_APPROVE_ENABLED=true` to run, and per KIT-104
no live producer emits `provenance:epic` children under a live dispatcher yet. So this
hardens a latent path before it carries traffic, rather than closing a live hole.

## Verified

`npm run test:approve` — 44 cases (was 40). New regression cases in
`scripts/check_auto_approve.py`'s selftest:

- `epic in ready approves` — the positive: the one state that releases the tree.
- `epic in working holds`, `epic in review holds`, `epic in done holds` — the three
  states the old code approved and this one rejects, each on the `epic` gate. This is the
  direct regression guard for the "any non-`raw` = approved" defect.
- `epic in raw holds`, `epic in unknown state holds`, `blocked epic holds` (the last now
  exercised at `ready`, so a supervision label still holds even in the approval state) —
  unchanged intent, re-anchored on the tightened rule.

The baseline `epic/*` happy path (`good_epics()` now returns an epic in `ready`) still
approves, so the tightening rejects the weak signals without breaking the real one.
