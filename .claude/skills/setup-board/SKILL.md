---
name: setup-board
description: Provision and record a project's Linear conventions — read the workspace, diff it against the delivery contract, print the plan, apply only what's missing, then fill delivery.json's Linear IDs and validate it against the contract. Idempotent, so re-running doubles as a drift check. Use at bootstrap, or whenever the board and delivery.json may have drifted apart.
argument-hint: <team key or name> [--check]
---

## What this is for

The delivery pipeline (tracker issue → Claude Code session → PR) only holds together
while the board and the repo agree on the same labels, statuses and IDs. This skill
makes them agree, and — more importantly — **writes down what they agreed on**.

Its highest-value output is the **reading**, not the creating: a `delivery.json` full
of real Linear **IDs**. Guards match on IDs, so a label renamed in the UI can never
silently desync them; a guard keyed on the display name would just stop firing.
Collecting those IDs by hand means clicking through settings pages transcribing UUIDs
— miserable, and wrong the first time. That is the job.

`--check` runs steps 1–2 only: read, diff, report, change nothing.

---

## Before you start

1. **Read `docs/PIPELINE-CONTRACT.md` first.** It is the authority on the label
   taxonomy, status names, provenance values and the `delivery.json` schema.
   Everything below is the *mechanism*; the contract is the *content*. If it is
   missing, say so and stop — do not invent a taxonomy.
2. **Linear MCP must be connected.** If its tools error with an auth failure, stop:
   the human connects it (claude.ai connector settings, or `claude mcp` / `/mcp` in an
   interactive session). You cannot run an OAuth flow for them.
3. **`LINEAR_API_KEY` must be in the environment** for the GraphQL half. Reference it
   by name only (`$LINEAR_API_KEY`) — never read it out of a file, never echo it, never
   paste it into a PR or an issue. Linear personal keys go in the header **raw**, with
   no `Bearer` prefix (OAuth tokens use `Bearer`).
4. **The egress guard will block your first curl.** `curl --data … https://api.linear.app/graphql`
   is an upload to a non-allowlisted host, which is exactly the exfil shape
   `.claude/hooks/pre-tool-use.py` exists to stop. It needs `linear.app` in that hook's
   stack-specific `EGRESS_ALLOW_SUFFIXES` slot (a separate PR adds it). **If you are
   blocked, halt and print the fix — do not shim around it**, and note that the hook is
   self-protected: only the human can apply the change.

---

## Which half does what

The MCP covers the everyday objects; everything structural needs raw GraphQL.

| Concern | Use |
|---|---|
| Teams, projects, issues, statuses (**read**) | MCP `list_teams` / `list_projects` / `list_issue_statuses` |
| Labels (read + create) | MCP `list_issue_labels` / `create_issue_label` |
| Projects (create/update) | MCP `save_project` |
| Team creation | GraphQL `teamCreate` — **prefer the human** (see step 5) |
| Workflow states (create/update) | GraphQL `workflowStateCreate` / `workflowStateUpdate` |
| Issue templates | GraphQL `templateCreate` |
| Git automations | GraphQL `gitAutomationStateCreate`, `gitAutomationTargetBranchCreate` |
| Webhooks | GraphQL `webhookCreate` |
| **`gitBranchFormat`** | GraphQL `organizationUpdate` — **workspace-wide**, see step 3 |

Endpoint `https://api.linear.app/graphql`; POST `{"query": "…"}` with
`-H "Authorization: $LINEAR_API_KEY"`. Put the query in a file and send `--data @file`
rather than inlining it — long GraphQL does not survive shell quoting.

---

## Step 1 — Read everything. Mutate nothing.

One query returns almost the whole board state. Run it before you form an opinion:

```graphql
query BoardState {
  organization { id name urlKey gitBranchFormat }
  teams(first: 50) {
    nodes {
      id key name
      states(first: 50) { nodes { id name type position color } }
      templates { id name type }
      gitAutomationStates {
        nodes { id event state { id name } targetBranch { id branchPattern isRegex } }
      }
    }
  }
  issueLabels(first: 250) {
    nodes { id name color isGroup parent { id name } team { id key } }
  }
  webhooks(first: 50) { nodes { id label url enabled resourceTypes team { id key } } }
}
```

Notes that save a round trip:

- A label whose `team` is `null` is a **workspace** label; a label with a `team` is
  scoped to it. The contract decides which the taxonomy wants — they are not
  interchangeable, and you cannot convert one into the other.
- `isGroup` / `parent` are label **groups** (mutually-exclusive sets). A taxonomy with
  `type/feat`-style names is usually a group plus children, not flat labels.
- `state.type` is one of `triage`, `backlog`, `unstarted`, `started`, `completed`,
  `canceled`, `duplicate`. Only `type` carries behaviour; `name` is cosmetic. Match on
  `type` and treat `name` as the thing that drifts.
- Read git automations from `team.gitAutomationStates`. The older
  `team.startWorkflowState` / `mergeWorkflowState` / `reviewWorkflowState` fields are
  deprecated — do not write through them.

Also pull the MCP views (`list_teams`, `list_issue_labels`, `list_issue_statuses`,
`list_projects`) for the team in `$ARGUMENTS`. If the argument is empty, list the teams
and **ask which one** — never guess, and never "helpfully" pick the only team.

---

## Step 2 — Diff against the contract, then PRINT THE PLAN

Build one table and **show it to the human before touching anything**. Every row is
either already satisfied or a specific action:

```
Concern              Exists                    Contract wants          Action
─────────────────────────────────────────────────────────────────────────────────
Team                 ENG "Engineering"         ENG                     ok
Status: In Review    — (no state of type       "In Review" (started)   CREATE
                       `started` named that)
Label: agent:queued  agent:queued (workspace)  workspace               ok
Label: risk/high     risk-high (team ENG)      workspace group `risk`  ASK — rename?
gitBranchFormat      (null → Linear default)   feat/{issueIdentifier}… CONFIRM (org-wide)
Webhook              — none                    <contract URL>          NEEDS SECRET
Ideas project        — none                    "Ideas"                 HUMAN
```

Rules for the plan:

- **Create-if-absent only.** Never rename, retire, reorder or delete something a human
  made. A near-match (`risk-high` vs `risk/high`) is an **ASK** row, never a silent
  create — creating the second one leaves the board with both, which is worse than the
  drift you started with.
- **Never fold the plan into the action.** Print, wait, then apply. `--check` stops here.
- Say what is already correct, not only what is missing — that is what makes a re-run
  read as a drift report instead of a wall of noise.

---

## Step 3 — `gitBranchFormat` is workspace-wide. Confirm it separately.

`organizationUpdate` sets `gitBranchFormat` for the **entire workspace** — every other
product, every other team, everyone's "copy branch name" button. It is the one setting
here whose blast radius exceeds the project you were asked to set up, so it gets its
own confirmation even if the human already approved the plan.

Show the current value, the proposed value, and the blast radius; then ask outright.

Recommend:

```
feat/{issueIdentifier}-{issueTitle}
```

Why that one: Linear slugs generated branch names to lower case, so this yields
`feat/eng-42-add-widget` — which satisfies the kit's branch-naming guard
(`^(feat|fix|chore|refactor|docs)/[a-z0-9][a-z0-9-]*$`) **and** puts the ticket ID in
the leading segment where the "Ticket link" CI job looks for it. Linear's default
(`{username}/{issueIdentifier}-{issueTitle}`) fails that guard on the very first
character, so every agent session would have to rename its branch by hand.

The literal `feat/` prefix is the accepted tradeoff: one format serves the whole
workspace, so fix-type tickets also get `feat/` branches and are renamed per-branch when
it matters. Say this out loud when you propose it — do not let the human discover it.

```graphql
mutation SetBranchFormat($format: String!) {
  organizationUpdate(input: { gitBranchFormat: $format }) {
    success
    organization { id name gitBranchFormat }
  }
}
```

If the human declines, that is a complete answer: record the actual value in
`delivery.json` and move on. Do not re-ask on the next run.

---

## Step 4 — Apply, narrowest scope first

Order matters — later objects reference earlier IDs: **workflow states → labels →
templates → git automations → webhooks**.

```graphql
mutation AddState($teamId: String!, $name: String!, $type: String!, $color: String!) {
  workflowStateCreate(input: { teamId: $teamId, name: $name, type: $type, color: $color }) {
    success  state { id name type position }
  }
}

mutation AddTemplate($teamId: String!, $name: String!, $data: JSON!) {
  templateCreate(input: { teamId: $teamId, name: $name, type: "issue", templateData: $data }) {
    success  template { id name }
  }
}

# Move the issue when a PR opens / merges. stateId: null = "take no action".
mutation AddGitAutomation($teamId: String!, $stateId: String, $event: GitAutomationStates!) {
  gitAutomationStateCreate(input: { teamId: $teamId, stateId: $stateId, event: $event }) {
    success  gitAutomationState { id event state { id name } }
  }
}

mutation AddWebhook($teamId: String!, $url: String!, $types: [String!]!) {
  webhookCreate(input: { teamId: $teamId, url: $url, resourceTypes: $types, enabled: true }) {
    success  webhook { id url enabled resourceTypes }
  }
}
```

- `GitAutomationStates` events are `draft`, `start`, `review`, `mergeable`, `merge`.
  Scope one to a branch with `gitAutomationTargetBranchCreate` (`branchPattern`,
  `isRegex`) and pass the resulting `targetBranchId`.
- Labels go through the MCP `create_issue_label`. Omit the team to make a **workspace**
  label; groups are a parent with `isGroup: true` plus children carrying `parentId`.
- **Idempotency is on you** — these mutations are not upserts. Re-check the step-1 read
  immediately before each create and skip anything already present. A second run must
  produce zero writes; if it does not, the diff in step 2 was wrong.
- On any error, stop and report the raw GraphQL `errors[]`. Do not retry a mutation that
  failed for an unknown reason — a partial board is easier to fix than a doubled one.

---

## Step 5 — At a human boundary, STOP and hand over explicit steps

Some things you must not do on someone's behalf, and a few you simply cannot. Never
skip them quietly, and never mark them "done" from a guess. Print the exact steps, then
**wait**; when the human confirms, re-run step 1 and pick up where you left off.

| Boundary | Why it is theirs |
|---|---|
| Creating a **team** | Permanent, workspace-visible, and the key becomes every future ticket ID. `teamCreate` exists; use it only if they explicitly ask you to. |
| Creating the **Ideas project** | It is a product decision about where work is triaged, not a config value. |
| Issuing the **API key** or a **webhook secret** | Credentials. You never handle these. |
| Changing **`gitBranchFormat`** | Step 3 — workspace-wide. |
| Anything the plan marked **ASK** | A near-match is drift with a human cause. |

Format the handover so it can be pasted, not interpreted:

```
I need you to do these two things — I can't:

1. Create the team
   Linear → Settings → Teams → New team
   Name: Engineering        Identifier: ENG

2. Create the triage project
   Linear → Projects → New project
   Name: Ideas              Team: Engineering

Tell me when both exist and I'll re-read the workspace and continue.
```

---

## Step 6 — Fill in `delivery.json` (the point of the whole exercise)

**You do not design this file.** `docs/PIPELINE-CONTRACT.md` §1 owns the schema,
`schemas/delivery.schema.json` is that schema in machine-readable form, and
`delivery.example.json` is the canonical shape — open all three. Your job is to replace
tokens inside the `linear` block with the real IDs you read in step 1:

| You fill | With |
|---|---|
| `linear.teamKey` / `linear.workspace` | the team key (uppercase) and the org `urlKey` |
| `linear.stateIds.{raw,ready,working,review,done}` | the five workflow-state **UUIDs** |
| `linear.labels.ids.<canonical key>` | each label's Linear **UUID**, as a bare string |

**Every other section — `version`, `github`, `branch`, `stack`, `commands`, `budgets`,
`auth`, `autonomy`, `dispatch`, `monitoring` — carries through untouched** from
`delivery.example.json` on a fresh project, or from the existing `delivery.json` on a
re-run. Those are human-authored policy, not board readings; a re-run that rewrote them
would silently reset someone's budgets.

So the file you hand over is the example, with the `linear` block resolved:

```json
{
  "version": 1,
  "linear": {
    "teamKey": "ENG",
    "workspace": "acme",
    "stateIds": {
      "raw":     "8f1c…",
      "ready":   "2b40…",
      "working": "c7d9…",
      "review":  "51ae…",
      "done":    "e034…"
    },
    "labels": {
      "ids": {
        "track:platform": "9a11…",
        "effort:S": "4c62…",
        "agent:queued": "7de5…",
        "…": "… one row per canonical key in §6 …"
      },
      "required": ["… unchanged from delivery.example.json …"]
    }
  },
  "…": "… every other section exactly as delivery.example.json has it …"
}
```

Rules that are not negotiable, because guards depend on them:

- **`version` must be present and must be `1`.** The PreToolUse hook treats a config
  whose `version` it does not recognize as **BROKEN** and fails closed — blocking every
  `Edit`, `Write` and `Bash` in the repo until a human repairs the file by hand. Handing
  over a `delivery.json` without `version` bricks the project you were setting up.
- **Keys are canonical, values are ID strings.** `stateIds` is the closed five-key map
  `raw/ready/working/review/done` — never keyed by the display name a state happens to
  have in the UI. If the `review` state is called "In Review", that name belongs in the
  step-8 report, not in the file. Labels the same: `"agent:queued": "<uuid>"`, never
  `"agent:queued": {"id": …}` — every consumer indexes `labels.ids[key]` and expects a
  string. This is §1's corollary: states and labels are referenced by ID, never by
  display name, so a rename in the UI cannot silently desync a guard.
- **Do not invent fields.** `organizationId`, `gitBranchFormat`, project IDs, team
  display names, `generatedBy`/`generatedAt` — none of them exist in §1. A second shape
  for the same structure is precisely what the contract exists to prevent. Everything
  you learned that has no schema slot goes in the report.
- **The schema is the arbiter, not your memory of this page.** Every rule above is
  mechanized in `schemas/delivery.schema.json`; step 7 runs it. If you are unsure whether
  a field exists, read the schema rather than guessing — and if a field you want is not
  in it, that is the answer.
- **Merge, do not re-emit.** Read the current `delivery.json` if there is one, replace
  only the fields in the table above, and leave the rest byte-for-byte. Show the diff
  before writing; an empty diff is the drift check passing, so say so.
- **Record what is actually true, including refusals.** If the human declined the branch
  format in step 3, report the value the workspace really has — do not compensate by
  editing `branch`.
- **The pin is not yours.** The dispatcher writes the per-session ticket binding at
  `<dispatch.pinsRoot>/<sha256(realpath(session root))[:16]>.json`, **outside every
  worktree and uncommitted**, before the session starts, and deletes it at session end
  (§3). This skill neither reads nor writes it, and **no ticket ID ever goes in
  `delivery.json`** — that file is committed per-project config, not per-run state.

---

## Step 7 — Validate before you hand it over

Contract §7's checklist is mechanized, and all fifteen rules already run in one command.
A config that fails it is **broken**, not merely imperfect — §2 says a present-but-invalid
`delivery.json` fails closed.

**Run the shape check first.** It answers one question — is this the shape §1 defines? —
against the schema itself, so a wrong-shaped file is caught before anything interprets
its contents:

```bash
python3 scripts/check_schemas.py --instance delivery.json --schema delivery
```

Then the full validator, shape plus semantics:

```bash
python3 scripts/check_delivery_config.py
```

- **Exit 0 from both is the only acceptable outcome.** If either exits non-zero, fix what
  it names and run it again. Never report the file as done, and never tell the human it is ready,
  while the validator is red — you would be handing them a repo that blocks its own next
  session.
- Warnings do not fail the run, but read them out loud: an unresolved non-required label
  ID means a guard reaching for that label gets nothing.
- If it exits 0 and prints nothing at all, there is no `delivery.json` in the working
  tree — that is §2's *off*, and it means step 6 did not actually write the file.

---

## Step 8 — Report

Close with: what already existed, what you created, what the human still owes you,
whether `delivery.json` changed, and the validator's verdict. If the file changed on a
run you expected to be a no-op, that is the finding — the board drifted. Say so plainly
rather than burying it in the diff.

This is also where everything with no slot in the §1 schema lives, because it is
genuinely useful and genuinely not config — write it out as prose, mapped to the
canonical key it corresponds to:

```
Workspace   acme (org 8f1c…), gitBranchFormat feat/{issueIdentifier}-{issueTitle}
Team        ENG "Engineering" (2b40…)
States      raw "Ideas" · ready "Ready" · working "In Progress" · review "In Review" · done "Done"
Projects    Ideas (c7d9…)  — triage target, created this run
Schema      python3 scripts/check_schemas.py --instance delivery.json --schema delivery → OK
Validator   python3 scripts/check_delivery_config.py → OK, 0 errors, 0 warnings
```
