# The heartbeat monitor — the job that reads the other three's heartbeats

Stage E's three daemons each write a heartbeat after every pass, so that *the job is dead*
and *the job ran and had nothing to do* stop looking identical (contract §13). For a while
**nothing read them.** A heartbeat with no consumer is not a health signal; it is a file
that would have told someone, if anyone had looked. `scripts/pipeline_heartbeat_monitor.py`
is the consumer.

It is deterministic code — no model, no prompt, no tokens — run by the **same role account**
as the daemons it watches, on a **longer interval**. One pass: read three files, judge each,
and when the verdict *changes*, post **one** comment on a configured tracker ticket.

## What it cannot catch, first

A monitor on the same Mac shares that Mac's failure domain. These are properties of the
design, not defects in the script:

| Blind spot | Why it is blind |
|---|---|
| The Mac asleep, off, or logged out | No process runs, so nothing is judged and nothing is posted. The silence looks exactly like health (KIT-45) |
| Its own death | A monitor that is not running pages nobody. It writes its own heartbeat, and the installer's `verify` reads it — but only when a person runs `verify` (KIT-45) |
| A tracker outage | The page is undeliverable precisely when the tracker is what broke. Reported as exit 3 and retried; never recorded as delivered |
| Wrong rather than dead | A heartbeat proves a pass ran and what it decided. A daemon doing the wrong thing every interval looks healthy here |

Closing the first two needs something **off this box** — an external service that alerts
when a ping *stops* arriving, which is the inverse of this design because it fails loud
rather than silent. That is separate work (KIT-45) and deliberately not attempted here: a
half-built dead-man's switch that quietly stops pinging is worse than none at all.

## What it watches

One row per job, because the three heartbeats do not agree on field names and rewriting
three daemons for one reader's convenience would be the larger change:

| Job | File | Freshness from | Good result |
|---|---|---|---|
| review poller | `<state_dir>/heartbeat.json` | `ended_at`, then `started_at` | `ok`, `declined` |
| bounce driver | `<state_dir>/bounce-heartbeat.json` | `finished_at`, then `at` | `ok`, `idle` |
| finding poller | `<finding_state_dir>/heartbeat.json` | `ended_at`, then `started_at` | `ok` |

The review poller's `declined` (exit 3) is a good result. That pass settled a NOT-reviewed
verdict and said so on the pull request: no acceptance criteria, a review that timed out, a
diff over the cap. That is the poller doing its job, and the notifier does not page on it
either. The bounce driver's `problems` and `deadline` stay bad: each is a pull request it
could not act on, or a pass cut short. The finding poller has no exit 3.

The two pollers' files share a filename and are told apart by **directory**. The selftest
cross-checks every schema string and filename against the three writers, and judges
heartbeats the review poller's and the finding poller's own writers produced, so a renamed
field turns CI red instead of quietly reading as healthy — or, the way it actually failed
once, paging on every healthy file.

**Not watched: the conflict waker's heartbeat.** That job runs as a *person*, as a
LaunchAgent, and its file sits under that person's home, where this role account cannot
read it. It also goes stale at every logout, so a staleness page would be nightly noise.
Its installer's `verify` reads it instead, and the conflict monitor pages on the pull
request whenever a waiting request goes unclaimed (`STAGE-E-OPERATOR.md`, Step 6).

## The verdicts

| Verdict | Meaning | Pages? |
|---|---|---|
| `ok` | Ran, good result | no |
| `running` | A pass was in flight when it wrote (the bounce driver's mid-pass beat) | no |
| `unknown-after-gap` | Staleness was not judged this pass — see *the sleeping laptop* below | no, and always named |
| `stale` | Newest timestamp older than `stale_multiplier` × that job's configured interval | yes |
| `wedged` | Stale **and** it says a pass was running: a pass started and never finished | yes |
| `failing` | Fresh, and the result is bad. It **ran and could not do it** — not the same as down | yes |
| `missing` | A watched job has written no heartbeat here at all | yes |
| `unreadable` | The file cannot be judged: bad JSON, an unknown schema, no parseable timestamp | yes |

`unreadable` is a claim about **the monitor**, never about the daemon, and it pages for the
§13 reason: a monitor that cannot judge must be as loud as a job that is down, or *I could
not tell* quietly becomes *nothing to report*.

**`watch` is required.** It names the jobs this machine actually runs, so an un-deployed
job's permanently absent heartbeat cannot page forever. A job left out is **named as
unjudged** in every report — never implied healthy.

**Intervals are required too.** "Stale" has no meaning without the interval, so a watched
job with no interval is a config error rather than a guessed default.

**An interval is the longest a healthy job can go between two heartbeats.** That is its
launchd interval plus one pass's wall clock. The review poller writes only when a pass ends,
and the bounce driver's `running` beat stands until its pass ends. So a long pass inside the
daemon's own deadline leaves an old file. With launchd's interval alone, that pass reads as
`stale` or `wedged`. The installer adds 900 s to each: the review poller's and the bounce
driver's default pass wall clock.

## The sleeping laptop

launchd runs a missed interval on wake, so for one interval after a wake **every** heartbeat
is legitimately old and a naive monitor pages on all three. So the monitor reads its own
last-run timestamp first: if it missed its *own* schedule by more than `stale_multiplier`
intervals, the machine was not running, staleness is not judged this pass, and the report
says so in those words. `missing`, `failing` and `unreadable` are still judged — none of
them depends on the clock. The next pass judges staleness normally.

`rearm --config FILE` does the same on purpose. It removes the last-run timestamp from the
state file, keeps every other field, posts nothing and writes no heartbeat. The installer
runs it just before every load of the monitor. The daemons were just reloaded, so their
heartbeats are old for that reason. Without it, the first pass would page about daemons that
are running.

A pass that left any heartbeat `unknown-after-gap` has only part of the picture:

- It posts only for a `missing`, `failing` or `unreadable` heartbeat that the last comment
  did not already name. Those do not depend on the clock, so they do not wait. The comment
  then names, in its first line, the jobs it did not judge.
- Otherwise it posts nothing, and records nothing about posting. No change is carried.
- It never posts a recovery. A stale daemon it could not judge has not been seen to recover.

The next pass that judges staleness decides. So a rearm during an open incident adds no
comment, and the judged pass after it posts only if the verdicts differ from the last comment.

## One comment per incident, never one per pass

The state file holds the fingerprint of the last verdict-set that was *posted*. A pass whose
fingerprint matches posts nothing. A return to health posts one recovery comment. A changed
problem is a new fingerprint and is announced.

- **Per ticket:** the state also records the ticket the last comment went to. Change
  `notify_ticket_id` during an open incident, and the new ticket is told about it on the
  next pass that judges staleness. A new ticket gets no recovery comment for an incident it
  never saw. A state file written before the ticket was recorded re-announces an open
  incident once.
- **Flap bound:** `min_seconds_between_comments`. A change suppressed by it is **carried,
  counted and said later** — the next allowed comment names how many changes it swallowed.
  A recovery is exempt, because a delayed recovery leaves a false impression standing.
- **Written after a successful post**, so a failed post retries. Losing the state file
  therefore costs a duplicate comment, never a missed one; a corrupt one is named loudly,
  treated as empty, and the duplicate it causes is disclosed in the comment itself.
- **Outside every git working tree**, and refused inside one: a file a session can write is
  a file a session could use to suppress a page about its own pull request.

## Off unless a ticket is set — but never silently off

With no `notify_ticket_id` the monitor still runs, still judges, still prints the whole
verdict table and still writes its heartbeat. What it cannot do is tell anyone, so a pass
that finds a problem exits **3** and names the missing key. *I found something and had
nowhere to say it* is a different sentence from *everything is fine*.

That is the script's own config. Under the Stage E installer the monitor is off only by
name (`HEARTBEAT_MONITOR_TICKET=off`), and an empty value stops `run` at card `CK-9`.

| Exit | Meaning |
|---|---|
| 0 | Ran and reported: posted, already reported, or everything watched is healthy |
| 1 | Owed a page and could not deliver it, or a bug escaped. Not recorded as sent; said again next pass |
| 2 | Usage or config error — nothing read, nothing posted |
| 3 | Judged, and said nothing a person will see: no ticket configured, the cooldown carried it, or a `check` found a problem |
| 4 | Hit the wall clock. Distinct from 1 so a scheduler log tells hung from failed |

Every pass prints what it asked and what the answer was. A silent green is not one of this
script's outcomes.

`check` and `rearm` write no heartbeat. `rearm` exits 0 when the next pass will not judge
staleness, 1 when it could not rewrite the state file, and 2 on a config error.

## What it never does

Its only tracker write is `commentCreate` on the one configured ticket. It applies no label,
moves no ticket state, creates no ticket, touches no pull request, approves and merges
nothing, and launches no session. It writes **no** `pipeline-escalation` mark, because a mark
makes the human-action notifier apply a lifecycle label and a label is not this job's to
write. `--selftest` asserts all of that against the file's own source.

## Installing it

**The Stage E installer installs it (KIT-127).** Its `heartbeat-monitor` step runs after the
three daemons are loaded. Name the ticket in `stage-e.conf`, or turn it off by name:

```sh
HEARTBEAT_MONITOR_TICKET=KIT-123      # or: HEARTBEAT_MONITOR_TICKET=off
MONITOR_INTERVAL_SECONDS=1800         # optional; this is the default
```

Left empty, `run` stops at card `CK-9`. Pick a ticket you read that no session is ever
delegated, and that stays open: a closed ticket can auto-archive, and the monitor's lookup
then no longer finds it. Sessions hold workspace-wide tracker write, so a session can still
move or archive that ticket, and every comment the monitor owes then fails (no ticket yet;
`STAGE-E-OPERATOR.md`, *Accepted risks*).

With a ticket named, the step does six things:

1. It checks that the stored tracker key can read the ticket. A comment to a missing ticket
   reaches nobody.
2. It writes `~/.stage-e/monitor.json` under the role account. The intervals come from the
   same conf values launchd schedules the three daemons by, plus 900 s for one pass.
3. It runs the monitor's own `check`. A config the monitor refuses (exit 2) is never loaded.
   Exit 3 means it found a problem on its first look, which is its job.
4. It installs `<prefix>.stage-e-monitor` as a fourth system LaunchDaemon, logging to
   `~/.stage-e/monitor.log`.
5. It runs the monitor's `rearm`, so the first pass does not judge the reloaded daemons'
   old heartbeats as stale. A failed `rearm` is a failed step, and the job is not loaded.
   On a reload, the old job is unloaded first. If launchd still holds it after the wait,
   the step fails before `rearm` and names the `sudo launchctl bootout` command.
6. It loads the job and waits for a heartbeat newer than the load.

`verify` re-measures all of it. A loaded monitor whose heartbeat is older than two of its
intervals, plus two minutes and one pass, is **NOT RUNNING** (exit 4). One whose last pass
ended `error`, `usage` or `timeout` is a failure. `off` unloads a monitor an earlier run
installed and removes its plist, so the next boot does not load it again. If launchd still
holds the job after the unload, the step fails, names the `sudo launchctl bootout` command,
and keeps the plist so the next run finds the job again.

The `code` step unloads a loaded monitor with the other three before it moves the clone they
all run from. `enable` loads the three again, and the `heartbeat-monitor` step loads the
monitor. A run that stops between them prints which jobs are still off.

## Running it by hand

This section is for a machine where the installer does not manage the monitor: there is no
Stage E installer, or `HEARTBEAT_MONITOR_TICKET=off`.

```sh
python3 scripts/pipeline_heartbeat_monitor.py --example-config > ~/.stage-e/monitor.json
$EDITOR ~/.stage-e/monitor.json          # watch, intervals, run_interval_seconds, ticket
python3 scripts/pipeline_heartbeat_monitor.py check --config ~/.stage-e/monitor.json
python3 scripts/pipeline_heartbeat_monitor.py run   --config ~/.stage-e/monitor.json --dry-run
```

`check` judges and prints and writes nothing at all — not even the run clock, because a
rehearsal that moved it would make the next real pass mis-judge. `run --dry-run` is the same
plus the heartbeat, marked `dry_run` so it cannot impersonate a real pass.

**On a machine the installer set up, run only `check`**, as the role account, against the
installer's own file:

```sh
sudo -u <ROLE_ACCOUNT> -H /bin/sh -c 'cd / && /usr/bin/python3 ~/.stage-e/kit/scripts/pipeline_heartbeat_monitor.py check --config ~/.stage-e/monitor.json'
```

- Never write `--example-config` over `~/.stage-e/monitor.json`. The loaded job reads that
  file on every pass. It would judge by the example's intervals and send its comments to the
  example's ticket until the next `run` rewrites the file.
- `run --dry-run` leaves a `dry_run` heartbeat. `verify` reports the step as not settled
  until the job's next real pass, or the next `run`.

It reads its credential from the same `~/.stage-e/env` file as the daemons
(`linear_key_env` names the variable; the value never enters the config). Nothing is
scheduled in this repository, and the `--selftest` above is all that runs in CI.
