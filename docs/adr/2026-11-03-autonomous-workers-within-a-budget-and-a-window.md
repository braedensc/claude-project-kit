# Autonomous workers start approved work within a budget and a working window

**Date:** 2026-11-03 · **Status:** Accepted (owner, 2026-11-03, KIT-276) · **Context:** MVP 2, the autonomous workers; built in parts, one pull request each

## Decision

Approved work starts without a person, in priority order, skipping what is blocked, within a
daily budget and a working window. A planning session proposes each day's batch and its lanes.
Code decides everything that grants authority.

1. **What may start without a person: the owner's own work.** That is a ticket the owner moved
   into the team's ready state, a backlog ticket the owner filed, or a piece of a plan the owner
   approved. "The owner" is checked by actor in the tracker's history, never by state alone
   (KIT-161). A ticket that anyone else edited after the owner filed or approved it is held until
   the owner looks. The readiness gate gates everything. Another person's approval never starts
   work on the owner's subscription. Each part is configuration, with a stricter setting.
2. **Agent-filed tickets start only within limits that code enforces.** A session files a ticket
   only through the finding request path (contract §8), never directly, and never picks its own
   next ticket. Such a ticket starts by itself only when all of these hold: it is small, it names
   no guarded path, it fits the day's budget, it came from work the owner approved (first
   generation only), and the day's count of such starts is under its cap. Each one is listed in
   the day's stand-up. Anything else waits for one tap from the owner.
3. **Spend is measured, not guessed.** The yardstick is the SDK's own cost estimate at API
   prices, read from each session's log by the dispatcher's role account. On a subscription it
   measures plan use; nothing is billed. Each day has a planned load, sized from measured costs
   per kind of work and from each ticket's effort label, and a hard stop well above a normal day.
   The plan's own warning status holds every new start. The acceptance check keeps its own cap
   per plan.
4. **A working window.** Its days, start time, length and time zone are configuration. Nothing
   new starts outside it: no piece, planning run, automatic review, re-prompt or retry. The
   dispatcher has no clock, so every job that starts work checks one shared window. A run in
   progress finishes. The chat lane answers at any hour; work the owner asks for outside the
   window waits for it, unless the owner says to start now. Reviews a person requests by hand are
   not held.
5. **The daily plan is a forecast, not a script.** At the window's start, a fenced, read-only
   planning session proposes the day's batch and groups it into lanes whose expected files do not
   overlap. Code checks the answer: every piece is in the pool, blockers come before what they
   block, lanes stay apart wherever the repository's merge gate cannot yet test open pull
   requests together, and the batch fits the planned load. Anything else is dropped, and the
   stand-up says so. If the session fails, the plain queue runs the day. Through the day the queue
   starts the next ready piece whenever a lane and the budget have room. Review findings,
   questions, new tickets and the owner's steering change the day as it goes.
6. **The stand-up reads files, not a model.** The day's plan, its "needs your OK" items, and the
   spend-and-hours ledger are fields-only files written by code. Each "needs your OK" item carries
   the exact reply that approves it. The stand-up messages, and any later app, read these files
   with no model in between. A lane keeps the same id from day to day.
7. **Parallel within caps.** The lanes per repository and the pieces running at once each have a
   cap sized for a runaway, never a normal day. A repository runs lanes that share files only once
   its merge gate tests open pull requests together.
8. **Restarts.** A piece stopped by the plan's limit, or held by the window, resumes once in its
   own thread after the reset or at the next window. A piece that ended on an error is handed over
   once more. These counts live outside the session.
9. **Runaway stops.** Each of these pings once and holds new starts: the plan near or at its
   limit, the day's hard stop, one piece costing far more than a normal piece, too many pieces at
   once or too many starts in a day, a reader that cannot read, or a session that signed in with
   an API key. Nothing raises its own limit.

Merging stays a person's. Nothing here approves a pull request or enables auto-merge.

## Why

- **Nothing paced the delegation lane, and nothing stopped a runaway.** A dispatcher that binds a
  session by a person's delegation writes no pin, so no budget applied (KIT-114). The coding,
  review and planning sessions, the chat lane, the writer and the owner's interactive sessions
  all draw on one subscription plan. A week's limit ran out with no warning.
- **Measured spend, because the plan shows the dispatcher no running percentage.** The plan's
  usage events carry its status and reset times. A running figure appears only near a limit. So
  the budget counts the pipeline's own spend, which can be measured exactly, and uses the plan's
  own warning as the account-wide stop. Rejected: pacing on the plan's percentage, which is not
  available below a warning; and moving the dispatcher to an API key, which is a real bill, when
  the owner chose the subscription for the owner's own work on the owner's own machine.
- **Code holds authority; the model only plans.** A planning session reads ticket text, which is
  untrusted data. If it decided what may start, a crafted ticket could widen the mandate. Its
  answer is ids and lanes, checked by code. The most a crafted ticket can do is change the order
  among work that is already allowed.
- **Actor, not state.** Anything holding a tracker write token can set a state, sessions
  included, and a session can attach a ticket under an approved plan (KIT-169). The history's
  actor, and a hold on anyone else's edit, are what a session cannot forge. The owner's own jobs
  still write as the owner (KIT-313), so a test pins the list of jobs that file or move tickets.
- **First generation only.** An agent-filed ticket that starts a session, which files more
  tickets, is a loop with a budget. One generation and a daily count make it a bounded
  convenience.
- **No pause.** The dispatcher ends a run only when its ticket reaches a terminal state, and that
  also deletes the worktree. So the window decides starts, never stops, until a stop that keeps
  the worktree is proven live.
- **Slots per repository, not per plan.** A limit per plan let two approved plans run two pieces
  in one repository at once (KIT-417).
- **The terms.** The plan's own sign-in flow, on the owner's own machine, for the owner's own
  work. The subscription is never made available to another person, which is why only the
  owner's approvals start work.

## Verified

Measured on the reference deployment on 2026-11-03, before any code, by a read-only measurement
the owner ran as the dispatcher's role account:

- The dispatcher writes every SDK message to its own session logs, which its role account can
  read: each session's start, naming how it signed in; its usage events; and its result, carrying
  the SDK's cost estimate per model (cyrus-claude-runner 0.2.73).
- The weekly reset in those events matched, to the minute, the reset the account's own usage
  card showed. The dispatcher draws on the same plan as the owner's interactive sessions.
- A week of usage events carried no running percentage, and none had passed a warning
  threshold.
- Every run that week signed in with the plan's token. None used an API key.
- The week's spend split by kind of work, from records no session can write.

Each part is built and checked in its own pull request, from KIT-422 on. Its selftest and its
contract section are its evidence.
