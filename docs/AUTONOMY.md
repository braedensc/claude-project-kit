# Autonomy — what runs without a human, and how to turn it on

The delivery pipeline answers three questions without a person. Each is narrower than
the last, each has its own deterministic gate, and **every rung above the first is off
until an operator turns it on**. The formats are frozen in
[`PIPELINE-CONTRACT.md`](PIPELINE-CONTRACT.md) §11; this file is how you actually
enable them.

| Tier | Question | Gate | Ships as |
|---|---|---|---|
| **0 — local** | *(nothing runs without a human — a person binds each session by hand)* | `scripts/pipeline_dispatch_local.py` | on, needs no credential |
| **Dispatch** | may this *approved* ticket start a session? | dispatcher WIP / budget / attempt gates (§9) | on |
| **Approve** | may this ticket move `raw` → `ready` without a person? | `scripts/check_auto_approve.py` | on for `epic/*` only |
| **Merge** | may this PR merge without a person? | `scripts/check_auto_merge.py` + the platform ruleset | **off** |

Nothing here is required. A project that never enables the second and third tier still
gets the whole pipeline — it just has a human at two points in it, which for most teams
is the right answer for a long time. **Tier 0 is not really an autonomy tier at all** —
it answers no question without a person — but it belongs in the same table because it is
the rung the other three are built on, and the one to start from.

One thing *is* required before any of it works: **[the push credential](#the-push-credential--required-before-any-tier-does-anything)**.
Without it the dispatcher and the bounce workflow skip, on purpose.

---

## The rule that outranks everything below

**No agent ever merges.** `.claude/hooks/pre-tool-use.py` blocks the CLI merge command
in every form, including `--auto`, unconditionally and in every project. Enabling the
merge tier does not relax that by one character.

What the merge tier does instead is ask **GitHub** to enable *its* auto-merge on a PR.
The platform then merges — if and when branch protection is satisfied — under rules that
live in repository settings: outside the repo tree, outside any diff, unreachable from a
session. The capability is never held and then restrained. It is never held.

That distinction is the entire security argument, and it collapses if the branch has no
protection. **Set up the ruleset first.**

---

## The push credential — required before *any* tier does anything

**This is not optional and it is not a tier.** Set it up before you turn on tier 1,
because without it the pipeline runs sessions that produce PRs nothing ever looks at.

GitHub deliberately **does not create workflow runs from events triggered by
`GITHUB_TOKEN`**. A session that pushes its branch and opens its PR with the default
token therefore fires no `pull_request` event and no `push` event — so
`pipeline-review.yml` never runs, CI never runs, and since `pipeline-bounce.yml` and
`pipeline-auto-merge.yml` trigger on `workflow_run`, they never fire either. The whole
review → bounce → merge half of the pipeline is silently dead, and the only symptom is
that PRs appear and nothing ever happens to them. Fix sessions fail the same way from
the other end: `/fix-ci` pushes and then watches a CI run that never starts.

So the dispatcher and the bounce workflow push under a **different identity**, and both
refuse to start (green, with a warning) until one is configured.

### Option A — a GitHub App (preferred)

1. Settings → Developer settings → GitHub Apps → **New GitHub App**. Name it something
   like `<project>-pipeline`. Uncheck *Webhook → Active*.
2. Repository permissions — grant exactly these two, and nothing else:
   - **Contents: Read and write** (push the ticket branch)
   - **Pull requests: Read and write** (open the PR)
3. **Install** it on this repository only.
4. Generate a private key; it downloads as a `.pem`.
5. Repo → Settings → Secrets and variables → Actions:
   - Variable `PIPELINE_APP_ID` = the App's ID
   - Secret `PIPELINE_APP_KEY` = the entire contents of the `.pem`, newlines included

The App is its own identity: its events trigger workflows, it can be uninstalled in one
click, and its blast radius is two permissions on one repository.

### Option B — a fine-grained PAT (fallback)

If you cannot install an App, create a **fine-grained** personal access token scoped to
**this repository only**, with *Contents: Read and write* and *Pull requests: Read and
write*, and store it as the secret `PIPELINE_PAT`. The dispatcher logs a warning when it
uses this path, and the reason is worth repeating: a PAT carries **your** identity, so
every pipeline commit is attributed to you and the token is only as narrow as you made
it. A classic (non-fine-grained) PAT with `repo` scope grants write access to every
repository you can reach — do not use one here.

### What it does *not* buy the agent

Nothing about this credential lets a session merge. `gh pr merge` is hook-blocked in
every form, and the platform-side gate is branch protection, which lives in repository
settings. Grant the App or PAT no permission beyond the two above — in particular not
*Administration* or *Workflows* — and the ruleset in tier 3 stays outside its reach.

---

## Tier 0 — local, and why it exists

**The point of tier 0 is to prove the loop before you build the machine.** A ticket
becomes a briefed session, becomes the smallest diff, becomes a PR that goes green — all
of it on one workstation, with no push credential, no Actions secrets, no webhook and no
queue. What you learn is whether *your tickets* are good enough to work from, which is
the thing that actually decides whether the higher tiers are worth standing up.

Without it there is no local loop at all. Every guard that binds a session reads a **pin**
(contract §3), and until this script existed the only thing that wrote one was the GitHub
Actions dispatcher. So a project that had just gained a `delivery.json` could not run
`/work`: it resolved `pinsRoot`, computed the pin key, found nothing, and failed closed —
correctly, because §2 makes *configured + `session_mode: ticket` + missing pin* **broken**,
not off. The guard was right; the missing half was a human way to write the binding.

### It is a human tool. An agent must never run it.

The pin is the one piece of authority a session cannot write, and that is the whole
security argument of §3 — everything else the session touches (branch name, PR body,
ticket comments, env vars, every file in the worktree) is reporting. A session that could
place its own pin could retarget itself at another ticket, widen its own scope fence, or
hand itself a budget nobody approved.

So the script refuses to run when it sees a Claude Code / agent environment, and has no
override flag. That refusal is **tamper-evident, not tamper-proof** — the same posture §3
takes about `chmod 0444` — because the session's shell runs as the same user. It raises
the cost and makes a bypass visible; it is not a boundary.

### Using it

```bash
# 1. bind this worktree to one ticket (reads the COMMITTED delivery.json)
python3 scripts/pipeline_dispatch_local.py ENG-123

# 2. it prints the branch to create, then work the ticket in a session rooted here
git checkout -b feat/eng-123-token-refresh
#   /work ENG-123     ... then /ship

# 3. you are the dispatcher, so you do §3 step 5 as well
python3 scripts/pipeline_dispatch_local.py --release
```

`--show` reports what is bound to this worktree using the hook's own vocabulary
(`ok` / `absent` / `expired` / `mismatch` / `malformed`), which is the fastest way to
tell a stale pin from an unbound session. `--dry-run` prints the pin without writing it.
`--ticket-file <json>` dispatches offline from a saved issue object instead of calling
Linear; otherwise the key is read from `$LINEAR_API_KEY` (the **name** of the variable is
what `--api-key-env` takes — never the key itself).

Reading a ticket over MCP needs the project's Linear server to be keyed **`linear`** in
`.mcp.json`: MCP tool names are `mcp__<server-key>__<tool>`, and `/work`, `/ship` and
`/weekly-review` grant `mcp__linear__*` by that literal name. A server under any other key
(a connector's opaque UUID, say) leaves the grant matching nothing. `--ticket-file` needs
no MCP server at all.

### What it deliberately does not do

| | |
|---|---|
| Start a session | you do, in your own terminal |
| Create the branch | it prints the command; the pin records the expected name |
| Touch ticket state or `agent:*` labels | those stay dispatcher- and human-owned (§6) |
| Keep a dispatcher state record (§9) | there is none, so a local dispatch consumes no `totalAttempts` slot and reserves no `dailyUsd`. The `budget` block it pins is advisory — nothing meters a human's own session. `--attempt N` exists so a re-run can say which attempt it is honestly. |
| Invent acceptance criteria | **never.** An empty `## Acceptance criteria` section is a Definition-of-Ready failure (§7), so the script prints the reason and exits non-zero rather than writing a pin with a grader it made up. Criteria written by the thing being graded are not a definition of done. |
| Merge | nothing in this kit merges. |

Everything else is identical to the CI dispatcher, on purpose: the same pin key
(`sha256(realpath(session root))[:16]`), the same store, the same write protocol (temp
file → `fsync` → `0444` → atomic `rename`), the same `ledger.jsonl` row — with
`"stage": "local-dispatch"` so a hand dispatch is never mistaken for the queue's — and
the same ticket parser, imported from `scripts/check_ticket_dor.py`. `npm run test:local-dispatch`
asserts that agreement, including that the hook and the dispatch workflow still derive the
key the same way.

### Moving on to tier 1

Tier 0 and tier 1 write the same pin into the same place, so they are not compatible in
the same worktree at the same time: the dispatcher checks out a fresh workspace and pins
that, and the script refuses to overwrite an existing pin without `--force`. Once the
queue is running, use tier 0 for the checkouts a person drives by hand and let the
dispatcher own the ones it creates.

---

## Tier 1 — dispatch, and how a ticket actually enters the queue

Tier 1 is on by default, and the thing to know about it is **who applies
`agent:queued`**: the dispatcher, and only the dispatcher.

Contract §6 makes every `agent:*` label dispatcher-owned — a session that sets its own
lifecycle labels is a session editing its own supervision, and the safe-outputs validator
rejects any batch that tries. So the human-facing action is simply **move the ticket to
`ready`** (by hand, or via tier 2). On its next poll the dispatcher's `claim` job walks
`ready`, applies `agent:queued` to everything eligible, and then selects from that. The
label is internal bookkeeping; the state is the trigger.

The `claim` job declines to queue a ready ticket in exactly three cases, all of them a
dispatcher-owned hold:

| Label on the ticket | What un-holds it |
|---|---|
| `agent:needs-human` | a person removes it — that is the whole point of the label |
| `agent:blocked` | a person answers the escalation and removes it |
| `agent:working` | the session finishes; the lifecycle job hands the ticket back |

`blocked:capacity` is the exception, because it is the one hold the dispatcher owns on
both ends: it is cleared automatically, along with its `agent:blocked`, on the first poll
after the provider pause lifts.

### The other half: tickets come back

A session that does not finish perfectly used to hold its `working` slot forever, which
after `wipLimit` such tickets killed the queue outright. The `lifecycle` job now
guarantees the reverse invariant — **it never leaves a ticket in `working`**:

| Outcome | What happens to the ticket |
|---|---|
| completed | it is already in `review`; `agent:working` comes off |
| retryable failure, attempts left | back to `ready` **with `agent:queued`** — the next poll re-dispatches it |
| session escalated (`outcome: blocked`, or `/work`'s escalation marker) | back to `ready` with `agent:blocked` and **without** the trigger; a person answers, removes the label, and the next poll picks it up |
| escalated a `riskPaths` change | `agent:needs-human`, terminal |
| `totalAttempts` exhausted | `agent:needs-human`, terminal |
| provider capacity | `agent:blocked` + `blocked:capacity`, attempt refunded, cleared automatically |

A re-queue adds nothing to the attempt counter — `claim` increments it at dispatch,
because §9 requires the count to be known *before* a session starts. `totalAttempts` is
what makes the loop a bounded retry rather than a loop.

---

## Tier 2 — auto-approval

### What it does

Moves tickets from `raw` to `ready` when a deterministic script says every gate passes.
No model runs in that decision: `scripts/check_auto_approve.py` is stdlib Python with no
network access and no credential, and ticket text reaches it as *data it parses*, never
as a prompt. `templates/workflows/pipeline-auto-approve.yml` fetches, hands over, and
executes the verdict.

### The gates

All must pass, and all are recomputed from something a session cannot write:

- **provenance resolves to `epic/<ID>`** (§5 rule 4 — the class from the label, the ID
  from the parent link)
- **that epic exists and is itself out of intake** (§5 rule 2 — otherwise
  `epic/<anything>` would be a self-serve approval)
- the ticket is in `raw`
- it carries no dispatcher-owned `agent:*` / `blocked:*` label, and no human-applied
  `hooks-change`
- `scripts/check_ticket_dor.py` passes in `--strict`
- nothing the ticket names matches `autonomy.riskPaths`

### Why only `epic/*`

`monitor`, `review`, `retro-proposal` and `human` never auto-approve. That is not a
stylistic preference — those four are the classes an agent, or anything that can trip a
probe or influence a diff, can cause to be filed. If any of them could approve itself,
the pipeline could widen its own mandate by writing a ticket, and *"file a ticket asking
for X"* is a capability every one of those paths has. `epic/*` is the only class whose
approval traces back to something a person did.

### Turning it on

1. `autonomy.autoApproveProvenance` is `["epic"]` in `delivery.json` (the shipped
   default; §5 rule 3 caps it there and the §7 validator hard-fails anything else).
2. Activate the workflow: `git mv templates/workflows/pipeline-auto-approve.yml .github/workflows/`
3. Repo → Settings → Secrets and variables → Actions:
   - Secret `LINEAR_API_KEY`
   - Variable `PIPELINE_AUTO_APPROVE_ENABLED` = `true`

Leave the variable unset for the first few weeks. The workflow still runs, still
evaluates, and writes its verdicts to the run summary — you get to read what it *would*
have approved before it approves anything. Two switches with two owners is deliberate:
the config value can be proposed in a PR, the repo variable can only be changed by a
human in the GitHub UI.

---

## Tier 3 — auto-merge

### Set up the ruleset FIRST

Without required status checks, GitHub auto-merge merges the instant it is enabled, and
the eight conditions below become the *only* gate. They are designed to be the second
line, not the first.

Repo → Settings → General → check **Allow auto-merge**. Then Settings → Rules → New
ruleset, targeting the default branch:

- **Require status checks to pass** — add your CI job's context name (for a kit-derived
  project that is `Kit checks`, or your app CI's job name). This is the load-bearing one.
- **Require a pull request before merging**, with at least **1 approving review**
- **Require linear history**
- **Block force pushes**

Then create the label the grader-path job needs:

```bash
gh label create hooks-change --description "Touches guard machinery — needs human review" --color B60205
```

### Turning it on

1. `delivery.json` → `autonomy.autoMergeMaxLines` to a real ceiling. Start small — 150
   changed lines is a sane first number, and it is one you can raise later once you have
   watched it for a month. `0` (the shipped default) disables the tier entirely.
2. Optionally `autonomy.autoMergeMethod`: `squash` (default), `merge`, or `rebase`.
3. `git mv templates/workflows/pipeline-auto-merge.yml .github/workflows/`
4. Variable `PIPELINE_AUTO_MERGE_ENABLED` = `true`

Three switches, three owners: a repo variable (human, GitHub UI), a config value
(reviewable in a PR), and the branch ruleset (human, GitHub UI). All three must agree.

### The eight conditions

Every one is required, and every input comes from somewhere a session cannot write:

| Condition | Read from | **Not** read from |
|---|---|---|
| Tier enabled | config + repo variable | the config alone |
| The ticket still passes tier 2, recomputed live | a fresh gate run | a stored "was approved" flag |
| **Zero bounces** | Actions run history | `pipeline:bounce-N` PR labels — the fix session's token can edit those |
| No findings at or above `reviewSeverityThreshold` | the review artifact | a PR comment the author can edit |
| Every check run terminal and green | the check-runs API | "CI passed" in a commit message |
| `mergeStateStatus` not `DIRTY` / `UNSTABLE` / `UNKNOWN` | the PR API | — |
| Diff touches no `riskPaths` | `git diff base...head` | the PR body's account of itself |
| Changed lines ≤ `autoMergeMaxLines` | `git diff --numstat` | — |

### Two conditions worth not relaxing

**Zero bounces.** This is the one people want to loosen first. A bounce means the first
attempt was wrong in a way review or CI caught. The fix may well be right — but the
evidence that the pipeline *understood the ticket* is now mixed, and mixed evidence is
exactly the case worth a human's thirty seconds. It is also the state carrying the most
machine-authored churn on the branch, so it is where a human read is worth the most.
Bounces exist to get a PR ready **for a person**, not ready to merge itself.

**A push revokes the request.** Every condition above describes one head sha. GitHub
keeps auto-merge enabled across subsequent pushes, so without this a PR that qualified
at 40 changed lines could be amended to 400 — or amended to touch a hook — and still
merge on a verdict about different code. The workflow's `invalidate` job disables
auto-merge on any push, and the PR has to qualify again from scratch. Revoking is cheap;
the reverse mistake is not recoverable.

### What "held" looks like

Held is the normal outcome, not a failure. The workflow posts a comment listing every
gate and why each passed or held, and the PR sits waiting for a human — which is what
would have happened anyway without the tier. Nothing is blocked; nothing is red.

---

## Telemetry and the weekly review

`templates/workflows/pipeline-telemetry.yml` sweeps the telemetry blocks sessions post
as ticket comments (§4) into three Postgres tables (§10) and regenerates a
self-contained HTML dashboard.

```jsonc
// delivery.json
"telemetry": {
  "store": "postgres",
  "dsnEnv": "PIPELINE_TELEMETRY_DSN",   // the NAME of the env var, never the DSN
  "schema": "pipeline",
  "lookbackDays": 30
}
```

Add the DSN as a repository secret under that name. Delete the whole block to skip
collection — absent is off, exactly as §2 means it.

The sweep is idempotent on natural keys (`run_id`; `(ticket_id, event, at)`; a content
digest for findings), so it keeps no cursor and re-running over an overlapping window is
free. Malformed blocks are counted and skipped; only an unreachable store is an error.

`/weekly-review` then reads the **structured summary**
(`telemetry_dashboard.py --json`), never the rendered page — one `summarize()` call
feeds both, so the human and the model cannot end up reasoning about different numbers.
Its three limits are enforced by `scripts/check_weekly_review.py`, not by wording:

1. **Proposed rubric and prompt changes ship as normal reviewed PRs.** Grader paths are
   in `riskPaths`, so the auto-merge gate refuses them, CI demands the human-applied
   `hooks-change` label, and the review's own proposals must declare `"delivery": "pr"`.
2. **Invented features carry `retro-proposal` provenance** and are filed into `raw` —
   a class §5 bars from auto-approval, permanently.
3. **The review cannot raise its own budgets, WIP or caps.** Every proposed config
   change is diffed against the committed `delivery.json`, and any loosening — a cap
   raised, the severity threshold raised, a riskPath dropped, an approval class added —
   is rejected. Tightening always passes.

A system that concludes it should be allowed to spend more, and then allows itself, does
not have a budget.

---

## Running the gates by hand

Every gate is a plain script. None needs a credential, a network, or a running pipeline:

```bash
npm run test:local-dispatch  # the tier-0 dispatcher's battery (incl. key-derivation drift)
npm run test:approve     # the auto-approval gate's own battery
npm run test:merge       # the auto-merge tier's battery
npm run test:telemetry   # the collector's battery
npm run test:dashboard   # the dashboard's battery
npm run test:review      # the weekly-review limits
```

To evaluate a real ticket without touching anything:

```bash
python3 scripts/check_auto_approve.py --config delivery.json --epic epics.json ticket.json
```

Exit 0 means it qualifies; exit 1 means held, with a reason per gate.
