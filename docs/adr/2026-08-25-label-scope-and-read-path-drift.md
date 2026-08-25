# Label scope is workspace-wide, and read paths must detect their own drift

**Date:** 2026-08-25 · **Status:** Accepted · **Context:** `fix/label-scope-and-read-path-drift`

## Decision

Two decisions, from one incident.

1. **Every label in the §6 taxonomy is workspace-scoped** — created with `teamId`
   omitted. Not only the machinery (`agent:*`, `provenance:*`, `blocked:capacity`,
   `hooks-change`) but the project taxonomy too (`track:*`, `effort:*`). One workspace
   serves many teams sharing one taxonomy. **Scope cannot be converted after creation**,
   so the mandate is stated in the contract (§6), asserted in `/setup-board`'s diff, and
   the apply step names the trap by name: the session is holding a `teamId` from its
   read phase and must not pass it.

2. **A read path must detect a stale label ID explicitly**, and its severity is keyed on
   `linear.labels.required` — fatal for a required key, a warning otherwise. Resolution
   is still by ID, per §1. The display name is used **only as a diagnostic**: an unmapped
   label whose name matches a canonical key means the config's record is wrong, not that
   the label belongs to somebody else. One implementation,
   `scripts/pipeline_labels.py::resolve_label_keys`, imported by tier-0 dispatch and by
   the Actions dispatcher's two loops.

The callers differ in what they do with a fatal row, deliberately. Tier 0 binds one
ticket for one human and refuses outright. The Actions dispatcher is mid-queue and
part-way through writing labels, so it annotates and **drops that one ticket** — one
drifted ticket costs a slot, never the run.

## Why

A downstream project ran `/setup-board`, which created 11 pipeline labels **team**-scoped
when they needed to be **workspace**-scoped. Scope cannot be converted, so the human
deleted and recreated them by hand. `delivery.json` kept the dead UUIDs.

Both validators passed the whole time. They check that an ID is a non-empty string —
which 11 dead UUIDs are. Dispatch then degraded rather than stopping: `effort:*` fell
back to `M`, `provenance:*` to `human`, and the labels a person applies to *park* a
ticket — `agent:needs-human`, `agent:blocked` — resolved to nothing, so the dispatcher
would have dispatched work someone had deliberately stopped. Every read path shared one
line:

```python
keys = {id_to_key.get(l["id"]) for l in nodes}
keys.discard(None)
```

The contract had licensed this, in a sentence that was half true: *"a deleted ID fails
loudly at the API call, which is the behavior we want."* It fails loudly on a **write** —
Linear rejects `issueAddLabel` with a dead ID, and all three write paths are
key → ID → mutation, so they were fine. A **read** is a local dict lookup, and a miss is
just a miss. Correcting that sentence is half this change; the other half is the code
that the corrected sentence now requires.

**Why ID matching is not the bug, and stays.** IDs survive a rename in the UI, which is
the failure names have. Falling back to name matching would trade a rare, loud failure
for a common, silent one. The name is now the *diagnostic* — the queries already return
`labels { nodes { id name } }`, so the second question is free.

**Why severity keys on `labels.required`.** `scripts/check_delivery_config.py` already
applies exactly this required=error / optional=warning split to the sibling `""` case, so
projects tune one list rather than learning a second taxonomy. Decisively: `track:*` is
open-ended and project-named (`track:{{DEFAULT_TRACK}}`), so it *cannot* be in
`required`, and a blanket-fatal rule would wedge the queue over a condition the
Definition-of-Ready gate already rejects at intake.

**This flips the dispatcher's supervision labels from fail-open to fail-closed**, which is
a security-model change, not a tidy-up. `agent:needs-human` and `agent:blocked` are how a
human stops the pipeline. Reading "no hold found" out of "the hold would not resolve" is
an absence of evidence dressed as evidence — and it is the one direction in which this
bug dispatches unattended work.

**Alternatives rejected.**

- *Make any unresolvable canonical label fatal everywhere.* Rejected: `track:*` can never
  be in `required`, so this kills dispatch on a DoR condition that is already caught,
  and a guard that breaks the daily path gets routed around.
- *Match by name when the ID misses.* Rejected — that reintroduces the rename desync §1
  exists to prevent, and it would resolve a label the config has never seen.
- *`sys.exit` in the dispatcher's loops.* Rejected: those loops run mid-queue and
  part-way through label writes. One drifted ticket must not wedge everybody else's work.
- *Duplicate the resolver into the workflow's embedded Python.* Rejected on §3's "one
  description, one parser" doctrine. The dispatcher already imports the DoR gate's
  parser; this is the same shape, and a private copy is how the two halves start
  disagreeing about whether a ticket is parked.
- *Validate label liveness in CI.* Rejected as misplaced, not as wrong — see below.

## Verified

- **`npm run test:local-dispatch`: 56 → 77 cases**, green. Each behavioural case was run
  against the pre-fix `build_pin` and **seen to fail**: a stale *required* label id
  dispatched silently (exit 0, empty stderr — the incident, reproduced), and the no-id
  and unresolved-but-live diagnostics produced nothing at all.
- The two workflow sites are pinned **structurally, via `ast`** over the step's embedded
  Python — that it imports `resolve_label_keys`, calls it, guards both loops, and that
  neither guard exits. Reverting the workflow turned three of the four red; patching one
  guard to `sys.exit` turned the fourth red.
- Covered: stale required (fatal, both ids shown) · stale non-required (warning, dispatch
  proceeds, degraded value pinned as `null`) · non-canonical label (ignored in silence) ·
  ticket-file label with no `id` (its own diagnostic, blaming the payload) · live label
  recorded as `""` · a renamed-but-live label still resolving by id.

**Known gap, deliberately left.** A label that was **renamed *and* recreated** matches by
neither ID nor name, so it reads as a label the project added for its own reasons — the
right default for every label that genuinely is. Closing it needs a liveness check
against the tracker API, which belongs in `/setup-board`: that skill holds the API key,
already reads the whole label set, and runs at the moment the IDs are written. CI cannot
do it without a credential, and a guard on the hot path should not make a network call to
decide whether to dispatch. The name heuristic catches the common case — delete and
recreate with the name preserved, which is exactly what a rescope is.
