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

So a delegation made while the Mac slept for more than 7 hours is lost, with no error
anywhere, and a disabled webhook loses everything after it the same way.

## Decision

A Stage E daemon, **the catch-up poller**, runs every 15 minutes as the dispatcher's role
account with the owner-scoped tracker key Stage E already stores.

- **It finds what the dispatcher never answered.** The agent's sessions, read newest first: an
  agent session with no activity at all from the agent, never started, ended or dismissed, on
  a configured work team, not a planning ticket, not done or canceled, still delegated to the
  agent, with a person who delegated it and a root comment to reply under.
- **It waits until the tracker has given up.** A delegation is eligible only after 7 hours
  30 minutes. The script refuses any wait below the tracker's 7-hour retry ladder. Replying
  sooner would race a late retry into a second session, since the dispatcher keeps no record
  of what it started. Older than a week is skipped.
- **It replies once, in the thread.** The reply is fixed text with a marker, under the
  session's root comment. Nothing else is written: no ticket, state, label or delegation.
- **Once is enforced twice.** A ledger outside every worktree records `owed` before the
  reply and `posted` after; a damaged ledger stops the job. Before replying it reads the
  thread for its own marker, so a lost ledger or a lost answer never sends a second reply.
- **A session still silent an hour after its reply is reported as stuck, once**, and never
  replied to again. The dispatcher stays silent on purpose in some cases, and only a person
  can tell which.
- **It holds its pass when the dispatcher cannot hear it.** Each pass first asks the
  dispatcher's local status, and the public front door when configured. A reply sent then
  would be one more lost delivery.
- **It checks the webhook.** A disabled webhook is a firing finding; the heartbeat monitor
  pages it once. While it fires, no reply is sent, since none could arrive.
- **At most three replies a pass, a minute apart, oldest first.** The rest are carried and
  named, never dropped.

## Alternatives rejected

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
- **Stuck sessions need a person.** The job reports them and stops.
- **Not covered:** a delegation older than the week-long lookback; a missed chat message (the
  chat app's own retries end after about six minutes; replaying one needs its signing secret);
  the review team, whose poller times its own sessions out.
- **Not proven live** (`docs/CATCH-UP.md`, *What is not proven*): a real recovery; that a late
  retry after the reply does not start a second run; that the owner's key can list the
  dispatcher's webhook.
