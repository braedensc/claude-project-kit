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

## When the Mac is short of room

Before any start, the queue reads three numbers: memory pressure, free disk on the data volume, and swap in use. Past a
line on any of them, it starts nothing new. Running work finishes. The lines default to under 25 GB free, memory pressure
at critical, and over 4 GB of swap. To change them, add a `room` section to the queue's config: `min_free_disk_gb`,
`max_pressure` (`warn` or `critical`) and `max_swap_gb`. A reading it cannot take holds starts too.
Good: `QUEUE: free disk 18.2 GB is under the 25 GB line`, then later `room again: starts resume`.
Not that: starts going on while the disk fills. Free some space; the next pass starts again.

## Pausing it

Unload a job: `sudo launchctl bootout system/<label>`. `disable` it too to survive a restart. The installer never reloads
a job. To stop all starting, empty `QUEUE_CONFIG` in `bridge.conf`.

Under a model, `run` is refused, and `run --dry-run` says `NOT MEASURED` for whatever needs `sudo`.
