# The acceptance poller: when a check may start

*Slices 1 and 2 of 5 (KIT-551 and KIT-552, part of KIT-324). They decide and read. They start nothing yet.*

The acceptance check looks at a finished plan's app in a real browser, before the last piece
merges. The poller is the job that will start it. This first slice answers one question for each
plan: **may a check start now, and has it already started for this exact code?**

## The rule

A check may start for a plan when all of these are true:

1. Acceptance is on for the plan's repository.
2. Every piece but one is Done.
3. That one piece, the last, has a pull request that is **open**, **green** and **reviewed**.

"The last" is the one piece still open, wherever it sits in the plan's list. Pieces merge in any order.

Anything else is a named reason, never a silent no.

| Word | Meaning |
|---|---|
| `start` | All three are true, and no check has started for this head. |
| `acceptance-off` | The repository has no acceptance section. |
| `no-pieces` | The plan lists no pieces. |
| `pieces-pending` | More than one piece is not Done yet. |
| `all-done` | Every piece is Done. Nothing is left to check before merge. |
| `no-pr` | The last piece has no pull request yet. |
| `pr-not-open` | Its pull request is closed or merged. |
| `checks-red`, `checks-pending`, `checks-unknown` | Its required checks are not green. |
| `review-pending`, `review-declined`, `review-unknown` | Stage E has not cleared it. |
| `already-started` | A check already started for this head. |
| `start-unconfirmed` | A check was about to start for this head, and the job stopped before saying it did. |
| `start-failed` | Starting it failed. |
| `could-not-tell` | A fact is missing or unreadable. |

**Good:** `checks-unknown`. The job says it could not read the checks.
**Not:** treating an unreadable check as green.

## One check per head

The ledger keeps one row for each plan and **pull-request head**. A head that is in it never starts
again. A push gives a new head, which is a new row, and a new check.

The row is written **before** anything starts, with a temp file, a sync and a rename. If the job
stops between the write and the start, the row says `starting`. That row never starts again by
itself, and the next pass says `start-unconfirmed`. This is on purpose: a missed check is a
sentence in the morning, and a check run twice is a bill.

If the ledger exists and cannot be read, the whole pass stops. Starting from an empty ledger would
start every plan again.

A pass that can start things holds the ledger's lock file for its whole run. A second pass at the same
time does nothing and says so, so two passes can never start the same head. The read-only command takes no lock.

## Reading what the check found

After a check runs, the poller reads two things and puts them together.

- **The session's result**: one block, `pipeline-acceptance/1` (contract section 21).
- **The look tool's own record**: what actually ran in the browser, and what failed. The tool writes it
  where the session cannot.

The poller trusts the record for what ran and what failed mechanically. It trusts the session only for
what it judged on screen. The answer is one of three words.

| Answer | When |
|---|---|
| `found-nothing` | The session found nothing, and the record confirms every run it names passed. |
| `found-problems` | The session listed problems. Or the record has a failed run the session did not report. |
| `could-not-check` | Anything below. It is never read as a pass. |

`could-not-check` comes from any of these:

- The block is missing, does not parse, names another schema, fails the schema, is about another plan or
  another head, or repeats a problem id.
- The look tool's record folder cannot be read, holds a malformed record, or holds no record at all.
- A run could not run, or a run crashed, and the session said it found nothing.
- The session says it ran a check that the record does not hold.
- The session says it could not check, and gives its reason.

A run that failed, or could not run, and was then **done again and passed**, is cleared: the newest
record for that run wins. A record that says it crashed, failed or could not run but names **no run** is
different. A later run cannot be tied to it, so it is not cleared. It blocks a `found-nothing` until a
person clears the plan's look-tool records. This is on purpose: the safe side of "never a pass".

**Good:** `could-not-check: the look tool has no record for this plan`.
**Not:** a session that says "all passed" with nothing recorded.

Where the two disagree, the answer says so, by name: *the session says it ran home/desktop/dark; the look
tool's record holds no such run.*

A record the tool marked as the runner's alone, or as a base copy, is **mechanical**. A record marked
`session-code-in-runner`, or with no mark, is **not mechanical**: the answer says so, because the pass
then rests partly on code the session could change.

The session's own words (a problem's `what`, a `reason`) come back as they are. They are data. Whoever
puts them in a comment, a page or a request cleans them first (slices 3 and 4).

## Try it

The command is read-only. It writes nothing.

```
python3 scripts/pipeline_acceptance_poller.py decide --facts facts.json --ledger ledger.json
```

`facts.json` is fields only: ticket ids, a repository name, piece states, a pull-request number and
head, and closed words for the checks and the review. No ticket or pull-request text goes in it, and
none comes out. Its shape is `pipeline-acceptance-facts/1`; a later slice writes it from the
tracker and GitHub.

```json
{"schema": "pipeline-acceptance-facts/1", "plans": [
  {"plan": "ENG-100", "repo": "acme/app", "acceptance": true,
   "pieces": [{"id": "ENG-101", "state": "done"},
              {"id": "ENG-102", "state": "review",
               "pr": {"number": 7, "head": "<40 hex characters>", "open": true,
                      "checks": "green", "review": "reviewed"}}]}]}
```

## Test it

```
npm run test:acceptance-poller
```

## What comes next

Slice 3 is the second pass that
tries to disprove each problem. Slice 4 writes the verdict on the epic. Slice 5 sends the owner a
page. Until the rest and the installer step (KIT-326) are in, nothing runs this job.
