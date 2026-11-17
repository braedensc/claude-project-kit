# Code review and its jobs: how it works, part by part

*Written 2026-11-11, from the code at that date. The set-up steps are in `docs/runbooks/code-review.md`. The teaching version is `docs/lessons/code-review.md`. The dense reference is `docs/STAGE-E-OPERATOR.md`.*

Read this once, end to end. Each part is short, and unfamiliar words are explained at the end.

A coding session opens a pull request. A second, read-only session reviews it. If the review finds something serious, the first session is asked to fix it, a capped number of times. Then the ticket comes to you, and you merge. This guide walks every part, plus six smaller jobs that ride along.

> **Where it stands on 2026-11-11: live.** The review jobs have reviewed real pull requests, sent fixes back and handed tickets over. The review comment under its own login is built and off by default. Several paths have run only in tests. The last section lists them.

---

## The one-line version

A session opens a pull request. Within a few minutes a job files a review ticket, and a read-only session answers with findings. The job posts them as one comment. The fix-round job reads that comment and the build status, and asks the coding session to fix what matters. It repeats up to a cap, then moves the ticket to your approval column. Nothing in this layer can merge.

---

## The parts, in order

### 1. The review job: finds pull requests and files a review ticket

The review job runs every 120 seconds by default. It asks the tracker what the session runner worked on, and takes the pull request from each ticket's links.

It reviews a pull request only when all of these hold:

- the branch name carries a ticket id;
- the pull request is not a draft and not from a fork;
- it has not been reviewed at this commit already.

A pull request whose branch names no ticket gets one comment saying it will not be reviewed. A pull request a person opened is never reviewed here.

### 2. The review ticket: the reviewer's whole world

The job files one review ticket on the coding ticket's own work team and hands it to the agent. The body holds a brief, the acceptance criteria, the severity threshold and the diff.

Every string it copies is cleaned first. Routing tags and fence tags in the diff are neutralised. The job writes one routing tag of its own, on the first line, and a routing label with the same name.

A diff over the size cap is reviewed in parts. The job withholds whole files, largest first, and files a second ticket for the rest. The comment says which files wait.

### 3. The route check: right after filing

On a work team, a failed routing read falls back to the coding setup, which can edit and run anything. So the job checks. The session runner posts a routing note at the start of each session. The job reads the earliest one. It waits up to two minutes, or five and a half while a repository setup hook runs.

- The note names the review setup: the review is collected.
- Anything else, or no note: the job cancels the ticket, writes the evidence and pages you.

Reviews pause for that repository until you sign the routing probe again. Each waiting pull request gets one comment saying reviews are paused and why.

### 4. The reviewer: a session that can look and not touch

The session runs in a sandbox with a fence of removed tools. It has no shell, no file writes, no fetch, no scheduling and no tracker tools. It reads files and answers.

Its final message ends with one block of structured text: a summary, findings with severities, and a field for "I could not judge this". The reviewer's brief tells it that trying to act is itself a finding.

### 5. The verdict: one comment, never an approval

The job reads the block from the reviewer's last message only. It checks the whole document, then posts one comment on the pull request. The comment shows which criteria it judged against and a one-line reading, such as "a fix pass would be started".

It never posts an approval or a "request changes" review. A review that could not happen says "NOT reviewed", loudly.

A declined pull request is not declined for good. The job reviews it again when its head moves or its ticket's criteria change. After ten retries it stays declined and logs a `HELD` line.

### 6. The fix-round job: sends findings back

The fix-round job runs every 120 seconds by default. It reads each review and the build status. It sends the coding session back to work only when all of these hold:

- the review found something at or above the threshold, or a required check is red;
- the review is of the commit the pull request is on now;
- the pull request has budget left.

The budget is `maxBounces` in the repository's `delivery.json`. A repository with no such file gets advice only: the comment still says a fix pass would start, but nothing starts.

A check that waits on a person, such as the guard-change label, is set aside. It never causes a fix round.

### 7. A fix round: the same session, resumed

The job writes a row to its ledger first. Then it replies in the coding ticket's own session thread, with the findings inside an untrusted-text fence and a fixed instruction block. The session runner resumes the same session, in the same worktree.

When the pull request's head moves, the review job reviews again. No push means no re-review and no cost. If the session cannot be resumed, the job falls back to a fix ticket.

### 8. The hand-over: your turn

When nothing is left to send back, the job writes one `concluded` row. It moves the coding ticket to your needs-approval column. It leaves a ready-to-merge comment, or a spent-budget comment that also adds the `agent:needs-human` label.

If a delivered fix round sees no push for six hours, the job leaves one comment on the ticket and the same label. It spends no round.

**After the hand-over the pull request is yours.** A later red check or fresh finding gets one comment, and no re-prompt. You merge, or you reply in the session's thread to hand it back.

### 9. Side findings become backlog tickets

A session sometimes notices something true and out of scope. It must not widen its diff or file a ticket. It leaves one comment on its own ticket holding a `pipeline-finding/1` block.

The side-findings job runs every 300 seconds. It files each as a backlog ticket in the same team, with the agent-filed label, you as subscriber and no assignee. It strips any protected label the session named.

It files at most three findings per source ticket, ever, and twenty per pass. The ticket never starts a session on its own. It leaves one comment on the new ticket so you get paged.

### 10. The catch-up job: re-sends a hand-off the session runner missed

The session runner hears about work only through the tracker's messages. If the Mac sleeps past the tracker's retries, a hand-off is lost and nothing says so.

The catch-up job runs every 15 minutes. It finds a delegation nobody answered that is older than 7 hours 30 minutes and younger than a week. It replies once in that delegation's own thread, and the session starts.

It sends at most three replies a pass, a minute apart, and one per delegation, ever. It is installed only after you read its first count.

### 11. The status job: one file of where every plan stands

The status job runs every 15 minutes. It writes one file of facts on every plan: pieces, pull requests, checks, reviews and merges. Ids, states, counts, times and links only.

The tech lead and the stand-up read that file, so neither holds a GitHub token. A source the job could not read is said in the file, never guessed. Two awake hours of blindness pages you once.

### 12. The job-health monitor: says when a job stops

Every job writes a small heartbeat file on every exit. The heartbeat monitor reads them every 30 minutes and judges each: `ok`, `stale`, `failing`, `missing` and a few more. When health changes it comments once on one ticket you chose.

It is slow on purpose: a job counts as stale after about twice its interval plus an allowance. It cannot see the Mac asleep or itself dead. **Silence is not health.**

### 13. Reviews under their own login

Without this step, reviews post under your login, which every session on the Mac can also use. A review comment then proves nothing about who wrote it.

The step makes a GitHub App that may write pull-request comments and nothing else. You click GitHub's own Create button. The key goes straight into the role account's home. It is built and off until you run it. If the app's token fails, the review job posts nothing that pass rather than post as you.

### 14. The conflict loop: fixes your pull requests when main moves

A pull request that conflicts with its base skips its required checks, so it can look green. A GitHub workflow runs every 20 minutes, labels the conflict and posts a fix request. It allows three per pull request, ever.

Who answers depends on who owns the session. Never both:

| The pull request came from | Who answers | Where the fix runs |
| --- | --- | --- |
| A session the session runner started | The fix-round job | That session, resumed in its own thread and sandbox |
| One of your own local sessions | Your conflict fixer, every 5 minutes | A new capped session in your worktree, as you |

The conflict fixer runs only while you are logged in. It is capped by a dollar limit, a timeout and a number of sessions per pass. The workflow pages you if nothing answers in 15 minutes, or if GitHub still says conflicting after the result.

---

## How far "nothing here approves or merges" reaches

An approval claims that someone else read the code. A program supplying one makes that claim false. So the design keeps the reviewer from acting and the jobs from approving.

**What holds it up:**

1. The reviewer has no tool that acts. Its fence is checked by a live probe, and the sign-off is tied to the exact fence probed.
2. The job reads the verdict from the reviewer's last message only. It never reads the ticket text or comments.
3. The budget lives in a ledger the session cannot write.
4. The criteria are copied when a person delegates the ticket, outside every worktree. A session cannot narrow them after the fact.
5. The merge command is blocked by the hooks in every session.

**Where it is thin, today:**

- **"Cannot approve" is a fact about the code, not the key.** The token that posts comments could also post an approval. Nothing does. The tests assert there is no such path.
- **A misrouted review ticket can run in the coding setup for up to the wait** before the cancel lands. That is two minutes, or five and a half with a setup hook.
- **A review that could not see the change can read as clean.** Read the summary of every clean review.
- **A pull request over the size cap gets parts, not one review.** A person reads any part that cannot be made.
- **Without the app, every comment appears under your login.**
- **The fence is a deny list.** A tool the session runner adds later reaches the reviewer until someone names it.
- **The review jobs share an account** with the sessions they review. The sandbox is the whole boundary.
- **Nothing limits what a coding session costs.** The fix-round budget is the only cap on this lane.

---

## What you do, day to day

1. Put the ticket id in every branch name you want reviewed.
2. Delegate the ticket. Keep the acceptance criteria under a `## Acceptance criteria` heading, as checklist items.
3. Read the review comment when it lands, including the summary of a clean one.
4. When the ticket reaches your approval column, read the pull request and the last review. Merge, or do not.
5. Once a day, glance at the monitor's heartbeat. Silence on the alert ticket is not health.

---

## When something looks wrong

| What you see | What to do |
| --- | --- |
| No review comment after ten minutes | Read the review job's heartbeat, then its log. Runbook, "If something goes wrong" |
| "will NOT be reviewed" | The branch names no ticket. Rename it to carry the ticket id |
| "NOT reviewed" with a reason | Fix what it names, such as the criteria heading. It reviews again when the head or criteria change |
| "reviews are paused" | The route check failed. Sign the routing probe again by running the one command |
| A ticket sits with no fix round | The review is of an older commit, or the budget is spent. Read the last review comment |
| `agent:needs-human` on a ticket | The budget is spent, or a fix round got no push for six hours. Read the pull request |
| A conflict label that stays | Nothing answered. Check the conflict fixer's heartbeat, and whether you are logged in |
| A delegated ticket never started | The catch-up job replies after 7 hours 30 minutes. Check its heartbeat |
| A page: a job is stale | Read that job's heartbeat. A stale time means it is not running |
| The tech lead gives old plan facts | Read the status file's `written_at` time. The status job may be stopped |

---

## Words used here

| Word | Meaning |
| --- | --- |
| Session runner | The program that turns a handed-over ticket into a sandboxed AI session. |
| Agent | The session runner's user in the tracker. Handing a ticket to it starts a session. |
| Review job | The background job that files review tickets and posts the verdict. |
| Fix-round job | The background job that sends findings back to the coding session and hands the ticket over. |
| Reviewer | The read-only session that reads the diff and answers with findings. |
| Fence | The tools removed from the reviewer before it starts. |
| Basis | The version of the acceptance criteria a review was judged against. |
| Snapshot | The criteria as they stood when a person delegated the ticket. |
| Fix round | One automated re-prompt of the coding session. Counted against a budget. |
| Threshold | The lowest finding severity that starts a fix round. |
| Ledger | The fix-round job's append-only record. The budget authority. |
| Heartbeat | A small file each job writes on every exit. The only proof a job ran. |
| Routing note | The note the session runner posts at session start, saying which setup answered. |
| Role account | The separate account the background jobs run as. |
| Conflict fixer | The job under your own login that fixes your local pull requests. |

---

## What is not proven

- **A spent budget, with its label and both comments, has run only in tests** (no ticket yet).
- **A fix round caused by a red check rather than a finding** has run only in tests (no ticket yet).
- **The fallback fix ticket** has never been needed live (no ticket yet).
- **The criteria snapshot pass** is tested but has not been watched live (no ticket yet).
- **Either conflict lane** has never fired on a real pull request (no ticket yet).
- **A real side finding turned into a real ticket** has not been seen (no ticket yet).
- **A real lost hand-off has never been recovered** by the catch-up job (KIT-268).
- **The review comment under its own login** has not been run against a live app (no ticket yet).
- **A watcher off this Mac,** so that a sleeping Mac pages you, does not exist (KIT-45).
- **Keys expire without warning.** A dead GitHub token shows at the next installer run, not before (no ticket yet).
