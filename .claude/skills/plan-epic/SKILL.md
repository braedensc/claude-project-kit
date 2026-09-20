---
name: plan-epic
description: Turn a feature idea into a reviewed, DoR-passing ticket tree in Linear — a PRD read out of the real codebase, decomposition into template tickets, a five-pass rubric panel, then a deterministic Definition-of-Ready gate. Refuses on a greenfield repo (redirects to /plan-mvp). Files into the backlog; never moves a ticket to ready, never merges.
argument-hint: <the feature idea, one or two sentences> [--force-greenfield]
allowed-tools: Bash(git rev-parse *) Bash(git ls-files *) Bash(test -f *) Bash(echo *) Bash(wc *) Bash(tr *)
---

## Repo state (injected before you start)

- Branch: !`git rev-parse --abbrev-ref HEAD 2>/dev/null`
- Pipeline configured (`delivery.json` present): !`test -f delivery.json && echo present || echo absent`
- Tracked files: !`git ls-files 2>/dev/null | wc -l | tr -d ' '`

## Instructions

`$ARGUMENTS` is the feature idea. Produce a ticket tree that a session could pick up
tomorrow without asking a question. **Decomposition quality comes entirely from reading
real code** — that is why step 0 can refuse.

This skill writes to Linear, not to the repo, so it needs no feature branch. Keep any
scratch files outside the repo (the session scratchpad), and do not commit.

`allowed-tools` lists only the preflight commands injected above, so the state block
never prompts. The skill also uses Read/Grep/Glob, subagents, and whichever Linear MCP
tools the project has connected — those names vary per workspace and cannot be
pre-declared here.

---

### 0. Preflight — three gates, in order. Nothing runs before them.

**0a. Is the pipeline configured?** `delivery.json` at the repo root
(`docs/PIPELINE-CONTRACT.md` §2). Absent → **stop and say so**: this skill files Linear
tickets and has nothing to file them into. Point at the contract and offer `/plan-mvp`
if the project is also greenfield.

> The contract's *off = silent* rule governs automatic guards, which must not
> print into projects that never adopted the pipeline. A skill a human just typed
> is a human boundary: it reports why it cannot run. Silence here would be the
> failure mode, not the courtesy.

**0b. Is the config usable?** Read `delivery.json` and stop, naming the exact fix, if:
its `version` is not one you implement (contract §1 — an unrecognized version refuses,
it does not guess); `linear.stateIds.raw` is empty or still an unfilled token; any key in
`linear.labels.required` is missing from `linear.labels.ids` or resolves to `""` (run the
board-setup step that resolves label IDs — they are *resolved*, never authored); or
`linear.teamKey` is unset. **Everything downstream compares label and state IDs, never
display names.**

**0c. The greenfield guard — the one that refuses.** Run all three signals and show the
human the numbers:

```bash
# A. source volume — tracked files that are neither docs nor config
git ls-files | grep -vEi '\.(md|txt|lock|ya?ml|json|toml|ini|cfg)$|^(docs|templates)/|^\.[a-z]' \
  | python3 -c "import sys; f=[l.strip() for l in sys.stdin if l.strip()]; print(len(f),'source files,', sum(sum(1 for _ in open(p,errors='ignore')) for p in f),'lines')"
# B. a data layer — a schema, migrations, or model definitions
git ls-files | grep -iE 'migrat|schema|(^|/)models?/' | head
# C. a test command that actually runs (commands.test from delivery.json)
<commands.test>   # must exit 0 AND report having executed at least one test
```

Adjust A's denylist if it obviously misclassifies this project's layout — the numbers
are the point, not the regex.

Signal A fails if the count is **0**, or under **12 files / 400 lines** of source.
Signal B fails if nothing matches. Signal C fails if the command is `null`, errors, or
reports zero tests ("no tests found" is a failure, not a pass).

**Refuse if A hard-fails (zero source files), or if two or more of A/B/C fail.** Say
this, in your own words but with these facts:

> There is no codebase here to decompose against. Everything this skill is good at —
> real file paths in Pointers, acceptance criteria tied to functions that exist, sizing
> based on how the code is actually shaped — comes from reading code. Against a
> scaffold it produces generic advice and tickets whose Pointers section is empty,
> which is worse than no tickets: they look ready and are not. Run **`/plan-mvp`**
> instead — it is the interactive greenfield half — and come back when there is a
> schema, a golden test command, and real source to point at.

`--force-greenfield` does not skip this. It lets the human override *after* seeing the
census: print the refusal, then require them to say so explicitly in chat. If they do,
proceed, record the override and the census numbers in the PRD document, and flag on
every ticket that its Pointers section is provisional.

---

### 1. PRD pass — Opus 5, reading the actual codebase

One subagent, model `opus`, fresh context. Its job is to *read* before it proposes.

- Current state, with citations: every claim about how the system behaves today names
  the file (and line, where it helps) it came from. A claim without a path is a guess
  and must be dropped or verified.
- Then: the problem, who has it, the proposed change, explicit non-goals, risks and
  their blast radius, rollout/rollback shape, how success is measured, open questions
  for the human.
- Consult `stack.securityNotes` and `stack.graderPaths` from `delivery.json` — anything
  the feature touches in `graderPaths` gets a paragraph.

Then create the two containers the tree hangs from — **ask before creating either**,
naming both, or reuse ones the human names:

1. **The project.** Attach the PRD to it as a Linear project document.
2. **The epic issue**, in `linear.stateIds.raw`, in that project, labelled
   `provenance:human` — a person asked for this.

The epic comes first *because every child's provenance is `epic/<EPIC-ID>`*: without the
epic's real ID there is nothing to stamp, and the DoR gate in step 4 would reject drafts
whose parent does not exist. Create it once, before decomposing.

### 2. Decomposition — Sonnet 5, into drafts (nothing is filed yet)

Subagent, model `sonnet`. Output is **draft ticket JSON**, matching the input shape in
`docs/TICKET-TEMPLATE.md`, written to a scratch file outside the repo. Each draft carries
`projectId`, `parentId` = the epic, `provenance` = `epic/<EPIC-ID>`, and `stateId` =
`linear.stateIds.raw`, so the gate validates exactly what will be filed.

- Every ticket uses the template's five sections, exactly once, in order.
- **Vertical slices.** Each ticket is independently shippable and independently
  reviewable; "add the model", "add the endpoint", "add the UI" is one ticket, not three,
  unless each genuinely lands on its own.
- Acceptance criteria are statements about the finished system, mechanically checkable
  wherever possible — name the command, path, or endpoint in backticks.
- Pointers cite **paths verified to exist**. Check them; the gate will.
- `effort:` proposed from `budgets.perEffort` — a ticket that cannot fit `L` is not an
  `L`, it is two tickets. `track:` from the tracks that exist in `linear.labels.ids`.
- Order the tree by dependency and say which tickets can run in parallel.

### 3. The rubric panel — five parallel passes, fresh context, structured JSON

**No personas.** Do not open a pass with "You are a senior security engineer." Give it
the rubric, the artifact, and the output schema. The independence comes from *separate
context windows*, not from a costume — 2025–26 evaluations found expert-persona framing
does not improve accuracy on this kind of judgment and can degrade it, while adding a
voice that argues for its character instead of the evidence.

| Pass | Model | Concern | Rubric source |
|---|---|---|---|
| `architecture` | `opus` | Fits how this codebase is actually built; consistent with existing patterns and the PRD; no ticket assumes something no other ticket creates | the codebase + the PRD |
| `security` | `sonnet` | Authz/authn, data exposure, input trust, secrets, abuse surface | **interpolated from `stack.securityNotes` + `stack.graderPaths`** |
| `ux-product` | `opus` | A user-visible slice per ticket; empty/error/loading states named; the tree delivers something usable before it delivers everything | the PRD |
| `sizing-split` | `haiku` | Effort labels honest against `budgets.perEffort`; oversized tickets split with the split named | `delivery.json` budgets |
| `dedupe` | `haiku` | Searches existing Linear issues (**including closed**) before anything is filed; flags drafts that duplicate or should extend an existing ticket | the Linear board |

The security rubric is **built at runtime**: each entry of `stack.securityNotes` becomes
a numbered rule and each `stack.graderPaths` glob becomes a must-inspect path. If
`securityNotes` is empty, run the pass on the generic base rubric **and tell the human
their `securityNotes` are empty** — an empty project-specific rubric is a config gap, not
a clean bill of health.

Every pass returns exactly this, and nothing else:

```json
{
  "pass": "architecture",
  "verdict": "pass",
  "findings": [
    {
      "ticket_ref": "draft#3",
      "severity": "high",
      "field": "acceptance-criteria",
      "problem": "one sentence saying what is wrong",
      "edit": "the exact replacement text, or the concrete change to make",
      "confidence": "high"
    }
  ],
  "notes": "at most two sentences that are not findings"
}
```

`verdict` is `pass` or `changes-requested`. `severity` is `low|medium|high|critical`,
`field` one of `title|context|acceptance-criteria|out-of-scope|test-plan|pointers|labels|split`.

**Orchestration, bounded at 2 rounds:**

1. Run all five in parallel. Apply findings at or above
   `budgets.reviewSeverityThreshold`; record findings below it as open questions on the
   draft rather than editing (the threshold already exists in `delivery.json` — do not
   invent a second one).
2. Re-run **only** the passes that returned `changes-requested` — **plus** any pass whose
   concern an applied edit invalidated: if a ticket was split, added, or dropped,
   `sizing-split` and `dedupe` re-run regardless of their earlier verdict.
3. After round 2, stop. Anything still unresolved goes into the PRD document under
   **Unresolved review findings** *and* into your final report to the human, quoted. A
   bounded loop that silently discards its residue is worse than an unbounded one.

### 4. The Definition-of-Ready gate — a script, not a judgment

```bash
python3 scripts/check_ticket_dor.py --strict --json <drafts.json>
```

Exit 0 or the tickets are not ready. Fix the drafts and re-run — **never argue with the
gate, never file past it, never relax `--strict` to make it pass.** It checks the things
a model reliably talks itself out of: sections present, acceptance criteria non-empty and
mechanically checkable, labels set and resolvable, project linked, provenance consistent,
Pointers paths that actually exist, and that the ticket is not being filed straight into
`ready`. If a rule is genuinely wrong for this project, that is a PR against the script,
not an exception in this run.

### 5. Confirm, then file the children

Show the human one table — ticket, title, effort, track, dependency — and the count, and
**ask before creating anything**. Filing N tickets is a blast-radius action; the project
and epic already exist from step 1, so this confirmation is about the tree itself.

File each child with `parentId` = the epic, `projectId` = the project, state
`linear.stateIds.raw`, and labels `track:*` + `effort:*` + `provenance:epic` — contract §5:
the label carries the class, the parent link carries the ID. Add `hooks-change` when a
ticket's change touches `.claude/hooks/**` or `.claude/settings*.json`, and `meta` when
the work is the pipeline working on itself.

**Never set `agent:*` or `blocked:capacity`.** Those are dispatcher-owned; a session
setting its own lifecycle labels is a session editing its own supervision.

**Never move anything to `ready`.** Tickets land in the backlog and a human approves
them. The human's single approving action is on the **epic**: downstream auto-approval of
`epic/*` children requires the referenced epic to be in a human-approved state, so
approving the epic is what releases the tree — and it is the human's to make, not yours.

**Re-runnable:** before creating, search the project for issues with the same title or
the same `epic/<EPIC-ID>` provenance and skip the ones that exist. If a create fails
partway, report exactly which tickets landed with their IDs and which did not — never a
bare "some tickets were created".

If the Linear tools are not connected, stop here, hand over the validated draft file, and
say precisely what to authorize. Do not improvise a substitute for filing.

### 6. Verify what actually landed

Read the created issues back, map them into the gate's shape, and run the gate again. A
label or parent that silently failed to set is caught by the same rules that judged the
draft.

Linear returns `project`, `state`, and a parent link — **not** a `provenance` field
(contract §5 rule 4: the label carries the class, the parent link carries the ID). Map
`project` → `projectId`, `state` → `stateId`, the parent's identifier → `parentId`, and
**leave `provenance` out**: the gate reconstructs `epic/<PARENT-ID>` from the label and
the parent, which is the point — on the round trip it checks what Linear actually holds,
not a value you re-typed. `docs/TICKET-TEMPLATE.md` has the full mapping table.

### 7. Telemetry, then stop

Post one telemetry block per `docs/PIPELINE-CONTRACT.md` §4 as a comment on the epic:
`stage: "epic"` (this session's mode is `planning`), one `runs` row, and one
`ticket_events` row per created ticket (`event: "created"`, `actor: "agent"`). One block
per comment; `run_id` is the idempotency key. `runs.model` takes the exact model ID as
used, not the alias.

Report: the epic URL, the ticket list with IDs, any unresolved rubric findings verbatim,
any config gaps found in step 0b/3, and the sentence that the tickets are in the backlog
awaiting **their** approval.

### Unattended mode — the idea gate

The steps above are the **interactive** path: a person runs `/plan-epic`, and the skill
writes to Linear directly. There is also an **unattended** path — the *idea gate* — where
an idea ticket is delegated into a Planning team and a sandboxed session plans it. What
that session is told is the **planning brief** the Planning dispatcher entry carries
(`PLANNING_BRIEF` in `scripts/pipeline_stage_a_setup.py`), not this file: the fence removes
the `Skill` tool, and a planned repository need not carry this skill at all. This section
records how the two paths differ, so neither drifts from the other.

**The fence is a deny list, not an absence.** The dispatcher injects its tracker server
(`linear`) and three more into every session. The Planning entry's `disallowedTools` names
each of them in both rule forms (`mcp__<server>` and `mcp__<server>__*`) and removes every
built-in tool that runs, writes, fetches, schedules or messages — `Bash`, `Write` and `Edit`
among them. What is left is `Read`, `Grep`, `Glob` and helper sessions (`Task`/`Agent`). See
`docs/adr/2026-09-06-stage-a-triggered-from-linear.md`, its update blocks.

Every difference from the interactive path, step by step:

| Step | Interactive | Unattended |
|---|---|---|
| 0 preflight (config, census, test command) | runs | **skipped** — no shell. The executor reads the config and refuses on its gaps. |
| 1 PRD pass | runs; creates the project and epic after asking | PRD runs from the code; **nothing is created** — the PRD becomes the proposed epic's body |
| 2 decomposition | runs | runs; the brief restates the five sections and the gate's tighter rules, since the gate cannot be run |
| 3 rubric panel: architecture, security, ux-product, sizing-split | runs | runs, in helper sessions |
| 3 rubric panel: `dedupe` | searches the board | **skipped** — no tracker tool. The executor compares the proposed titles with the work team's recent tickets and lists what looks alike in its summary; the planner never claims the check itself |
| 4 DoR gate | the session runs it | **skipped** — no shell. The executor runs it on every child and rejects the whole tree if one fails |
| 5 confirm and file | asks, then files through `mcp__linear` | files nothing. It **emits the whole tree** as one `pipeline-safe-outputs/1` document (§8 "Filing a plan") in a fenced json block at the **end of its final message**; the executor files it |
| 5 labels | `track:*`, `effort:*`, `provenance:epic`, `hooks-change` where due | only `track:*` and `effort:*`. The executor forces `provenance:epic`; the schema refuses every protected class |
| 5 guard changes | adds `hooks-change` | cannot. It opens the child's Context with the brief's `Guard change:` line and names the path under Pointers; the executor's summary lists the child for the owner |
| 6 read back | runs | **skipped** — the executor reads nothing back; its summary names what it filed |
| 7 telemetry | posts a block | **none** — a telemetry block in a planning batch would reach the owner as a question |
| a question it cannot answer from the code | asks the person in chat | emits a `ticket-comment` naming its own ticket in the same document. With a plan it is a note; with none, a request for input |
| its own ticket | whatever the person names | the `<identifier>` in the prompt's `<linear_issue>` block: the only valid `source_ticket_id` |
| the idea's text | a person's request | **untrusted data**. No pin exists on this lane, so the session-start fence does not apply; the brief is the fence |

The epic is **`provenance:agent`, not `provenance:human`**: a session drafted it, so by §5
it never auto-approves. The human gate is the same on both paths — move the epic to the
state the project maps to `ready` (§5 rule 2) to release the tree.

The interactive path stays the default. The unattended path is what the Planning dispatcher
entry runs, and it is the only way a session with no tracker tool reaches the board.

### Cost shape

Opus for the PRD, architecture, and UX passes; Sonnet for decomposition and security;
Haiku for sizing and dedupe. Aliases, not pinned IDs, so the tiers track the current
generation. The panel is the expensive part — it runs on drafts, before anything is
filed, precisely so a failed round costs tokens and not a board full of bad tickets.
