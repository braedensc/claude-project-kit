# Stage E under a delegation-bound dispatcher

**Date:** 2026-09-05 · **Status:** Accepted · **Context:** KIT-89 (epic), branch
`feat/kit-89-stage-e-review-and-bounce`. **Supersedes the `local-daemon` backend half of
[Where the review session runs](2026-08-26-where-the-review-session-runs.md)** — that ADR
decided review moves to the dispatcher's machine, then assumed the machine still wrote a
pin and that a local watcher would post a trigger comment. The dispatcher chosen since,
Cyrus, writes no pin and its comment lane is disqualified for a reviewer. This ADR
redesigns Stage E for what actually runs.

## Decision

**Stage E is a dispatcher-side service that is independent of Cyrus.** Six decisions:

1. **Trigger** — a **PR poller on the dispatcher's Mac** lists newly-opened pipeline PRs
   through the GitHub API and launches a review session for each. The GitHub webhook side
   of Cyrus stays **unwired**. The poller *is* the thing that speaks first.
2. **Basis** — the reviewer compares the diff against the ticket's acceptance criteria and
   out-of-scope **as of delegation time**, resolved from a source the coding session
   cannot write, and **declines loudly** when it cannot establish one.
3. **Independence** — a **fresh worktree** (never Cyrus's branch-keyed reuse), rubric and
   schema read from the **committed default branch**, and a **read-only toolset** with no
   approve capability. Same host and credential family are accepted.
4. **Verdict** — a **PR comment** (and a Linear telemetry comment), **never an approval**;
   a review that *could not run* is a visibly different, loud outcome from one that ran and
   found nothing.
5. **Bounce** — the same poller notices red CI or threshold findings and starts a
   **bounded fix session**; `maxBounces` is read from `delivery.json` **on the committed
   default branch**, and the count lives in a **daemon-owned ledger** the session cannot
   write.
6. **Coupling** — E reads **GitHub and Linear and nothing inside Cyrus**, so it survives
   Cyrus's Phase-2 replacement. The one Cyrus-specific fact it must respect
   (worktree force-deletion on ticket close, KIT-51) is isolated to a single assumption.

**Mechanism ships in the kit** as tested `scripts/` (the deterministic reviewer shell,
publisher, decline logic, findings normalizer, bounce counter) — the same seam the rest of
the pipeline uses. **Activation** (the launchd poller, its config, its credentials) is a
**dispatcher-side operator step handed to Braeden**, and belongs with the Phase 2 seed
`~/clawdispatch-setup`; no session installs or edits a service.

**The GitHub webhook side of Cyrus is not wired by this ADR, and this ADR does not
authorize wiring it.** That remains refused for the reason the audit gave — see
[Trigger](#1-trigger) — and reopening it needs its own written decision and Braeden's
confirmation.

The first slice — the reviewer core plus the publish-or-decline path
(`scripts/pipeline_review_local.py`) — is built with this ADR and proven on a real PR
(KIT-90). Everything else is filed as KIT-91 … KIT-95 under the KIT-89 epic.

This is an **epic-level** ADR: it records the *direction* for all six decisions so the
children are coherent, at `Status: Accepted` because that direction is Braeden's call and
is settled. It does not pre-empt each child's implementation review — KIT-91 (trigger),
KIT-93 (bounce) and KIT-95 (the daemon lift) are each built and reviewed on their own
diff, and this ADR is amended if one of them forces a change of direction.

## Why

The kit put review in GitHub Actions when the dispatcher was also in Actions: one trust
domain, the pin sitting next to the reviewer as a build artifact, and a fresh runner
giving isolation for free. The 2026-08-26 ADR moved review to the dispatcher's machine but
still assumed two things that are now false: that the dispatcher writes a pin, and that a
local watcher can post a mention comment to trigger a Cyrus review session. Cyrus does
neither usefully. So the design is redone from the events and credentials that exist.

### The constraints that are not negotiable here

- **No pin.** Cyrus binds a session by *a named person delegating in Linear* and writes no
  file (field guide §04; build record §04, §17). Every consumer the pin fed — budgets,
  telemetry, the scope fence, and review's dispatch-time snapshot — lost its input. KIT-18
  is the umbrella; this ADR solves only the review-basis slice of it.
- **Cyrus has no PR-opened trigger.** Its GitHub transport accepts exactly
  `issue_comment`, `pull_request_review_comment`, `pull_request_review` and `push`; a PR
  opening starts nothing (2026-08-26 ADR, read from source `@85aeaaa`). Something must
  speak first.
- **Cyrus's comment lane is the wrong shape for a reviewer.** Its GitHub sessions carry a
  hardcoded *"make changes and push"* system prompt and reuse a worktree by branch name,
  so a reviewer started that way would be told to edit, and could land inside the coding
  session's own tree (2026-08-26 ADR). Prompt-versus-prompt is not a boundary.
- **The GitHub side is deliberately unwired for security.** Both repos are public, so a
  fork's branch name and a stranger's comment are attacker-reachable text, and the audit's
  primary mitigation — private repos only — is unavailable (KIT-25, Urgent, open).
- **Sessions are sandboxed** under `_clawdispatch` with a 19-host egress allowlist, and
  `gh` cannot verify TLS inside the sandbox; a REST fallback exists (`scripts/gh_fallback.py`,
  KIT-75) and every GitHub write E makes goes through it (field guide §06–07).

### 1. Trigger

**Decision: a poller on the dispatcher's Mac, GitHub webhook unwired.**

The poller lists open PRs via the GitHub API, selects those opened since its last run
whose head branch is a pipeline ticket branch, and launches a review session per PR. Its
seen-set is a daemon-owned file outside every worktree, so a PR is reviewed once and a
restart does not re-review the backlog. It preserves the cloud template's `opened`-only
rule (never re-review on every push) so review cost does not multiply with bounce pushes.

**Rejected — a GitHub Action that posts a mention comment** (the 2026-08-26 ADR's own
"Building it" path). It works and is the cheapest wiring, but it (a) keeps a cloud
dependency in the critical path, the incoherence the move off Actions was resolving, and
(b) if it triggered a *Cyrus* session, that session is the "make changes and push" shape
with a reused worktree — wrong for review. Not chosen.

**Rejected — wiring Cyrus's GitHub webhook** so a `pull_request` event starts a review.
This is the refused surface: on public repos a fork branch name or a stranger comment is
attacker-reachable, and KIT-25's mitigation is unavailable. **Reopening it is gated on**
either making the repos private (KIT-25) or an author-association gate on the event plus a
signed-payload check, **and Braeden's written confirmation.** Not done here.

**Rejected — the coding session requesting its own review as its last act.** This is the
2026-08-26 ADR's vector #5 with the safety filed off: the reviewed party authoring the
reviewer's trigger. It is self-approval-adjacent by construction — the party under review
does not get to set the terms of its review — and is rejected on the same grounds the pin
doctrine exists.

**Why not route the review through Cyrus at all.** Cyrus's Linear lane *could* run a
review-shaped prompt template, but doing so (a) couples E to Cyrus internals, which
decision 6 forbids, (b) hands the session Cyrus's unconditional workspace-scoped Linear
write token and a full toolset, and (c) dies when Cyrus is replaced in Phase 2. So E
launches its own review session directly. This makes E a second, tiny dispatcher whose
only job is review and bounce — a real operational cost, accepted for decoupling.

### 2. What the reviewer compares against — the snapshot problem

**This is the hardest question and it has no clean answer under Cyrus.** Stated plainly,
so the design does not pretend otherwise.

The review's entire value is catching a change that quietly did more than the ticket
asked. That judgement needs *what the ticket asked, as of when work started*. Under the
old design the pin froze the acceptance criteria at dispatch, outside the worktree, where
the session could not touch them. There is no pin now, and worse: a Cyrus session holds a
**workspace-scoped Linear write token** (`docs/AUTONOMY.md`) and can therefore **edit its
own acceptance criteria mid-flight**. So "read the live ticket at review time" is not
merely weaker — it lets the reviewed party rewrite the very standard it is judged against.

Three candidate sources, none free:

| Tier | Source | Not session-writable? | Cost |
|---|---|---|---|
| 1 | Linear's **server-side description history** read as-of the delegation timestamp | **Yes** — history is server-authoritative; a later self-edit does not rewrite the past record | Needs the Linear API to reliably return description-as-of-timestamp; **unverified** |
| 2 | An **independent reconciler** that snapshots criteria at delegation to a daemon-owned store outside every worktree | **Yes** — the reconciler is trusted, the store is outside the sandbox | A second daemon; duplicates KIT-47's polling; snapshot keyed by a ticket id the branch only *hints* at |
| 3 | The **live ticket**, with post-delegation edits **flagged** | **No** — but the flag surfaces the exact drift as a finding | Weakest basis; only as good as the flag |

**Decision.** The basis resolver tries tiers in that order and **declines loudly if none
is available** — it never silently reviews against a basis it cannot trust (the TOD-112
failure: a green check that reviewed nothing). Whatever tier it uses, it *also* reads the
live ticket and includes whether the criteria changed after delegation, so a mid-flight
self-edit is a finding rather than a silent pass. The shipped default is decided by a
spike (KIT-92): if the Linear API cleanly returns description-as-of-timestamp, tier 1 is
the default and needs no new infrastructure and no pin — it talks only to Linear, which is
decision-6-clean. If it does not, tier 2 is the buildable fallback and tier 3 the floor.

**The honest two bad options, if tier 1 fails the spike:** build a second reconciler
daemon (tier 2) — more infrastructure, and the branch→ticket resolution it keys on is a
session-choosable hint, though claiming another ticket's snapshot produces *more* scope
findings, not fewer, so the attack is not profitable; **or** accept tier 3 and lean
entirely on the edit-flag — simplest, and genuinely weak against a session that edits its
criteria before the first reconciler poll would even have run. Neither is as strong as the
pin was. This is a cost of choosing a dispatcher that writes no pin, recorded as such
rather than papered over. The clean fix is the pin (KIT-18); until then, review runs on
the best basis available and says which one it used.

**Not used — Cyrus's worktree-creation hook (`cyrus-setup.sh`) as a snapshot writer.**
Disqualified by [Budgets belong to whatever writes the pin](2026-08-25-external-daemon-budget-enforcement.md)
and KIT-18: it runs inside the worktree, from branch contents a prior session controls,
with daemon privileges, and cannot refuse to start a session. A snapshot it wrote would be
a snapshot a session could plant.

### 3. Independence

**What is actually achieved:**

- **Fresh worktree** cut per review, never Cyrus's branch-keyed reuse — closes the
  dirty-worktree inheritance vector (#4 of the 2026-08-26 ADR).
- **Rubric, `schemas/review-findings.schema.json`, and the criteria basis read from the
  committed default branch or from Linear server-side** — never from the PR head working
  tree, closing the rubric-edit vectors (#1, #2). The diff is handed to the session as a
  **file** and the working tree is declared untrusted data, so a `CLAUDE.md` on the branch
  is not loaded as instructions (vector #3).
- **Read-only toolset** — the session runs with `Read, Grep, Glob, Write` (Write scoped to
  its findings file) and no `Bash`, no `gh`, no tracker MCP. It has no tool with which to
  approve, label, push, or merge. This is backed by the self-approval guard already in
  `.claude/hooks/pre-tool-use.py` (KIT-21, closed) as a second layer.
- **Fresh context** — a new session with no memory of the work; a stranger to it by
  construction.
- **A cheaper model** — the reviewer runs on a cheaper model than the coding default
  (field guide §12 model labels), because review is a bounded read, not open-ended
  construction.

**What is accepted as lost, versus the cloud design:**

- **Structural token scoping.** The cloud reviewer's `pull-requests: read` token *could
  not* approve. Locally, "cannot approve" is enforced by the toolset and the hook, not a
  token scope — weaker, because config can be edited by a human in a hurry where a token
  scope cannot. Accepted, same as the 2026-08-26 ADR accepted it.
- **A separate trust domain.** Same host as the dispatcher and the coding sessions;
  isolation is process + account + fresh worktree + read-only toolset, not a separate
  machine.
- **An independent credential.** Same Claude credential family. `auth.review: api-key`
  remains available for anyone who would rather pay than contend for the subscription
  window; this ADR does not force it, and recommends the reviewer run metered on a cheap
  model so an unattended review cannot drain the interactive window.

### 4. How the verdict lands, and how a decline looks

**Decision: a PR comment, never an approval, and a decline that is loud and distinct.**

The reviewer's whole deliverable is `REVIEW-FINDINGS.json` in the §14 shape. A
**deterministic publisher** — not the model — validates it whole against the schema,
computes `usable` / `max_severity` / `meets_threshold`, and posts **one** PR comment
through `scripts/gh_fallback.py` (which has no merge endpoint by construction). This reuses
the one piece of the cloud design that survives unchanged: the poster is deterministic and
separate from the reviewing model.

Three outcomes, three visibly different results, per contract §13:

| Outcome | PR comment | Exit |
|---|---|---|
| Reviewed, findings | the findings, highest severity, whether a fix pass will start | 0 |
| Reviewed, clean | explicit "no findings against correctness, security, test assertions, or scope" | 0 |
| **Could not review** (no basis / unusable findings / reviewer failed) | **"This PR was NOT reviewed — treat as unreviewed, not clean."** | **non-zero** |

A decline is never exit 0 and never a silent green — the exact inversion of the TOD-112
defect, where a declining cloud run exited green and nothing distinguished "reviewed and
found nothing" from "could not review at all." A malformed findings file is **unusable, not
silently smaller**: the whole document conforms or the review is reported unreviewed
(§14). The comment is **not a required check**, so a review can never wedge the merge
queue.

### 5. Bounce

**Decision: the poller drives a bounded fix loop; the bound and the count live where the
session cannot reach.**

- **Who notices, who starts it:** the poller watches open pipeline PRs' check-runs and
  review outcomes; a terminally-red required check, or review findings at or above the
  threshold, starts one fix session running `/fix-ci` per bounce, in E's own fresh
  worktree.
- **Where the bound lives now:** `maxBounces` and `reviewSeverityThreshold` are read from
  `delivery.json` **on the repo's committed default branch**, fetched fresh — the same
  "config from the default branch" anchor the rest of the pipeline already trusts, and not
  writable by the PR head, the branch, the session, or the dispatcher. This replaces the
  dead path where bounce read `delivery.json` from a dispatcher that never opens it.
- **How it is counted:** from a **daemon-owned append-only ledger keyed by PR number**,
  outside every worktree, so the fix session has no write path to the number that decides
  whether to spend another one. (The cloud template counted from Actions run history for
  the same reason; on the Mac the ledger is that unforgeable record.) After `maxBounces`
  the daemon — not the session — applies `agent:needs-human` and stops.
- **KIT-51 trap:** a merged fix PR makes Linear move the ticket to Done, and Cyrus
  force-deletes *its* worktree. E runs bounces in **its own** worktree, so that deletion
  cannot pull work out from under a fix session; and E **skips a bounce whose ticket is
  already terminal** (its Cyrus worktree may be gone). Above all, **E never merges** and
  never enables auto-merge, so E never triggers the Done transition — the human's merge
  does, and the timing hazard is KIT-51's to own.

### 6. Coupling

**Decision: E talks to GitHub and Linear and reaches into Cyrus for nothing.** No Cyrus
import, no read of `/opt/clawdispatch`, no assumption that a Cyrus worktree exists. The
**single coupling seam** is one fact, isolated and named: *Cyrus force-deletes its own
worktree when a ticket reaches a terminal state* (KIT-51). E's response to that seam is to
own its own worktrees and treat the coding session's worktree as possibly-absent — which
is a one-line assumption, not a dependency. When Phase 2 replaces Cyrus (KIT-10), E is
unaffected, because a replacement dispatcher still opens PRs and still has tickets, which
is all E reads.

## Where Stage E lives

E is split the way the whole pipeline already splits, and the split answers "does E belong
in the kit or in Phase 2?": **both, by role.**

- **Mechanism → the kit.** The deterministic reviewer shell, publisher, decline logic,
  findings normalizer and bounce counter are `scripts/`, source-agnostic and CI-tested,
  beside `gh_fallback.py` and `check_schemas.py`. The cloud templates E supersedes already
  live in `templates/workflows/`; their rubric, severity logic, findings shape and bounce
  accounting are mined, and their transport discarded.
- **Activation → the dispatcher / Phase 2 seed.** The poller loop, the launchd service,
  the daemon config and the credentials are operator steps for the dispatcher machine,
  handed to Braeden and destined for `~/clawdispatch-setup` (KIT-95). No session installs
  or edits a service. This is the same mechanism-in-repo / activation-per-deployment seam
  `docs/AUTONOMY.md` already documents, and it is what keeps E liftable into the Phase 2
  standalone project unchanged.

## What is accepted as lost, versus the original Stage E

- The **dispatch-time snapshot** as a hard, unforgeable, pin-carried fact. Replaced by a
  best-available basis with a loud decline, and an explicit edit-flag. Weaker; honest.
- **Structural non-approval** via a scoped token. Replaced by a read-only toolset plus a
  hook. Weaker; a config edit can widen it where a token scope cannot.
- **CI as a free fresh, isolated runner.** Replaced by a same-host session with a fresh
  worktree. Weaker isolation; accepted with the reasoning the 2026-08-26 ADR recorded.
- **Budgets, WIP and the attempt counter on the review lane.** Not restored here — those
  are the rest of KIT-18. The review lane is bounded by its toolset and a cheap model, not
  by a pinned budget.

## Verified

- **The reviewer core is built and proven on a real PR (#63).** `scripts/pipeline_review_local.py`
  resolves a PR, gathers `base...head`, runs a fresh read-only `claude -p` review against a
  basis, normalizes the findings against `schemas/review-findings.schema.json`, and posts
  one comment through `gh_fallback.py`. On PR #63 it posted, run with no basis, a **distinct
  decline comment** (`## 🛑 Stage E — … was NOT reviewed`, exit 3), and, run with KIT-90's
  acceptance criteria as the basis, a **genuine review comment** (4 findings, highest
  `high`, exit 0).
- **The reviewer earned its keep by reviewing its own PR.** That genuine review found a real
  §13 hole in this very slice: `pr_diff()` and `post_comment()` were unguarded where
  `pr_metadata()` was not, so a GitHub hiccup would have crashed with a traceback and posted
  *nothing* — a silent could-not-review, exactly what the file exists to prevent. It was
  fixed in the same PR (every "could not review" path now routes through one `decline()` that
  posts a distinct comment and returns a documented exit code), and a driver-level
  integration test was added so `--selftest` exercises `review()` and not only its helpers.
  Catching, on its first run, the class of bug it was built to catch is the strongest
  evidence the design works.
- **The decline is not green.** `python3 scripts/pipeline_review_local.py --selftest`
  asserts the three outcomes have three different exit codes, that a malformed findings
  file is reported unusable rather than silently reduced, and that the script constructs no
  approve / label / merge API path (the same guarantee `gh_fallback.py` makes, asserted
  the same way).
- **Cyrus's four accepted event types and the absent `pull_request` trigger** are taken
  from the 2026-08-26 ADR, which read them from source `@85aeaaa`; not re-verified here,
  and not upgraded to a fresh measurement.
- **The webhook-unwired decision is unchanged from the audit**, not reopened. Nothing in
  this ADR wires it, and the gate to reopen it is stated above.
- No `delivery.json` ships in the kit, so every pipeline script — including the new one —
  is inert here; its `--selftest` is what has teeth, and it runs in CI.

## Update, same day: KIT-91–95 built, and the KIT-92 spike answered

The remaining children shipped as four scripts (`pipeline_review_poller.py`,
`pipeline_review_basis.py`, `pipeline_telemetry_local.py`, `pipeline_bounce_local.py`) plus
`docs/STAGE-E-OPERATOR.md`, each its own PR against this ADR's decisions.

**The KIT-92 spike's answer: tier 1 (Linear history) is not implementable from what is
verifiable from this codebase, so tier 3 (live ticket + edit-after-delegation flag) is the
shipped default.** No tool in the Linear MCP surface available to a session exposes issue
history or an as-of-timestamp read, and this build had no live Linear credential or
confirmed egress to test a raw history query against Linear's schema directly — a
documented best-effort finding, not a live measurement, stated as such rather than guessed
past. `pipeline_review_basis.py`'s tier-1 interface (`resolve_tier1`) is real and tested
against a fake backend regardless: its signature accepts no live issue at all, which is
the forgery-resistance argument made concrete, so wiring in a verified query later changes
one function's body, not any caller. Tier 2 (an independent reconciler) ships as a
consumer-only stub, per this ADR's own "filed as future work" — no reconciler daemon
exists yet.

Two narrowings from what the cloud template (and this ADR's prose) describe, both stated
where they were decided rather than left implicit:

- **The bounce loop applies no label, ever — not even `agent:needs-human` on
  exhaustion.** `templates/workflows/pipeline-bounce.yml`'s `announce` job does apply that
  label, from a trusted CI position holding the Linear credential directly. This round's
  build narrowed Stage E's own scripts to comment-only across the board; `agent:needs-
  human` on exhaustion is a manual follow-up a human applies from the bounce loop's PR
  comment, not a gap in the design.
- **The bounce loop's "is this ticket already terminal" check (KIT-51 safety) is
  caller-supplied (`--ticket-status-type`), not a live Linear lookup.** Accepted because
  the actual safety property — a bounce can never lose work to a force-deleted worktree —
  is already structural: every fix session runs in a worktree Stage E creates and removes
  itself, never the dispatcher's. Skipping the live check costs at most one wasted attempt
  against an already-closed ticket, not data loss.

Machine-specific activation (the launchd service, real credentials, the real deployment's
paths) is deliberately outside this repo — see the standalone phase document handed to the
owner alongside these PRs, which is where those specifics live.
