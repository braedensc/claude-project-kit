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

- **It asks; a member answers.** A request is one strict line (`plan <ticket id>`) from anyone,
  the chat bot included. The bridge reads the ticket with the owner's key and posts its OWN
  question, built only from what it read. Only a "yes" in that question's thread, after it,
  from a verified member, takes the action. The yes binds to the question in the bridge's own
  state, never to a ticket named in someone's text. A bare "yes" is refused when another
  bot posted in the thread after the question, or the bridge's own account posted something
  it has no record of, since a look-alike could have misled the person; `yes <ticket id>` is
  asked for instead. An edited question is closed. A tricked chat bot can make the bridge
  ask; it cannot make it act.
- **It knows its own bot.** The config names the bridge app's bot user, and a token that
  answers to anyone else is refused before anything is read.
- **The sender is checked with Slack itself.** The `user` field from Slack's own history call,
  then `users.info`: a human, not deleted, not a guest, not an app, same workspace. Bot posts,
  edits, files and subtypes are refused. An optional list narrows who may answer.
- **Any full member may answer, with the owner's reach** (owner decision, 2026-10-10, KIT-117).
  That is how he shares the pipeline with people he trusts. The trusted-members rule stands:
  fully trusted people only, full members only, two-factor sign-in for everyone.
- **It polls; it does not listen.** Each pass reads the channel's history over a window (seven
  days), the threads that changed, and every open question's thread, and handles each message
  once. A yes sent while the Mac slept is acted on when it wakes, inside the question's 24
  hours.
- **It runs as the dispatcher's own account and reuses the owner's stored key** (owner
  decisions, 2026-10-10). **It has its own Slack app.** If it posted as the chat bot, the chat
  bot could post a question that looked like the bridge's, and a member's yes under it would
  act.
- **It says which nothing it did** (contract §13). A refusal it can explain is said once in the
  thread. A Slack or tracker failure exits 1 and leaves the message for the next pass. A state
  file it cannot read is refused, never taken for a first pass. State is written after every
  message, one pass runs at a time, and a message that fails three passes running is given
  up on and said, so one bad message cannot stall the bridge.

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
