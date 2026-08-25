# Pipeline guards are anchored on the dispatcher, not the session

**Date:** 2026-08-24 · **Status:** Accepted · **Context:** `feat/pipeline-guards` — the
enforcement half of `docs/PIPELINE-CONTRACT.md`

## Decision

Add six **pipeline guards** to the PreToolUse hook — self-approval (state transition into
`ready`), own-ticket-only tracker writes, acceptance-criteria integrity, grader-path
protection, ticket-in-branch, and pin binding — plus a matching extension of CI's
existing *Hooks change guard* job. They are **inert unless `delivery.json` exists at the
repo root**, and every value they trust comes from the **dispatcher**: the pinned ticket,
session mode and ticket snapshot from a pin file *outside* the worktree; config values
from the committed copy on the default branch (`git show origin/main:delivery.json`);
states and labels compared by **ID**, never by display name.

Hook scripts and `settings*.json` stay blocked **unconditionally**, pipeline or not.
Nothing in this change made an existing guard mode-conditional.

## Why

The kit already learned once that **a guard's anchor must not be something the model can
move** (`2026-08-23-hook-hardening-and-session-root.md`: a cwd-derived session root let a
persisted `cd` switch off three guards). The delivery pipeline reintroduces that hazard
in a new shape, because almost everything about a session's mandate is agent-authored.
Each rejected transport fails for the same reason:

| Rejected anchor | Why |
|---|---|
| Branch name | The agent chooses it. `branch.requireTicketId` is a consistency convention, not a trust anchor |
| PR body / title, ticket comments, commit trailers | The agent writes them |
| Environment variables | Model-mutable inside the session; subagents inherit them, and a `Bash` call can re-export anything |
| Any file inside the worktree (`CLAUDE.md`, `.claude/**`, the working-tree `delivery.json`) | Edit/Write and the shell reach all of it — the cross-worktree guard permits writes *inside* the session's own worktree by design |
| Settings-level wiring | `settings.local.json` overrides project scalars |

So the pin lives at `<pinsRoot>/<sha256(session root)[:16]>.json`, keyed on the one anchor
the kit already establishes as not model-mutable, and config **values** are read from the
default branch even though config **existence** is read from the working tree. That split
is deliberate: existence must be cheap, offline-safe and impossible to fail before it
answers; values must be untamperable. A battery case pins the consequence — a fully
disarmed working-tree `delivery.json` (blank `ready` id, empty `riskPaths`, redirected
`pinsRoot`) moves no guard at all.

**Off is not broken.** The kit is a template for *any* project, and most adopters will
never run a pipeline. A pipeline guard that failed closed on "no config found" would block
every `Edit`/`Write` in an ordinary project — and because the guard machinery is
self-protected, the agent could not repair it; recovery would need a human at a terminal.
That is the hook-bootstrap-order failure (`docs/LESSONS.md`) with a new precondition. So
the existence test comes first and nothing that can fail runs ahead of it, and there is
exactly **one** discriminator — a second one is a second thing to desync.

**Fail direction is per-guard, and tested.** Write-blocking guards, state transitions and
pin checks fail **closed**; checks that merely *withhold* autonomy from an unpinned
session fail **open**, because a human's ad-hoc session in a configured repo must not be
bricked. An adversary must not be able to flip a guard open by inducing an error, so the
battery covers the error paths, not only the happy ones. One deliberate carve-out: when
the config is broken, editing `delivery.json` itself stays allowed — the repo is never
held hostage by its own config.

**Approval is the guard that matters most.** Only `epic/*` provenance — work decomposed
from an epic a person already approved — can ever auto-approve, and only when the
definition of ready is complete *and* the session touches no risk-listed path. Everything
a monitor, a review pass or a retrospective files waits in `raw` for a person, because
that is precisely the path an attacker-influenced payload would take to mint itself a
mandate. A project that wants the stricter posture `PIPELINE-CONTRACT.md` §5 describes
("only out of session") gets it by emptying `autonomy.autoApproveProvenance` — one config
value, already covered by the contract's validator, rather than a fork of the hook.

### Accepted tradeoffs

- **Tamper-evident, not tamper-proof.** The session's shell runs as the same user, so
  `0444` and a path outside the repo raise the cost of deleting or rewriting a pin without
  making it impossible. A deleted pin degrades to the unpinned (fail-open-for-withholding)
  path; the ledger is what makes that divergence visible. The real backstop stays what it
  has always been — nothing lands without a reviewed PR and CI.
- **Tracker mutations are recognized by tool verb plus self-identifying payload values**
  (a configured state/label ID, or a `<teamKey>-<n>` identifier as a *whole field value*),
  deliberately not by MCP server name: Claude Code names servers however the user wired
  them, often as an opaque id, so server-name matching would be the weakest link. An
  unknown verb carrying a configured ID is treated as a write; an unknown verb with no
  such fingerprint is not seen. Rejected alternative: a new `linear.mcp` config field —
  it would have required amending a contract three sibling streams were already building
  against.
- **An issue write whose target cannot be resolved to the pin is denied**, so a session
  that addresses its own ticket by opaque UUID gets friction rather than a hole. The pin
  carries the human identifier only; adding `ticket.uuid` to the pin would remove the
  friction and is the obvious future amendment.
- **CI's job runs in head context.** A PR can still edit the workflow and script that gate
  it. Reading the gated path set from the PR's *base* sha closes the config half; the
  property "a green check can never be produced by the code under review" additionally
  needs `pull_request_target` plus a required review, which is owner-set and deliberately
  not claimed yet.

## Verified

- **Battery: 218/218 green against the candidate** (was 147 before this change; 71 new
  cases). Validated *before* the change landed — composed in a scratch file, `py_compile`d,
  and run through the battery's sandbox helpers in a throwaway checkout, because the
  running hook forbids editing itself and a syntax error fails **closed**, which would
  block every tool call and need human-only terminal recovery.
- **Mutation-tested, so no case is vacuous:** the same 218-case battery run against the
  *previous* hook fails exactly 42 cases — every block-expecting pipeline case, and no
  others. A guard that had silently not fired would have shown up as a pass on both.
- **Adversarial cases:** a disarmed working-tree `delivery.json` moves no guard; a pin
  written for a different worktree is a hard stop; an expired pin is treated as absent for
  withholding checks yet still fails closed for approval; a `ready`-state payload is
  matched by ID even from an opaque MCP server name; and a prose ticket mention in a
  GitHub PR title is *not* treated as a tracker target.
- **Regression gate for non-pipeline projects:** four cases assert that with no
  `delivery.json` a tracker write, a `ready`-state payload, a workflow edit and a
  `sed -i` on a workflow are all still allowed. The branch-name regex change was proved
  language-identical against the previous pattern over a sample set before it shipped.
- **CI checker** exercised on real commit ranges (hook-touching range blocks without the
  label, passes with it) and on synthetic ranges proving a docs-only diff exits 0 and a
  glob that exists *only* in the base branch's `autonomy.riskPaths` is enforced.
