# Protected-label guard: an acknowledgement is the human's to give

**Date:** 2026-08-25 · **Status:** Accepted · **Context:** `fix/protected-label-guard`

## Decision

An agent may not apply or remove a **protected label** — `hooks-change`, `agent:*`,
`blocked:*`, `provenance:*` (contract §6). Three layers enforce it:

1. **PreToolUse hook** blocks the `gh` spellings that write them (`pr`/`issue edit
   --add-label`/`--remove-label`, `pr`/`issue create --label`/`-l`, and `gh api` writes
   to `/issues/<n>/labels`), routing the agent to *print the command for the human* —
   the flow self-protection already uses for a hook edit.
2. **`scripts/check_grader_paths.py`** checks **who** applied the label, not just that
   it is there: a machine identity can never supply the acknowledgement, and a
   bot-authored PR cannot label itself.
3. **The gate runs from the PR's base sha**, and `scripts/check_*.py` joins the gated
   `FLOOR`, so a head cannot weaken the gate that judges it.

Reading and listing labels stays free, as does any *unrelated* label (`bug`) and `gh
label create` — defining a label is setup, not acknowledgement.

## Why

PR #14 made a guard-touching PR stay red until someone applies `hooks-change`. The
mechanism is a **human attention router**: the label means a person read the change.
But applying it was never blocked, so a session could supply its own acknowledgement,
turn the check green, and delete the one signal telling a reviewer to look closely.

The realistic exploit is not editing a guard — self-protection blocks that. It is that
**`test_hooks.py` is deliberately not self-protected**. A session can weaken a battery
case — the test that *proves* a guard works — self-acknowledge it, and land a PR that
reads like any other in the queue. Severity is bounded (Claude can never merge, so a
human is still the final gate), but bounded is not zero when the thing defeated is
precisely the mechanism that directs the human's attention.

The framing that settles the design is the **never-merge guard**: an action that
*grants permission* belongs to the human, never to the session asking for it. Merging
and acknowledging are the same act at different points in the pipeline.

**Alternatives rejected.**

- *Match the single literal `hooks-change`.* Rejected: a second gating label would need
  a second guard. Matching contract §6's set means a future label is a table row, not
  new code — and it makes the GitHub path refuse exactly what the tracker path (§8)
  already refuses.
- *Block all labelling.* Rejected as over-blocking: ordinary `bug`/`documentation`
  labelling is normal work, and a guard that breaks the daily path gets routed around.
- *Rely on the hook alone.* Rejected: a denylist of shell spellings is never
  exhaustive. Naming that limit is the honest move, and it is why layer 2 exists.
- *Rely on the CI check alone.* Rejected for the mirror-image reason — see the
  tradeoff below, which is the sharpest thing in this ADR.
- *Fail whenever the labeller equals the PR author* (the obvious reading). **Rejected
  as actively wrong here**: Claude Code locally drives `gh` with the human's own
  credential, so on PR #39 the label was applied by `braedensc`, who also authored the
  PR. That rule would have failed #39, would fail this very PR, and would brick every
  PR in this repo — while detecting nothing, because the two are the same account.

**The accepted tradeoff, stated plainly.** The identity check has teeth exactly when
the agent holds an identity *distinct* from the acknowledging human — the pipeline
case, where the PR is authored by an App/bot. It **cannot** help when a local session
uses the human's credential: GitHub sees one identity for both, and no API separates
"the human typed it" from "the agent ran it". That topology is covered by the hook
instead. Each layer covers the other's blind spot; neither is complete alone, and the
script says so in its own output rather than implying a guarantee it does not have.
`STRICT_LABEL_IDENTITY=1` is available for repos whose agents *do* hold a separate
account and which want a distinct applier required unconditionally.

## Verified

- **Battery 263 → 281**, all green against the candidate. The 9 block cases + 2
  stderr-reason cases were **first run against the committed hook and seen to fail**
  (`270/281`) — the bugfix doctrine applied to a guard: the test proves the hole was
  real before the fix closes it.
- Covered spellings blocked: `--add-label`, `--add-label=`, a protected label inside a
  comma list, `--remove-label`, `create --label`, `gh api -f` to `/issues/<n>/labels`,
  an opaque `gh api --input` body to that endpoint, and a protected label in a
  **chained** command after `&&`.
- Not over-blocking, asserted as allow-cases: unrelated labels (single and comma
  list), `gh pr view --json labels`, `gh label list`, `gh label create hooks-change`,
  repo-level label CRUD (`repos/o/r/labels`), and a plain GET of an issue's labels.
- **22-case selftest** (`npm run test:graders`) over the gated floor and the identity
  rules, including both re-label orderings (a human re-apply clears a bot's; a bot
  re-apply after a human fails), unattributable labels, and the fail-closed path when
  the events API is unreadable.
- Dogfooded: this PR touches `.claude/hooks/**` and so needs `hooks-change`. The label
  was **not** self-applied — the command was printed for the human, which is the flow
  this ADR creates.

**Known gap, deliberately left.** The hook's Bash matcher does not cover a label
applied through a non-`gh` HTTP client (`curl` to the REST API), nor a payload hidden
in a variable it cannot resolve. Extending the denylist further has sharply
diminishing returns; layer 2 is the answer to that whole class, and layer 3 is what
keeps layer 2 from being edited away.
