# Autonomy — what runs without a human, and how to turn it on

The delivery pipeline answers three questions without a person. Each is narrower than
the last, each has its own deterministic gate, and **every rung above the first is off
until an operator turns it on**. The formats are frozen in
[`PIPELINE-CONTRACT.md`](PIPELINE-CONTRACT.md) §11; this file is how you actually
enable them.

| Tier | Question | Gate | Ships as |
|---|---|---|---|
| **Dispatch** | may this *approved* ticket start a session? | dispatcher WIP / budget / attempt gates (§9) | on |
| **Approve** | may this ticket move `raw` → `ready` without a person? | `scripts/check_auto_approve.py` | on for `epic/*` only |
| **Merge** | may this PR merge without a person? | `scripts/check_auto_merge.py` + the platform ruleset | **off** |

Nothing here is required. A project that never enables the second and third tier still
gets the whole pipeline — it just has a human at two points in it, which for most teams
is the right answer for a long time.

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
