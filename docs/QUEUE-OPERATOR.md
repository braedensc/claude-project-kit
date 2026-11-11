# The queue's installer — operator steps

The queue starts the owner's approved work without a person, inside a working window and a daily budget. Two small jobs
feed it: the usage job (KIT-422) and the morning job (KIT-446). `scripts/pipeline_queue_setup.py` (KIT-456,
`npm run test:queue-setup`) sets them up. You run it, at your own terminal. No session installs or loads anything.

**It needs Stage E and the bridge first** (`docs/BRIDGE-OPERATOR.md`, *With the queue on*). The jobs run as the
dispatcher's role account, from Stage E's clone, with its env file. The installer creates none of these.

## Run it

    cp queue.conf.example queue.conf && chmod 600 queue.conf    # edit it; none of it is secret
    python3 scripts/pipeline_queue_setup.py run --dry-run        # read it first: it changes nothing
    python3 scripts/pipeline_queue_setup.py run                  # stops at each card; do it, then run it again

Good: `DRY RUN: nothing was changed`, and each row says `ALREADY-DONE` or `WOULD-CHANGE`.
Not that: `Your conf has N problem(s)`. Each line names a key, never its value. Three placeholders are refused until you
edit them. `UNKNOWN` means it could not look, which is not a pass.

## What each step does

**preflight.** It checks the role account, the queue modules' selftests, the account's clone and its env file.
Good: one line saying all pass. Not that: `the clone ... is not level with this checkout`. Merge the change, then let
Stage E's installer move the clone. Not that: `lacks <name>`. That name is missing from the env file.

**config.** It writes two files, mode 600, after the jobs' own loaders accept them.
Good: `wrote ..., mode 600`. Not that: `the jobs' own loaders REFUSE this composition`. Their words follow. Nothing was written.

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

## When the Mac is short of room

Before any start, the queue reads three numbers: memory pressure, free disk on the data volume, and swap in use. Past a
line on any of them, it starts nothing new. Running work finishes. The lines default to under 25 GB free, memory pressure
at critical, and over 4 GB of swap. To change them, add a `room` section to the queue's config: `min_free_disk_gb`,
`max_pressure` (`warn` or `critical`) and `max_swap_gb`. A reading it cannot take holds starts too.
Good: `QUEUE: free disk 18.2 GB is under the 25 GB line`, then later `room again: starts resume`.
Not that: starts going on while the disk fills. Free some space; the next pass starts again.

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

## Pacing each day to the plan's weekly reset

The plan's usage limit resets once a week, and a busy Monday can spend what Friday needed. Set `PACE_TO_RESET=yes` and
`WEEKLY_BUDGET_USD` (dollars at API prices) in `queue.conf` and the queue sizes each day's planned load from the budget
as the day began (the week's spend less today's), divided by the working windows left before the reset, today's included. The day's hard stop keeps its ratio to the
load. Today's own spend does not shrink the day, so a pass late in the day plans what the morning did. Both the morning job and the queue's pass do this, so the plan and the starts agree.

The reset is the usage file's own `seven_day` reset time, never a fixed day, so pacing moves when the plan's reset moves.
Pacing only ever lowers a day: the figures never go above `PLANNED_LOAD` and `DAY_STOP`. A week with nothing left plans a
cent, so any spend holds every start. It is off by default, and off changes nothing.

Good: `paced to the weekly reset: 54.0 left of 90 at the start of today, over 3 working windows, so today plans 18.0 and stops at 27.0`. Not that:
`pacing is on, but the usage file cannot place the plan's week, so the day stays at its configured figures`. The day then
runs at `PLANNED_LOAD` and `DAY_STOP`, as if pacing were off. Check the usage job. Other causes: a reset in the past or more
than eight days off, no spend for today in the usage file, and no working window before the reset.

## Sprint planning at the reset

A sprint is the plan's week (`scripts/pipeline_sprint.py`). Set `SPRINT_PLAN_FILE` and `WEEKLY_BUDGET_USD` in `queue.conf`
and, on the first morning of each new sprint, the morning job also writes **one proposal** of the week's work into that
file: the pieces the pool admitted, in the queue's own order, as many as fit the week's budget less what the week has
spent, each at its effort's estimate. What does not fit says why: over the budget, no size yet, or the pool's reason in plain words.

A held piece that one reply would start keeps that reply in the list. The proposal offers nothing the pool did not
admit, so it never offers work the pipeline cannot land. It is not proposed again for the same sprint. The stand-up
sends it (`docs/STANDUP.md`, "The sprint plan"), and your reply `plan sprint <date>` records the plan in the same file.

What the queue does with a recorded plan: **its pieces go first** among the pieces already allowed to start. That is all.
It admits nothing, raises no budget and changes no stop. With no plan recorded, the order is what it was.

Settings (`queue.conf` key, then the morning job's key): `SPRINT_PLAN_FILE` is `morning.sprint_plan_file`; `WEEKLY_BUDGET_USD` is
`order.weekly_budget_usd`. The working days a sprint counts are `morning.sprint_days` (default `mon` to `sat`). A plan file
must be outside every git working tree: it holds your yes. Name the same path in `bridge.conf` (`STANDUP_SPRINT_PLAN_FILE`).

Good: `proposed a sprint plan for the week starting 2026-11-11: 4 piece(s), $18.00 of estimates`. Not that:
`no sprint plan was proposed: the sprint is not known (...)`, or `... what the week has spent so far could not be read ...`.
The morning job then exits 3. A plan file it cannot read is never planned over, since your yes may be in it.

## Pausing it

Unload a job: `sudo launchctl bootout system/<label>`. `disable` it too to survive a restart. The installer never reloads
a job. To stop all starting, empty `QUEUE_CONFIG` in `bridge.conf`.

Under a model, `run` is refused, and `run --dry-run` says `NOT MEASURED` for whatever needs `sudo`.
