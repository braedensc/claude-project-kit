# The owner's yes in Slack starts a piece on our own runner

**Date:** 2026-11-24 · **Status:** Accepted on merge (the owner chose option (b) on 24 Nov, KIT-961) ·
**Context:** `feat/kit-961-owner-chat-start-on-runner`

## Decision

On a project whose coding lane runs on our own runner, the tech lead takes `start`, `retry` and
`resume` again. The bridge asks its own question. A yes from the owner is written to the bridge's
record (`slack_starts` in its state file), with the Slack message it came in: workspace, channel and
ts. Nothing is written to Linear.

This is a second approval source beside the owner's move into Todo. It is called `slack-start`
in the queue's judge and in the run's record.

- **A start goes the queue's way, as a move into Todo does.** The queue's reader reads the record,
  and reads each ticket it names as a candidate in any state. The pool admits the ticket on it.
  The queue's order then starts it inside the working window, a free lane and the day's points.
  The runaway stops hold it, and the ledger row is written first. The request is the queue's own
  `code` start. The runner judges it with that same pool, its one rule for a code start.
- **A resume goes the same way.** `retry` and `resume` ask the runner to resume the ticket's newest
  stopped code run. The queue's own pass files that request inside the window, after the stops,
  with a ledger row, as its limit-stop resumes are filed. The runner's existing resume path judges
  it.
- **The answer is said where the yes was given.** So is a hold the queue's judge puts on the
  ticket, once for each reason. The runner's answer closes the record.

## Why

The runner's one authority for a code start is the queue's judge. A Slack start needed a second
source, and the source had to plug into that judge, never sit beside it.

- **Rejected: words only (option a).** No new authority, but the owner still leaves the chat to
  start work.
- **Rejected: the tech lead moves the ticket into Todo (option c).** The bridge writes to Linear with
  the owner's key. Its move would read as his, so the judge could not tell a job's move from his.
- **Rejected: the request carries the approval.** The runner's store says a caller's word is not
  the authority, because any process on the account that passes the walls can name any caller.
  A request that carried its own approval would make the request the authority.
- **Rejected: the runner applies the window and the day's limits itself.** That makes two deciders
  of what starts now. Both would fill the same lanes, and the ledger would have two writers.

**No new trust root.** The record lives in the same file, read by the same reader, as a plan the
owner approved in Slack. The pool admits a plan's Slack approval only from a member in the queue's
`owner_slack_user_ids`. It admits this one by the same check, `slack_owner`.

## The rules, and what proves each

Each rule has a selftest case and a mutant that turns the battery red.

1. **Only the owner's Slack user approves.** The bridge first runs KIT-221's checks on the sender:
   no bot, no app, no guest, no edit, no other workspace. Then the yes must come from a member in
   `owner_slack_user_ids`. Anyone else's yes is refused in words, and the question stays open for
   him. The pool checks the same list again when it judges. A session, a ticket comment and the
   tech lead's own words never reach the record. Cases: `ho961-a-yes-from-anyone-but-the-owner…`
   and `ho961-an-edited-yes-records-nothing` (bridge); `KIT-961 …a yes from a Slack member who is
   not the owner` (pool); `…another member's yes is no approval` (runner).
2. **One message is at most one start.** The bridge marks each message handled, so a message read
   again acts on nothing. The record is keyed by the ticket, and the runner's answer closes it. A
   closed record approves nothing. The runner also refuses a second code start of a ticket on the
   same `slack-start` approval: the same member at the same second. It reads its own run records
   for that. Cases: `ho961-the-same-yes-read-again…`, `…a record the runner has answered starts
   nothing again`, and `…the same yes asked again starts nothing` (runner).
3. **The approval names the message and the ticket.** The record holds the ticket's id and key, the
   workspace, the channel and the ts. The approval's time is the ts's own second. The pool refuses a
   record that names another ticket, has no message, or whose time is not its message's. It also
   refuses one given before the ticket's last change by anyone but the owner. Cases: `KIT-961 …a
   record with … names no message` and `…someone else's change after the yes holds it` (pool).
4. **The same limits as a move into Todo.** The record only makes the pool admit the ticket. A
   ticket any rule holds without the owner's `start` tap stays held: not ready, never started, or
   waiting inside a plan. The window, lanes, day points and runaway stops apply unchanged. Cases:
   `KIT-961 …a ticket held without a tap stays held` (pool) and `KIT-961 …a closed window starts it
   no sooner` (starter).
5. **The request goes in through the store's walls.** The bridge's queue pass files it as the
   `queue` caller, the bridge's own, with the store's own `ask()`. No session can write the store or
   the bridge's state file: both sit outside every git working tree. Cases: `runner-961-a-start-on-
   the-owners-yes…` and `runner-961-a-resume-he-said-yes-to…` (bridge).
6. **A project on the old lane keeps today's path.** The runner's own `lanes.code` for the project
   is the switch. A yes asked before the switch moved acts on nothing, and says so. Case:
   `ho961-the-runners-own-lane-switch-moves-a-project-back…`.
7. **Nothing is dropped in silence (contract §13).** Every refusal is posted. So is the runner's
   answer, and a hold the queue's judge puts on an approved ticket, once per reason. Cases:
   `ho961-each-runner-check-is-said-once…`, `runner-961-a-refusal-of-his-yes…` and
   `runner-961-a-yes-the-queue-holds…`.

## Known limits

- **Same account, same walls.** The record has the same protection as the plan record beside it.
  A process on the bridge's account that is outside every sandbox could write either. This ADR adds
  no new way in, and closes none.
- **A review of a run started this way is refused.** The runner lets a review follow only a code run
  started by the owner's move or creation (KIT-644). A plan approved in Slack has the same gap today.
- **The queue does not yet count runner runs in its lanes.** That is KIT-723's to change.

## Verified

The pool, the queue's reader and starter, the runner and the bridge each run cases for the rules
above. Each module's mutants of those rules turn its battery red: owner, ticket, message, change,
answered and tap in the pool; record reading in the reader. Six more mutants were each run in a
throwaway copy of the branch, and each turned its battery red:

- anyone's yes taken for the owner's (the bridge);
- one yes let start a second run (the runner);
- the runner's answer never said, and the record never closed;
- a resume never asked by the queue's pass;
- a start already asked said to be held;
- a yes from before the lane switch still recorded.
