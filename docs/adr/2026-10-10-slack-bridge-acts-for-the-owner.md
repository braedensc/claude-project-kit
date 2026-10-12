# A Slack bridge acts for the owner, after asking its own question and checking with Slack who answered

- **Status:** Accepted (owner, 2026-10-10, KIT-220). Built in steps: KIT-221 (this ADR's first
  build: read, verify, ask, act on nothing), then KIT-222 (plan an idea), KIT-223 (approve an
  epic and start its children in order), KIT-224 (answer a stuck session; replaces the reply
  relay), KIT-225 (ready-to-merge pings), KIT-226 (the chat bot's instructions).
- **Date:** 2026-10-10
- **Supersedes, in part:** `2026-09-06-human-action-notifier-and-reply-relay.md`, where it says
  approve, label and state "never transit the channel". A state move into Plan it, an epic's
  move to ready and a child's delegation now do, through this bridge only. Merging and
  approving a pull request still never do.

## Context

The owner's goal, in his words (2026-10-10): "Simply go to chat and say 'I want dark mode', and
then it plans the tickets and feature and kicks it off for me." He wants to run the pipeline
from the Slack chat, with no tracker app and no laptop.

The chat bot answers questions but cannot act for him, and that is on purpose. It is the
dispatcher's built-in Slack lane, and its tracker writes land as the dispatcher's app user. The
planner accepts only a move into Plan it made by the owner, and the dispatcher starts a session
only on the owner's delegation (`allowedUsers`). Those two checks are what stop a ticket's text,
or anyone else, from starting paid work.

Three facts shape the answer:

1. **Who sent a Slack message is known only to Slack.** The chat bot's own account of who asked
   is text, and a session's text can be steered.
2. **The chat lane runs with no sandbox and holds the dispatcher's tool server**, which reads
   any file the dispatcher's account can read (KIT-162). Anything stored in that account is
   readable by the chat bot.
3. **Slack delivers an event at most four times over about six minutes.** A Mac that is
   unreachable longer loses the message for good.

## Decision

A separate job, **the bridge**, is the only path from the chat to an action as the owner.

- **It asks; a member answers.** A request is one strict line, `plan <ticket id>` or
  `approve <ticket id>`, from anyone, the chat bot included. The first build answers `approve`
  once, saying it is not built yet, and changes nothing. For `plan`, the bridge reads the
  ticket with the owner's key and posts its OWN question, built only from what it read. Only a
  "yes" in that question's thread, after it, from a verified member, takes the action. The yes
  binds to the question in the bridge's own state, never to a ticket named in someone's text. A
  bare "yes" is refused when anything other than the bridge or a verified member posted in the
  thread after the question (a bot, an app, Slackbot, a guest), or the bridge's own account
  posted something it has no record of, since a look-alike could have misled the person;
  `yes <ticket id>` is asked for instead. An edited question is closed, and so is one that is
  gone from its thread. A question's status changes only after the bridge's reply saying so is
  posted. A tricked chat bot can make the bridge ask; it cannot make it act.
- **It knows its own bot.** The config names the bridge app's bot user, and a token that
  answers to anyone else is refused before anything is read.
- **The sender is checked with Slack itself.** The `user` field from Slack's own history call,
  then `users.info`: a human, not deleted, not a guest, not an app, same workspace. Same
  workspace must be shown: an account Slack names no workspace for is refused. Bot posts,
  edits, files and subtypes are refused. An optional list narrows who may answer.
- **Any full member may answer, with the owner's reach** (owner decision, 2026-10-10, KIT-117).
  That is how he shares the pipeline with people he trusts. The trusted-members rule stands:
  fully trusted people only, full members only, two-factor sign-in for everyone.
- **It polls; it does not listen.** Each pass reads the channel's history over a window (seven
  days), the threads that changed, and every open question's thread, and handles each message
  once. A yes sent while the Mac slept is acted on when it wakes, inside the question's 24
  hours. A thread is found through its first message, so a new reply in a thread older than
  the window is not read unless the thread holds an open question.
- **It runs as the dispatcher's own account and reuses the owner's stored key** (owner
  decisions, 2026-10-10). **It has its own Slack app.** If it posted as the chat bot, the chat
  bot could post a question that looked like the bridge's, and a member's yes under it would
  act.
- **It says which nothing it did** (contract §13). A refusal it can explain is said once in the
  thread. A Slack or tracker failure exits 1 and leaves the message for the next pass. A state
  file it cannot read is refused, never taken for a first pass. State is written after every
  message, one pass runs at a time, and a message that fails three passes running is given
  up on and said, so one bad message cannot stall the bridge. Every request that reads the
  tracker counts against the hourly limit, found or not, since the key is shared with
  Stage E; past it, requests are refused unread and that is said once an hour.

## Consequences

- The owner's goal becomes reachable: the chat bot files an idea and asks the bridge; the owner
  says yes; planning starts; he approves the plan the same way, and the children start in
  order.
- **Accepted residual: the bridge's key, token and records sit in the dispatcher's account.**
  Any session holding that account's tool server can read them (KIT-162). The key is the same
  one Stage E already stores there, so the bridge adds no new tracker exposure. Its Slack token,
  if read, lets someone read the channel and post as the bridge. Such a post binds nothing: an
  action needs a verified member's yes to a question in the bridge's own records, an edited
  question is refused, and a bare yes after an unrecorded bridge post is refused. Its records
  are forgeable only by something that can write the
  dispatcher's account's files; today no session can (the chat bot has no write tools, and
  coding sessions write only inside their worktree). If either limit changes, this residual
  grows, and this ADR must be revisited.
- The reply relay is retired in favour of the bridge (owner decision, 2026-10-10; KIT-224).
- A second path into the tracker exists. Its writes are few and pinned by its selftest: in this
  first build, none.

## Rejected

- **Let the chat bot act directly**, with the owner's key in its reach. It would make every
  steered chat session the owner. The point of the planner's and dispatcher's checks is that
  text cannot start paid work.
- **A second hidden account for the bridge.** With the owner's key reused, it would have
  protected only the bridge's Slack token and records, at the cost of an account to create and
  look after (owner, 2026-10-10).
- **Act on a typed command, with no question.** One step faster, but a mistyped id acts at once,
  and the chat bot's text could name the ticket.
- **Listen for Slack events.** Events are lost after about six minutes of unreachability.

## Not proven

- That a live Slack answers `users.info` with the guest and app flags as documented. The
  selftest uses Slack's documented fields; the first live run checks them (KIT-227).
- That the bridge app can read a private channel's history with `groups:history` alone. A
  missing scope is a named failure, exit 1 (KIT-227).
- That an idea created by the chat bot and moved by the bridge is recorded in history at once
  (KIT-227).

## Update 2026-10-10 — the first action: plan an idea (KIT-222)

With `act` on, a verified "yes" to a `plan` question moves that idea into its team's Plan it
state, with the owner's key, so the planner job plans it. The bridge refuses exactly what
the planner would: it imports the planner's own `live_work_reasons` and team-state query,
and runs them before it asks and again before it moves. It re-reads the ticket first, so a
ticket already in Plan it is said and left alone, and a pass that crashed after a move
never moves twice. Its tracker writes are exactly two, pinned by the selftest: one
`issueUpdate` setting `stateId` and nothing else, and one `commentCreate` naming the Slack
member who said yes and when. It acts at most `max_actions_per_hour` times an hour.

**A yes binds to what the question showed.** The question records a fingerprint of the
ticket (its id, title, description, team, labels and state) and the settings its text states
(`act`, `plan_it_state`, `question_ttl_hours`). A coding session or the chat bot can write a
ticket, so a yes after the ticket changed moves nothing; and a yes after the settings changed
acts on nothing, so a question that promised "nothing moves" never moves anything. Either
closes the question and asks for a new request. A ticket already in Plan it is refused before
asking. These follow the 2026-10-10 review of KIT-222.

**A move whose comment fails is owed a comment.** The move cannot be taken back. The thread
says the comment could not be written; the state records it as owed; every pass writes it
again, and exits 1 until it lands, so a missing record of who said yes is never silent.

`act` ships off. The owner watches the bridge ask and record first, then turns it on.

## Update 2026-10-10 — the second action: approve a plan (KIT-223)

With `act` on, a verified "yes" to an `approve` question moves an epic to its team's
`ready_state`, with the owner's key, and records it as approved in the bridge's state.
Every pass after that hands its children to the agent user named by `agent_user_name`,
again with the owner's key, because the dispatcher starts a session only for a person it
trusts.

**Only what the idea gate filed.** The epic must carry `provenance:agent` and have no
parent; every child must carry `provenance:epic`, sit on the epic's team, and have no
children of its own. These are checked before asking, before approving, and on every pass.

**Dependency order.** A child starts only when every ticket that blocks it is Done. The
order check (`blockers_of`, `open_blockers`) is a pure function over the tracker's
`blocks` relations, kept importable for the order job that checks hand-overs made by hand
(B9). At most `epic_wip` children of one epic are started and unfinished at once; a child
someone else handed over, or one with a session, counts and is never handed over again.

**Only what the member saw.** The question records a fingerprint of each child: its title,
description, labels, project and blockers. A coding session holds the tracker's write
tools, and on the dispatcher lane nothing stops it writing tickets (B8). A yes after any
child changed, or one was added or removed, approves nothing. After the yes, a child added
to the epic, or one whose fingerprint changed, is never handed over: it is said once, and
the owner hands it over by hand if the change is theirs. Without this, a session's text,
route or order would start work with the owner's authority. The first build took the
fingerprint at the yes; a review showed a session could change a child during the
question's 24 hours, so it moved to the question.

**The dispatcher's own modes.** An epic or child carrying `orchestrator` or `graphite` is
refused: from 0.2.73 those prompts hand sub-issues to the agent themselves, and a
`graphite` child is cut from its blocker's branch.

**No silent hold, no shared failure.** A child outside the plan at work, and a child
handed over that has not started after two hours, are said once, since each holds the
limit. One epic the tracker will not read is retried each pass and said after three; the
others go on, and the pass exits 1.

**What it refuses to do.** It never hands over the epic itself, and stops following an epic
that someone else handed to the agent, because the dispatcher cuts a sub-issue's worktree from its
parent's branch when one exists, so every child would stack. It never hands a child over
twice: a reopened child is the owner's to restart. Moving the epic back to Backlog pauses
it; cancelling or finishing it ends it.

**The terms the member read.** An approve question records the settings its text states
(`act`, `epic_wip`, `ready_state`, `agent_user_name`, `question_ttl_hours`). A yes after any
of them changed approves nothing and closes the question, so a question that said "nothing
moves or starts" never starts paid sessions.

**A Done state is not the end of a session.** A session holds the tracker's write tools and
can mark its own child Done while it still runs. A finished child with a session that has
not ended (by the planner's own `SESSION_FINISHED`) counts toward `epic_wip`, and the hold
is said once. The sessions are read once a pass already, so this costs no extra read. With
`epic_wip` above 1 a blocker marked Done early can still release what waits on it; the
default of 1 holds the order too.

**Owed comments.** A hand-over or an approval whose attribution comment fails stands, since
it cannot be taken back. The thread says so, the comment is recorded as owed, and every
pass writes it again and exits 1 until it lands.

These three follow the second review of KIT-223 (2026-10-10), which also pinned every
re-check of the epic with a selftest case.

**Writes.** One more, pinned by the selftest: an `issueUpdate` carrying `delegateId` and
nothing else. Each hand-over posts one comment whose first line names the member who
approved and when, and one line in the Slack thread. Hand-overs count against
`max_actions_per_hour`. Merging stays the owner's.
