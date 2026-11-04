# Your own clear ask acts after a stop window

**Date:** 2026-11-04 · **Status:** Accepted (owner, 2026-11-02, decision 1 on KIT-275, option b) ·
**Context:** KIT-368 (ACT-4); `scripts/pipeline_bridge_direct.py`

## Decision

When the owner turns it on (`DIRECT_ASKS`, one typed yes at the bridge installer's
`direct-asks --apply`), a listed member's own DM message that names plan or replan and up to
`DIRECT_MAX_TICKETS` tickets (3) gets no question. The fixed reader of ACT-3 reads it, never
a model. The bridge says what it will do and when, then acts once that window ends:

> In 90 seconds I'll hand it to the planner: PROD-6 “Light mode”. Say stop to cancel.

It takes that path only when every one of these holds:

- the plain shape, or the looser one with no `?` and no hedge word;
- no note: a re-plan's note is not passed on yet (RP-2), so a note gets a question;
- each ticket is the member's own pick, named before any post of the tech lead's (ACT-1);
- no ticket was filed by the agent's own account, and none carries `provenance:agent`;
- the bridge first reads it within `DIRECT_MAX_AGE_MINUTES` (10) of when it was sent;
- a yes from that sender would be taken, and each ticket passes a request's own checks.

Otherwise it gets the question it got before, or one combined question for more tickets.

**The stop.** Any other message from them in that DM before the act stops everything that
waits there, and so does `no <id>` naming a ticket that waits. These do not: a new ask,
`yes <id>`, `no <id>` naming another ticket, a reply under a dev's page, and a short
acknowledgement. A bare `no` stops them and answers no question; the DM
asks for `no <id>` if they meant one. A `stop` after the act hears what already happened.

**Before acting** it reads their message from Slack again, reads it as the same ask, reads
each ticket again as it was echoed, and checks the settings and the action limit. If any
changed, it acts on nothing and says which. Then the existing `act_plan` or `act_replan`
moves each ticket. Each attribution comment names them and their message's time, and says no
question was asked.

## Why

The owner's decision 1 of 2 Nov: what you clearly asked for just happens. A fixed reading, an echo
naming each ticket with its title, and a window that any other message closes are the
safety net. Of ACT-3's 100 test phrasings, 16 act with no question, against 14 under the
first, stricter draft. None of the red team's 20 do.

## What still asks

- Approve and cancel, always.
- Anything the tech lead picked or proposed. Its words can be steered by text others wrote.
- A channel message, a bot or app post, an edited message, and a sender a yes would refuse.

## Consequences

- A message in the window stops the action even when it meant something else. That is the
  safe direction.
- The window is counted in passes: the act happens on the first pass at or after its end,
  a minute apart by default.
- `DIRECT_MAX_TICKETS` only sets where asking starts. It blocks nothing.
- Everything lands under the owner's own key, as every bridge action does.

## Update 2026-11-04 — a pause acts at once, the one exception (KIT-451)

**The owner decided on 4 Nov (KIT-451):** with direct asks on, a member's own typed
`pause <id>` acts at once, with no echo and no window. It is the one direct ask that
skips the agent-filed check.

**Why.** Every plan the idea gate files carries `provenance:agent`, so without this
exception a direct pause could never act. A pause starts no work, and a resume undoes it,
so words others wrote into a plan can't use it to start anything. For the same reason, a
pause the tech lead set up already takes a bare yes (KIT-351).

**What still holds.** Every other check stands: the sender, the time since the message
was sent, a ticket the tech lead named first, the action limit, and the read again before
the act. The plan move re-reads its plan itself, and acts only in the state the ask found
it in. That replaces the ticket's fingerprint check, since the epic read holds none of the
plan's own text. Its comment says no question was asked. `resume` and `cancel` still ask,
and the tech lead's lines never take this path.

## Update 2026-11-04 — a re-plan's note goes with it (RP-2, KIT-372)

The "no note" check above no longer holds. A re-plan of one idea with a note takes the
direct path too. The echo quotes the note line by line, inside its fixed words:

    In 90 seconds I'll hand it back to the planner for a new plan: PROD-6 “Light mode”,
    with your note as you wrote it:
    > the gate is fixed
    Say stop to cancel.

**The note is checked as an answer to a dev is.** It is at most `relay_max_chars`, its angle
brackets are removed, and one with a credential shape is refused. With several tickets, a
note is refused aloud: it goes with one idea at a time.

**It reaches the planner in the act's own comment.** The note rides in the attribution
comment, under the planner's fixed line, and the next planning run quotes it. No tracker
write is added. Before acting, the bridge reads the message again, and a changed note stops
the act, as any other change does.
