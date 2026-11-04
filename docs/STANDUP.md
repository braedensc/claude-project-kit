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
3. **Still waiting on you.** The same items as the morning's first section.

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

The bridge sends it from its own one-minute loop, through its own DM. That hook is the
bridge's own change. Until it lands, nothing sends the stand-up on a schedule.

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

`~/.stage-e/standup.json`, in the role account's home. Only `schema` is required, and a
missing file means off.

```json
{"schema": "pipeline-standup-settings/1", "on": true}
```

| Key | Default | What it does |
|---|---|---|
| `on` | `false` | Turns the stand-up on. |
| `morning_at` | `"09:00"` | The morning rundown's local time, 24-hour. |
| `afternoon_at` | `"15:00"` | The afternoon wrap's local time. It must be later than the morning's. |
| `days` | `["mon", "tue", "wed", "thu", "fri", "sat"]` | The days it posts, each named once from `mon` to `sun`. |
| `late_by_minutes` | `180` | How late a rundown may still go out. It must be shorter than the gap between the two times. |
| `stale_after_minutes` | `60` | A status file older than this is old news. The status job writes every 15 minutes. |
| `status_file` | `~/.stage-e/status/pipeline-status.json` | The status job's file. |
| `bridge_config` | `~/.stage-e/bridge.json` | Where the DM member, the Slack token's variable and the writer are named. |
| `record_file` | `~/.stage-e/state/standup-record.json` | The record below. |

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

## For the bridge

Each pass:

1. `settings = load_settings()` and `record = load_record(settings)`.
2. `kind = due(now, settings, record)` gives `"morning"`, `"afternoon"` or `None`.
3. `composed = compose(kind, now, settings, record, bridge_cfg, voice.write)`.
4. `previous = claim(kind, record, settings, now)`. It saves the record before the post. If it
   raises, post nothing: a rundown that went out unrecorded would go out again every minute.
5. Post `composed["text"]` as it is: the writer has already run. Post `composed["plain"]` if
   the bridge's own checks refuse the text. If the post fails, call
   `release(kind, record, settings, previous)`, so the rundown stays due.
6. `mark_sent(kind, channel, ts, record, now, composed)`, then `save_record(settings, record)`.

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
