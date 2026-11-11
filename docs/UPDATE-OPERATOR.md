# Review and update — what the pipeline needs updated, and how to update it

Added 2026-10-10 (KIT-238). The pipeline is built from parts that each move on their own:
- the dispatcher, and the Claude Code bundled inside it;
- Homebrew's Claude Code, which the conflict waker and terminal sessions use;
- the front door and the tunnel;
- `gh` and `git`;
- this kit.

Nothing used to update any of them, or say that one was behind. On the reference machine
the dispatcher was three releases behind, and its bundled Claude Code could not run the
newest model. `scripts/pipeline_update.py` is the one place that says what is behind, and
it walks you through updating it.

## What it checks, and what it does not yet

`review` is read-only. It asks for no password, so you, a session or a scheduled job can
run it at any time:

```bash
python3 scripts/pipeline_update.py review
```

One row per component. Each row is in one of these states:

| State | Meaning |
|---|---|
| `CURRENT` | installed equals the target |
| `BEHIND` | the row prints the exact line that updates it |
| `RESTART` | a running daemon's newer version is installed, but its process started before that, so it still runs the old binary. The row prints the restart line |
| `AHEAD` | installed is newer than a version you pinned |
| `UNKNOWN` | it could not be measured. **This is not current** |
| `ABSENT` | not configured, or not installed here |

The components it checks:
- **The dispatcher.** The version its own `/version` route reports, against the package
  registry's latest, or against `DISPATCHER_VERSION` if you pinned one. A dispatcher that
  does not answer is `UNKNOWN`: it may be down.
- **Homebrew casks and formulae** you list. The target is Homebrew's version including its
  packaging revision, so `2.56.0_1` is current against `2.56.0` revision 1. A formula that a
  launchd job runs (the front door, the tunnel) gets an update line that also restarts the
  job onto the new binary. When the version is current, the job's process is checked too:
  if it started before that version was installed, the row is `RESTART`. If the job is not
  running at all, the row is `UNKNOWN`. `launchctl print` and `ps` need no password.
- **The kit checkout** against its origin's default branch. On a pipeline machine, point
  it at the checkout the hooks run from (KIT-214).

**Not checked yet.** Every review prints this list, so a short table never reads as the
whole machine:
- the role account's Node;
- the Xcode command-line tools behind `/usr/bin/python3`;
- macOS (KIT-229);
- the role account's kit clones and the user-scope skills. `scripts/pipeline_install.py`
  moves those.
- each served repository's vendored contract (`/sync-kit`);
- the kit's npm dev dependencies and pinned GitHub Action versions;
- credential expiry (KIT-60, KIT-143).

Exit codes, per contract §13:
- **0:** all current.
- **10:** something is behind, or a daemon needs a restart.
- **4:** something could not be measured. This one wins over 10, because "could not
  check" must never look like "nothing to do".
- **2:** the conf is missing or wrong.

## Updating the dispatcher

A person runs this, in a terminal:

```bash
python3 scripts/pipeline_update.py update dispatcher --dry-run   # what it would do
python3 scripts/pipeline_update.py update dispatcher
```

It refuses in an agent environment. A session that stops the dispatcher that runs it is
exactly what the refusal is for. Then it works in order, and stops at the first problem:

1. **Reads the installed and target versions.** Already current means done.
2. **Pre-flight, read-only.** Every name in `DISPATCHER_FORBIDDEN_ENV` must be absent from
   the dispatcher's env file. Only names are read, never values. No token store may sit
   beside it either. Each of these changes what a newer dispatcher does at start-up. A hit
   stops here with nothing changed.
3. **Shows the plan.** It says whether a session is running, then waits for you to type
   `yes`.
4. **Keeps a rollback copy** of the installed package in the role account's home.
5. **`DISPATCHER_SENTRY=off`:** adds `CYRUS_SENTRY_DISABLED=1` to the env file, if it is absent.
6. **Stops the dispatcher**, and waits until launchd has really let go of it. Starting into a
   job launchd still holds is what returns `Bootstrap failed: 5: Input/output error`.
7. **Installs the target version** as the role account.
8. **Starts it.** It retries only that launchd error, then waits for `/version` to answer with
   the target.
9. **Records the result** in `~/.pipeline-update/ledger.jsonl`, and prints what to re-check.

**If anything fails after the stop,** a banner says what state the dispatcher is in:
stopped, running but silent, or running the wrong version. It also prints the exact
commands to start it or roll it back. It never rolls back by itself: a rollback starts
nothing, and that choice is yours.

**Re-check after every dispatcher update.** Each of these was measured against the old
version:
- `python3 scripts/pipeline_stage_e_setup.py verify`, and `card CK-7` for the reviewer's
  tool list;
- `python3 scripts/pipeline_chat_lane_setup.py verify`;
- `python3 scripts/pipeline_install.py`. A new dispatcher version invalidates the idea
  gate's probe signature, so planning stops until this re-signs it.

Then delegate one small ticket, and confirm its session's model in the dispatcher's log.

**Read the release first.** Pin `DISPATCHER_VERSION` to a version someone has compared
against the running one. The 0.2.69 → 0.2.73 comparison (KIT-235) found:
- a new tool that reads any session;
- parent sessions now resumed automatically;
- the chat lane's tracker token refreshed before every session.

None of these show up as a version number.

**If you run the chat lane, give the chat app `groups:read` before any upgrade past
0.2.73** (KIT-287): run `python3 scripts/pipeline_chat_lane_setup.py card CK-C7`.
Without it, the 0.2.74 prerelease drops every message from the chat lane's private
channel, and nothing says why. The update does not check this for you. It cannot read
the app's scopes, so its plan says this before it asks for your yes, in a dry run too,
whenever the target is past 0.2.73 (KIT-288).

Good: `BEFORE YOU SAY YES: if you run the chat lane, its Slack app needs the groups:read
scope first.` Run card CK-C7, then the update.
Not: saying yes first. The tech lead goes quiet, and nothing says why.

## Updating the rest

The review prints the line for each:
- `brew upgrade …`;
- `brew upgrade … && sudo launchctl kickstart -k system/<label>` for a running daemon;
- `python3 scripts/pipeline_install.py` for the kit.

Restarting the front door or the tunnel drops webhooks for a few seconds, and the tracker
retries them. Avoid a blanket `brew upgrade`: it restarts nothing, so a running daemon
keeps its old binary until something restarts it.

## Settings

`cp update.conf.example update.conf`. Every key is described in the example:
- the dispatcher's three keys are the same values as in `stage-e.conf`;
- every section is optional;
- no key holds a credential;
- a value with a credential's shape is refused.

## The health watch

`scripts/pipeline_watch.py` runs one pass of the checks below, by default every hour, as a
LaunchAgent under your login. It tells you when the answer **changes**: once when a problem
appears, once more when it clears. Added 2026-10-10 (KIT-236). The defect that started it,
KIT-214, broke every desktop session's guards for six days and told no one.

| Kind | Fires when |
|---|---|
| `hooks` | the Stop hook recorded, in the last 24 hours, that it could not tell which worktree a session ran in (`.claude/.stop-pr-nag/session-*__root-unresolved` in each `HOOK_CHECKOUTS` checkout). It clears 24 hours after the last such record |
| `updates` | a dispatcher row of `review` is `BEHIND`, or a Homebrew row is `BEHIND`, needs a `RESTART`, or is `UNKNOWN`. A dispatcher that cannot be measured makes `updates` `UNKNOWN`: an update stops it, and that must not clear the alert. The kit checkout is listed but never fires: it moves with every merge. A review that finds nothing wrong is reused for 20 hours; one that finds something is re-measured every pass, so a fix clears within the hour |
| `release` | a dispatcher release is newer than the version `DISPATCHER_VERSION` pins |
| `dispatcher` | the dispatcher has not answered `/version` on this machine for two passes |
| `front-door` | `PUBLIC_STATUS_URL` has not answered 200 for two passes while the internet answers |
| `disk` | free space on `/` is under `DISK_FLOOR_GB` |
| `temp` | your account's temp space holds more than `TEMP_CEILING_GB` (default 10): Claude Code's per-session scratch, `/tmp/claude-<uid>`, and your own `TMPDIR`, together. The alert names the three largest folders. A session's scratch folder is safe to remove once that session has ended, and a restart clears both. A count that cannot run, or runs past 60 seconds under the line, is `UNKNOWN`. Added 2026-11-04 (KIT-461): on 4 Nov the scratch held 49.5 GB before the disk ran out |
| `conflict-waker` | the conflict waker's heartbeat, in `CONFLICT_WAKER_STATE_DIR`, has not changed for an hour, reports `problems` or `error`, or was never written, for two passes. Empty: no waker is watched, and every pass says so. It reaches Slack only, never GitHub (KIT-339) |
| `watcher-page` | the watcher's page job's heartbeat, in `WATCHER_PAGE_HEARTBEAT_FILE`, is older than `WATCHER_PAGE_MAX_AGE_MINUTES` (default 120), unreadable, in another shape or missing, or says a page could not be sent and by which route, for two passes. Empty: it is not watched, and every pass says so. It reaches Slack only, never GitHub (KIT-549) |
| `push-connection` | the bridge's push-connection job's heartbeat, in `PUSH_HEARTBEAT_FILE` (KIT-561, only with `PUSH_MODE=yes` in bridge.conf), is older than ten minutes, unreadable, in another shape or missing, says the job is not connected to Slack, or shows nothing heard from Slack for five minutes, for two passes. Empty: it is not watched, and every pass says so. It reaches Slack only, never GitHub: the bridge's once-a-minute pass still answers a DM, only not at once |
| `credentials` | a date you wrote in `CREDENTIAL_EXPIRIES` is within `CREDENTIAL_WARN_DAYS` (default 30) of today, or past. Empty: no dates are watched, and every pass says so. Upkeep like `updates`: a GitHub issue, never a Slack page (KIT-579) |
| `memory` | less than `MEMORY_FREE_FLOOR_PCT` (default 10) of memory is free, or more than `SWAP_CEILING_GB` (default 4) of swap is used, on two passes in a row, so one brief spike says nothing. Read from macOS's own `memory_pressure` and `sysctl vm.swapusage`, without sudo; a reading that cannot be taken is `UNKNOWN`. A concern, not an outage: it is listed as advisory, opens no GitHub issue and pages nobody; the stand-up says one line about it (KIT-609). The watch also keeps each day's lowest free memory and most swap, for the sprint review |
| `burn` | the weekly window's use, projected to its reset from the use so far and the time left, reaches `BURN_WEEK_PCT` (default 90), or the 5-hour window is at `BURN_FIVE_HOUR_PCT` (default 90). It reads only the usage job's file, `BURN_USAGE_FILE`; a figure it cannot read, or a file more than two hours old, is `UNKNOWN`, never zero. In the first 12 hours of a week it does not project (too little to say). Advisory like `memory` (KIT-609). Empty: not watched, and every pass says so |
| `notifier` | the notifier's heartbeat, in `NOTIFIER_HEARTBEAT_FILE`, is an hour old, unreadable, in another shape or missing, for two passes. Empty: it is not watched, and every pass says so (KIT-286) |
| `monitor` | the same, for the heartbeat monitor's own heartbeat in `MONITOR_HEARTBEAT_FILE` (KIT-286). Nothing else reads it |

**The watcher's page job (KIT-549).** Name its `page-heartbeat.json` in `WATCHER_PAGE_HEARTBEAT_FILE`, a
file your account can read, and set `WATCHER_PAGE_MAX_AGE_MINUTES` to a little more than twice the job's
interval. This kind reaches Slack only; it opens no GitHub issue.

Good: `watcher-page OK checking in, and every page went`.
Not: `WATCHER_PAGE_HEARTBEAT_FILE is not set` once the page job is installed, which means it is not
watched; or a line that names a route, such as `could not send a page by slack`, which means a page you
should have had did not reach you by that route. Run `scripts/pipeline_watcher_page.py check`.

**The credentials' dates (KIT-579).** Three credentials end after about a year and nothing warns
you: the dispatcher's subscription token, the GitHub token Stage E publishes with, and the domain
registration. No API says a token's own end date, so you write each date down once, in `update.conf`:

```
CREDENTIAL_EXPIRIES=dispatcher-token:2027-07-27,stage-e-github-token:2027-09-08,domain:2027-08-01
CREDENTIAL_WARN_DAYS=30
```

- **Names and dates only.** A name is short, lower case, letters, digits and dashes. Never paste a token
  here. A value that does not fit is refused, and the refusal does not repeat it.
- **Where each date comes from.** The dispatcher token: the mint date in the comment above it in the role
  account's `.env`, plus one year. The GitHub token: its expiry on GitHub's token settings page. The domain:
  your registrar's renewal date. The Linear key `stage-e-poller` does not expire, so it has no row.
- **The warning starts `CREDENTIAL_WARN_DAYS` ahead** (14 at least). The issue opens once, adds a comment when a
  second credential comes due, and closes when no date is inside the warning. After you rotate a credential, change its date. Copy the new `pipeline-alert.yml` into `ALERT_REPO`
  first (`credentials`, since 2026-11-10): the workflow refuses a kind it does not list.
- **What it cannot do.** It trusts the date you wrote. A token minted on another day than you wrote, or a
  date never updated, is not seen; a token revoked early is seen only when something fails.

Good: `credentials OK none expires within 30 days; the next is dispatcher-token on 2027-07-27`.
Not: `CREDENTIAL_EXPIRIES is not set` once you hold a year-long token, which means nothing is watching it.

**The two clerks (KIT-286).** The notifier cannot page about itself, and nothing scheduled read the
monitor's own heartbeat. So each is a kind here, and each opens a GitHub issue, since Slack is what a
dead notifier cannot reach. Each file must be one **your account can read**, and the role account's state
folder is not by default: point the key at a copy a person or a job keeps fresh. A copy that stops being
refreshed reads as a stale clerk. Copy the new `pipeline-alert.yml` into `ALERT_REPO` first: the workflow
refuses a kind it does not list.

**What `temp` cannot see.** The watch runs as you. The dispatcher's account keeps its own
scratch and its own `TMPDIR`, which your account cannot read, so they are not counted. The
disk kind still catches the total.

A single bad pass never fires a liveness kind. With no internet, `front-door` is `UNKNOWN`,
not an outage: a laptop in a background wake has no network. `UNKNOWN` never pages and never
clears an alert.

**Where it tells you.**
- **GitHub:** a changed kind dispatches `ALERT_WORKFLOW` in `ALERT_REPO`, which must be a
  **private** repo, because alert text carries machine state. That workflow
  (`templates/workflows/pipeline-alert.yml`) opens or closes one issue per kind as
  `github-actions[bot]` and @mentions `ALERT_PAGE_TO`. The watch then finds that run and
  waits for it. A run that failed is not recorded as sent, so the next pass sends again.
  The workflow refuses a kind it does not list, so when the kit adds a kind, copy the new
  template into `ALERT_REPO` (for `temp`: since 2026-11-04). Until you do, that kind still
  reaches Slack, and every pass says its GitHub send failed.
- **Slack:** each pass writes `HEALTH_STATUS_FILE`, world-readable, for the heartbeat
  monitor to read and page through the notifier. Only outage kinds page: `hooks`,
  `dispatcher`, `front-door`, `disk`, `temp`, `conflict-waker`, `notifier`, `monitor`. `conflict-waker` opens no
  GitHub issue: the alert workflow knows a fixed list of kinds, and the monitor watches the
  waker for Slack because its own account cannot read your home. `updates` and `release` are upkeep. They open their
  GitHub issue but never reach Slack, so a Slack ping always means something is broken. The
  status file lists them under `advisory` (owner decision, 2026-10-27, KIT-264).
- Either one empty is OFF, and every pass says so.

The job cannot report its own death. The heartbeat monitor pages when the status file
stops changing.

**Asking the tech lead.** Every alert says where the full findings are: the status file. The
Slack tech lead runs as the dispatcher's account, which can read that file. Once the chat
lane's rules name the file (KIT-226, not merged), ask it in the channel to explain an alert.
Anything that needs `sudo`, `launchctl` or an installer it prints for you; it never runs it.

**Install it**, once, in your own terminal:

```bash
python3 scripts/pipeline_watch.py run --dry-run    # what it would say, sending nothing
python3 scripts/pipeline_watch.py install          # shows a pass that sends nothing, then asks for yes
python3 scripts/pipeline_watch.py status           # the last pass, per kind
```

`install` refuses in an agent environment. It records the `brew`, `gh`, `npm` and `git`
directories your shell has now in the job's `PATH`. Re-run it after moving any of them. A
"no" changes nothing: a job already loaded keeps running.

## What is not proven

- `update dispatcher` against the live dispatcher. The battery drives a scripted launchd
  and npm; the first real run is the 0.2.69 → 0.2.73 upgrade (KIT-235).
- That `brew info`'s index is fresh. Homebrew refreshes it on its own schedule, so a
  just-released version can read as current for a while (no ticket yet).
- The health watch end to end on a real machine: a GitHub issue opened and closed by a live
  run, and the Slack page through the heartbeat monitor reading the status file (KIT-218).
- That a `front-door` probe from this machine sees what the tracker's webhooks see. Both go
  through the same public name, but the tracker connects from elsewhere (no ticket yet).
