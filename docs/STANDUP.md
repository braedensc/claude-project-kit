# The daily stand-up

Two messages a day in the owner's Slack DM, written from the status file
(`docs/PIPELINE-CONTRACT.md` §16): a morning rundown and an afternoon wrap. They go out Monday
to Saturday by default, with Sundays off. It is off until you turn it on. The code is
`scripts/pipeline_standup.py`.

## What each message says

Each message is a report, not a request (KIT-483). It says what got done and what comes next.
Only a blocker asks you for anything, with the exact reply that clears it. With no blocker, the
message asks nothing.

**The morning** (09:00 by default):

1. **Done since yesterday.** Pull requests merged, pieces and plans finished or canceled, and
   pull requests reviewed, one line each. It covers the time since the last morning rundown,
   or the last 24 hours if there was none. After a day off it says since when, such as "since
   Saturday". With nothing, it names the last thing that shipped.
2. **Today.** From today's day plan when the stand-up reads one (`STANDUP_DAY_PLAN_FILE`,
   below): each piece, planned, started, done or held, and why. Otherwise what is in flight,
   with its pull request's checks and review, then each piece queued to start. With nothing,
   it says so, and that nothing starts until a plan is approved: a statement, never a
   question. A day plan that is set but cannot be used is said.
3. **Blockers.** Only what holds work until you act, each with what clears it:
   - A plan waiting for approval: reply `approve <plan>`. The DM then asks you to confirm.
   - A piece stuck until you answer: answer in its thread, or on the ticket.
   - A pull request whose conflict the automatic fix gave up on, or that fails only together
     with the other open pull requests: the link to act on GitHub.
   - The day plan's own items, each with the plan's reply, such as `start <ticket>`.

   A failing check or a review that sent work back is the pipeline's to fix, and shows under
   Today. With no blocker it says "No blockers. Nothing needs you."
4. **Waiting on your merge.** Each open pull request ready to merge, or waiting only on a
   check a person turns green, such as the `hooks-change` label, with its link. This covers
   pull requests outside any plan too, in every repository the status job lists (KIT-487).

**The afternoon** (15:00 by default):

1. **Shipped today.** Pull requests merged and plans finished since midnight.
2. **Started and finished today.** Each piece's start and finish.
3. **Spend** (only when `STANDUP_DAY_PLAN_FILE` or `STANDUP_USAGE_FILE` is set, below). One line
   from today's day plan, which is written once each morning and is used all day if it is for
   today: "Spend today: $14.50 of the $40 planned (hard stop $60), measured as plan use at API
   prices, not a bill." The planned load and hard stop are the plan's. Today's spend is the usage
   file's figure for today, which is rewritten every few minutes. If the usage file is off or
   cannot be read whole and fresh, the line gives the plan's own figure, "as of 09:05 when the
   plan was written". On the weekly day (the last of `days`, or `STANDUP_WEEKLY_DAY`) a short week
   from the usage file: the week's spend, how full the weekly window is when the file knows, and
   how many pieces of coding work had runs. The usage file holds no count of pieces started or
   finished, so it gives none. A file that is missing, unreadable, for another day (the plan) or
   over an hour old (the usage file) gives one line saying so. It never shows a zero it did not
   read.
   Both are numbers and fixed words, and the writer never sees them.
4. **Blockers** and 5. **Waiting on your merge**, as in the morning.

Every part is always there: an empty one says so in words.

**The queue's room (KIT-474).** When the machine is short of memory, free disk or swap, the queue
starts nothing new. If the status file says so (its `queue_room`), both messages carry one plain
line after the body and before the blockers, in fixed words and never through the writer:

- Held: *New starts are on hold for want of room: free disk 18.2 GB is under the 25 GB line (since
  14:05, last checked 14:20). Nothing already running is touched.* It names each line that is
  over. A reading that could not be taken says *could not be read*, never a number. If the check
  is more than three hours old, it adds how old, because the room may have changed since.
- Resumed: *New starts have resumed since 14:40: the machine has room again.* It is said once.
  The record keeps what was last said (`room_said`), so the next rundown stays quiet.
- Not known: *Whether new starts are on hold for want of room is not known: the queue's room
  record could not be read.* It never repeats the record's own words.

With no `queue_room` in the file, or `null`, nothing is said: the queue is off, or has not read
the machine yet. Nothing already running is stopped by a hold.

Good: *New starts are on hold for want of room: free disk 18.2 GB is under the 25 GB line.*
Not that: a hold that reads as a closed working window, or a zero where a reading could not be
taken.

Both end with one fixed line: how many plans, pieces and pull requests the file covers, and
how many open pull requests outside a plan. If the status job could not list every
repository's open pull requests, or names none, the line says so: check GitHub for those. A
status file from before KIT-487 holds no pull request outside a plan, and the line says that.

Old news is never sent:

- A status file that is missing, unreadable or over an hour old is said instead.
- A source the status job could not read (Linear, GitHub, the review records) is named.

**Who words it.** The bridge's writer rewords "done" and "today" (the afternoon's first two
parts), from the status file's fields only. Blockers, what waits on your merge and the last line
stay word for word. If the writer is off or fails a check, the plain text goes out.

## When it goes out

A rundown is due from its time until three hours after it (`late_by_minutes`), once a day, on
the days in `days`. Later than that it is missed. It is never sent late.

The bridge sends it from its own one-minute loop, into the DM of the first member in its
`dm_user_ids`. It is the bridge's own post, so a bare `yes` to an open question still works.
While the stand-up is on, a piece that starts gets no message of its own: the afternoon wrap
lists it. Anything the stand-up could not do is said at the end of that pass, and the bridge's
other work goes on.

Try it by hand, as the role account:

```bash
python3 scripts/pipeline_standup.py preview --kind morning
```

```bash
python3 scripts/pipeline_standup.py run --dry-run
```

`preview` prints the plain rundown and changes nothing. `run --dry-run` also asks the writer.
`run` without `--dry-run` posts the rundown that is due, through the bridge's Slack client.
The bridge has no record of a post made that way.

## Settings

Set them in `bridge.conf`, then run the bridge installer's `standup` command
(`docs/BRIDGE-OPERATOR.md`, "The daily stand-up"). It shows what it will write, asks one yes,
writes `~/.stage-e/standup.json` in the role account's home as that account, and reads it
back. `install --apply` writes the same file. A missing file means off.

The file is written whole, from `bridge.conf` alone. A hand edit is written over, and the three
settings with no `bridge.conf` key below always take their defaults.

| bridge.conf | In the file | Default | What it does |
|---|---|---|---|
| `STANDUP` | `on` | off | Turns the stand-up on. It needs `DM_USER_IDS`: it goes to the first member's DM. |
| `STANDUP_MORNING_AT` | `morning_at` | `09:00` | The morning rundown's local time, 24-hour. |
| `STANDUP_AFTERNOON_AT` | `afternoon_at` | `15:00` | The afternoon wrap's local time, more than 3 hours after the morning's. |
| `STANDUP_DAYS` | `days` | `mon, tue, wed, thu, fri, sat` | The days it posts, each named once from `mon` to `sun`. |
| `STANDUP_STATUS_FILE` | `status_file` | `~/.stage-e/status/pipeline-status.json` | The status job's file: stage-e.conf's `STATUS_FILE`, if you set one. |
| `BRIDGE_CONFIG` | `bridge_config` | `~/.stage-e/bridge.json` | Where the DM member, the Slack token's variable and the writer are named. |
| `STANDUP_DAY_PLAN_FILE` | `day_plan_file` | empty: off | The day plan file (contract §18), usually `~/.stage-e/queue/day-plan.json`. Set, the afternoon wrap says today's spend against the day's planned load and hard stop. Today's spend comes from the usage file when `STANDUP_USAGE_FILE` is set too. |
| `STANDUP_USAGE_FILE` | `usage_file` | empty: off | The usage file (contract §17), usually `~/.stage-e/usage/usage.json`. Set, the afternoon wrap on the weekly day adds the week. |
| `STANDUP_WEEKLY_DAY` | `weekly_day` | empty: the last of `days` | The day the week is added, one of `days`. |
| none: always the default | `late_by_minutes` | `180` | How late a rundown may still go out. |
| none: always the default | `stale_after_minutes` | `60` | A status file older than this is old news. The status job writes every 15 minutes. |
| none: always the default | `record_file` | `~/.stage-e/state/standup-record.json` | The record below. |

## The record

`record_file` holds, for `morning` and `afternoon`, the last time each went out:

| Field | What it holds |
|---|---|
| `day` | The local date it went out. |
| `sent_at` | When, in UTC. |
| `channel`, `ts` | The Slack message. |
| `by`, `why` | `model` or `fallback`, and why the plain text went out. |
| `status_written_at` | The status file it was written from. |
| `problem` | What it said instead of news, or `null`. |
| `room_said` | What was last said of the queue's room hold (top level, not per message): `held <since>` or `resumed <time>`. A resume is said once. |

The missed-rundown check and the tech lead's wake read it. A missing record means nothing
has gone out yet.

## In the bridge

The bridge reads `is_on()` at the start of each pass, and skips each piece's start message
while it is on. After reading its DMs, it calls `bridge_tick(pass)` once, which:

1. Loads the settings and the record, and asks `due(now, settings, record)`.
2. Finds the owner's DM among the ones the pass has read. Until that DM is open, it waits.
3. Composes the rundown, with the writer cut to what is left of the pass (none in a dry run).
4. Claims it with `claim`, saving the record before the post. If that fails, nothing is posted:
   a rundown that went out unrecorded would go out again every minute.
5. Posts it through the pass's own `post()`, with no kind, so the writer does not run twice. A
   text that would mention the chat bot goes out plain. If the post fails, `release` keeps it
   due.
6. Records it with `mark_sent` and `save_record`.

Every text `compose` returns has Slack's `&`, `<` and `>` escaped, so nothing in it can mention
anyone, make a link or broadcast.

`missed(now, settings, record)` names a rundown whose window passed, so it can be said.

## Room for MVP 2

- `rundown_times` is the only place the two times and the days come from. The working window
  replaces it: its days, its start and its end.
- `ASK_SOURCES` (blockers) and `WAITING_SOURCES` (merges) already read the day plan's "needs
  your OK" items, each with how much of today's plan it holds up.
- "Today" already reads the day plan's lanes when `STANDUP_DAY_PLAN_FILE` is set. The afternoon
  gives the day's spend from it.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | A rundown went out from a current status file, or none was due. Off says so. |
| 3 | A rundown said the status file was stale, missing or partly read, or one was missed. |
| 1 | A rundown was due and could not be sent. |
| 2 | The settings are wrong. Every problem is named. |
