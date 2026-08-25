# Ticket template

**The shape every dispatchable ticket has.** One template, four readers:

| Reader | Uses it for |
|---|---|
| A person | Scanning a ticket in under a minute and knowing what "done" means |
| `/plan-epic` | The shape it decomposes into; the rubric panel critiques against it |
| `scripts/check_ticket_dor.py` | The Definition-of-Ready gate — section names are parsed **from this file** |
| The dispatcher | Snapshotting `acceptance_criteria` and `out_of_scope` into the pin (`docs/PIPELINE-CONTRACT.md` §3) |

Because the DoR gate parses its canonical section list out of the block below, **this
file is the single source of truth for the section names.** Renaming a heading here
changes the validator; the validator's `--selftest` fails if its baked-in fallback list
and this file ever disagree, so the two cannot drift silently.

> This template is part of the **optional** agentic delivery pipeline
> (`docs/PIPELINE-CONTRACT.md` §2). A project with no `delivery.json` can still use the
> shape as a plain writing convention — the DoR gate degrades to the checks that need no
> config and says which ones it skipped.

---

## The template

Paste this into Linear's issue-template feature (Team settings → Templates), or let
`/plan-epic` fill it. Everything between the markers is the template body.

<!-- BEGIN TICKET TEMPLATE -->

```markdown
## Context

<Why this ticket exists, in 2–4 sentences: the user-visible problem or the
constraint being satisfied, and what currently happens instead. Link the epic
and the PRD document rather than restating them.>

## Acceptance criteria

- [ ] <A statement that is true or false about the finished system, not a task.
      Prefer mechanically checkable: name the command, endpoint, file, or
      observable state. e.g. "`npm test` covers the expired-token path and
      passes">
- [ ] <...>

## Out of scope

- <A thing a reasonable reader would assume is included, and is not. This is the
  scope fence the session is held to — the dispatcher copies it into the pin.>

## Test plan

- <How the change is proven, in the order someone would actually run it: the
  command(s), then the manual check if one is unavoidable. "Existing suite
  passes" alone is not a test plan for new behavior.>

## Pointers

- `<path/to/real/file.ext>` — <what is there and why it matters here>
- `<path/to/another>` — <...>
```

<!-- END TICKET TEMPLATE -->

**Sections are required, exactly once, in that order.** An empty section is a failure,
not a shortcut: a ticket with no Out of scope has no scope fence, and a ticket with no
Pointers is a ticket nobody read the codebase to write.

---

## Fields outside the description

| Field | Rule |
|---|---|
| Title | One line, ≤ 90 characters, no trailing period, no ticket ID prefix (Linear adds it). Imperative: "Refresh tokens before expiry", not "Token refresh". |
| `effort:S` \| `effort:M` \| `effort:L` | Exactly one. Selects `budgets.perEffort` (contract §1). An epic-decomposition agent may *propose* it; a human confirms it at approval. |
| `track:*` | At least one. Workstream routing. |
| `provenance:*` | Exactly one, matching the full provenance value (contract §5). For epic-decomposed work: label `provenance:epic`, full value `epic/<EPIC-ID>` carried by the parent link. |
| Project | Required. The project holds the PRD document and the whole tree. |
| Parent | Required when provenance is `epic/<ID>` — the parent **is** the epic named in the value. |
| State | New tickets land in `linear.stateIds.raw`. **Only a human moves a ticket to `ready`.** |
| `agent:*`, `blocked:capacity` | **Never set by a session.** These are dispatcher-owned lifecycle labels (contract §6); a session setting them is a session editing its own supervision. |

---

## What the DoR gate checks

```bash
python3 scripts/check_ticket_dor.py --json tickets.json
```

Errors (exit 1 — the ticket is not ready):

- Every section present, exactly once, in canonical order
- Acceptance criteria has at least one real checklist item
- Title well-formed; no unfinished-draft residue — `TBD` and unfilled tokens anywhere,
  `TODO`/`FIXME`/`XXX` outside a code span (backtick one you mean literally), and no
  section left as this file's verbatim `<Prose prompt…>`
- Exactly one `effort:`, at least one `track:`, exactly one `provenance:`, and no
  `agent:*` / `blocked:capacity`
- Every label resolves to a non-empty ID in `linear.labels.ids` (when `delivery.json` is present)
- Project linked; parent linked when provenance is `epic/<ID>`; provenance value and label class agree
- State is the raw/backlog state (when `delivery.json` is present)

Warnings (exit 0 by default, errors under `--strict`):

- Out of scope or Test plan empty
- No acceptance criterion contains an inline-code span (nothing mechanically checkable)
- Pointers empty, or a pointer path does not exist in the working tree. Paths are
  repo-relative: an absolute path or one that `..`-escapes the root counts as missing,
  because a pointer names a file in *this* tree

The gate refuses outright (exit 2) if `delivery.json` declares a contract `version` it
does not implement — contract §1: a reader that does not recognize the version refuses
rather than guessing, since guessing here would silently *skip* the label and state rules
and report green.

`--strict` is what `/plan-epic` runs. The warning tier exists so the gate is still usable
on hand-written tickets and on ticket types where a pointer genuinely does not apply.

Run `python3 scripts/check_ticket_dor.py --selftest` (or `npm run test:dor`) to prove the
gate itself still works — it includes the drift check against this file.

---

## Input shape

The gate reads a single ticket object, a bare list, or `{"tickets": [...]}` — from a file
or stdin. This is **the gate's own normalized shape**, close to but not identical to the
Linear MCP tools' arguments:

```json
{
  "id": "ENG-123",
  "title": "Refresh tokens before expiry",
  "description": "## Context\n…",
  "labels": ["track:platform", "effort:M", "provenance:epic"],
  "projectId": "9f1c0e4a-0000-4000-8000-000000000001",
  "parentId": "ENG-100",
  "stateId": "3b7a2c11-0000-4000-8000-000000000002",
  "provenance": "epic/ENG-100"
}
```

`id` is `null` for a draft that has not been filed yet — that is the normal case, because
**drafts are gated before they are created**, not after.

Mapping to Linear, for the round-trip run `/plan-epic` does on what actually landed:

| Gate field | From Linear |
|---|---|
| `projectId` | the issue's `project` |
| `stateId` | the issue's `state` |
| `parentId` | the issue's parent, by ticket identifier (e.g. `ENG-100`) |
| `labels` | the canonical label keys |
| `provenance` | **not a Linear field.** Contract §5 rule 4 puts the class in the `provenance:*` label and the ID in the parent link, so a ticket read back from Linear has no `provenance` key — the gate reconstructs `epic/<PARENT-ID>` from the label class plus the parent. Supplying it explicitly (as a draft does) makes the gate check the two against each other instead. |

---

## A filled example

```markdown
## Context

Access tokens are refreshed only after a request fails with 401, so every session
shows one failed request at the hour mark. `src/auth/session.ts` refreshes
reactively in the response interceptor; there is no proactive path.

## Acceptance criteria

- [ ] A token within 5 minutes of expiry is refreshed before the request is sent
- [ ] `npm test` covers the near-expiry and already-expired paths and passes
- [ ] No 401-triggered refresh remains in `src/auth/interceptors.ts`

## Out of scope

- Refresh-token rotation and reuse detection (separate ticket)
- Anything touching the login flow itself

## Test plan

- `npm test src/auth` — new near-expiry cases
- `npm run e2e -- --grep session` — no failed request at the hour mark
- Manual: sign in, idle past expiry, confirm the network panel shows no 401

## Pointers

- `src/auth/session.ts` — the reactive refresh that this replaces
- `src/auth/interceptors.ts` — where the 401 retry lives today
- `src/auth/session.test.ts` — the suite the new cases join
```
