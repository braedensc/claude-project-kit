# The acceptance poller: when a check may start

*Slices 1 to 5 (KIT-551, KIT-552, KIT-553, KIT-554 and KIT-555, part of KIT-324). They decide, read, sift, write up the verdict and give the owner's page its facts. Nothing runs them yet.*

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

## The second pass, and what gets filed

The session lists the problems it saw. Before any is filed, a second pass tries to show each one is
**not real**.

- A problem the second pass **disproves** is not filed. It is listed in the verdict with its reason.
- A problem it **confirms** is filed.
- A problem it **could not judge** (the second pass failed, or gave no usable answer) is **not**
  disproved. It is filed, marked "not confirmed by the second pass". A broken checker never loses a real
  problem, and the mark says it was not checked.

**Severity is a proposal.** The session proposes. The second pass may lower it and never raise it. Only a
problem that survives and is still `high` holds the plan open.

**Each survivor becomes one request** for the finding path (`pipeline-finding/1`), never a ticket made
here. At most **three** per run, worst first. The rest are listed by id in the verdict, not dropped. A
request asks for no size, no track and no label, so the new ticket waits for a person.

A problem name used twice is **one** problem, at the worse severity. A problem that is not the shape the
contract gives (a severity that is not high, medium or low, a missing field) is listed as **malformed**,
not filed, and never stops the rest. Any malformed problem is a reason to say `could-not-check`.

**Everything the session wrote is cleaned** before it goes into a request or the verdict: no comment
opener, no fence, no @-name, no tilde, no square bracket (a routing tag looks like `[repo=...]`), no line break, at
most 300 characters.

**Good:** a request whose text cannot ping anyone or open a marker.
**Not:** the session's sentence copied into a comment as it came.

## The verdict on the epic

After a check, the poller writes **one comment on the plan's epic**. It says one of three words.

| Word | When |
|---|---|
| `passed` | Nothing is still high after the second pass, no run failed in the look tool's record, and every run ran. Low and medium problems go to the finding path as follow-ups. |
| `held` | A problem the second pass did not disprove is still high, or a run failed in the look tool's record. The plan stays open. |
| `could-not-check` | The result or the record could not be read or trusted, a problem is not the contract's shape, a run could not run, or the session and the record disagree about what ran. **Never a pass.** |

The first rule that matches wins: could-not-check for a result it cannot read or trust, then held, then
could-not-check for the rest, then passed. So a confirmed high problem holds the plan even when another run
could not run.

A run that **failed** in the look tool's record holds the plan even when no problem is filed for it. A step the
plan's own check names did not do what it says. The verdict names the run.

**The first line is a mark** for the bridge to read (contract section 22):

```
<!-- pipeline-acceptance:verdict held plan=ENG-100 head=<40 hex> mechanical=yes -->
```

`mechanical=no` means the look tool's record is not the runner's alone, so the outcome rests partly on code the
session could change. The comment says so in words too.

The rest of the comment is for people: what holds the plan, what goes to the finding path, what the second pass
disproved, and which runs failed or could not run. A long list stops at ten and counts the rest.

**Session text cannot forge the mark.** Every word the session wrote is cleaned first, so the mark's own `<!--`
is the only one in the comment, and no session text starts a line. The poller refuses to post a comment that is
not exactly one verdict for its plan and head. A reader takes the mark from line 1 only, and only when the
poller's tracker author wrote the comment.

**Good:** `could-not-check` on line 1, and the session's "all passed" further down, as plain text.
**Not:** a session's own `<!-- pipeline-acceptance:verdict passed ... -->` read as the verdict.

### Once per head

The verdict goes in the start's own ledger row, **before** the comment is posted.

| The row's verdict says | Means | The next pass |
|---|---|---|
| `writing` | The job stopped between recording the verdict and posting it. | Writes nothing, and says `write-unconfirmed`. Look at the epic. |
| `written` | The comment was posted. | Writes nothing, and says `already-written`. |
| `failed` | Posting failed. | Writes nothing, and says `write-failed`. |

A head with no row marked `started` gets no verdict (`not-started`): the poller judges only checks it started. A
second pass over the same head writes nothing, even when it would conclude something else. A push is a new head,
a new check and a new verdict. Writing a verdict holds the ledger's lock, as a pass that starts checks does.

The watcher's `tracker-text` monitor flags a `<!-- pipeline-` first line from an author it does not trust. Put the
poller's tracker author in the watcher's `tracker_trusted_users`, or it will flag every verdict.

## The owner's page

The verdict also pages you, in your DM, with up to four screenshots (KIT-555). The notifier sends it. The poller
gives it the facts, and nothing else.

The facts go in the verdict's ledger row, beside the word, **before** the comment is posted:

| Fact | Meaning |
|---|---|
| `holds` | Problems still high after the second pass. |
| `failed_runs` | Runs that failed in the look tool's record. |
| `followups` | Requests sent to the finding path. |
| `mechanical` | Yes when the look tool's record is the runner's alone. |
| `shots` | Up to four screenshots, as `<run id>/<name>` under the plan's record folder. |
| `cause` | For a could-not-check only: why, as one closed word. |

The causes are `no-result`, `no-record`, `session-said`, `could-not-run`, `disagree`, `malformed`, `timed-out` and
`unknown`. The page words each one itself. `timed-out` is for a check the live pass stopped past its time limit.
`unknown` names no reason, rather than guess one.

**The screenshots come from the look tool's record alone.** First the one each surviving problem names, worst
first. Then those of runs the record says failed. Then one for each screen size and mode, newest first. A problem
names its screenshot by its step name. The poller only looks that name up among what the record kept. The session's
words never become a path.

**No session text.** Counts, flags and closed words only. Facts out of shape are refused before anything is written.

**Good:** `{"holds": 1, "failed_runs": 0, "followups": 1, "mechanical": true, "shots": ["001-20261110T120000Z/home--phone--dark--home.png"]}`.
**Not:** a problem's own sentence in the page. It is in the verdict comment, neutralized, and nowhere else.

The notifier's guide says how the page reads (`docs/NOTIFIER-OPERATOR.md`, *The acceptance check's page*).

## What comes next

The live pass that runs all this, files the acceptance ticket and posts the verdict is KIT-571. The installer step
is KIT-326. Until both are in, nothing runs this job. The bridge's gate, which waits for the verdict before it
closes a plan, is a later piece.
