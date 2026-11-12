# A prerequisite is filed under an approved plan, only when the bridge asks

- **Status:** Accepted. The owner said yes on 12 Nov 2026 (KIT-611) and set the runaway cap at 15 per plan per day.
- **Date:** 2026-11-12

## Context

A started piece of an approved plan can write a plan note (KIT-320). One kind says work no piece covers is
missing. The tech lead judges the note, and the bridge's own code decides what follows. Until now a missing
prerequisite always asked the owner, because no path could file it.

Every filing path refused on purpose. The plan executor's rule 1 sets each child's parent only to the epic it
created in the same run: a session that could name an already-approved parent could have its children
auto-approve. The finding poller links a finding as `related`, never as a child. The bridge's own follow-up carries a
team, a title and a description, and nothing else.

The owner's direction of 11 Nov is to be as autonomous as possible. He hears about a change from a spec he gave, or a
decision that truly needs him.

## Decision

Rule 1 is relaxed for one path, which the bridge gates.

1. **One mode.** The plan executor gains `--prerequisite`. It files exactly one ticket: under the approved epic, in the
   epic's team and project, in the team's backlog, with `provenance:agent` and nothing else set. One `blocks` edge
   runs from it to the piece whose note asked. It posts no comment; the bridge says what happened.
2. **The parent comes from the bridge's record alone.** The bridge writes the request: the epic and the piece by the
   ids its approval record keys them by, and the note's two fields. The executor reads that record itself, from the
   bridge's state file. It refuses an epic the record does not hold, a piece that is not one of its approved pieces,
   an identifier (what a note's text names), and an epic the tracker now places on another team. It also refuses a
   piece the tracker no longer lists under that epic.
3. **The bridge decides when.** A prerequisite is filed only for the piece whose note asked, an approved piece of
   that plan, not a ticket the idea names, with the idea read. The plan must also hold fewer than 15 changes made
   without the owner in the last day. Anything else asks him, as before.
4. **A runaway cap of 15 per plan per day**, counted twice. The bridge counts its ledger. The executor counts the
   epic's `provenance:agent` pieces made in the last day, in the tracker. A piece whose time cannot be read counts.
   The 16th is refused, and the owner is asked.
5. **It waits for a person.** `provenance:agent` is never approved by any tier (§5), and the bridge starts only the
   pieces the owner approved. So a filed prerequisite starts when he hands it over. The piece that needed it waits.

## Why rules 2 to 5 still hold

- **Rule 2.** The ticket is created in the backlog. The mode moves nothing: its only tracker calls are two reads,
  one create and one edge, pinned from its own source.
- **Rule 3.** Nothing is made until the whole request is checked: the request, the record, the tracker's view of
  both, the cap and the backlog state. An edge that fails after the create is a partial filing, named in the
  verdict. A retry finds the receipt and makes only the missing edge. The readiness gate judges what an approval may
  release. A `provenance:agent` ticket is never released by one, so, like the epic itself, it is a draft a person
  makes ready, and the gate does not run on it.
- **Rule 4.** A refusal exits 3, a failure 2, a filing 0, each with its own verdict class.
- **Rule 5.** It runs on the dispatcher host, as the bridge's account, from the bridge's own clone. The approval
  record is refused when a session could have written it: a link, a file another account owns, one others may
  write, one in a folder others may write, or one inside a git working tree.

## Consequences

- The owner no longer files a prerequisite by hand. He reads one ledger line: what was filed, under which plan, and
  that cancelling it undoes it. He hands it over when he wants it.
- A note with a credential's shape is never written to a request. It asks, as does every refusal and failure, in
  fixed words. The executor's own reasons go to the bridge's log.
- Rejected: the bridge filing the ticket with its own key. Its tracker writes are pinned at five, and a parent,
  label, project or relation is none of them.
- Rejected: filing it as a `related` finding. It would not be part of the plan, and nothing would hold the piece.
- Rejected: starting the prerequisite without the owner. That would start `provenance:agent` work no person
  approved, a further relaxation this decision does not make.

## Verified

- `npm run test:plan-executor` pins the mode's cases. An epic id taken from a note's text is refused four ways, and
  nothing is read or filed. The 15th files and the 16th is refused. A day-old one does not count, and an unreadable
  time does. Rules 2 to 5 each have cases, and so does the partial filing and its retry.
- `npm run test:bridge-modules` pins the wiring. The request names the record's plan when the note names another.
  Each rule that asks runs no filing. A refusal asks in fixed words. A run of the real executor reads the bridge's own
  state file and request, and its verdict comes back.
