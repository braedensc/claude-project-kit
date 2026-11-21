# The queue's installer — operator steps

The queue starts the owner's approved work without a person, inside a working window and a daily budget. Two small jobs
feed it: the usage job (KIT-422) and the morning job (KIT-446). `scripts/pipeline_queue_setup.py` (KIT-456,
`npm run test:queue-setup`) sets them up. You run it, at your own terminal. No session installs or loads anything.

**It needs Stage E and the bridge first** (`docs/BRIDGE-OPERATOR.md`, *With the queue on*). The jobs run as the
dispatcher's role account, from Stage E's clone, with its env file. The installer creates none of these.


**Review tickets are never work** (KIT-596). They sit on the work teams, told by their
`[repo=reviews-…]` tag or `reviews-…` label, and the pool names them `review-ticket`.
`REVIEWS_TEAM_KEY` in `queue.conf` is optional: set it only to name an old separate Reviews
team, whose tickets are never work either.

## Run it

    cp queue.conf.example queue.conf && chmod 600 queue.conf    # edit it; none of it is secret
    python3 scripts/pipeline_queue_setup.py run --dry-run        # read it first: it changes nothing
    python3 scripts/pipeline_queue_setup.py run                  # stops at each card; do it, then run it again

**The one command runs this for you** (`python3 scripts/pipeline_install.py`, step 3e) when this Mac has a `queue.conf`.
It runs the dry run and shows it. If a row says `WOULD-CHANGE` it asks, then runs `run` on a yes. If only a card is left
(loading the jobs is yours) it asks nothing and the end list says "waiting on you". With no `queue.conf` the queue reads
"off on this Mac". This is how the usage job gets its settings and job file after a merge.

Good: `DRY RUN: nothing was changed`, and each row says `ALREADY-DONE` or `WOULD-CHANGE`.
Not that: `Your conf has N problem(s)`. Each line names a key, never its value. Three placeholders are refused until you
edit them. `UNKNOWN` means it could not look, which is not a pass.

## What each step does

**preflight.** It checks the role account, the queue modules' selftests, the account's clone and its env file.
Good: one line saying all pass. Not that: `the clone ... is not level with this checkout`. Merge the change, then let
Stage E's installer move the clone. Not that: `lacks <name>`. That name is missing from the env file.

**config.** It writes three files, mode 600, after the jobs' own loaders accept them: the two configs, and an installed
copy of your queue.conf beside the queue's config (KIT-700). First it carries in any queue setting the tech lead changed
(next section). Good: `wrote ..., mode 600`. Not that: `the jobs' own loaders REFUSE this composition`. Their words follow. Nothing was written.

**jobs.** It installs two LaunchDaemon plists and does not load them.
Good: `installed ... NOT loaded`. Not that: `... is NOT the usage job`. Another job's plist is already there, so it will not
write over it. Pick labels nothing else uses.

**dry-run.** The usage job, the reader and the morning job run once each, as the role account, with their dry runs.
Good: three lines, each `exit 0` or `exit 3` (ran, and one source was not read whole). Not that: `FAILED`, with the
job's last line. A credential is never shown.

**bridge (card CK-Q1).** Set `QUEUE_CONFIG` in `bridge.conf`, then run the bridge installer's `install --apply`.
Good: `the bridge's config names this queue config`. Not that: `names another queue config`. The two paths differ.

**enable (card CK-Q2).** Run the card's `sudo launchctl enable` and `bootstrap` lines. Loading starts the jobs. Inside the
window the morning job makes one capped model call. Neither starts a session: the bridge does.
Good: `both jobs are loaded`. Not that: `loaded and running a DIFFERENT command`. The plist changed after you loaded it.
Unload it, then load it.

**handover.** Good: `ON`, `OFF` and `NOT PROVEN` lines, then `DONE`. Read the last group. The first live start is the test.

## When the watcher holds new starts

The watcher (`docs/WATCHER.md`) can stop the queue from starting anything. When a monitor flags something at level
`pause`, it writes a hold file. Name that file in the queue's config, in a `hold` section: `{"file": "/path/to/hold.json"}`.
While the file exists, the queue reads nothing and starts nothing, and says so. A file that is not a valid hold holds
too. With no file named, the watcher cannot stop a start, and every pass says so.

Only you clear a hold, in your own terminal: `python3 scripts/pipeline_hold.py clear --hold-file /path/to/hold.json`. It
shows what is held, asks for a typed `yes`, removes the file and reads it back. It is refused inside an agent's
environment.

Good: `held: the watcher has held new starts: protected-label acme/app#12: nothing would start`.
Not that: a start after a hold was set. Run `python3 scripts/pipeline_hold.py show --hold-file /path/to/hold.json`.

## When the validator approves a piece's start

A piece of a plan you approved waits for your `start` when it was added to the plan, or changed, after your
approval. With the validator's pieces switch on, the validator may give that start instead (KIT-543,
`docs/VALIDATOR-OPERATOR.md`, "A piece starting"). The queue reads its decision from two files, named in the
`reader` section of the queue's config:

- `validator_settings_file`: the validator's settings. The pieces switch must be on.
- `validator_ledger_file`: the validator's ledger.

Both empty is the default, and then your `start` decides, as before. The queue's account must be able to read
and trust both files: a copy owned by root or by that account, outside every repository. The install puts the
copies in place.

A piece starts on the validator's approval only when the approval is newer than the piece's last change. A piece
changed after it is held for your `start` again, until the validator judges it again. The watcher's hold still
stops every start. A piece the validator held, or escalated, waits for you as before. Its start comment says the
validator approved it, never that you did.

Good: `Started by the queue, in lane acme/app:1: it is a piece of TOD-10, and the validator approved its start at
2026-11-18 10:00 UTC.` Not that: a piece changed after the validator's approval starting on that approval.

The bridge reads the same two files for plans. With the plans switch on, the validator may approve a plan in your
place (KIT-544, `docs/VALIDATOR-OPERATOR.md`, "A plan"). The bridge records that approval as the validator's, and
the queue starts its pieces as it starts those of a plan you approved. Each start comment names the validator.

## When the Mac is short of room

Before any start, the queue reads three numbers: memory pressure, free disk on the data volume, and swap in use. Past a
line on any of them, it starts nothing new. Running work finishes. The lines default to under 25 GB free, memory pressure
at critical, and over 4 GB of swap. To change them, add a `room` section to the queue's config: `min_free_disk_gb`,
`max_pressure` (`warn` or `critical`) and `max_swap_gb`. A reading it cannot take holds starts too.
Good: `QUEUE: free disk 18.2 GB is under the 25 GB line`, then later `room again: starts resume`.
Not that: starts going on while the disk fills. Free some space; the next pass starts again.

## When a runaway stop holds every start

The queue has stops for a runaway: the plan near or at its limit, a day over its hard stop, a piece over its cost stop,
too many running or started, a usage file that is missing or stale, or a run signed in with an API key. Each holds every
start. A stop ends the bridge's pass loud for as long as it stands, so the heartbeat monitor pages you once that the bridge is failing, and says it is recovered on the first pass
after the stop lifts. The page does not carry the stop's name: the bridge's log and its heartbeat summary do
(`QUEUE: a runaway stop holds every start (day-hard-stop (61.5 against 60))`), and the log says
`queue: the runaway stop is lifted: starts resume` when it clears.
Good: one page when `day-hard-stop` trips, one when it clears.
Not that: starts held for hours with no page. Look at the stop the page names.

The weekly share's pause (`weekly-share`, KIT-923) holds every new start too, but it is your own setting working, so a pause at the share
sends no page: the stand-up says it. A week the usage file gives no figure for (`weekly-share unreadable`) is a stop that pages.

## When the plan's limit stops a piece

If the 5-hour or weekly window cuts a session off, the usage file lists it (`limit_stops`). After that window resets, and
only inside the working window and with room on the Mac, the queue's pass hands the piece over once more: the agent
taken off the ticket and put back, as `retry` does in a DM. It counts as a start in the day's ledger (source `resume`)
and leaves one comment naming the window and the two times.
A pass that resumes anything starts nothing new: the next pass counts the resume in the day's load first. A failed
resume ends the pass loud, before any start.

Each stop resumes once. Said in the bridge's log (`queue: resume: ...`) and as a failing pass, once per change, and never
retried by itself: a piece stopped a second time, a window that reset more than two days ago, a reset time it cannot
read, a usage file that is missing, stale or failed. It lets go, with no resume, a piece that is closed, no longer the
agent's, or handed over again since it stopped. It holds, with nothing written, one with a session still running or a
session list not read whole. Only coding pieces resume. An ordinary error is `retry`'s.

Good: `acted: the queue handed ENG-5 over again`. Not that: `queue: resume: the usage file cannot be trusted ...`. Check
the usage job. Not that: `I took the agent off ENG-5 ... nobody holds it now`. Hand it to the agent in Linear.

On a project that codes on our own runner (KIT-645, `docs/RUNNER.md`), such a piece is let go, and the log says so: the
runner's own resume of it is not built yet.

## On a project that codes on our own runner

With the runner's `lanes.code` on for a project, and the bridge wired to it (`docs/BRIDGE-OPERATOR.md`), the queue
decides as before and the bridge's start is a code request to the runner (KIT-645). The runner judges each start with
this queue's own reader and judge, and starts only a ticket a named approver let through. So a small agent-filed finding
the limits admit (`agent_filed: limits`) is refused there, and the bridge's pass ends loud once. Move such a finding into
Todo yourself to start it, or keep `agent_filed` off while a project codes on the runner. The runner reads this queue's
config file (`queue_config` in its own settings), so both must name the same file.

## Sizing the day in story points

On a subscription, dollars at API prices are not how a team sizes its work (KIT-923). The queue sizes a day the way a team
sizes a sprint, in **story points**.

- **Points per piece.** A piece's effort label gives it points: `EFFORT_POINTS=S=1,M=2,L=3` by default. A piece with **no effort
  label is not started**, and the stand-up says so (`No effort label, so not started: ...`). Give it a label and it starts.
- **Points per day and per sprint.** The queue takes on up to `POINTS_PER_DAY` points in a working day (default 10). Sprint
  planning proposes up to the sprint's points. Set **one** of `POINTS_PER_DAY` and `SPRINT_POINTS`; the other follows from the
  working days in `WINDOW_DAYS` (the sprint's points are the day's times the sprint's working days, and a `SPRINT_POINTS` of 52
  over five working days is 11 a day, rounded up). Setting both is refused. A piece bigger than the whole day may still take an
  empty day, so none is stuck. A day's points count when the queue starts a piece, and what runs now counts if that is more.
- **Lanes.** `LANES_PER_REPO` stays the number of side-by-side lanes in a repository (1 to 3). A day's points can run in them in parallel.
- **A pause at the weekly usage share.** Set `USAGE_PAUSE_SHARE` (a percent, for example 85) and new starts pause when the
  subscription's weekly window has filled that far. Work already running goes on, and new starts go on when the week resets or you
  raise the share. Empty is off. A usage file that gives no figure for the week holds new starts too, and says so: that is never
  read as being under the share.
- **Dollars are runaway stops only.** `PLANNED_LOAD` and `DAY_STOP` (and the piece stop) stay, set high on purpose (400 and 600). A day
  that reaches one is a day something ran away, not a normal day.

The stand-up says each state in words: `Points today: 4 of 10 done (started), 6 left.`, `Points this sprint: 12 of 40 done (started),
28 left.`, `New starts are paused at the weekly share: the week's usage is at 87.5%, and the share you set is 85%. ...` and `New starts are
holding: the usage file gives no figure for how full the week is, ...`. The same numbers are in the day plan file (`points`, contract §18)
and the status file (`queue_points`). A piece counts as done when the queue starts it; a finished-and-merged count is not kept.

**An existing `queue.conf`** that lacks these keys still loads, with the defaults. But if it still says `PLANNED_LOAD=40` and `DAY_STOP=60`
(the old dollar day), those are now runaway stops, and a normal day will reach them: raise them (`PLANNED_LOAD=400`, `DAY_STOP=600`) or delete the two
lines, then add `POINTS_PER_DAY` (or `SPRINT_POINTS`) and, if you want the pause, `USAGE_PAUSE_SHARE`.

Good: `Points today: 4 of 10 done (started), 6 left.` Not that: no points line at all. The day plan has no `points` block until the morning job
has run the new version once, and the stand-up says nothing about points until then.

## Pacing each day to the plan's weekly reset

The plan's usage limit resets once a week, and a busy Monday can spend what Friday needed. Set `PACE_TO_RESET=yes` and
`WEEKLY_BUDGET_USD` (dollars at API prices) in `queue.conf` and the queue sizes each day's planned load from the budget
as the day began (the week's spend less today's), divided by the working windows left before the reset, today's included. The day's hard stop keeps its ratio to the
load. Today's own spend does not shrink the day, so a pass late in the day plans what the morning did. Both the morning job and the queue's pass do this, so the plan and the starts agree.

The reset is the usage file's own `seven_day` reset time, never a fixed day, so pacing moves when the plan's reset moves.
Pacing lowers the two dollar runaway stops, never the day's points, and never above `PLANNED_LOAD` and `DAY_STOP`. A week with nothing left plans a
cent, so any spend holds every start. It is off by default, and off changes nothing.

Good: `paced to the weekly reset: 54.0 left of 90 at the start of today, over 3 working windows, so today plans 18.0 and stops at 27.0`. Not that:
`pacing is on, but the usage file cannot place the plan's week, so the day stays at its configured figures`. The day then
runs at `PLANNED_LOAD` and `DAY_STOP`, as if pacing were off. Check the usage job. Other causes: a reset in the past or more
than eight days off, no spend for today in the usage file, and no working window before the reset.

## Sprint planning at the reset

A sprint is the plan's week (`scripts/pipeline_sprint.py`). Set `SPRINT_PLAN_FILE` in `queue.conf`
and, on the first morning of each new sprint, the morning job also writes **one proposal** of the week's work into that
file: the pieces the pool admitted, in the queue's own order, as many as fit the sprint's story points, each at its effort's points.
What does not fit says why: over the sprint's points, no effort label (so not started), or the pool's reason in plain words. No dollar
budget is needed. A proposal written before points (in dollars) still loads and reads as it was.

A held piece that one reply would start keeps that reply in the list. The proposal offers nothing the pool did not
admit, so it never offers work the pipeline cannot land. It is not proposed again for the same sprint. The stand-up
sends it (`docs/STANDUP.md`, "The sprint plan"), and your reply `plan sprint <date>` records the plan in the same file.

What the queue does with a recorded plan: **its pieces go first** among the pieces already allowed to start, both in the
morning job's day plan and in the starter that actually starts them (it reads the same `morning.sprint_plan_file`). That is all.
It admits nothing, raises no budget and changes no stop. With no plan recorded, the order is what it was.

Settings (`queue.conf` key, then the morning job's key): `SPRINT_PLAN_FILE` is `morning.sprint_plan_file`; `SPRINT_POINTS` is
`order.sprint_points`, and `POINTS_PER_DAY` is `order.points_per_day`. The working days a sprint counts are `morning.sprint_days` (default `mon` to `sat`). A plan file
must be outside every git working tree: it holds your yes. Name the same path in `bridge.conf` (`STANDUP_SPRINT_PLAN_FILE`).

Good: `proposed a sprint plan for the week starting 2026-11-11: 4 piece(s), 12 of 60 point(s)`. Not that:
`no sprint plan was proposed: the sprint is not known (...)`.
The morning job then exits 3. A plan file it cannot read is never planned over, since your yes may be in it.

## When the tech lead changes a setting

Ask in your DM, in one sentence: a verb such as set, lower or raise, the setting, and the new value. *Lower the planned
load to 30*, *raise the day stop to 80*, *make it two lanes per repo, so lanes 2*. A question asks for nothing (*can you
raise the day stop to 80?*), and neither does a sentence with don't, not, never or keep. Neither does a number in a time,
a date or a percentage. The tech lead can change three settings: `PLANNED_LOAD`, `DAY_STOP` and
`LANES_PER_REPO`. The rest of queue.conf stays yours to edit.

The jobs' account cannot reach your queue.conf. So the change goes into the installed copy beside the queue's config,
and into the config itself, and both are read back. The queue uses it from its next pass. The tech lead then tells you
the old and the new value. Each change adds one row to `settings-changes.jsonl` in the same folder, and the next
stand-up names it.

Your next queue install carries each change into your own queue.conf, and says so: `carried PLANNED_LOAD 40 -> 30 (the
tech lead's change)`. If you changed that line yourself since the last install, your value stays, and it says that too.
A run with `--dry-run` only says what it would carry.

A value the queue would refuse is never written: the day's planned load above the day stop, a fourth lane, zero. The
tech lead says why, in the queue's own words. A change a ticket, a pull request or a session asks for is refused, even
when the tech lead read it and tried: only your own message counts.

Good: *Changed the planned load (PLANNED_LOAD) from 40 to 30. ...* Not that: *I didn't change the planned load to 9:
your own message has to name that setting and the new value*. Ask again in your own words.

## The calls no dispatcher log records

Four callers run the claude CLI themselves: the morning job, the stand-up, the tech lead's turns and the conflict
waker. None leaves a dispatcher log. Each keeps its own spend ledger instead, one row per call (contract §30). The
usage job reads every ledger beside the logs and counts its calls into the day and the week (KIT-844).

The usage file's `spend` names each ledger, and the pass's summary says one line for each.

Good: `spend    morning         read, 1 call, $0.04`. Not that: `spend    tech_lead       could-not-read: ...`. The
reason names what to check. That ledger's calls are not counted, never as zero, and the pass is `partial`. A call
whose row could not be written is marked beside its ledger, and the line says it was not counted.

Each caller writes its own ledger. The morning job's is beside the day plan. The stand-up's and the tech lead's are
in the bridge's state folder. The installer names those three in the usage job's config, from its own paths. The
stand-up's and the tech lead's sit beside `BRIDGE_STATE_FILE`, so leaving it empty turns them `off`. The waker runs
as you, so its ledger is in your own home, where the role account cannot read. It stays `off`, and the waker's calls
are not counted.

If a caller cannot write a row, its own log says so, and the call itself still runs. It leaves a mark beside the
ledger, so the usage job says the call was not counted. Only a disk that takes no write at all loses the mark too.
For example, `morning.log` says `NOTE: could not record the planner call's spend in ...`.

## The runner's runs

Our own runner (docs/RUNNER.md) keeps one record for each run it starts, and no dispatcher log holds those runs. The
usage job counts them beside the logs once `RUNNER_STORE` in `queue.conf` names the runner's `store_dir` (KIT-860). Set
it before the first lane moves to the runner, then run the installer again, or that lane's runs do not count against
the day and the week.

Each day and the week say where their runs came from, in `by_source`: the logs, the runner, the spend ledgers and the
judge. The four add up to the totals. A run both the logs and the runner hold is counted once, from its log.

Good: `runner   read            asked 1, answered 1`. Not that: `runner   could-not-read  asked 1, answered 0: the
runner's store could not be read (the store does not exist)`. Then check `RUNNER_STORE`. None of the runner's runs is
counted, never as zero, and the pass is `partial`. Empty, the line says `off`.

## Pausing it

Unload a job: `sudo launchctl bootout system/<label>`. `disable` it too to survive a restart. The installer never reloads
a job. To stop all starting, empty `QUEUE_CONFIG` in `bridge.conf`.

Under a model, `run` is refused, and `run --dry-run` says `NOT MEASURED` for whatever needs `sudo`.
