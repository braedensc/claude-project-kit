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

## What is not proven

- `update dispatcher` against the live dispatcher. The battery drives a scripted launchd
  and npm; the first real run is the 0.2.69 → 0.2.73 upgrade (KIT-235).
- That `brew info`'s index is fresh. Homebrew refreshes it on its own schedule, so a
  just-released version can read as current for a while (no ticket yet).
- A scheduled weekly review that pages through Slack and GitHub. That is the next slice of
  KIT-238, using KIT-236's channels.
