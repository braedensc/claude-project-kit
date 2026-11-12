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
   Both are numbers and fixed words.
4. **Blockers** and 5. **Waiting on your merge**, as in the morning.

Every part is always there: an empty one says so in words.

**The kit copy (KIT-581).** A merged fix is not live until the one command moves the role account's
copy of the kit, and the board reads Done in between. If the status file says that copy is behind (its
`kit_clone`), both messages carry one plain line before the blockers, in fixed words:

- Behind: *The role account's copy of the kit is behind main by 3 merges, in files the jobs run:
  scripts/pipeline_voice.py, schemas/x.schema.json and 2 more. A merged fix is not live until you run
  `python3 scripts/pipeline_install.py`.* Behind on a document or a test only is not said: nothing the jobs
  run changed.
- Not known: *Whether the role account's copy of the kit is up to date could not be checked, so a merged fix may
  not be live yet.* It never repeats the job's own reason.

It needs `kit_repo` (OWNER/REPO) in the status job's config; without it the field is `null` and nothing is
said. It counts every job's files, installed here or not, and cannot see local edits in the clone.

Good: *behind main by 1 merge, in files the jobs run: scripts/pipeline_notify_local.py.*
Not that: a line on every merge, or a blank where the copy could not be checked.

**The sprint (KIT-574).** A sprint is the plan's week: from one reset of the weekly usage
window to the next. With `STANDUP_SPRINT=yes`, both messages carry one fixed line right under
the opening, in fixed words:

*Sprint day 2 of 6. The week resets Tue 20:00, in 5 days. The weekly window is 38% full.*

The week's start and reset come from the usage file (`week.starts_at` and `week.resets_at`),
never from a weekday or an hour in the config, so the line moves when the plan's reset moves. A
working day is one of `days`. A date belongs to the sprint that holds its midday (12:00 local):
a reset at 20:00 belongs to the sprint that is ending. How full the window is comes from the
usage file's last word from Anthropic.

A file that cannot place the week says so, and never shows a day: *The sprint is not known: the
usage file can't place the plan's week yet.* The same for a missing, unreadable, failed or stale
file. A window whose fill is not known says that, never 0%. With the setting off (the default),
both messages are what they were.

Good: *Sprint day 2 of 6. The week resets Tue 20:00, in 5 days.* on the second working day.
Not that: a day number when the usage file is a week old, or a zero where the fill was not read.

**The sprint plan (KIT-575).** On the first morning of each new sprint, the queue's morning job writes one proposal
of the week's work. The stand-up sends it to your DM once, as a message of its own (not part of a rundown), when
`STANDUP_SPRINT_PLAN_FILE` names the file:

*The sprint plan for the week starting Wed 11 Nov. The week's budget is $60 and $10 is spent, so there is room for $50.
The plan holds 4 pieces, $18 of estimates. In the plan, in order: ... Not in the plan: ... Reply `plan sprint 2026-11-11`
to plan the sprint this way. Nothing starts from this message alone, and with no reply nothing changes.*

Your reply, `plan sprint` and the date it names, records the plan. Only your reply, in your DM, in exactly those words,
does it. A longer message, another member's, or the wrong date records nothing and is answered in words. The plan is
the proposal's pieces, as they were sent. The queue then puts those pieces first among the ones it may already start;
it starts nothing it could not start before. Code builds every word of the message from the proposal: no model writes
it, and a title is cleaned as the status file cleans one.

Good: `Recorded: the sprint starting 2026-11-11 is planned with 4 pieces.` Not that: a plan recorded for a sprint you
did not name, or one recorded twice (a second yes says `already planned` and changes nothing).

A week the usage file cannot place, no weekly budget, or a week's spend that cannot be read gives no proposal and no
message: the morning job says why in its own output and ends with exit 3. The stand-up's sprint line says when the
sprint is not known. See `docs/QUEUE-OPERATOR.md`, "Sprint planning at the reset".

**The sprint review (KIT-576).** On the sprint's last working day, from the afternoon time (or an hour before the plan's
reset, when that comes first, so a reset at 13:00 cannot lose the sprint), the stand-up writes a recap of
the week as a **Linear document** and sends **one message** that links it. Off unless `STANDUP_SPRINT_REVIEW_CONFIG`
names the review's config. Code builds the recap and the message from three things, with no model:

- the usage file: what the week spent, by kind of work, and its dearest pieces;
- GitHub: the pull requests merged in the sprint's period, which merged with auto-merge on, and what Stage E's review
  comment on each said (how many findings, of which kind);
- the weekly budget you set in the config, for the cost against the plan;
- the health watch's state file (`~/.pipeline-update/watch-state.json` of the account that runs the watch), when the
  config names it (KIT-602): the week's lowest free memory and most swap.

The document has six parts: what shipped, what merged on its own, what it cost against the plan, **the machine and the
budget**, what the reviews taught, and the screenshots the acceptance check kept (listed by name, not uploaded).

*The machine and the budget* says the week's lowest free memory and most swap, each with its day, and how many days
the watch recorded (days are the watch's own, in UTC). Under it, a table of each day's spend, the running total, and
that total as a share of the plan against an even pace (the share of the week gone by the day's end). It ends with the
plan's weekly window as the usage file last saw it. A figure the sources did not give is said, never shown as zero: with
no health file named, or one that cannot be read, the memory line says why; a day the usage file left out is *no
figure*, and a day with runs the usage file could not price (its `unpriced`) is *at least* its dollars; every running total after either reads *at least*.

Get a config with `python3 scripts/pipeline_sprint_review.py --example-config`. Its keys: `repos` (OWNER/REPO, at most
10), `github_token_env` and `linear_key_env` (the NAMES of variables; a READ-ONLY GitHub token, and a Linear key that
can create a document), `review_authors` (the GitHub logins whose comments count as Stage E's review: a comment from
anyone else, or with the mark far from its start, is ignored), `linear_project_id` (the project the document is filed in), `weekly_budget_usd`, `review_dir`
(the record and a copy of each review), `screenshots_dir`, `health_state_file` (the health watch's state file, `memory_trend`; empty: the memory line says it is not shown), `queue_config_file` (the queue's settings file: names it to turn on the proposals below), `max_pull_requests` and `max_pages` (caps).

Good: *Sprint review: Wed 11 Nov to Tue 18 Nov. 7 pull requests merged (2 on their own). The sprint spent $62.50 of
the $100 planned. Stage E raised findings on 3 of the 7 it reviewed. The full review: <link>*.
Not that: a message with no link and no reason. If Linear refuses the document, the message says *I could not write the
full review to Linear (...). A copy is saved on this machine at <path>.* The copy is always saved.

It happens once a sprint. The record in `review_dir` is written before the message is sent, so a review is never built
twice; a post that fails is sent again from the saved text. A page limit that was hit, or a repository that could not be
read, is said inside the document, never counted as complete. GitHub is read for at most about a minute and a half in
one pass, and the review is not started with less than 40 seconds left of the pass. "Merged on its own" is GitHub's own `auto_merge` field,
so it is only as true as that field. The model-written deeper review is `/weekly-review --sprint`.

**The machine and the budget (KIT-609).** The health watch checks that the Mac's memory is not running out and that
the plan's usage is not being burnt too fast (`memory` and `burn`, `docs/UPDATE-OPERATOR.md`). When `STANDUP_HEALTH_FILE`
names its status file, both messages stay **silent about both when all is usual**, and add **one plain line** when
either is firing: what, how far over, and what the system does about it.

Good: *Machine and budget: At this pace the weekly window will be 105% used at its reset (60% used, 4.0 days into the
week; the line is 90%). Nothing is stopped by this: the watcher's own limits and the daily pacing (if it is on) apply
as before.* Not that: a line every day saying memory is fine.

The line is the watch's own sentence, numbers and fixed words only. A health file that is off, missing, more than three
hours old or in another shape says nothing here: a dead watch is the heartbeat monitor's to page. Neither kind pages
by itself; the urgent rules are unchanged.

**Changes the sprint review proposes (KIT-577).** With `queue_config_file` in the review's config (the queue's settings
file, `~/.stage-e/queue/queue.json`), the review also looks at what the sprint spent against what it shipped and
proposes **at most three small changes** to that file. Each comes as a message of its own, after the review, built by
code from the key and the two values:

*Sprint change 1 of 1: lower planned_load from 40 to 30. The sprint spent $62.50 of the $50 planned (125%). Reply
`apply planned_load 30 2026-11-10` to make this change; nothing changes without that exact reply. It is kept in
queue.conf, so the next queue install keeps it.*

The rules are two. Over the plan: a lower `planned_load` (three quarters of it). A quarter of the plan spent by the coding runs and no pull
request merged: one lane fewer (`lanes_per_repo`), or a lower load when there is only one. The list of keys a review may
propose is two: `planned_load` and `lanes_per_repo`. Models per role, a grader, a guard and a budget are not on it, so
none can be proposed. It proposes only to **tighten**: it never raises a load, a lane count or a budget, so it cannot
make the queue spend more. Loosening a limit is your own edit.

Only your exact reply makes a change, in your DM: `apply`, the key, the value and the sprint's first day, with nothing
else in the message. It changes that one key if that exact change was sent to you, is not more than ten days old, is
still a lower setting, and the queue's settings still say what the proposal read; otherwise it changes nothing and says
why. A second yes says it was already done. The old and the new value are always in the reply. The change goes through
the one write path into queue.conf (KIT-700): the installed copy of queue.conf and the queue's settings, both read back,
and a ledger row. The next queue install carries it into your queue.conf. The same function backs a command, which
shows both values and changes nothing without `--yes`:

```
python3 scripts/pipeline_sprint_changes.py apply --changes <review_dir>/changes.json \
    --queue-config ~/.stage-e/queue/queue.json planned_load 30 2026-11-10
```

With no settings file named there is no proposal and no section. One the review cannot read means no proposal, and the document says so. The section also lays out each kind of work's spend against what it shipped (only the coding runs ship pull requests), so every proposal's numbers can be checked against the whole.

**Queue settings that changed (KIT-700).** When the bridge names the queue's config, both messages name each queue
setting changed since the last stand-up, from the ledger beside it, in one plain line:

*Queue settings changed: the planned load from 40 to 30 (you asked in the DM, 14:02).*

With no change there is no line. A ledger that cannot be read is said: *Queue settings: the queue settings ledger could
not be read.*

**The queue's room (KIT-474).** When the machine is short of memory, free disk or swap, the queue
starts nothing new. If the status file says so (its `queue_room`), both messages carry one plain
line after the body and before the blockers, in fixed words:

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

**Who words it.** Code, from the status file's fields only, word for word. The writer that
reworded "done" and "today" went with the cutover (KIT-492).

## When it goes out

A rundown is due from its time until three hours after it (`late_by_minutes`), once a day, on
the days in `days`. Later than that it is missed. It is never sent late. A stale status file at its time is
waited out, not given up on (below).

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

`preview` prints the plain rundown and changes nothing. `run --dry-run` prints the rundown that
is due, and posts and records nothing.
`run` without `--dry-run` posts the rundown that is due, through the bridge's Slack client.
The bridge has no record of a post made that way.

## After the Mac wakes

The status job writes its file every 15 minutes, but only while the Mac is awake. A Mac that sleeps across
the stand-up's time wakes with a file from before the sleep. The stand-up can fire in the first minutes
after the wake, before the status job's first run, and would see that old file.

So a stale file at the stand-up's time is waited out, not given up on:

- **It waits, and says nothing.** Each pass looks again. Nothing goes to the owner while it waits.
- **A fresh file sends the stand-up then.** The window is still `late_by_minutes` from the stand-up's time.
- **It never sends twice** for one moment.
- **If the file never freshens in the window,** one plain line says so, once: the stand-up was missed,
  when the file was last written, and that it stopped waiting. A file that stays stale while the Mac is
  awake ends there, never in silence.
- **A missing or unreadable file is not a wake.** It is said at once, as before.
- **A window that passed with no wait recorded** (the bridge was down) is not said late.

Good: `Good morning. There was no stand-up this morning: the status file was last written at 6:59 AM, and
it still wasn't fresh when I stopped waiting at 12:10 PM. I won't pass on old news.` Not that: no message
at all, or an old file's news.

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
| `BRIDGE_CONFIG` | `bridge_config` | `~/.stage-e/bridge.json` | Where the DM member and the Slack token's variable are named. |
| `STANDUP_DAY_PLAN_FILE` | `day_plan_file` | empty: off | The day plan file (contract §18), usually `~/.stage-e/queue/day-plan.json`. Set, the afternoon wrap says today's spend against the day's planned load and hard stop. Today's spend comes from the usage file when `STANDUP_USAGE_FILE` is set too. |
| `STANDUP_USAGE_FILE` | `usage_file` | empty: off | The usage file (contract §17), usually `~/.stage-e/usage/usage.json`. Set, the afternoon wrap on the weekly day adds the week. |
| `STANDUP_WEEKLY_DAY` | `weekly_day` | empty: the last of `days` | The day the week is added, one of `days`. |
| `STANDUP_SPRINT` | `sprint` | `no` | `yes`: both messages add the sprint line above. Needs `STANDUP_USAGE_FILE`. |
| `STANDUP_SPRINT_PLAN_FILE` | `sprint_plan_file` | empty: off | The sprint plan file (KIT-575): the queue's `SPRINT_PLAN_FILE`, the same path. The stand-up sends a waiting proposal to your DM once. Needs `STANDUP_SPRINT=yes`. |
| `STANDUP_SPRINT_REVIEW_CONFIG` | `sprint_review_config` | empty: off | The sprint review's config file (KIT-576, below). Needs `STANDUP_SPRINT=yes`. |
| `STANDUP_HEALTH_FILE` | `health_file` | empty: off | The health watch's status file (its `HEALTH_STATUS_FILE`, `docs/UPDATE-OPERATOR.md`). Set, both messages add the machine-and-budget line below when something is off. |
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
| `by`, `why` | `code`: every rundown is worded by code since the cutover (KIT-492). A record from before it may say `model` or `fallback`, and why. |
| `status_written_at` | The status file it was written from. |
| `problem` | What it said instead of news, or `null`. |
| `waiting` | Top level, by `morning` and `afternoon`: while a rundown waits for a fresh status file, its `day`, when it `since` began waiting, the file's `last_written`, and whether the missed line was `said`. A send removes it. |
| `room_said` | What was last said of the queue's room hold (top level, not per message): `held <since>` or `resumed <time>`. A resume is said once. |

The missed-rundown check reads it. A missing record means nothing has gone out yet.

## In the bridge

The bridge reads `is_on()` at the start of each pass, and skips each piece's start message
while it is on. After reading its DMs, it calls `bridge_tick(pass)` once, which:

1. Loads the settings and the record, and asks `due(now, settings, record)`.
2. Finds the owner's DM among the ones the pass has read. Until that DM is open, it waits.
3. Composes the rundown, by code alone.
4. Claims it with `claim`, saving the record before the post. If that fails, nothing is posted:
   a rundown that went out unrecorded would go out again every minute.
5. Posts it through the pass's own `post()`, with no kind, so the post is the bridge's own. If
   the post fails, `release` keeps it due.
6. Records it with `mark_sent` and `save_record`.

### The tech lead's take

With a blocker, and only then, the tech lead gives its take (KIT-493). It needs the tech lead's
own turn switched on (`TECH_LEAD_TURN=yes`, `docs/TECH-LEAD-TOOLS.md`). Before the post, the
bridge runs one turn over the tech lead's tools. Its prompt is fixed words with the blockers
fenced as data. The turn answers in at most three plain sentences, or `NO TAKE`. A take goes at the
end of the same message, after "The tech lead's take on the blockers:". It is made inert as any
tech lead words are: a credential shape or a line telling you what to type is taken out, or the
take is dropped.

If the take proposes an action, it calls the matching tool. The tool only records it. Right
after the rundown, the bridge asks you about it in its own fixed words, as it does for any action
the tech lead sets up. A ticket the tech lead picked needs `yes <id>`. The tools refuse work the
pipeline cannot land. A rundown carries none of your words, so it never asks to send an answer.

The bridge records a rundown with a take as the tech lead's words. So a ticket the take names
needs `yes <id>`, even when you then ask for it yourself. A reply under that rundown goes to the
tech lead's turn, as a reply under its answer does.

With no blocker the rundown is the only message, asks nothing of the turn, and pings nobody.
The turn off, too little of the pass left, a failed turn, `NO TAKE` and a withheld take each
leave the message as it was.

Good: the morning has one blocker. The rundown has its take at the end, and one fixed question
follows if the tech lead proposed an action.
Not: a second message from the tech lead after the rundown, or a take on a quiet morning.

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
