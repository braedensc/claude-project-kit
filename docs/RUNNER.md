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
   removed. Its session's final message is kept first, for the caller that asked for the run
   (below). Anything left of it is killed first, and again until nothing is: one kill can miss a
   process that is being forked at that instant. A run that left no result and no exit is
   *interrupted*, and is resumed once. A lock only the wrapper holds says a run is alive; the
   session never gets it, so it cannot fake its own end.
2. **Answers each waiting request, oldest first.** It starts, resumes or stops a session, or refuses
   with a reason. A request it cannot judge now waits for a later pass.

**A resume whose session is gone starts afresh, once.** The program may no longer have the
conversation a resume names. Then the runner starts one new session in the same workspace, given
the ticket and the text of the request whose work it carries on. Its record says `started_for:
session-gone` and names the lost session in `gone_session`. It happens once per request: a fresh
start that is itself lost later is not started again, and its record says why. A resume that fails
any other way keeps its own reason.

**A start may carry its caller's text** (KIT-858). A job can send the facts its session needs with
the start: a planning run's earlier attempts, say, or the head an acceptance check builds. The
session gets them after the ticket, fenced as data, as it gets a resume's text. A fresh start after
a lost session gets them again. A stop carries no text.

**The session's final message is kept for its caller** (KIT-858). When a run ends, the runner keeps
the final message the program reported in `<store_dir>/outputs/<run>.json`. That is its last 150,000
characters, marked `cut` when the message was longer. The caller reads it with `run_output()` once
the run's record says it ended. `None` means the run left no result at all. A result that carried
no message is kept as one with none, never as an empty message. The message is the session's own
words: data for its caller, never part of the run's record.

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
| A workspace | `<work_dir>/<kind>/<TICKET>` | A fresh git worktree on a new branch. A folder already there is never reused. A review, plan or acceptance start first removes the workspace and branch an earlier run of its kind left on the ticket, once that run has ended (KIT-858). |
| A branch | `<type>/<ticket, lower case>-<title or kind>` | One fixed pattern. Every git command is a list of arguments. No repository hook runs. |
| A pin | the project's pins root | Written before the program runs, removed when the run ends. Budget, mode, criteria and out of scope. |
| The run's folder | `<store_dir>/runs/<run>/` | The command's inputs, the program's output, its exit, and the run's record. |
| Its remembered processes | `<store_dir>/trees/<run>.json` | The runner's alone. Never in the run's folder, which the run's own processes write. |
| Its final message | `<store_dir>/outputs/<run>.json` | Kept when the run ends, for its caller. The runner's alone, for the same reason. |
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
  with its start time, in the runner's own store. A session cannot name a process for it to kill.
  When the run ends, any of them still alive is killed. The start time tells it from a later
  process that reused its number.
- **Start times, as fine as the system gives them.** To the microsecond on macOS, from the kernel's
  own record. In clock ticks on Linux, from `/proc`. To the second, from `ps`, only where neither is
  there. The record names its resolution.
- **A tree of more than 512 processes** counts as past its memory limit, however little each holds.
  So do more than 4096 of its processes alive at once. Each look forgets the ones that are gone, so a
  long run never fills the record. Past 4096 the newest are kept, and the run is stopped.
- **Below the floor,** no new session starts and requests wait. Running sessions finish.
- **It measures only its own:** its runs' trees, by process group and parent, its runs' folders, and
  three numbers about the whole machine. It reads nothing about any other app.
- **Each incident is told once,** in plain words, in `<store_dir>/incidents.json`: a run stopped at a
  limit, a run it could not measure, a run whose processes it could not remember or check, or the
  floor holding starts. A floor incident ends when there is room again.

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
- **No tracker sign-in.** No tracker key is in its environment, its `mcp.json` lists no tracker
  server, and every tracker key file is walled from its file tools and from Bash. With
  `session_tools` set, a code, fix or conflict session gets five tracker tools of its own instead.

## The session's own tracker tools

A code, fix or conflict run gets one tool server of its own when `session_tools` is set:
`scripts/pipeline_session_tools.py`, started for that run alone. The runner writes the server's run
file in the run's folder, outside the workspace. The run's `mcp.json` lists that server and no other.
A review, a plan and an acceptance check get none: each returns one checked block.

| Tool | What it does |
| --- | --- |
| `read_ticket` | Reads the run's own ticket: its fields, labels, description and comments, fenced as data. |
| `comment` | Posts one comment on it: a plan, progress, a plan note. |
| `ask_question` | Asks the run's one question, under the escalation mark the notifier pages on. |
| `file_finding` | Requests a follow-up ticket: the finding block the finding filer files into the backlog. Three a run. |
| `report_done` | Ends the run, once: the summary and the run's telemetry block. After it, nothing more is written. |

- **No tool takes a ticket id.** The server knows only its own run's ticket. So no tool can write
  to another ticket, make a ticket, or set a state, a label or an approval.
- **It writes what a session writes today.** Each comment holds the same mark or block as before,
  so the notifier, the finding filer, the bridge and the telemetry collector read it unchanged.
- **It writes as the session writer:** the tracker user those readers already trust as a session's
  author. That is never the owner. Before its first call, it checks the key belongs to
  `writer_user_id`. If it does not, the server refuses everything.
- **Every argument is hostile.** Text is judged as the tracker will store it. A mark, another
  tool's block, a receipt line or a credential's shape is refused. Routing tags, fence tags and
  @mentions are made inert.
- **Its caps are contract section 8's:** 20 comments a run, the last kept for `report_done`; three
  findings; one question; bodies of 16 000 characters. Each write is counted before it is sent.
  Reads stop at 100 a run, which only a runaway meets.

Good: a session calls `file_finding` with a title and a body. The finding filer files it in the
backlog and replies on the ticket.
Not: a session puts a finding block in a `comment`. The comment is refused, because a finding goes
through `file_finding`, where the cap of three holds.

**The writer's key.** Put a Linear API key for the user named in `writer_user_id` in a file of the
role account's, mode 0600, outside the work and home folders. The readers must trust that user. The
finding filer's `agent_user_id` and the bridge's agent user must both name it.

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
| `session_tools` | `key_file`, the session writer's tracker key, and `writer_user_id`, the user it must belong to, never an owner. See "The session's own tracker tools". Absent, no session gets tracker tools. | none |
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

```bash
npm run test:session-tools
```

The session tools' selftest checks every refusal and every cap. It checks that only the fixed
requests reach the tracker, and that no answer, log line or file holds the key. It holds each
comment against the job that reads it, then runs the real server over stdio.

## When it refuses

Each refusal says why, in the request's answer (`<store_dir>/taken/<id>.json`).

| It says | What to do |
| --- | --- |
| the ticket's newest move was not the owner's | The owner moves the ticket himself. |
| a workspace is there already | For a code start: the ticket's earlier code run keeps its workspace for its fixes, so remove it before a new start. For a review, plan or acceptance start: no run of the runner's left that folder, so it is never removed for you. Look at what it is first. |
| an earlier run is still going or is to be resumed | Wait. A review, plan or acceptance start can follow once that run has ended. |
| the project has no committed delivery.json | The project is not set up for the pipeline. |
| `<name>` is not set | The sign-in token's variable is missing from the job's environment. |
| the tracker could not be read | Nothing to do: the request waits, and the next pass tries again. |
| the machine is below its floor | Free disk or memory. The request waits; it starts when there is room. |

The runner refuses to start from inside a Claude session, or as root.

## Not proven yet

- That a session outlives a restart of the runner under launchd. The selftest proves it for a runner
  killed with its whole process group. One live check is owed.
- That the program says a lost session in the words the runner looks for: "No conversation found",
  as an error result or as the last line of its errors. The tech lead's turn relies on the same
  words. The selftest's stand-in prints them. One live check is owed.
- That one sign-in serves the runner and the tech lead together (KIT-629).
- That sandboxed Bash may write the session's temp folder. Each kind's walls are measured in KIT-704.
- That a real session calls its own tracker tools, and that Linear takes their comments. Their
  selftest runs the real server over stdio against a stand-in tracker. The first coding lane on
  the runner (KIT-645) is the first live run.
- How the owner's answer to a session's question reaches the work again on this lane. The
  notifier pages the question as today. Taking the answer back to the session by resume is
  KIT-707's.
