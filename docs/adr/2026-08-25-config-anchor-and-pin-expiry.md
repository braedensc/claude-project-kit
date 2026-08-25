# Config anchor, pin expiry, and no in-session approval

**Date:** 2026-08-25 · **Status:** Accepted · **Context:** remediation stream R3,
`fix/pin-and-config-anchoring` · amends
[Pipeline guards, dispatcher-anchored](2026-08-24-pipeline-guards-dispatcher-anchored.md)

## Decision

Three fixes to the pipeline's trust model, plus one guard the contract named but nobody
had written:

1. **An expired pin is BROKEN, not absent.** `_read_pin` gains a distinct `expired`
   status and returns the parsed pin with it. On a `ticket`-mode session an expiry fails
   **closed** (contract §2). In the other modes it does not brick the session, but the
   pin object is still returned, so every constraint the lapsed pin was carrying stays
   on.
2. **The git ref store is human-only** — a new universal **config-anchor** guard. Writing
   a protected ref (`update-ref`, `branch -f/-D`, a fetch refspec targeting
   `main`/`master`, `symbolic-ref`, `replace`, history rewriters), repointing `origin`,
   or mutating `.git/**` is blocked in Bash and in Edit/Write. Reads and a plain
   `git fetch` are untouched. Alongside it, a `dispatch.pinsRoot` that resolves inside
   the repo or any worktree is now a **broken config**, enforced by the hook rather than
   assumed of the validator.
3. **There is no in-session `raw` → `ready` approval path.** The narrow allow-path in
   `_approval_guard` is deleted; the guard is now unconditional, and the hook no longer
   reads `autonomy.autoApproveProvenance` at all.
4. **`lifecycle-label` is implemented in the hook**, blocking a pinned session from
   naming an `agent:*` or `blocked:*` label in a tracker write — by canonical key *and*
   by configured ID, in `add` and `remove` alike.

A fifth change fell out of writing (4): `_walk_items` did not yield scalar **list
elements**, so `{"labels": ["agent:queued"]}` and `{"issueIds": ["ENG-456"]}` reached no
value-matching guard at all. Fixed, with cases for the state-ID and ticket-ID halves.

## Why

### An expiry is not an absence

The old reader collapsed "expired" into `absent`, quoting §3's "readers treat it as
stale". But §2's state table is explicit that a `ticket`-mode session with a *missing,
expired, or mismatched* pin is **broken**, and broken fails closed. Collapsing the two
silently disabled five of the six pipeline guards: `pin` went `None`, so grader-path
protection, ticket-branch, own-ticket writes, AC integrity and lifecycle scoping all
stopped firing — and the battery encoded that direction as correct.

The distinction is the whole fail-direction doctrine. An absence means nothing ever bound
this session; a human's ad-hoc session in a configured repo is exactly that, and must not
be bricked. An expiry means a binding **was** issued for this worktree and lapsed, so the
ticket, scope and branch it bound can no longer be verified. Treating a lapse as freedom
makes **waiting** an escape — the one attack that needs no cleverness at all.

The parsed pin is returned with `expired` so the caller can read the `session_mode` it
was dispatched with. That is sound only because the worktree check now runs *before* the
expiry check: a lapsed pin we hand back is provably this session's own, never a
neighbour's. §2 scopes broken to `ticket` mode, so a lapsed `planning` pin keeps working
— but it does not hand back what it was withholding.

### The anchor a guard reads must not be one the model can move

The config-trust story is "read `delivery.json` from the committed copy on the default
branch, not from the agent's worktree". Its whole force comes from the worktree being
model-writable and the ref not being — and a plain local git ref *is*, via one unblocked
`git update-ref`. That is precisely the failure
[the dispatcher-anchored ADR](2026-08-24-pipeline-guards-dispatcher-anchored.md) names,
sitting inside the mechanism meant to embody it.

The payoff for moving it was total, because config feeds the pin: poison
`dispatch.pinsRoot` and the hook looks for the pin somewhere the session can write, which
is a forged pin, which is arbitrary scope, budget and ticket. Blank `linear.stateIds.ready`
and the approval matcher never fires. One command, whole model.

**Alternatives considered:**

- **Verify the ref against the remote** (`git ls-remote`). Authoritative, and rejected:
  the hook runs before *every* tool call and must be offline-safe and fast. A guard that
  needs the network fails when the network does, and the fail-open version of that is
  worse than the hole.
- **Reflog provenance** — trust `origin/main` only when its newest reflog entry came from
  `fetch`/`clone`. Genuinely appealing: local, offline, and it detects exactly this
  attack. Rejected because the reason strings for *local* branches are ambiguous
  (`git branch -f main X` and a legitimate `git reset --hard` both write "branch: Reset
  to …"), reflogs can be absent, and the failure mode of a false negative is a bricked
  repo. Worth revisiting if the anchor ever narrows to remote-tracking refs only.
- **Drop the local `main`/`master` fallbacks.** Rejected: a repo with no remote would
  then fall through to the *working tree*, which is strictly worse than a local ref.
- **A config digest in the dispatcher-written pin** — the genuinely immutable answer,
  since the pin already lives outside the worktree and is written before the session
  starts. Not available this wave: it changes the §3 pin shape, which is a contract
  amendment. **This is the recommended next step**, and it would let the ref read become
  a cross-check rather than the anchor.

**What was accepted instead:** the ref store is protected the same way the hook files
are, and `pinsRoot` containment caps the blast radius if it is ever bypassed. This is
**tamper-evident, not tamper-proof** — the posture §3 already states for the pin itself.
A shell cannot be perfectly fenced by regex. What it buys is that the cheap, silent,
single-command version of the attack is gone, and the backstop stays what it always was:
nothing lands without a reviewed PR and CI.

Two deliberate narrowings keep the guard from taxing honest work. `git fetch` without a
refspec stays allowed — it is the *one* honest writer of `origin/main`, and a guard that
stopped the repo from learning the truth would be worse than the hole. And `git remote
add origin` stays allowed: git refuses to add a remote that already exists, so `add`
cannot repoint an anchor (repointing needs `remove` or `set-url` first, and both are
blocked), which keeps first-push bootstrap working.

### Hole 3: the contract was right, the code was wrong

`_approval_guard` shipped a tested ALLOW path for an in-session `raw` → `ready` move.
§2's `self-approval` row, §5 and §8 all say that must not exist. Deciding between them:

- **Three sections agree and one function dissents.** §8's validator refuses `ready`
  "even when a caller passes them in `allowed_to_states` — a belt the caller cannot
  unbuckle". A hook that permits what the validator beside it refuses is not defense in
  depth, it is a disagreement — and in a disagreement between a permissive layer and a
  restrictive one, the permissive layer decides.
- **The allow-path could not check the rule it implemented.** §5 rule 2 requires the
  referenced epic to exist and itself be in a human-approved state. A PreToolUse hook
  holds no tracker credential and cannot read the epic, so it matched `^epic/\S+$`
  against a string and called that verification. A fabricated `epic/ENG-99999` in a pin
  would have satisfied it.
- **It was only reachable where the architecture had already failed.** Under §8 a session
  holds *no* tracker credential: transitions travel as write-requests a separate job
  executes. So the allow-path could only ever fire for a session holding a direct tracker
  credential — precisely the deployment where a guard should be at its most conservative.

**The example default was deliberately left at `["epic"]`.** The review suggested
changing it, and that would be wrong: `autonomy.autoApproveProvenance` configures the
**out-of-session** approve tier (§11, `scripts/check_auto_approve.py`, `docs/AUTONOMY.md`),
which §11 documents as on-by-default and epic-only. Emptying it would switch off a tier
that works correctly — it runs where the epic *can* be read and every gate re-derived. The
bug was never the value; it was the hook reusing an out-of-session knob as an in-session
permission. So the hook now ignores the field entirely, and two paired battery cases (one
config listing `epic`, one empty, same payload, same BLOCK) prove it reads it in neither
direction.

Deleting the allow-path made `_dor_gaps` and `_session_changed_paths` dead; both are
removed. Duplicating the Definition-of-Ready check in a hook was only ever in service of
the wrong path — it lives in `scripts/check_ticket_dor.py`, out of session, where it can
be checked against the real ticket.

### `lifecycle-label` had no implementation, not just no coverage

Of the six guards §2 names, this one existed only in
`templates/workflows/pipeline-safe-outputs.yml` — the validator layer. Nothing stopped a
session holding a direct tracker credential from setting `agent:needs-human` on its own
ticket, or clearing `agent:blocked`, or applying `agent:queued` and so queueing its own
next dispatch. The hook now refuses any tracker write that *names* such a label.

Scope choices worth recording: it matches `agent:*` and `blocked:*` but **not**
`provenance:*` or `hooks-change`, though §8's validator refuses those too. A `planning`
session legitimately files child tickets and labels their provenance (`/plan-epic`), and
blocking that in the hook would break a working path to close a hole the validator
already closes on the channel that matters. It is scoped to **pinned** sessions
(withholding → fails open unpinned), matches on whole field values so prose asking for a
label is untouched, and matches the canonical key as well as the configured ID — the key
half is what keeps it failing closed when `linear.labels.ids` resolves nothing.

`telemetry-required` is **not** implemented here, and that is deliberate rather than an
omission. A PreToolUse hook cannot check "exactly one telemetry block before the turn
ends"; the Stop hook could, but it would need to know whether telemetry is required for
this run, and that flag is a dispatcher input with no home in the §3 pin shape. It stays
where it is (the safe-outputs validator) until a pin field exists to carry it.

## Verified

`npm run test:hooks` — **263 cases, all green** against the candidate.

- **Every new case has teeth.** Run against the *committed* hook, the 31 new/changed
  cases fail and the rest pass (232/263) — so none of them is vacuous, and no existing
  behavior moved except the two that encoded the wrong direction.
- **Both wrong-direction cases were flipped, not deleted.** `expired pin: treated as
  absent…` now asserts BLOCK across ordinary edits, risk paths, Bash and tracker writes,
  with Read still allowed for diagnosis; `ready: epic provenance + complete DoR…` is now
  the regression gate — the *best-case* session (pinned, `epic/<ID>` provenance, complete
  DoR, no risk-path change, targeting its own ticket) is still refused, so the allow-path
  cannot return unnoticed.
- **Optionality is unregressed**, which was the explicit constraint: the four
  `pipeline off:` cases pass unchanged, `_pipeline_configured()` is still a bare
  `os.path.isfile` called first, and both new broken states keep the carve-out that
  leaves `delivery.json` itself editable.
- **The config-anchor guard is tested in both directions** — 11 block cases (ref writes,
  refspec, origin repointing, `.git/**` via redirect, `sed -i` and the Edit/Write twin)
  against 9 allow cases chosen as the plausible false positives: `git fetch origin main`,
  `git diff origin/main...HEAD`, `git branch --merged main`, `git config --get
  remote.origin.url`, `cat .git/config`, the documented `git branch -m` rename, an SSH
  clone URL (`…:main/repo.git` — a colon that is not a refspec), `git show
  HEAD:src/replace.ts` (a path that is not the `replace` verb), and `git remote add
  origin`.
- **Error paths are covered per the fail-direction doctrine**: a `pinsRoot` of the wrong
  *type* is broken rather than silently defaulted; unresolvable `linear.labels.ids` still
  blocks by canonical key; a lapsed `planning` pin still blocks risk-path edits and
  lifecycle labels.
- **The candidate was validated before it landed** — `py_compile` plus the full battery
  against the scratch copy, via a new `HOOK_UNDER_TEST` override in `test_hooks.py`
  (unset in CI, so it cannot change what CI verifies). A syntax error in a self-protected
  hook fails closed and needs human-only terminal recovery, so composing and proving the
  candidate *before* the human applies it is the only safe order (`docs/LESSONS.md`).

### Contract amendment requested of stream R1

Clarifying, not substantive — §2, §5 and §8 already agree; §5's table is what an
implementer could misread. **§5 should state that the "Yes" in its auto-approval column
is an out-of-session capability only, and that `autonomy.autoApproveProvenance`
configures the §11 approve tier rather than granting any in-session permission.** Without
it, the next reader re-derives exactly the allow-path this ADR removes.
