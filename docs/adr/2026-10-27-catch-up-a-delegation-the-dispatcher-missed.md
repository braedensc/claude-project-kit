# Catch up a delegation the dispatcher missed, by replying in its thread after the tracker gives up

- **Status:** Accepted (owner, 2026-10-27, KIT-47). The wait is the owner's choice of the safe
  floor; delayed chat events are out of scope by the same decision ("Not now").
- **Date:** 2026-10-27
- **Script:** `scripts/pipeline_catchup_poller.py`. Operator doc: `docs/CATCH-UP.md`.

## Context

The owner's goal is a pipeline that survives a weekend or longer with the dispatcher's Mac
asleep in a bag. The dispatcher on the delegation lane hears about work only from the
tracker's webhooks:

1. **It never polls.** Read from source at 0.2.73: no poll loop, and no duplicate check on a
   session's `created` event.
2. **The tracker retries a failed delivery three times**, after 1 minute, 1 hour and 6 hours,
   and then drops it. Its documentation also says a webhook that keeps failing "might be
   disabled", after which every later delivery is dropped.
3. **A `prompted` event for a session the dispatcher has never seen rebuilds that session.**
   A reply in the delegation's own thread produces that event. Its access check is on the
   person who delegated, not on who replied. The thread-reply route has been proven live
   since 2026-09-08: it is how the bounce driver re-prompts a session.
4. **Replaying the webhook ourselves would need its signing secret**, which could forge any
   event the dispatcher accepts.
5. **The dispatcher can answer late.** It runs a blocking `git fetch` as it starts a session,
   before it answers other webhooks, so a burst of session starts can push those answers
   past the tracker's 5-second limit. A late answer is a failed delivery.
6. **It checks no `webhookTimestamp`.** A replayed delivery is accepted however old, so the
   host's clock is not load-bearing. This job replays nothing, and its reply is a fresh event.

So a delegation made while the Mac slept for more than 7 hours is lost, with no error
anywhere, and a disabled webhook loses everything after it the same way.

## Decision

A Stage E daemon, **the catch-up poller**, runs every 15 minutes as the dispatcher's role
account with the owner-scoped tracker key Stage E already stores.

- **It finds what the dispatcher never answered.** An agent session with no activity at all
  from the agent, never started, ended or dismissed, the agent's newest session on its ticket,
  on a configured work team, not a planning ticket, not done or canceled, still delegated to
  the agent, with a person who delegated it and a root comment to reply under. Only the newest
  session counts because a person who restarted a lost delegation made a newer one, and a
  reply under the old one would run the ticket twice.
- **It waits until the tracker has given up.** A delegation is eligible only after 7 hours
  30 minutes. The script refuses any wait inside the tracker's 7-hour retry ladder. Replying
  sooner would race a late retry into a second session, since the dispatcher keeps no record
  of what it started. Older than a week is skipped.
- **It replies once, in the thread.** The reply is fixed text ending in a plain receipt line,
  under the session's root comment. Nothing else is written: no ticket, state, label or
  delegation.
- **Once is enforced twice.** A ledger outside every worktree records `owed` before the
  reply and `posted` after; a damaged ledger stops the job, and one pass runs at a time.
  Before replying it reads the thread for its own receipt, in a reply by its own key, so a
  lost ledger or a lost answer never sends a second reply.
- **A session still silent an hour after its reply is stuck.** It is never replied to again,
  and it is reported on every pass, under its own ticket's kind, until a person acts. The
  monitor pages each stuck ticket once. The dispatcher stays silent on purpose in some cases,
  and only a person can tell which.
- **It holds its replies when the dispatcher cannot hear it, but keeps looking.** Each pass
  asks the dispatcher's local status, and the public front door when configured. A reply sent
  then would be one more lost delivery. A route down for two hours of AWAKE time pages: a
  wrong URL looks exactly like that, and would otherwise read healthy forever. Awake time,
  because a night of dark wakes with no network is many held passes and no outage.
- **What it found stays found until it can look again.** A pass that could not read the
  tracker, failed or ran out of time carries the last full pass's open findings, so it never
  reads as a recovery. A webhook read that fails keeps a disabled webhook disabled, and its
  hold on replies. A pass that could not finish is its own incident in the monitor.
- **Only the newest session on a ticket, and only a read it can trust.** A tie on one ticket
  gets one reply. A read cut short at its page limit in an order it could not tell is newest
  first sends no reply: an unread session could be the newer one. A session the ledger still
  watches that the paged read did not reach is re-read by id (`agentSession(id)`), never by
  reading deeper, so an old stuck ticket neither drops out of view nor stalls every pass. A
  delegation held back is recorded as owed, so one held past the lookback is reported.
- **The first count is a person's.** Its first pass would reply to every unanswered
  delegation from the past week, each a paid session. The installer lists them all, and
  installs the job only once the owner signs that count (card CK-11). It has its own step,
  like the heartbeat monitor's, so an unsigned job stops nothing else: it simply has no
  plist, and no reboot can start it. A count that grows past the signed one before the first
  load keeps it off. `CATCHUP_INTERVAL_SECONDS=off` declines it by name.
- **It checks the webhook.** A disabled webhook is a firing finding; the heartbeat monitor
  pages it once. While it fires, no reply is sent, since none could arrive.
- **At most three replies a pass, a minute apart, oldest first.** Each reply starts a paid
  session, so three caps what one pass can spend. And a burst of session starts can delay the
  dispatcher's other answers past the tracker's 5-second limit (context, point 5). Three per
  15-minute pass still drains 12 an hour, and it matches the finding poller's cap of three. The
  rest are carried and named, never dropped. A reply that fails goes to the back of the line;
  failing twice in a row it is its own incident until it lands.

## Alternatives rejected

- **A scheduled GitHub Actions job, as the ticket first proposed.** It would need the owner's
  full-scope tracker key as an Actions secret, a new place for that key to live. It also cannot
  see the dispatcher on its own machine, so it could not hold while the dispatcher is down.
  The role account already holds the key, and the heartbeat monitor plus the notifier is
  already this lane's deduplicated alert path.
- **Replay the delivery to the dispatcher locally.** Needs the webhook's signing secret, which
  forges anything.
- **Remove and re-add the delegation.** A delegation is a person's signal: the dispatcher's
  access check and the review basis's snapshot both read it. A job producing it is the
  human-signal defect class. It would also re-time every downstream "since delegation" clock.
- **A shorter wait.** Without a live test of what a late retry does after the reply, a shorter
  wait risks a second paid session. It stays a floor until that test is run.
- **Ask the dispatcher upstream to poll.** Not available; this job is the stand-in.

## Consequences

- A delegation made while the Mac slept is started 7.5 to 7.75 hours after it was made, or on
  the first pass after the Mac wakes, whichever is later. A shorter outage is covered by the
  tracker's own retries, as before.
- **The reply appears as the owner.** It is posted with the owner's key, because that key is
  what Stage E stores. Its text is fixed and says it came from this job. Accepted.
- **Stuck sessions need a person.** The job reports them until one acts.
- **Not covered:** a delegation older than the week-long lookback; a missed chat message (the
  chat app's own retries end after about six minutes; replaying one needs its signing secret);
  the review team, whose poller times its own sessions out.
- **Not proven live** (`docs/CATCH-UP.md`, *What is not proven*): a real recovery; that a late
  retry after the reply does not start a second run; that the owner's key can list the
  dispatcher's webhook; what status the tracker gives an unanswered delegation.
