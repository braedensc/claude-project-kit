# Code review and its jobs, explained

*A lesson for someone who has never seen it. Written 2026-11-11.*

This is a teaching document, not a manual. It explains what the review layer is, why it is shaped the way it is, and how to tell whether it is working. The guide and the runbook sit beside it for the day you need to operate or change it.

**The one line.** A coding session opens a pull request. A second session, which can read but not touch, reviews it. The findings go back to the first session a limited number of times. Then the ticket comes to you.

The last sections say what has been watched working and what has not.

**Four words you need before anything else.**

- **The tracker** is where tickets live. A *ticket* is one unit of work with a title, a description and a list of acceptance criteria.
- **The session runner** is a program on a Mac. When a ticket is handed to its agent, it starts an AI session in a sandbox and gives it that ticket.
- **A pull request** is a proposed change on GitHub. Nothing reaches the main branch until someone merges it.
- **A pass** is one run of a background job: it starts, looks, acts and exits. The jobs do nothing between passes.

---

## The problem it solves

A coding session has one ticket, no memory of other work, and nobody to ask. It reads the ticket, writes code, pushes a branch, opens a pull request and stops. Nothing reads the diff. Nothing checks whether the change did what the ticket asked, or more.

The session runner is good at one job: turning a ticket into a pull request. It has no review step. The review layer is the review step.

The obvious shortcuts fail, and the reasons are the lesson.

**Let the session review its own work.** Same eyes, same blind spots. Worse, the session would grade itself, or choose when and how it is graded. The rule the whole layer rests on is short: *the reviewed party does not get to set the terms of its review.*

**Trust what the session writes.** Everything a coding session can write is *reporting*, not *authority*. That means the branch name, the pull request text, its comments, and files in its worktree. It includes its own ticket's acceptance criteria, because the session holds a write key to the tracker. If a safety check reads a value the session could have written, the session can pass the check by writing it.

**Run the reviewer under your own account.** The session runner builds a sandbox around every session. A review started by a background job under your login would have none of that. It would read a diff an untrusted process wrote, with your credentials in reach.

So the reviewer has to be a fresh session. It is fenced down to read-only tools and runs in the same sandbox. Something the coding session cannot do for itself starts it.

---

## The cast

Three words first.

- **A worktree** is a separate checkout of a repository, one per session, so sessions never trample each other.
- **The sandbox** is the box around a session. It may write only in its worktree, reach only a short list of hosts, and cannot read the role account's home.
- **The role account** is a separate, non-admin user made for the session runner. The sessions and the review jobs run as it, never as you.

**Two things were here before the review layer.**

| Who | What it is |
| --- | --- |
| The session runner | Hears about a hand-over, cuts a worktree and starts a sandboxed session. Later it resumes that session when a comment lands in its thread. |
| A coding session | Works one ticket in its own worktree, pushes a branch named after the ticket, opens a pull request, and stops. |

**The review layer adds these.** Only the reviewer is a model. The rest is plain code.

| Who | What it is | One sentence |
| --- | --- | --- |
| The reviewer | A second session, also started by the session runner | Reads the diff and the criteria with read-only tools and answers with findings. |
| The review job | A system job, a pass every 2 minutes | Finds pull requests, files review tickets, posts the verdict as one comment. |
| The fix-round job | A system job, a pass every 2 minutes | Sends serious findings back to the coding session, up to a budget, then hands the ticket to you. |
| The side-findings job | A system job, a pass every 5 minutes | Turns the small out-of-scope things a session noticed into backlog tickets. |
| The catch-up job | A system job, a pass every 15 minutes | Re-sends a hand-over the session runner missed while the Mac slept. |
| The status job | A system job, a pass every 15 minutes | Writes one file of where every plan stands. |
| The heartbeat monitor | A system job, a pass every 30 minutes | Checks the others are alive and says so on one ticket when they are not. |
| The conflict monitor | A GitHub workflow, every 20 minutes | Notices when a pull request conflicts with its base and asks for a fix. |
| The conflict fixer | A login job under you, a pass every 5 minutes | Answers those requests for pull requests your own local sessions opened. |

The system jobs run as the role account so a reboot brings them back with nobody logged in. The conflict fixer runs as you because it needs your login keychain.

The analogy: the coding session is a contractor working in a locked room. The reviewer is an inspector with a clipboard and no keys. The fix-round job is the clerk who carries the notes back to the room a limited number of times, then brings the paperwork to you. The heartbeat monitor is the night watchman who checks the clerks punched their time cards.

---

## One pull request's journey

Take a ticket called "Add token refresh" on a project called acme-app. Here is what happens, and what you would see.

### Stop 1: you hand it over

You hand the ticket to the session runner's agent. It cuts a worktree and starts a coding session. The session pushes a branch whose name carries the ticket id, such as `feat/app-900-token-refresh`, and opens a pull request. The tracker's GitHub link attaches the pull request to the ticket.

If a message was lost while the Mac slept, the catch-up job is the safety net. After seven and a half hours it replies once in the ticket's thread, and the session starts late instead of never.

### Stop 2: the review job notices

Within two minutes the review job looks at what the session runner has worked on and the pull requests attached. It reviews a pull request only under three conditions. The branch carries a ticket id. It is not a draft or a fork. It has not been reviewed at this commit.

It then works out the *basis*: which version of the acceptance criteria the review is judged against. That is a saved copy when one exists, otherwise the live ticket. Stop 6 says why the copy exists.

### Stop 3: it files a review ticket, addressed by hand

The job files a review ticket on the same team as the coding ticket. The body holds a brief, the criteria, the severity threshold and the whole diff.

A diff is text the coding session wrote. So the job scrubs every string it copies. A line in the diff reading like a routing instruction is replaced with a visible mark. The job then writes the address itself: one routing tag on the first line, and a routing label of the same name.

If the diff is too large for one ticket, the job withholds whole files, largest first. A second review ticket carries the rest. The comment names what waits.

### Stop 4: it checks the delivery receipt

The session runner reads each address with a separate network call, and a failed call counts as "no address". On a work team the fallback is the coding setup, which has hands. That is rare, and it is the one way a diff could meet tools that act.

So the job checks the receipt. The session runner posts a note at the start of every session, before any AI runs, saying which setup answered. The job reads the earliest one. If it names the review setup, fine. If it names anything else, or none arrives in two minutes, the job cancels the ticket, writes down what it saw and pages you. Reviews for that repository stop until the probe is signed again.

### Stop 5: the reviewer reads and answers

The session runs under a *fence*, a list of removed tools. Gone: anything that runs a command, writes a file, fetches from the network, schedules work, starts another agent or touches the tracker. Left: tools to read and search files.

It answers with one block of structured text: a summary, findings with severities, and a field for "I could not judge this change". That last field matters. An empty list of findings on its own reads as clean.

The job reads the block from the reviewer's last message only. It posts one comment on the pull request. It is never an approval, and the job has no code path that merges.

### Stop 6: the fix-round job decides

Within two minutes it reads the verdict and the required checks. It sends the coding session back only when three things hold. A finding meets the threshold, or a required check is red. The review is of the commit the pull request is on. Budget remains. A check that waits on a person is set aside first. No session can fix it.

A fix round is this: the job writes a row to its ledger, then replies in the ticket's session thread. The reply carries the findings inside a fence marked as untrusted text. The session runner resumes the same session in the same worktree.

Three facts here are values the session cannot write. The ledger holds the budget, because a session with tracker tools could delete a comment that a count was read from. The criteria copy was taken outside every worktree when a person delegated. Otherwise a session could narrow its own criteria and the review would agree. The head commit comes from GitHub.

### Stop 7: re-review, and repeat

When the session pushes, the review job reviews the new head and posts a second comment. No push means no re-review and no cost. This repeats up to the budget. The worst case is a handful of reviewer sessions for one pull request.

A review that declined to run is not final. If the pull request's head or its ticket's criteria change, the job tries again. After ten tries it stops and says so in its log.

### Stop 8: the hand-over

When nothing is left to send back, the job moves the ticket to your needs-approval column. That is the only ticket move it makes. A spent budget also adds a label that says a person is needed.

If a fix round was delivered and the session never pushed, the job notices after six hours. It leaves one comment on the ticket and the label, and spends nothing. It does not reply in the thread, because that would wake the session that stopped.

**Then it is your turn.** You read the pull request and the last review, and you merge or you do not. Nothing here can merge.

### The side loops

**A finding.** A session sometimes notices something true and out of scope. It must not widen its diff, and it must not file a ticket, because a session that files its own tickets has promoted itself. It leaves one comment on its own ticket in a fixed shape. The side-findings job files it as a backlog ticket marked agent-filed, strips any protected label the session named, and subscribes you. The new ticket never starts a session.

**A conflict.** While a branch conflicts, GitHub cannot build the merge, so the required checks never run. A conflicting pull request is not red. It is untested, which is worse. The conflict monitor labels it and posts a fix request. Whoever owns the session answers.

| The pull request came from | Who answers | How |
| --- | --- | --- |
| A session the session runner started | The fix-round job | Replies in the session's own thread, so the session fixes it inside its sandbox. |
| One of your local sessions | The conflict fixer | Starts a short, capped session in your worktree to merge the base, resolve and push. |

They are split for one reason. The conflict fixer starts a session outside every sandbox, with your reach. Pointed at a branch an untrusted session wrote, it would run your credentials over that session's work.

---

## Why the chain is shaped this way

Look at what each part holds:

| Who | Can read the diff | Can write to the tracker | Can act on code | Can approve or merge |
| --- | --- | --- | --- | --- |
| The coding session | yes | its own ticket | **yes** | no |
| The reviewer | **yes** | no | no | no |
| The review and fix-round jobs | yes | comments, one lane move, one label | no | no |
| You | yes | yes | yes | **yes** |

Nobody in the chain holds both the ability to act on code and the ability to bless it. The reviewer can only say something. The jobs can only carry what it said. The one move that counts, the merge, is yours.

One rule sits across the table: **the job checks where every review ticket went.** Filing on the project's own team is cheaper than a team of its own. The cost is that a misrouted ticket lands next to tools that act. The receipt check pays for that.

---

## How to tell whether it is working

Every job writes a *heartbeat* on every way out: a small file with a time and a result. The trap is that three situations look identical from outside, with no output and nothing red.

| The job's answer | What it means | What you see |
| --- | --- | --- |
| Did the work | It found something and acted. | A fresh heartbeat saying `ok`. |
| Nothing to do | It ran, looked and found no work. | A fresh heartbeat saying `ok` or `idle`. |
| Could not do it | It hit a problem. | A fresh heartbeat saying `error`, and a trace in the log. |
| Did not run | The system never started it. | No fresh heartbeat at all. |

Those need opposite fixes. So every job says which kind of nothing it did.

The heartbeat monitor reads them so you do not have to. When the picture changes, it posts one comment on one ticket. One comment per incident, one on recovery, never a stream. It is slow on purpose, and a stopped job reaches the ticket after about an hour.

Four signals, in the order you would look:

- **A review comment appeared** within a few minutes of a pull request opening.
- **The heartbeats are fresh.** Three timestamps inside the last ten minutes is good.
- **No page about reviews being paused.**
- **The tech lead's plan facts are recent.** The status file carries its own time.

---

## The honest limits

- **No ticket id in the branch name, no review.** A branch named `fix/typo` is skipped, and one comment says so once.
- **A repository with no `delivery.json` gets advice only.** The comment says a fix pass would start, but none does. You have to read it before merging.
- **A review that could not see the change can read as clean.** Read the summary of every clean review.
- **A misrouted ticket runs for seconds.** The check catches it quickly. It does not prevent it.
- **The fence is a deny list.** A tool added later reaches the reviewer until someone names it, and a live probe is what catches that.
- **A review can ask for a fix no session can make.** Anything under `.github/workflows/` is out of a session's reach.
- **Cost.** Nothing limits what a coding session spends. The fix-round budget is the only cap on the lane.
- **Without the app step, every comment appears under your login.** A review comment then cannot prove who wrote it.
- **The monitor cannot see the Mac itself.** Asleep, off or waiting at a disk password all look like health.

---

## Words

- **Basis**: the version of the acceptance criteria a review was judged against.
- **Fence**: the list of tools removed from the reviewer before it starts.
- **Fix round**: one automated re-prompt of the coding session, counted against a budget.
- **Heartbeat**: a small file a job writes on every exit. The only proof it ran.
- **Ledger**: the fix-round job's append-only record. The budget authority.
- **Pass**: one run of a background job.
- **Required checks**: the CI jobs that branch protection insists on. The fix-round job ignores the rest.
- **Review ticket**: the ticket the review job files and the reviewer is run from.
- **Role account**: the separate user the session runner and the review jobs run as.
- **Routing note**: the note the session runner posts at session start, saying which setup answered.
- **Sandbox**: the limits around a session: writes only in its worktree, a short host list, no reading of the role account's home.
- **Snapshot**: the criteria as they stood when a person delegated the ticket.
- **Threshold**: the lowest severity that starts a fix round.
- **Worktree**: a separate checkout of a repository, one per session.

---

## What is not proven

- **A spent budget and the label it adds** have run only in tests (no ticket yet).
- **A fix round caused by a red check** rather than a finding has run only in tests (no ticket yet).
- **Either conflict lane** has never fired on a real conflict (no ticket yet).
- **A real side finding filed as a real ticket** has not been seen (no ticket yet).
- **A real lost hand-over recovered by the catch-up job** has not been seen (KIT-268).
- **A watcher off this Mac,** so that a sleeping Mac pages you, does not exist (KIT-45).
