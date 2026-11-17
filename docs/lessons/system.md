# The whole system, explained

*A lesson for someone who has never seen it. Written 2026-11-11.*

This is a teaching document, not a manual. It follows one idea from a ticket to a checked, merged feature. It says why each stop is shaped the way it is. The part-by-part guide (`docs/SYSTEM.md`) and the runbook (`docs/OPERATIONS.md`) sit beside it for the day you need to operate or change anything. After a merge, the one command (`python3 scripts/pipeline_install.py`, see `docs/START-HERE.md`) brings every part up to date.

**The one line.** You write a rough idea as a ticket. Machines plan it, build it, review it and fix it. Nobody but you can merge it.

The last sections, *The honest limits* and *What is not proven*, say which of this has not been watched working.

**Four words you need before anything else.**

- **The tracker** (Linear) is where tickets live. A *ticket* is one unit of work with a title, a description and a list of acceptance criteria.
- **The session runner** is a program on an always-on Mac. When you hand it a ticket, it starts a Claude session and gives it that ticket.
- **An epic** is a parent ticket. Its children are the pieces of work that add up to it.
- **The backlog** is the column where nothing starts. A ticket there is a proposal.

---

## The problem it solves

A Claude session is fast and often right. It is also a program that can run commands. Left alone, it could merge its own work, approve its own review, or change the rules it works under.

So the whole design answers one question: **how do you let a machine do the work without letting it grade, bless or release that work?**

The answer is a relay. At each stop, one party holds one power. No party in the chain holds both the power to write the work and the power to approve it.

---

## One idea's journey

Follow a single idea. Suppose you write: *"Let a user export their notes as a file."* Ten stops.

### Stop 1 — you write it, and move it to Plan it

You write the idea on your project's board, in a few lines. Then you move it to a column called **Plan it**.

You do not hand it to the session runner. On this board, handing a ticket over means *build this*. Moving it to Plan it means *plan this*. Two gestures, two meanings.

### Stop 2 — a job notices, and asks who moved it

A job wakes up every few minutes and looks at Plan it. It asks two questions. Did you make the move? Is this really an idea, and not work someone already started?

Only your move counts. A planning run costs money and carries your name.

### Stop 3 — a clean copy goes to a planning session

The job never hands your idea over as written. It writes a second ticket. Your words sit inside it, quoted as data, with any instruction aimed at the session runner removed.

The analogy: you hand the courier a photocopy, not the original. The "open me first" stickers are gone, and the address is written for you.

The planning session reads your real code. It has no tracker tools, no shell and no way to write a file. It can only answer.

### Stop 4 — the plan is checked by plain code

The session ends with one block of structured text: an epic and its pieces. A program, not a model, reads that block. It holds the only key that writes plans to the tracker.

It checks that every piece has acceptance criteria a machine can test, a test plan and real file paths. If one piece fails, the whole plan is refused, with reasons. A passing plan is filed in the **backlog only**.

The plan is a proposal. It waits.

### Stop 5 — you release one piece

You read the plan. You fix or cancel what is wrong. Then you hand one piece to the session runner. A piece goes only after the ones it depends on.

This is the first place the relay needs you. Nothing in the backlog moves by itself.

### Stop 6 — a coding session builds it

The session runner starts a Claude session in its own folder, on its own branch, named from the ticket. The safety checks apply to every tool call it makes. It cannot read a secret file, push to main or edit a guard.

It plans first, as a comment on the ticket. It makes the smallest change that meets the criteria. It runs the project's own tests, opens a pull request and stops.

If the ticket is unclear, it asks one question on the ticket and ends. Nobody is watching live, so it never waits.

The analogy: the coding session is a contractor working in a locked room. It can build anything inside. It cannot open the door.

### Stop 7 — CI and a reviewer read the pull request

Two things happen. The checks that run on every pull request run, and branch protection makes the required ones block a merge.

A second job finds the pull request and files a review ticket. A reviewer reads it. This reviewer is a Claude session with no shell and no way to edit or write. It can only read and answer.

The answer becomes one comment on the pull request: findings, clean, or *NOT reviewed*. It is never an approval. Approval is a claim that a person read the code, and a machine cannot make it.

### Stop 8 — a fix round, if needed

If the review has findings above the threshold, or a required check is red, a driver asks the coding session to fix it. The same session resumes, in the same folder, on the same branch.

The count of fix rounds is capped. When the cap is spent, a person is called. This is not a failure. It is the system telling you it will not loop forever on its own.

If main moves while all this happens, the pull request may start to conflict. Another job then starts a capped session to merge main in and push. A conflicting pull request gets no checks at all, so it would otherwise look untested, not red.

### Stop 9 — you get a message, and you merge

When the work is clean, or the rounds are spent, the ticket moves to a lane that means *waiting for a person*. A message reaches you. It says what happened and links the pull request.

You read the review. You read the change. You merge it. A guard refuses a session's attempt to merge, so this stop is yours alone.

### Stop 10 — the acceptance check tries the app

If the acceptance check is on, one more thing happens before the last piece of a plan merges. A job notices that one piece is left and that its pull request is open, green and reviewed.

It opens the finished app in a real browser, in a locked sandbox with no network. It tries to disprove each problem it finds. What it cannot disprove becomes a follow-up request, and it posts one verdict on the epic.

If it cannot check, the verdict says so. It never reads as a pass.

---

## Why the chain is shaped this way

| Who | Can do | Cannot do |
|---|---|---|
| You | Write ideas, release pieces, apply the guard-change label, merge | Be everywhere, so jobs watch for you |
| The planning session | Read your code, propose a plan | Write to the tracker, run commands |
| The planning job | File a checked plan in the backlog | Release anything |
| The coding session | Build in its own folder, open a pull request | Merge, approve, label itself, edit a guard |
| The reviewer | Read a pull request, write one comment | Run anything, approve |
| The fix-round driver | Ask the same session for a fix, a capped number of times | Fix the code itself, merge |
| The guards | Refuse a risky action before it runs | Stop a determined caller on their own |
| CI and branch protection | Block a merge that is red | Be bypassed from a session |
| The acceptance check | Try the app, post a verdict | Merge, change the code |

Read the table by its second column. Every power is held by exactly one party, and the powers that approve are held by you.

---

## How to tell whether it is working

1. **Silence is the warning, not green.** This system fails quietly. A job that ran and found nothing looks the same as a job that never ran. Look at each job's heartbeat file, which says when it last ran.
2. **A moved idea gets a comment in minutes.** If a planning ticket does not appear, planning may have stopped on purpose, waiting for you.
3. **Every pull request gets one review comment.** If one does not, the branch name may lack the ticket id. A review that could not run says *NOT reviewed* in plain words.
4. **You get a message when only a person can act.** A blocked ticket, spent fix rounds and a pull request ready to merge each send one. If you stop getting them, check the job that sends them first.

When these four hold, the relay is intact.

---

## The honest limits

- **The guards stop mistakes, not adversaries.** The checks that read shell text can be respelled around. The things that cannot be bypassed from a session are branch protection, CI and the operating system's sandbox.
- **Some parts are built and off.** Four are off until a person switches them on: the queue that starts work, the auditor, the app check, and approving and merging for you.
- **The always-on machine is described, not built.** The kit says what a correct one looks like. You build it.
- **Nothing guarantees the plan is good.** The readiness check proves a plan is well formed. It does not prove it is wise. You read it.
- **A heartbeat is not a proof.** It says a job ran. It cannot say the job did the right thing.

## What is not proven

- **That a newcomer can follow this lesson and then run the system.** It was written from the guides and the code. No ticket yet.
- **The acceptance check on a real plan, end to end.** It is built and tested offline. KIT-324.
- **A long unattended run through every stop.** Each stop has run. A full chain over many plans has not been measured. No ticket yet.
- **Approving and merging for you.** Built, never run live. No ticket yet.

## Words

| Word | Meaning |
|---|---|
| Acceptance criteria | The checklist on a ticket that says when the work is done |
| Backlog | The column where a ticket is only a proposal |
| Branch protection | A setting on the code host that makes required checks block a merge |
| Epic | A parent ticket whose children are the pieces |
| Fix round | One trip from the reviewer back to the coding session for a fix |
| Guard | A hook that checks a tool call and refuses a risky one |
| Heartbeat | A small file a job writes each time it runs |
| Pull request | A proposed change, waiting for review and merge |
| Session runner | The program that starts a Claude session on a handed-over ticket |
| Worktree | A separate folder where a session works on its own branch |
