# Autonomy is a tiered ladder, and the platform holds the merge capability

**Date:** 2026-08-24 · **Status:** Accepted · **Context:** `feat/autonomy-and-telemetry`
— the autonomy and measurement half of `docs/PIPELINE-CONTRACT.md`

## Decision

Three things the pipeline may do without a human, each a separate rung with its own
deterministic gate, and each above the first **off by default**: dispatch, approve
(`raw` → `ready`), merge. Approval is decided by `scripts/check_auto_approve.py` and
passes only `epic/<ID>` provenance whose epic is itself verified out of intake. Merging
is decided by `scripts/check_auto_merge.py` — which **never merges**: it asks GitHub to
enable *its own* auto-merge, so the platform performs the merge under branch protection
rules that live in repository settings.

Alongside them, a telemetry path: `scripts/telemetry_scrape.py` sweeps the §4 blocks
sessions post as ticket comments into three Postgres tables (§10);
`scripts/telemetry_dashboard.py` renders one summary object into both a self-contained
HTML page and the JSON that `/weekly-review` reads. The contract gains §10 (telemetry
store) and §11 (autonomy tiers), plus a `telemetry` config block, an optional
`review_findings` telemetry array, and the `dispatch_id` field §3 had promised as a join
key but §4 never carried.

## Why

**The merge capability is never held, not held-and-restrained.** The obvious design is
to let CI merge qualifying PRs, and it is wrong in a way that is hard to see: any
component that *can* merge is one prompt-injection or one logic error away from merging
something it should not, and the restraint is code that the same system could change. By
routing through `enablePullRequestAutoMerge`, the decision to merge is enforced by
required status checks in repository settings — outside the repo tree, outside any diff,
unreachable from a session. Revoking it is a settings change a human makes, not a code
change a PR can carry. The never-merge hook guard is untouched and stays unconditional.

The corollary is that **the branch ruleset is part of the tier, not an operational
footnote.** Without required checks, GitHub auto-merge merges the instant it is enabled
and the eight conditions become the only gate. `docs/AUTONOMY.md` leads with the ruleset
for that reason.

**Only `epic/*` auto-approves, and the epic is verified.** §5 already said the first
half. The second half is what makes it real: `epic/<anything>` without an existence-and-
state check is a self-serve approval, because the string alone is what the provenance
gate matches on. The four rejected classes — `monitor`, `review`, `retro-proposal`,
`human` — are exactly the paths an attacker-influenced payload or an agent's own output
takes into the backlog, and *"file a ticket asking for X"* is a capability all of them
have.

**Every gate input is something a session cannot write**, and the rejected alternatives
are as informative as the choices:

| Chosen | Rejected | Why the rejection matters |
|---|---|---|
| Actions run history for the bounce count | `pipeline:bounce-N` PR labels | The fix session's token can edit PR labels; a label cannot hold a number that decides whether something merges |
| A **recomputed** approval verdict | a stored "was auto-approved" flag | A stored bit is only as good as everything that could have touched the store since; a ticket relabelled or blocked after approval stops qualifying on its own |
| `git diff base...head` | the PR body's account of its own size | The agent writes the PR body |
| The review findings artifact | the review's PR comment | The PR author can edit comments |
| `delivery.json` from the PR's **base** sha | the head copy | A head that shortens its own `riskPaths` is grading its own homework |

**Natural keys, not a scrape cursor.** A "last scraped" pointer is state that can be
lost, skipped past or rewound, and every one of those failures is silent — double-count
or a hole nobody notices. §4 had already frozen a stable key for each row type
(`run_id`; `(ticket_id, event, at)`), so the collector re-reads a generous window every
time and lets the store deduplicate. A little wasted read removes an entire class of
silent corruption.

**One summary object, two readers.** The dashboard's HTML and the JSON `/weekly-review`
consumes come from a single `summarize()` call. Had the review scraped the rendered page
or recomputed its own totals, the model and the human could reason about different
figures with no way to tell which was wrong.

**The retrospective's three limits are implemented, not documented.** A limit that lives
only in a prompt is a limit the next model revision may not honour. So: grader paths
(now including `.claude/skills/**`, since skills *are* the prompts and rubrics) sit in
`riskPaths`, where the approve gate holds any ticket naming one and the merge gate
refuses any diff touching one; `retro-proposal` provenance is structurally unable to
auto-approve; and `scripts/check_weekly_review.py` diffs every proposed config change
against the committed `delivery.json` and rejects any loosening — a raised cap, a raised
severity threshold (which blocks *fewer* findings), a dropped `riskPath`, a widened
approval class. Tightening always passes: a review concluding the pipeline needs less
rope is a review working correctly.

**Zero bounces for the merge tier.** The tempting relaxation is "one bounce is fine, the
fix passed review too". But a bounce means the first attempt was wrong in a way review
or CI caught, and the evidence that the pipeline understood the ticket is now mixed —
which is precisely the case worth a human's thirty seconds. It is also the state
carrying the most machine-authored churn. This does not conflict with `maxBounces > 0`:
bounces exist to get a PR ready *for a person*.

## Accepted trade-offs

- **`telemetry` is not in the §7 validator.** Every §7 row gates autonomy; telemetry
  gates nothing. A misconfigured sink costs dashboards, not supervision, so it is
  validated by its consumer at the point of use. Adding it to the guard validator would
  imply the guards depend on it.
- **The scrape re-reads overlapping windows.** Wasted API calls, traded for the removal
  of cursor corruption.
- **Cost figures are self-reported** (§4) and can be under-reported. They drive
  dashboards only; `dailyUsd` is metered against the dispatcher's own ledger (§9).
- **The first metric set will be wrong.** Which numbers matter only becomes clear with
  real data, so the metrics are declared as data in one `METRICS` list and the renderer
  knows nothing about what any of them mean.

## Verified

- Five new selftests, all green and all wired into CI's *Kit checks*:
  `--selftest` on the approval gate (37 cases), the merge tier (36), the collector (79),
  the dashboard (68) and the weekly-review limits (42).
- **The two halves are asserted to connect.** The dashboard's selftest imports the
  collector, checks that every column it SELECTs is one the collector actually writes
  and is declared in the DDL, then runs a real comment through parse → rows → summary →
  HTML with no database. A renamed column fails CI instead of silently returning nulls.
- **Adversarial cases, not just happy paths.** Each of the four never-approving
  provenance classes is asserted to hold. A fabricated `epic/ENG-999` holds; an epic in
  `raw` holds; an epic carrying `agent:needs-human` holds. A shortened `riskPaths` in
  config does not defeat the gate's own floor. For the merge tier: one bounce holds, a
  `DIRTY` PR holds, an unusable findings file is UNREVIEWED rather than clean, an empty
  check list is "CI did not report" rather than "CI passed", and a PR failing several
  gates reports all of them.
- **Idempotency asserted at the SQL layer**, not by hopeful comment: the runs upsert is
  checked to be `ON CONFLICT (run_id) DO UPDATE` without overwriting its own key, events
  to be `ON CONFLICT (ticket_id, event, at) DO NOTHING`, and a re-posted finding to
  digest identically.
- **A `merged` event with `actor: agent` is refused outright** — recording it would
  corrupt every autonomy metric downstream.
- **Self-containment is asserted by regex**, not by intention: the rendered page is
  checked to contain no `<script>`, no absolute URL, no `<link>`, no `<img>`, no
  `@import` and no `url(`. An empty dataset renders rather than crashing.
- **The weekly-review limits are tested in both directions**: raising `maxTurns`,
  `wipLimit`, `dailyUsd`, `maxBounces`, `totalAttempts`, `fixIterations`, per-effort
  `maxUsd` or `autoMergeMaxLines` is refused, while *lowering* each is allowed; dropping
  a `riskPath` is refused while adding one passes; widening `autoApproveProvenance` is
  refused while narrowing it passes.
- Placeholder integrity, JSON/YAML parse validation and the hook battery re-run green
  with the new files in the tree.

**Not verified here:** no live Postgres, no live Linear workspace, and no exercised
GitHub auto-merge mutation. The kit ships no `delivery.json`, so all of this is inert in
this repo by construction (§2) and the selftests are what has teeth. The first project
to enable tier 3 should confirm the ruleset behaviour before trusting it — in
particular that `enablePullRequestAutoMerge` errors, rather than merging, on a repo with
no required checks.
