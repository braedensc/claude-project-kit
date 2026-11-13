# The runner

The runner is our own dispatcher's one always-on job. Other pipeline jobs ask it to start, resume or
stop a session. It runs the official `claude` program headless, one process per session, and keeps
one record per run.

Why it exists, and the plan it is part of: `docs/adr/2026-11-11-our-own-dispatcher-built-new-beside-the-old-one.md`.
The formats it reads and writes: `docs/PIPELINE-CONTRACT.md` section 28, and section 3 for the pin.

## Where it stands

- **Built, and off.** It is built beside the third-party dispatcher, which still runs every session.
- **No lane uses it yet.** Each lane moves later, one at a time, on the owner's yes, a drill repository first.
- **Nothing installs it yet.** Its installer comes with the first lane that moves.
- **No real session goes through it yet.** Its selftest runs it against a stand-in program.

One piece comes before any lane moves: each kind of session as measured data, the kit's guard
from a root-owned copy, and a live probe of the walls as the role account (KIT-704).

## What it does

Each pass, every few seconds:

1. **Finds each live run again.** A run whose process is gone gets its record finished and its pin
   removed. Anything left of its process group is killed first. A run that left no result and no
   exit is *interrupted*, and is resumed once. A lock only the wrapper holds says a run is alive;
   the session never gets it, so it cannot fake its own end.
2. **Answers each waiting request, oldest first.** It starts, resumes or stops a session, or refuses
   with a reason. A request it cannot judge now waits for a later pass.

Before it starts anything, it checks the request against the tracker:

- The ticket's whole history must show the owner's own move into an approved state for that kind.
- The newest move counts for a code or plan start. Any of his moves counts for the kinds that follow.
- The old dispatcher's own user never approves. A history it cannot read whole approves nothing.

Good: the owner moved ENG-12 to Todo. The queue asks for a code session. It starts.
Not: someone else moved ENG-12 to Todo. The request is refused, and says why.

Who asked is reporting, not authority. Under one account any process that can write the store can
name any caller. The tracker check is the authority. The store's walls and the session's sandbox
keep sessions from writing a request at all.

## What a start makes

| Thing | Where | Notes |
| --- | --- | --- |
| A workspace | `<work_dir>/<kind>/<TICKET>` | A fresh git worktree on a new branch. A folder already there is never reused. |
| A branch | `<type>/<ticket, lower case>-<title or kind>` | One fixed pattern. Every git command is a list of arguments. No repository hook runs. |
| A pin | the project's pins root | Written before the program runs, removed when the run ends. Budget, mode, criteria and out of scope. |
| The run's folder | `<store_dir>/runs/<run>/` | The command's inputs, the program's output, its exit, and the run's record. |
| A temp folder | `<work_dir>/.tmp/<run>` | The session's TMPDIR, outside every wall. Removed when the run ends. |

A start refused after its workspace was made removes the workspace and its branch, so the next start
of that ticket finds none.

The session's budget comes from the project's committed `delivery.json`: `budgets.perEffort` for the
ticket's effort label.

## Its limits

Every run is held to three limits, and the machine to a floor. Each sits far above any real run: a
limit hit in normal use is a defect.

| Limit | What it measures | Default |
| --- | --- | --- |
| Time | The run's time since it started, checked every pass. | 360 minutes |
| Memory | Its whole process tree's resident memory: its group, and every descendant by parent. | 8192 MB |
| Disk | Its own folders: its workspace, its run folder and its temp folder. | 20480 MB |
| The floor | The queue's own three lines: memory pressure, free disk and swap in use. | 25 GB free, pressure under critical, swap under 4 GB |

- **Past a limit,** the run's whole tree gets SIGTERM, then SIGKILL after the grace. Its record says
  `stopped`, with the limit as its reason (`limit:time`, `limit:memory` or `limit:disk`).
- **Its whole tree, even what leaves it.** Each process the runner sees in a run's tree is kept,
  with its start time, in the run's folder. When the run ends, any of them still alive is killed.
  The start time tells it from a later process that reused its number.
- **A tree of more than 512 processes** counts as past its memory limit, however little each holds.
- **Below the floor,** no new session starts and requests wait. Running sessions finish.
- **It measures only its own:** its runs' trees, by process group and parent, its runs' folders, and
  three numbers about the whole machine. It reads nothing about any other app.
- **Each incident is told once,** in plain words, in `<store_dir>/incidents.json`: a run stopped at a
  limit, a run it could not measure, or the floor holding starts. A floor incident ends when there is
  room again.

Good: a session's tree passes 8 GB. It is stopped, its record says `limit:memory`, and one line is told.
Not: the same session is told about again on every pass.

The health watch does not page from the incidents file yet. It learns the runner with the runner's
installer, when the first lane moves.

## What a session is given

- **Nothing from its workspace.** No settings, hooks, tool servers or `CLAUDE.md` load from it.
- **The project's instructions,** read from its default branch by the runner, handed in as a file.
- **The ticket,** as the runner read it, fenced as data.
- **Its tools, each by the workspace's path.** No file tool is ever allowed by bare name.
- **A kind with Bash** gets the built-in sandbox, with a deny-read list.
- **A short environment:** the path, a home of its own, a temp folder, the language, the sign-in
  token, and two switches.

## Settings

One JSON file. The example is `runner.example.json`. `~/` means the account's own home.

| Key | What it is | Default |
| --- | --- | --- |
| `enabled` | Takes requests and resumes runs when true. Off, it still finishes runs already started. | `false` |
| `store_dir` | The runner's store. Outside every git working tree, mode 0700. | required |
| `work_dir` | Where workspaces go. | required |
| `session_home` | The sessions' own home folder. Holds nothing of the account's setup. | required |
| `claude_bin` | The program's full path. | required |
| `model` | The model for every session. | `sonnet` |
| `token_env` | The NAME of the variable that holds the sign-in token. | `CLAUDE_WRITER_OAUTH_TOKEN` |
| `linear_key_file` | The tracker key, in a file of this account's, mode 0600. | required |
| `owner_user_ids` | The owner's tracker user ids. Only his moves approve. | required |
| `dispatcher_user_ids` | The old dispatcher's own users. Never an approver. | required |
| `poll_seconds` | Time between passes. | `5` |
| `stop_grace_seconds` | After a stop, how long before the session is killed. | `30` |
| `max_starts_per_ticket` | A runaway's limit on starts of one ticket and kind. | `20` |
| `hold` | The watcher's hold file (contract section 20). Set, nothing starts. | none |
| `limits` | `wall_minutes`, `memory_mb`, `disk_mb`, `check_seconds` (how often trees and folders are measured) and `room` (the floor). See "Its limits". | above |
| `projects` | Each project: `repo` (the runner's own clone), `base`, `approval_states` per kind, `allowed_hosts`. | required |

The four folders must not sit inside one another.

## Checking it

```bash
python3 scripts/pipeline_runner.py show --config <runner.json>
```

It prints each run: its kind, ticket, state, reason, start, length, cost and turns.

```bash
npm run test:runner
```

The selftest starts, resumes and stops sessions of a stand-in program, kills the runner mid-run to
show the session lives on, and leaves nothing behind.

## When it refuses

Each refusal says why, in the request's answer (`<store_dir>/taken/<id>.json`).

| It says | What to do |
| --- | --- |
| the ticket's newest move was not the owner's | The owner moves the ticket himself. |
| a workspace is there already | The sweep removes old workspaces. A new start needs a fresh one. |
| the project has no committed delivery.json | The project is not set up for the pipeline. |
| `<name>` is not set | The sign-in token's variable is missing from the job's environment. |
| the tracker could not be read | Nothing to do: the request waits, and the next pass tries again. |
| the machine is below its floor | Free disk or memory. The request waits; it starts when there is room. |

The runner refuses to start from inside a Claude session, or as root.

## Not proven yet

- That a session outlives a restart of the runner under launchd. The selftest proves it for a runner
  killed with its whole process group. One live check is owed.
- That one sign-in serves the runner and the tech lead together (KIT-629).
- That sandboxed Bash may write the session's temp folder. Each kind's walls are measured in KIT-704.
