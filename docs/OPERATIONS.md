# Operations: what to do when the pipeline stops

*Written 2026-11-10. For an agent or a person debugging a pipeline that is not doing what it should. Every entry cites where it came from. Where nothing has been observed yet, the section says so.*

This is a **symptom → cause → check → who acts** guide. It covers the delivery pipeline as a running system. It does not repeat the guides it points to:

| For | Read |
| --- | --- |
| Installing and configuring one part | That part's guide: `docs/STAGE-E-OPERATOR.md`, `docs/STAGE-A-OPERATOR.md`, `docs/NOTIFIER-OPERATOR.md`, `docs/BRIDGE-OPERATOR.md`, `docs/QUEUE-OPERATOR.md` |
| A leaked key, or any security incident | `docs/SECURITY.md`, *Runbooks* |
| The story of how a gotcha was found | `docs/LESSONS.md` |
| The formats parts agree on | `docs/PIPELINE-CONTRACT.md` |
| Setting up the dispatcher itself | The dispatcher's own setup runbook, which lives outside this repository |

**Who acts.** Each entry ends with one of two words.

- **Agent**: ordinary debugging. A session can read the files and run the check.
- **Human**: it needs a credential, a hosted page, an installer, or a file no session may edit.

---

## 1. Start here

Ask one question first: **did nothing happen, or did the wrong thing happen?** They have opposite paths.

- **Nothing happened.** No comment, no check, no message. Go to section 2. This system's usual failure is silence, and three separate sources say so (`docs/PIPELINE-CONTRACT.md` §13, `docs/TESTING.md` § *Workflows are code*, and the silent-failure tickets cited below).
- **The wrong thing happened.** A bounce that should not have fired, a wrong comment, a guard block you think is wrong. Go to section 3, then section 5.

**The rule behind section 2.** A component that can do nothing must say *which* nothing it did: *nothing to do*, or *could not do it*. Both look the same from outside: no output, no error, nothing red (`docs/PIPELINE-CONTRACT.md` §13).

---

## 2. The silent-failure index

Every known way this system reports success, or says nothing, while doing nothing. Each row has the check that tells it apart.

| # | Symptom | Cause | Check | Who | Source |
| --- | --- | --- | --- | --- | --- |
| 1 | A PR shows no checks at all, not even a failing one | The PR conflicts with its base. GitHub builds no merge, so it runs no checks. A conflicting PR is *untested*, not red | `gh pr view <n> --json mergeable,mergeStateStatus`: `CONFLICTING` or `DIRTY` | Agent: merge the base in, resolve, push | The silent-failure ticket KIT-22 (#1); `CLAUDE.md`, *Stop hook*; `docs/STAGE-E-OPERATOR.md`, *the conflict loop* |
| 2 | A workflow "did not run". No job, no annotation, no check run | `startup_failure`: GitHub could not build the job graph. The message exists only on the run's HTML page | Open the run on GitHub. Locally: `npm run lint:workflow-calls` | Agent for the lint. Human to read the hosted page | KIT-22 (#2); `docs/TESTING.md` § *Workflows are code, and one of their failures is silent* |
| 3 | A check is green, but nothing was reviewed | A review workflow found no dispatch pin and declined, exiting green. The checks list cannot tell *reviewed, found nothing* from *could not review* | Read the check's log for the decline line. In the first project, two of four runs declined green | Agent to read. Human to decide whether the workflow stays active | KIT-22 (#3); TOD-112 (the first project, 2026-08-26, which staged its cloud review off); `docs/adr/2026-08-26-where-the-review-session-runs.md` |
| 4 | Safe-outputs reports "no requests", but the session wrote some | The artifact download is `continue-on-error`. A permissions or network failure gives the same state as *the session wrote nothing* | Compare the run's artifact list with the verdict. A real "none" has no artifact; a failed download had one | Agent | KIT-22 (#4) |
| 5 | A PR is red and nobody was told | The failure alert filtered on `head_branch == 'main'`, so a red PR raised nothing | Check what the alert filters on | Agent | KIT-22 (#5) |
| 6 | Safe-outputs "passes" with a dead label id | The validators checked *non-empty string*, not *live reference* | Look the id up in the tracker | Agent | KIT-22 (earlier instances) |
| 7 | Safe-outputs does nothing, and the run is green | `LINEAR_API_KEY` is absent. It no-ops entirely | Check that the secret exists in the repository settings | Human | KIT-22 (earlier instances) |
| 8 | A review is "clean" with zero findings, but the reviewer could not judge the change | An empty findings list on its own is published as a clean review. The reviewer is told to set `blocked` when it cannot judge; one that does not reads clean | Read the summary of every clean review before merging | Human | `docs/STAGE-E-OPERATOR.md`, *Step 2* (the reviewer's instructions) |
| 9 | A PR never gets a review, and nothing says so | The branch name has no ticket id, so the review poller skips it, forever | Check the branch name | Agent: rename the branch, or open a new PR | `docs/STAGE-E-OPERATOR.md`, *How it works, in one pass* |
| 10 | A review comment says part of a PR was not read | A PR over the size cap, or files held back. The comment carries a coverage marker saying *all*, *part* or *none* | Read the comment's marker line | Agent | `docs/STAGE-E-OPERATOR.md`; `docs/STANDUP.md` (files unread are named) |
| 11 | A hand-over to the dispatcher never started | The tracker sends each hand-over as one web call and retries it three times, the last after six hours. A Mac asleep longer loses it | `docs/CATCH-UP.md`: the catch-up job starts it late, once, after the retries end | Agent to read the heartbeat. Human to install the job | The dispatcher resilience ticket KIT-44 (#1); `docs/CATCH-UP.md` |
| 12 | A killed dispatcher posts nothing to the tracker, and the session looks busy forever | A kill emits no error activity. The abort is swallowed by design | Compare the ticket's last activity time with the process list | Human | KIT-44 (#2) |
| 13 | A terminal closed, and the session ended with no saved state | No hangup handler. Only interrupt and terminate are caught, so a hangup is as bad as `kill -9` | Look for a state file written before the close | Human | KIT-44 (#3) |
| 14 | After a hard kill, the next comment starts a brand-new session with no context | The resume handle lives in memory and is saved only at a graceful stop | Check whether the new session knows the worktree holds half-finished work | Human | KIT-44 (#4) |
| 15 | The dispatcher starts with zero sessions after a crash | Its state file is written in place, not through a temp file. A crash mid-write leaves broken JSON; the load swallows the error; the next save overwrites the evidence | **Copy the state file before restarting anything** | Human | KIT-44 (#5) |
| 16 | The local status check passes, but the public address is dead | A dead tunnel is unsupervised. Only one info line is logged | Probe the **public** URL, not localhost | Agent for the probe | KIT-44 (#6) |
| 17 | The health page is quiet, and a job is dead | The monitor runs on the same Mac. It cannot report that Mac asleep, off, or logged out, nor its own death. A pass that runs and does the wrong thing looks healthy | Read the monitor's own heartbeat. Older than about an hour: the watcher stopped | Agent to read | `docs/HEARTBEAT-MONITOR.md`, *What it cannot catch, first* |
| 18 | A health check shows `UNKNOWN` pass after pass | It could not look: no internet, a source unreadable. `UNKNOWN` never pages and never clears an alert | Run the check by hand and read which source failed | Agent | `docs/UPDATE-OPERATOR.md`, *The health watch* |
| 19 | Nothing starts through the queue | A read failed, the ledger could not be counted, or a runaway stop holds every start. A figure nobody can read is a stop, never a zero | Run the starter's preview, and read the bridge's own log | Agent | `docs/QUEUE-OPERATOR.md`; `docs/BRIDGE-OPERATOR.md`, *With the queue on* |
| 20 | A clean-up job removed nothing | The tracker did not answer, so no ticket reads as done. This is deliberate: the job leaves what it cannot judge, and exits 4 | Read the pass's output for the source it named | Agent | `docs/SWEEP.md` |

**Principle for the next one.** Every row above was found by switching something on and looking, not by reading code (KIT-22). Assume there is another, and that it will look like success. When you find it, add a row here with its source.

---

## 3. By stage

Only entries traceable to something read in a guide or seen happen. A stage with nothing verified says so.

### Planning (the idea gate)

| Symptom | Cause | Check | Who |
| --- | --- | --- | --- |
| A moved idea gets no comment within about five minutes | The planner job is not running, cannot reach the tracker, or planning is stopped | The planner's heartbeat; the stop file the job writes | Agent to read. Human to sign off the probe again |
| Planning stopped for every project | A planning ticket reached a coding setup, or the dispatcher was upgraded past the tested version. The job stops and waits for a signed probe | The stop file names the cause | Human |
| A plan was refused with reasons | The filing code refused a child that failed the readiness check, so the whole plan was refused | The planning ticket's note lists the reasons | Agent: fix the idea, then plan it again |

Source for all three: `docs/STAGE-A-OPERATOR.md`.

### Dispatch, and the dev loop

| Symptom | Cause | Check | Who |
| --- | --- | --- | --- |
| A ticket handed over never starts | See row 11 | `docs/CATCH-UP.md` | Agent to read |
| Work vanished after a ticket was tidied | Moving a ticket to **Done** or **Canceled** runs `git worktree remove --force` and a recursive remove. It destroys uncommitted work (KIT-44, the destructive trap) | Do not tidy a ticket after a crash. Rescue the worktree diff first | Human |
| Work vanished right after a merge, and nobody touched the ticket | The tracker's GitHub integration moves a ticket to **Done** seconds after its PR merges (measured: 2 to 3 seconds). Done runs the same forced remove as the row above. A merge while that ticket's session still runs, such as a bounce re-prompt mid-fix, removes the worktree under it (KIT-51) | Before you merge, check that the ticket's session has ended. GitHub shows nothing about a session | Human |
| A ticket moved, and no person or job moved it | The integration moves every ticket whose id is in a PR's title, branch or body: to the team's review state when the PR opens, and to **Done** when it merges (KIT-51) | Read the ticket's history: the move lands within seconds of the PR event. Then name only the ticket the PR completes, in all three places. Put a note about another ticket on that ticket instead | Agent |
| A session asked a question nobody answered | Nobody is there. A session must comment on the ticket and end; it never waits for a person (`docs/SESSION-BRIEF.md`) | Read the ticket's comments | Agent |
| A guard blocked a command | Either the guard is right, or it is spurious | Read the block message. A correct block names the rule; **never respell the command** (`CLAUDE.md`). If the block is wrong, say so and stop | Agent reports. Human decides |

### Review, bounce and the fix loop

| Symptom | Cause | Check | Who |
| --- | --- | --- | --- |
| A bounced session went quiet | No push for six hours after a fix was asked for. The driver adds a label and one comment, and spends no bounce | The ticket's top-level comment | Human |
| The fix budget is spent and a problem remains | The bounce budget in `delivery.json` ran out | The ticket's `needs-human` label and the PR comment | Human |
| A required check is red and nothing bounces | The check waits on a person, such as the `hooks-change` label. The driver sets those aside | The PR's checks | Human |
| A review was declined | A body over the size cap, or criteria not under an `## Acceptance criteria` heading as checklist items. A decline is a visible comment headed *NOT reviewed* | The PR comment | Agent: fix the ticket's shape. A declined review is tried again once the PR or its ticket changes |

Source for all four: `docs/STAGE-E-OPERATOR.md`, *Step 3* and *Step 5*, and `docs/PIPELINE-CONTRACT.md` §11 (the `agent:needs-human` row).

### Telemetry, and the schema gate

Awaiting first operation. The telemetry collector and the schema gate are tested against fixtures only. No failure here has been observed running, and none is guessed. Add rows when one is. See `docs/PIPELINE-CONTRACT.md` §4 and §10.

### The tech lead, the bridge and the queue

| Symptom | Cause | Check | Who |
| --- | --- | --- | --- |
| The DM went quiet | The bridge stopped, or the tech lead's session is busy. The clerks watch each other, so a stopped bridge pages | `python3 scripts/pipeline_bridge_setup.py verify --conf <bridge.conf>` | Agent to read. Human to restart |
| *The tech lead hasn't answered yet* every time | The chat bot is not in the back room | Invite it to that channel | Human |
| A `yes` acted on nothing | The question expired after 24 hours, was edited or deleted, or its terms changed | The bridge's reply says which | Agent |
| No stand-up arrived | The status file is missing or over an hour old, or the rundown is more than three hours late | `python3 scripts/pipeline_standup.py preview --kind morning` | Agent |

Source: `docs/BRIDGE-OPERATOR.md`, `docs/STANDUP.md`, `docs/HEARTBEAT-MONITOR.md`. None of these has run live end to end for long; each guide's *What is not proven* lists what is still open.

---

## 4. Recovery rituals

**The destructive trap.** Moving a ticket to Done or Canceled deletes its worktree, uncommitted work included (KIT-44). After a crash, leave the ticket where it is until the work is rescued. A merge moves the ticket to Done on its own, so do not merge that ticket's PR either until the work is rescued (KIT-51).

**The safe order after a dispatcher crash** (KIT-44; the dispatcher's setup runbook, *Recovery*):

1. Stop any orphaned child processes first.
2. **Copy the state file.** A restart can overwrite the only evidence (row 15).
3. Check the state file is valid JSON.
4. Rescue each worktree's uncommitted diff.
5. Only then restart.

**After you merge a change to the kit.** The jobs run from their own copy. Run the one command (`python3 scripts/pipeline_install.py`) and read what it says. `docs/UPDATE-OPERATOR.md` explains each row.

---

## 5. What to look at first, and what lies

| Look at | It tells you | It can lie when |
| --- | --- | --- |
| A job's heartbeat file | When it last ran and how it ended | It never says the Mac slept. A job that runs every pass and does the wrong thing looks healthy |
| The status file | Where every plan, piece and PR stands | It is rewritten every 15 minutes. Read its `written_at` first |
| The bounce ledger | How many bounces were spent | Only the driver writes it. Comments in the thread are a record, never an authority |
| A check's colour | A required check's result | Rows 1 to 4 above: green can mean nothing ran |
| The monitor's silence | Nothing | Rows 17 and 18 |

---

## What is not proven

- **That this list is complete.** It holds only what was read or seen. The pipeline has run unattended for a short time. KIT-46.
- **The telemetry and schema-gate section.** No failure observed. KIT-46.
- **The three dispatcher unknowns** from the resilience ticket: whether the tracker retries a failed hand-over and for how long, whether an idle session stays "working", and whether a non-interrupt kill orphans the child. KIT-44.
- **That each *Check* column command still works** as the guides change. Run it before relying on it. KIT-46.
- **Every event the tracker's GitHub integration acts on.** Two moves were measured from ticket history: a PR opening and a PR merging. A draft PR, a review request and a PR closed without merging were not seen. The team's Git automation settings page says. KIT-51.
