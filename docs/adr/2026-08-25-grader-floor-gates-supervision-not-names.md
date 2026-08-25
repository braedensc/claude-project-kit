# The grader floor gates what a change can do, not what the file is called

**Date:** 2026-08-25 · **Status:** Accepted · **Context:** `fix/grader-floor-supervision`, KIT-12 gap 2. Amends [Protected-label guard](2026-08-25-protected-label-guard.md), which put `scripts/check_*.py` on the floor.

## Decision

`check_grader_paths.py`'s `FLOOR` gains three globs — `scripts/pipeline_*.py`,
`scripts/jsonschema_mini.py`, `templates/workflows/pipeline-*.yml` — and the
selftest gains an **exhaustiveness assertion**: every file in `scripts/` and
`templates/workflows/` must be either matched by `FLOOR` or listed in a new
`UNGATED` ledger with a written reason, checked in both directions so the ledger
cannot rot.

The telemetry scripts stay out, deliberately and now in writing.

## Why

PR #45 added `scripts/pipeline_labels.py`, which decides whether a ticket parked
with `agent:needs-human` dispatches. It did not match `scripts/check_*.py`, so it
was ungated. Nothing was wrong with the *reasoning* behind the old floor — the
problem is that the reasoning was encoded as a **naming convention**, and a
convention is not a boundary. A file gates supervision or it doesn't; what it is
called is orthogonal.

So the floor is stated as a capability test — *can editing this alter what the
pipeline is permitted to do, without a person seeing it?* — and three families
answer yes:

1. **The guard machinery and the workflows that run it.** Already floored.
2. **The graders (`scripts/check_*.py`) and the dispatch path they import.**
   `pipeline_labels.py` resolves the `agent:needs-human` hold;
   `pipeline_dispatch_local.py` writes the pin, which §1 makes the only
   authority; `jsonschema_mini.py` is the shape layer *two gated graders*
   validate through — neuter its `validate()` and `check_delivery_config.py`'s
   entire schema half goes dark with every gate still green. This is the floor's
   own original rationale ("gating the workflow while leaving the checker open
   just moves the soft spot one file over") applied one level further down,
   where it was already true and unapplied.
3. **`templates/workflows/pipeline-*.yml`.** `.github/workflows/**` is gated;
   these files *become* that on the next sync. One of them,
   `pipeline-safe-outputs.yml`, carries inline the rule that refuses a session's
   attempt to set its own `agent:*` labels — the single most supervision-critical
   logic in the repo, and it was outside the gate purely because the kit keeps
   workflows in two directories. That is an accident of layout, not a decision.

### Alternatives rejected

- **`scripts/**`** — over-gates. It would pull in the telemetry scripts, whose
  authority contract §4 explicitly sets at zero (`telemetry_block.py`'s own
  docstring: no value through it may gate a budget, an approval or a merge). A
  bad edit there costs dashboards, not supervision, and the pre-existing
  selftest assertion "floor leaves ordinary scripts alone" was a deliberate call
  about proportion that this change keeps and now explains.
- **Enumerating the extra files literally** — trades one brittleness for
  another: a new dispatch file is forgotten instead of misnamed.
- **Leaving it and documenting why** — the honest option the ticket offered, and
  rejected here for one reason: no argument survives that gates
  `check_delivery_config.py` but not the `jsonschema_mini.py` it validates
  through.

### The accepted tradeoff

More PRs now need a human's `hooks-change` label. Measured against this repo's
history: of 46 commits, **6 would newly require it** (5 touching
`templates/workflows/pipeline-*.yml`, 1 touching the scripts additions) — the
rest of the matching commits already touched a gated path and needed the label
anyway. 13% more label friction, concentrated entirely on supervision machinery,
is the intended price.

### What keeps this honest

Not the globs. The **exhaustiveness assertion** — a new file that matches neither
`FLOOR` nor `UNGATED` turns the battery red, so "we forgot to name it `check_`"
becomes a test failure instead of silence. That is the durable fix for the class;
the three new globs are just the current instance of it.

### Deliberately not changed

The PreToolUse hook's own `GRADER_PATH_FLOOR` is narrower (`.github/workflows/**`
plus `delivery.json`) and stays so. The two are related, not mirrored: the hook
blocks a *pinned session's local edits*, this gates *any PR from any author*. The
hook is self-protected, so aligning them is a human's edit in any case; the
comment claiming the two are "kept in step" is corrected to say what is actually
true.

## Verified

- **Each new assertion fails without the change.** Reverting `FLOOR` to the old
  `scripts/check_*.py` convention while keeping the assertions turns 6 cases red,
  and the exhaustiveness check names precisely the files the convention missed:
  `jsonschema_mini.py`, `pipeline_dispatch_local.py`, `pipeline_labels.py`, and
  all 8 `templates/workflows/pipeline-*.yml`.
- **The future case is caught.** Dropping a hypothetical
  `scripts/queue_arbiter.py` into the tree — a name no glob matches — fails with
  `every covered file is gated or excused with a reason (got
  ['scripts/queue_arbiter.py'])`.
- **The ledger cannot rot.** An `UNGATED` entry naming a file that is gated, or
  one that no longer exists, or one with an empty reason, each fails its own
  assertion.
- Full gate green: hooks 281/281, delivery, dor, local-dispatch, graders,
  approve, merge, telemetry, dashboard, review, secretlint, placeholders,
  schemas, provenance.
