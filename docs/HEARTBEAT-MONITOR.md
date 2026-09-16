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
| The Mac asleep, off, or logged out | No process runs, so nothing is judged and nothing is posted. The silence looks exactly like health |
| Its own death | A monitor that is not running pages nobody. It writes its own heartbeat, and the installer's `verify` reads it — but only when a person runs `verify` |
| A tracker outage | The page is undeliverable precisely when the tracker is what broke. Reported as exit 3 and retried; never recorded as delivered |
| Wrong rather than dead | A heartbeat proves a pass ran and what it decided. A daemon doing the wrong thing every interval looks healthy here |

Closing the first two needs something **off this box** — an external service that alerts
when a ping *stops* arriving, which is the inverse of this design because it fails loud
rather than silent. That is separate work and deliberately not attempted here: a half-built
dead-man's switch that quietly stops pinging is worse than none at all.

## What it watches

One row per job, because the three heartbeats do not agree on field names and rewriting
three daemons for one reader's convenience would be the larger change:

| Job | File | Freshness from | Good result |
|---|---|---|---|
| review poller | `<state_dir>/heartbeat.json` | `ended_at`, then `started_at` | `ok` |
| bounce driver | `<state_dir>/bounce-heartbeat.json` | `finished_at`, then `at` | `ok`, `idle` |
| finding poller | `<finding_state_dir>/heartbeat.json` | `ended_at`, then `started_at` | `ok` |

The two pollers' files share a filename and are told apart by **directory**. The selftest
cross-checks every schema string and filename against the three writers, and judges a
heartbeat the finding poller's own writer produced, so a renamed field turns CI red instead
of quietly reading as healthy — or, the way it actually failed once, paging on every healthy
file.

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
| `stale` | Newest timestamp older than `stale_multiplier` × that job's interval | yes |
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

## The sleeping laptop

launchd runs a missed interval on wake, so for one interval after a wake **every** heartbeat
is legitimately old and a naive monitor pages on all three. So the monitor reads its own
last-run timestamp first: if it missed its *own* schedule by more than `stale_multiplier`
intervals, the machine was not running, staleness is not judged this pass, and the report
says so in those words. `missing`, `failing` and `unreadable` are still judged — none of
them depends on the clock. The next pass judges staleness normally.

## One comment per incident, never one per pass

The state file holds the fingerprint of the last verdict-set that was *posted*. A pass whose
fingerprint matches posts nothing. A return to health posts one recovery comment. A changed
problem is a new fingerprint and is announced.

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

| Exit | Meaning |
|---|---|
| 0 | Ran and reported: posted, already reported, or everything watched is healthy |
| 1 | Owed a page and could not deliver it, or a bug escaped. Not recorded as sent; said again next pass |
| 2 | Usage or config error — nothing read, nothing posted |
| 3 | Judged, and said nothing a person will see: no ticket configured, the cooldown carried it, or a `check` found a problem |
| 4 | Hit the wall clock. Distinct from 1 so a scheduler log tells hung from failed |

Every pass prints what it asked and what the answer was. A silent green is not one of this
script's outcomes.

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

Left empty, `run` stops at card `CK-9`. With a ticket named, the step does five things:

1. It checks that the stored tracker key can read the ticket. A comment to a missing ticket
   reaches nobody.
2. It writes `~/.stage-e/monitor.json` under the role account. The intervals come from the
   same conf values launchd schedules the three daemons by.
3. It runs the monitor's own `check`. A config the monitor refuses (exit 2) is never loaded.
   Exit 3 means it found a problem on its first look, which is its job.
4. It installs `<prefix>.stage-e-monitor` as a fourth system LaunchDaemon, logging to
   `~/.stage-e/monitor.log`.
5. It loads the job and waits for a heartbeat newer than the load.

`verify` re-measures all of it. A loaded monitor whose heartbeat is older than two of its
intervals, plus two minutes and one pass, is **NOT RUNNING** (exit 4). One whose last pass
ended `error`, `usage` or `timeout` is a failure. `off` unloads a monitor an earlier run
installed and removes its plist, so the next boot does not load it again.

The `code` step unloads a loaded monitor with the other three before it moves the clone they
all run from.

## Running it by hand

```sh
python3 scripts/pipeline_heartbeat_monitor.py --example-config > ~/.stage-e/monitor.json
$EDITOR ~/.stage-e/monitor.json          # watch, intervals, run_interval_seconds, ticket
python3 scripts/pipeline_heartbeat_monitor.py check --config ~/.stage-e/monitor.json
python3 scripts/pipeline_heartbeat_monitor.py run   --config ~/.stage-e/monitor.json --dry-run
```

`check` judges and prints and writes nothing at all — not even the run clock, because a
rehearsal that moved it would make the next real pass mis-judge. `run --dry-run` is the same
plus the heartbeat, marked `dry_run` so it cannot impersonate a real pass.

It reads its credential from the same `~/.stage-e/env` file as the daemons
(`linear_key_env` names the variable; the value never enters the config). Nothing is
scheduled in this repository, and the `--selftest` above is all that runs in CI.
