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

A lane runs through the runner only when both switches are on: the caller's own (the job that asks)
and the runner's lane for that project. Either one off is the way back.

**Its runs count against the day and the week** once `RUNNER_STORE` in `queue.conf` names its
`store_dir` (KIT-860). The usage job reads every run record through the store's one reader, beside
the old dispatcher's logs (docs/QUEUE-OPERATOR.md, "The runner's runs"). Set it before the first
lane moves.

Each kind of session is data now, behind walls the probe proved live for every kind, as the role
account, on 18 Nov 2026: 95 of 95 (KIT-704). One thing still comes before any lane moves: a
root-owned copy of the kit for the runner to run from. Its installer makes that copy.

## What it does

Each pass, every few seconds:

1. **Finds each live run again.** A run whose process is gone gets its record finished and its pin
   removed. Anything left of it is killed first, and again until nothing is: one kill can miss a
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
| Its remembered processes | `<store_dir>/trees/<run>.json` | The runner's alone. Never in the run's folder, which the run's own processes write. |
| A temp folder | `<work_dir>/.tmp/<run>` | The session's TMPDIR, outside every wall. Removed when the run ends. |

A start refused after its workspace was made removes the workspace and its branch, so the next start
of that ticket finds none.

The session's budget comes from the project's committed `delivery.json`: `budgets.perEffort` for the
ticket's effort label.

## Its limits

Every run is held to three limits, and the machine to a floor. Each sits far above any real run: a
limit hit in normal use is a defect. Each kind of session also has its own ceilings (below), and a
run is held to the smaller of the two.

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

## Each kind of session

Each kind is one data file in `session-kinds/`, checked by `scripts/pipeline_session_kinds.py`.
One adapter, `scripts/pipeline_harness_claude.py`, turns a kind into the program's command line,
settings and environment. A second program would be a second adapter, reading the same files.

| Kind | Tools | Wall | Hosts | Time, memory |
| --- | --- | --- | --- | --- |
| code | Bash, Read, Edit, Write | the built-in sandbox | the project's list | 360 min, 8 GB |
| fix, conflict | the same: each is a resume of a code run | the built-in sandbox | the project's list | 360 min, 8 GB |
| review | Read, Grep, Glob | our own sandbox | none | 180 min, 4 GB |
| plan | Read, Grep, Glob | our own sandbox | none | 180 min, 4 GB |
| acceptance | Bash, Read: it builds the app it checks | the built-in sandbox | the project's list | 180 min, 8 GB |

Every kind's disk ceiling is 20 GB. With `session_tools` set, code, fix and conflict also get their
own five ticket tools ("The session's own tracker tools", below), and the rules they are given name
each one: no skill loads from the workspace to tell them. What no kind's data can say, because the
loader refuses it:

- **A mode that skips the checks.** The only value is `deny-unlisted`: what no rule allows is refused.
- **A file tool allowed by name.** Each is allowed by the workspace's path only.
- **Bash inside our own sandbox,** or a kind without Bash outside it.
- **Less than the four walls:** the store, the pins, the keys and the sessions' own state.
- **An environment name the adapter has no source for,** so no API key, proxy or address can pass.
- **A review tool outside the reviewer's read-only set.** The selftest checks the review kind
  against the same allow list the old lane's probe uses, and the plan kind against the planner's.

The files sit behind the `hooks-change` label: a change to one changes what a session may reach.

## What a session is given

- **Nothing from its workspace.** No settings, hooks, tool servers or `CLAUDE.md` load from it.
- **The project's instructions,** read from its default branch by the runner, handed in as a file.
- **The ticket,** as the runner read it, fenced as data. A resume's text is fenced the same way.
- **Its kind's tools, each file tool by the workspace's path.** None is allowed by its bare name.
- **Deny rules** for the runner's store, the pins and the tracker key, for every file tool. In the
  sessions' shared home, no file tool may write anywhere, and none may read the program's own state
  (its `.claude` folder and `.claude.json`, which hold every transcript).
- **A kind with Bash** gets the built-in sandbox: a deny-read list of every place denied to Read,
  and the project's hosts only. Its git reads no global config (`GIT_CONFIG_GLOBAL=/dev/null`).
- **Why the rest of the home stays readable.** The program makes each Read deny rule part of
  Bash's deny-read list too (measured 12 Oct 2026). git stops when a read of its global config is
  refused, not missing. So a Read deny on the whole home would stop git in every session. The home
  holds nothing of the account's own, and with writes denied nothing else lands there. The probe
  checks that neither a file tool nor Bash can write it.
- **A kind without Bash** runs inside our own macOS sandbox, written for the run. It may write only
  the program's home and the run's temp folder. It may not read the store, the pins or the key,
  apart from its own inputs and its own pin. The folders on the way to those may be looked at by
  their metadata alone, never listed: the program checks each folder on a path before it reads a
  file there, and on 18 Nov 2026 it refused its own settings file when it could not.
- **The kit's guard on every tool call,** from the engine's own folder (below).
- **A short environment,** exactly its kind's names: the path (the program's own folder first),
  a home, a temp folder, the language, the sign-in token, and two switches.
- **No tracker sign-in.** No tracker key is in its environment, its `mcp.json` lists no tracker
  server, and every tracker key file is walled from its file tools and from Bash. With
  `session_tools` set, a code, fix or conflict session gets five tracker tools of its own instead.

Good: a review session's Grep reaches only its workspace, and the system itself stops it writing.
Not: a review session is given Bash, or a file tool allowed by its bare name.

**A workspace `.env` that names a variable the program reads is refused.** An API key there would
move the lane from the plan's usage window to a card, and a base address or a proxy would send its
traffic elsewhere. The runner checks the dotenv files a loader reads by itself (`.env`, `.env.local`
and the like) before every start and resume. It names the file and the variable, never the value.
`.env.example` is documentation, and is not read.

## Guards from the engine's own folder

The runner runs from a copy of the kit: the engine's folder. A session's walls come from there: the
kinds' data, the adapter and the kit's guard. On this Mac that copy is root-owned, so a session
cannot change its own guard.

- **The runner checks it.** No session starts while this account could change the engine's
  folder or a folder above it. The same goes for anything at any level of its scripts, its kinds'
  data, its guards or its sandbox profiles. The request waits, and the runner says why once.
- **The guard is told its folder.** The kit's guard finds its project from `CLAUDE_PROJECT_DIR`,
  and without it from its own file's folder. Run from the engine, that is the wrong checkout, and a
  branch check outside a repository lets an edit through. So a small launcher,
  `scripts/pipeline_runner_guard.py`, runs it. The runner writes the workspace into the launcher's
  command line, in the run's settings, outside the workspace.
- **The launcher refuses** when no workspace is named, or the one named is not the top of a git
  working tree, or `CLAUDE_PROJECT_DIR` names another folder. It refuses when the guard is missing,
  crashes or does not answer. The program reads any exit but 2 as "go ahead", so a crash becomes a
  refusal.

## The walls, measured (KIT-630, as the role account on 12 Nov 2026)

| Row | What it found | What the kinds do |
| --- | --- | --- |
| r1, r1c | With the three flags nothing in the worktree loads, and instructions from outside arrive | Every kind runs with them |
| r2, r2k | A guard run from outside the worktree refuses an edit on main, in its own words | Every kind's tool calls go through the kit's guard, from the engine's folder |
| r3, r3b | The built-in sandbox holds Bash; it reads outside unless a deny-read list says not to | Every kind with Bash has the built-in sandbox and a deny-read list |
| r4a | A file tool allowed by its bare name reads and writes outside, sandbox or not | No kind allows a file tool by name |
| r4b to r4d | Allowed by the workspace's path only, the outside is refused with no deny rule | Every file tool is allowed by the workspace's path, and denied the walls too |
| r5a | Our own macOS sandbox around the program holds the file tools | Review and plan run inside it |
| **r5b, FAIL** | Inside both sandboxes Bash does not start (`sandbox_apply: Operation not permitted`) | **The answer is a rule, in the data:** a kind with Bash has the built-in sandbox and rules for its file tools; a kind without Bash has our own sandbox. The loader refuses any other pairing. The acceptance kind keeps Bash and the built-in sandbox: its look tool is a tool server, which the program starts outside Bash's sandbox, so the tool can start a sandbox of its own. Inside ours it could not |
| **r3c, not measured** | The control with no sandbox: the model declined the commands | **A gap that proves nothing either way.** Row r4a wrote to the same folder, so r3's refused write was the sandbox's. No kind runs Bash with no sandbox |

What the program itself does, outside its tools, is held by our own sandbox only for review and
plan. For a kind with Bash it is the built-in sandbox and the rules: the program's own process is
not walled. That is r5b's cost, and a second account for sessions is the later wall (KIT-701,
decision 4).

## The probe

`scripts/pipeline_runner_probe.py` proves each kind's walls live, as the role account. A person
runs it, never a session: it starts about ten short sessions on the small model, about a dollar.
It builds each kind exactly as the runner would, from the same data, adapter and launcher. Then it
asks each session to reach past each wall. It judges from files on disk and the program's own
record of each call, never from what the model says.

```bash
python3 scripts/pipeline_runner_probe.py run --claude <the role's claude> --out <folder>
```

| Its rows | Must |
| --- | --- |
| Walls: its tools are only its kind's; nothing in the workspace loads; no read outside, of the store, the pins, the key or another session's transcript; the guard refuses a `.env` read and write; no write outside, nor into the sessions' home, by a file tool or Bash; Bash reaches no unlisted host and no inbox socket; every name in Bash's environment that looks like a key is proven useless in the same run | PASS |
| Work: the instructions arrive; reads, searches and writes inside work; Bash writes its workspace and temp folder; `git status` and a commit; a listed host is reached | WORKS, or the lane cannot move |
| Facts: whether the code host's command-line tool, Python and git each get through TLS in the sandbox, and whether the program reads a `.env` in its working folder | recorded |

**What else is in Bash's environment.** The program and its built-in sandbox add names the runner
does not give and cannot take away. Two of them look like keys, and neither is taken on trust:

- `CLAUDE_CODE_MESSAGING_TOKEN`, with `CLAUDE_CODE_MESSAGING_SOCKET`: the program's cross-session
  inbox, a Unix socket that takes messages into a session. The built-in sandbox blocks every Unix
  socket, so the probe has Bash try that socket, and the token counts as useless only when it is
  refused. Never set `sandbox.network.allowUnixSockets` or `allowAllUnixSockets` for a session.
- `CLOUDSDK_PROXY_PASSWORD`, with the rest of the `*_PROXY` family: the sandbox's own credential for
  its local proxy, which reaches only the listed hosts. It counts as useless only when that proxy
  refuses an unlisted host in the same run.

Any other name that looks like a key fails the row, and so does the sign-in token, which the
program keeps from Bash (`bash-token` says whether it did).

**No kind needs `gh` in a session.** `gh` fails TLS inside the built-in sandbox (`x509: OSStatus
-26276`, measured 18 Nov 2026): the sandbox keeps the system's trust service from it, and the fix is
not a wider network. When coding sessions move (KIT-645), they push with git and open a pull request
through the kit's plain HTTPS fallback, `scripts/gh_fallback.py`. The probe measures the TLS of both,
so the `gh` row stays information.

Before any session, the probe asks the program for its version and its help. If the help lacks an
option the command line uses, the probe stops and names it: a program that meets an unknown option
exits 1 before it writes anything. On 18 Nov 2026 the role account's own copy, 2.1.247, failed every
session this way, because `--permission-prompts` arrived in 2.1.259. Every session that fails
leaves the program's own last words in the results.

A row the session never tried is NOT MEASURED, never PASS. A refusal counts as the guard's only
when its own words are seen. Exit 0 means every wall held; 1 a wall failed; 2 some wall was not
measured; 3 it could not run.

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
| `claude_bin` | The program's full path: 2.1.259 or later, because the command line uses `--permission-prompts`. A copy the sessions' account cannot change, never that account's own. | required |
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
| `projects` | Each project: `repo` (the runner's own clone), `base`, `lanes`, `approval_states` per kind, `allowed_hosts`. | required |

The four folders must not sit inside one another.

**Each project's lanes.** `lanes` holds `true` or `false` for each kind of session: `code`, `fix`,
`conflict`, `review`, `plan` and `acceptance`. A kind left out is off, and so is a project with no
`lanes`. The runner refuses a start or resume for a lane that is off, before it makes or reads
anything, and says so in the request's answer. Its own resume of an interrupted run on such a lane
is refused too, and the run's record says `auto_resume: refused`. A stop is always taken. A lane that
is on needs its approved states in `approval_states`: `fix` and `conflict` use the `code` states.
`enabled` stays the runner's own switch: off, it takes nothing, whatever the lanes say.

Good: `"lanes": {"review": true}` for the drill repository. Only reviews of it go through the runner.
Not: a review request for a project whose `review` lane is off. It is refused: "the review lane is
off for project app".

## Checking it

```bash
python3 scripts/pipeline_runner.py show --config <runner.json>
```

It prints each run: its kind, ticket, state, reason, start, length, cost and turns.

```bash
npm run test:runner
```

The selftest starts, resumes and stops sessions of a stand-in program, kills the runner mid-run to
show the session lives on, and leaves nothing behind. The kinds, the adapter, the guards' launcher
and the probe each have their own: `test:session-kinds`, `test:harness-claude`, `test:runner-guard`
and `test:runner-probe`.

**Our own sandbox is checked for real only on a Mac.** On a Mac, `test:harness-claude` runs a
review's profile with the system's own `sandbox-exec`, and `test:runner` runs a review session
inside it. The CI job on the Mac's Python runs both on every pull request. On any other system they
print that they skipped it, and only the profile's text is checked. The probe runs the real program
inside it, on the owner's Mac.

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
| a workspace is there already | The sweep removes old workspaces. A new start needs a fresh one. |
| the project has no committed delivery.json | The project is not set up for the pipeline. |
| `<name>` is not set | The sign-in token's variable is missing from the job's environment. |
| the tracker could not be read | Nothing to do: the request waits, and the next pass tries again. |
| the machine is below its floor | Free disk or memory. The request waits; it starts when there is room. |
| the workspace's `.env` names a variable the program reads | Take the variable out of that file, or out of the project's committed copy. |
| the engine's folder could be changed by this account | The runner runs from its own copy of the kit, owned by root. The request waits until it does. |
| the session kinds could not be loaded | A kind's data file is missing or wrong: `python3 scripts/pipeline_session_kinds.py --check` says which. |

The runner refuses to start from inside a Claude session, or as root.

## Not proven yet

- That a session outlives a restart of the runner under launchd. The selftest proves it for a runner
  killed with its whole process group. One live check is owed.
- That the program says a lost session in the words the runner looks for: "No conversation found",
  as an error result or as the last line of its errors. The tech lead's turn relies on the same
  words. The selftest's stand-in prints them. One live check is owed.
- That one sign-in serves the runner and the tech lead together (KIT-629).
- Each kind's walls as the role account: the probe above has not been run live yet. Until it
  passes, no real session goes through the runner. It also measures what is still open here: that
  sandboxed Bash may write its temp folder, that a commit works in a workspace whose git folder sits
  in the runner's clone, and whether the code host's command-line tool gets through TLS.
- That our own sandbox lets the real program run for a review or plan session. The selftest runs
  the profile on a Mac with a stand-in. The probe runs the real program in it.
- That the acceptance kind's look tool can start its own sandbox in an acceptance session. It is
  a tool server, started outside Bash's sandbox, so nothing should nest. It joins that kind with
  the acceptance lane (KIT-708), and the probe gains a row for it then.
- That a real session calls its own tracker tools, and that Linear takes their comments. Their
  selftest runs the real server over stdio against a stand-in tracker. The first coding lane on
  the runner (KIT-645) is the first live run.
- How the owner's answer to a session's question reaches the work again on this lane. The
  notifier pages the question as today. Taking the answer back to the session by resume is
  KIT-707's.
