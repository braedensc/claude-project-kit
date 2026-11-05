# The daily stand-up

Two messages a day in the owner's Slack DM, written from the status file
(`docs/PIPELINE-CONTRACT.md` §16): a morning rundown and an afternoon wrap. They go out Monday
to Saturday by default, with Sundays off. It is off until you turn it on. The code is
`scripts/pipeline_standup.py`.

## What each message says

**The morning** (09:00 by default):

1. **Needs your OK.** Everything that needs you comes first. Each item says what it is, what
   it holds up, and how to approve it:
   - A plan waiting for approval: reply `approve <plan>`. The DM then asks you to confirm.
   - A pull request ready to merge: merge it on GitHub. The link is in the item.
   - A pull request waiting on a check only a person clears, such as the `hooks-change`
     label: act on it on GitHub. The link is in the item.
   - A piece stuck until you answer: answer in its thread, or on the ticket.

   If nothing needs you, it says so in one line.
2. **In progress.** Each started piece, with its pull request's checks and review.
3. **Moved since yesterday.** What changed since the last morning rundown, or in the last
   24 hours if there was none. After a day off it says since when, such as "since Saturday".

**The afternoon** (15:00 by default):

1. **Shipped today.** Pull requests merged and plans finished since midnight.
2. **Started and finished today.** Each piece's start and finish.
3. **Spend** (only when `STANDUP_DAY_PLAN_FILE` or `STANDUP_USAGE_FILE` is set, below). One line
   from today's day plan: "Spend today: $12 of the $40 planned (hard stop $60), measured as plan
   use at API prices, not a bill." On the weekly day (the last of `days`, or `STANDUP_WEEKLY_DAY`)
   a short week from the usage file: the week's spend, how full the weekly window is when the
   file knows, and how many pieces had runs. The usage file holds no count of pieces started or
   finished, so it gives none. A file that is missing, unreadable, over an hour old or, for the
   day plan, for another day, gives one line saying so. It never shows a zero it did not read.
   Both are numbers and fixed words, and the writer never sees them.
4. **Still waiting on you.** The same items as the morning's first section.

Old news is never sent:

- A status file that is missing, unreadable or over an hour old is said instead.
- A source the status job could not read (Linear, GitHub, the review records) is named.
- A quiet day says what was checked.

**Who words it.** The bridge's writer rewords everything after the asks, from the status
file's fields only. The asks stay word for word. If the writer is off or fails a check, the
plain text goes out.

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
| `STANDUP_DAY_PLAN_FILE` | `day_plan_file` | empty: off | The day plan file (contract §18), usually `~/.stage-e/queue/day-plan.json`. Set, the afternoon wrap says today's spend against the day's planned load and hard stop. |
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
- `ASK_SOURCES` takes the "Needs your OK today" items, each with how much of today's plan it
  holds up.
- `MORNING_SECTIONS` takes "today's plan", first. `AFTERNOON_SECTIONS` takes "spend against
  the day's limit".

## Exit codes

| Code | Meaning |
|---|---|
| 0 | A rundown went out from a current status file, or none was due. Off says so. |
| 3 | A rundown said the status file was stale, missing or partly read, or one was missed. |
| 1 | A rundown was due and could not be sent. |
| 2 | The settings are wrong. Every problem is named. |
