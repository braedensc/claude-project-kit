# Durable home for the dispatch attempt counter

**Date:** 2026-08-24 · **Status:** Accepted · **Context:** `docs/pipeline-contract-amendments`, amending `docs/PIPELINE-CONTRACT.md` after PR #24

## Decision

The per-ticket attempt count behind `budgets.totalAttempts` lives in a **dispatcher-owned
`pipeline-dispatcher-state/1` record**, frozen as PIPELINE-CONTRACT §9. The contract owns
the record shape and its invariants — one writer per run, never read from inside a
worktree, never written by a session, capacity refunds the attempt, a missing record
starts at zero and never blocks dispatch. Each `dispatch.backend` binds that record to a
concrete durable store through a new **`dispatch.statePath`** field: `null` for
`github-actions`, whose store is the `pipeline-state` artifact under a name the contract
now fixes; a required path for `local-daemon` and `cloud`.

The counter is deliberately **bounded, not exact**. The durable terminal signal is the
`agent:needs-human` label on the ticket, not the count.

## Why

The count must be known *before* dispatch — it selects the ticket, clamps the budget, and
is stamped into `pin.budget.attempt` / `.of`. That makes it authority, so it cannot live
in anything a session writes, and it cannot live in the pin (the pin is written per
dispatch, and the count is what decides whether to write one at all).

**Rejected: a dispatcher-authored ticket comment with a machine-readable marker.**
Durable and backend-independent, and §3 already forbids it — "ticket comments: the agent
posts them." The rescue argument is that the agent has no tracker credential in this
architecture (§8), so it cannot forge one. It does not hold, on three counts:

1. The credential split is one backend's implementation. `local-daemon` and `cloud`
   sessions may run with a tracker MCP server attached; a trust boundary that survives
   only until someone changes `dispatch.backend` is not one.
2. Even credential-less, the agent has a *validated* write channel into ticket comments:
   §8 executes `ticket-comment` requests using the dispatcher's own credential, and the
   validator bounds their count and size but does not inspect bodies for a state marker.
   A forged marker would be posted under the dispatcher's identity. Defending it needs a
   further rule whose security rests on a marker string staying obscure.
3. It would make a frozen rule conditional on an implementation detail — the second shape
   for the same structure the contract exists to prevent.

**Rejected: an append-only ledger in `pinsRoot`.** Reuses an existing directory and the
count is derivable from `ledger.jsonl`, but it conflates lifetimes: pins are per-dispatch
scratch with a sweeper deleting stale ones, while the counter must outlive every session
on the ticket. Long-lived authority does not belong where something is designed to prune.
`pinsRoot` is also machine-local by default, so it does not solve the transfer problem
that motivated the question.

**Rejected: `statePath` alone, with no contract-owned record.** A path field without a
frozen shape is the same gap one level down — every backend invents its own JSON.

**Accepted trade-off: at-most-one-extra-attempt on state loss.** If the store expires or
is lost, in-flight tickets get their attempts back. This is tolerable only because
`agent:needs-human` is checked *before* the count and is never cleared by the dispatcher,
so a ticket that already reached the terminal state stays there. The guarantee is "a
ticket never dispatches while carrying `agent:needs-human`", not "the count is exact." A
project needing exact accounting supplies a non-expiring store; the contract does not
require a database to run a queue.

## Verified

- **The backstop genuinely precedes the counter**, so the trade-off holds rather than
  merely being asserted. In `templates/workflows/pipeline-dispatch.yml` the selection loop
  checks `if "agent:needs-human" in keys: skipped … continue` *before* it reads
  `state.get("attempts", {})` — a ticket already handed to a human is skipped even when
  the state artifact is empty and every attempt has been refunded.
- **The frozen record matches what the shipped dispatcher already writes.** The seed value
  in the carry-forward loader is
  `{"schema":"pipeline-dispatcher-state/1","capacity":{},"spend":[],"attempts":{}}`, and
  the `capacity` sub-object written by the carry-forward job carries exactly
  `paused_until` / `resets_at` / `used_percentage` / `noted_at`. §9 ratifies the working
  shape; it does not invent a rival one.
- **One writer per run is real, not aspirational.** `pipeline-state` is published by the
  single `carry-forward` job; per-run drafts go to a distinct
  `pipeline-state-draft-<run_id>` name that no other run reads.
- **The refund path exists** — the carry-forward job decrements `attempts[ticket]` and
  drops the reserved spend row on a capacity flag, matching `dispatch.pauseOnCapacity`.
- **`delivery.example.json` still parses** with `dispatch.statePath: null` added, and both
  new fields carry real defaults rather than `{{…}}` tokens, so
  `python3 scripts/check_placeholders.py` needs no new row.
- `npm run test:hooks` green; `python3 scripts/check_placeholders.py` green.

**Follow-up (not in this PR — `templates/workflows/**` was out of scope):**
`pipeline-dispatch.yml` does not yet honour `dispatch.statePath`; it hardcodes the
`pipeline-state` artifact. That is correct behaviour for `github-actions` under §9, but
the field should be read and asserted `null` for that backend so a misconfiguration is
loud.
