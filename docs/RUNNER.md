# The runner

The runner is our own dispatcher's one always-on job. Other pipeline jobs ask it to start, resume or
stop a session. It runs the official `claude` program headless, one process per session, and keeps
one record per run.

Why it exists, and the plan it is part of: `docs/adr/2026-11-11-our-own-dispatcher-built-new-beside-the-old-one.md`.
The formats it reads and writes: `docs/PIPELINE-CONTRACT.md` section 28, and section 3 for the pin.

## Where it stands

- **Built, and off.** It is built beside the third-party dispatcher, which still runs every session.
- **No lane is on yet.** The review, planning, coding, fix, conflict and acceptance lanes are
  built. Each is switched on later, one project at a time, on the owner's yes, a drill repository
  first. See "The coding lane".
- **Its installer puts it in place with every lane off** for every project. See "Installing it".
- **No real session goes through it yet.** Its selftest runs it against a stand-in program.
- **The review lane is built (KIT-644).** The review job asks for a review run of a project whose
  `review` lane is on, and files no review ticket. No project's is on yet.
- **The planning lane is built (KIT-705).** The planner job asks for a plan run on the idea itself
  when a project's `lanes.plan` is on, and files no planning ticket ("The planning lane", below).

A lane runs through the runner only when the runner's lane for that project is on, and the caller's
own switch too where the caller keeps one. Either one off is the way back. The review job keeps
none: the runner's `lanes.review` is the review lane's one switch.

**The runner says which lanes it takes** (KIT-708). It writes them into its store, and the acceptance
job and the fix driver read them there. Each sends a kind to the runner only while the runner says that
lane is on for the project, and takes its old road otherwise. Their own `runner_lane` only says where the
runner's store is and which project a repository is: it switches nothing. The catch-up job reads the
`fix` lane there too (KIT-707): it leaves a team out of each pass while that lane is on for every one of
its projects, so the one switch moves a project's fixes and its catch-up together.

**Its runs count against the day and the week** once `RUNNER_STORE` in `queue.conf` names its
`store_dir` (KIT-860). The usage job reads every run record through the store's one reader, beside
the old dispatcher's logs (docs/QUEUE-OPERATOR.md, "The runner's runs"). Set it before the first
lane moves.

Each kind of session is data now, behind walls the probe proved live for every kind, as the role
account, on 18 Nov 2026: 95 of 95 (KIT-704). The runner runs from a root-owned copy of the kit. Its
installer makes that copy and refreshes it (KIT-919): see "Installing it". Until the copy is in place, a
start on a lane that is on is held.

## What it does

Each pass, every few seconds:

1. **Finds each live run again.** A run whose process is gone gets its record finished and its pin
   removed. Its session's final message is kept first, for the caller that asked for the run
   (below). Anything left of it is killed first, and again until nothing is: one kill can miss a
   process that is being forked at that instant. A run that left no result and no exit is
   *interrupted*, and is resumed once. A lock only the wrapper holds says a run is alive; the
   session never gets it, so it cannot fake its own end.
2. **Answers each waiting request, oldest first.** It starts, resumes or stops a session, or refuses
   with a reason. A request it cannot judge now waits for a later pass. It holds a lock on each
   request while it judges and answers it, so its caller cannot withdraw it meanwhile: a request
   withdrawn before the runner came to it is skipped, and gets no answer (KIT-644). A stop follows
   the run it names on through the runner's own resumes of it: their newest run is stopped, and a
   resume still to come is called off.

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
characters, marked `cut` when the message was longer. The runner reads the program's result line
whole up to 8 MiB; a longer one counts as no result. The caller reads it with `run_output()` once
the run's record says it ended. `None` means the run left no result at all. A result that carried
no message is kept as one with none, never as an empty message. The message is the session's own
words: data for its caller, never part of the run's record. Beside it the runner keeps the model the
run billed most and its token count, each none when the result did not say (KIT-644): a review's
comment names them.

Before it starts anything, it checks the request against the tracker:

- **A code start** is judged by the queue's own reader and judge, called as they are (KIT-645). It
  starts only a ticket the judge admits with a named approver: the owner's move, his creation where
  the judge counts one, his approval of a plan in Slack, or the validator in his place while its
  pieces switch is on. One rule, for the queue and the runner.
- **Any other start** needs the owner's own move into an approved state for that kind, in the
  ticket's whole history: the newest move for a plan start, any move for the kinds that follow.
- **A resume** follows its kind's own rule over the history, with the session writer's moves left
  out: the runner's own move into the working state counts neither way. Or it follows the approval
  the runner recorded for the start of the work it carries on, since a piece of a plan approved in
  Slack has no move of his in its history. That stands only while no later move of the owner's
  took the ticket back out of its approved states: that is him withdrawing it.
- The old dispatcher's own user never approves. A history it cannot read whole approves nothing.
- **A review follows its ticket's code run (KIT-644).** It is let start only by the approval this
  runner recorded when it started that ticket's code run: the newest one started by the owner's move
  or creation, while its approver is still the owner. Its record says `how: code-run`. A ticket
  coded on the old dispatcher has no such run, so its review is refused, in those words.

Good: the owner moved ENG-12 to Todo. The queue asks for a code session. It starts.
Not: someone else moved ENG-12 to Todo. The request is refused, and says why.
Not: the queue's limits admitted ENG-14, an agent-filed finding nobody approved. The runner refuses
it, and the bridge's pass ends loud once. The owner's own move of it into Todo starts it.

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
limit hit in normal use is a defect. Each kind of session also has its own ceilings (below), and a
run is held to the smaller of the two. Its time is held to its own pin's `maxMinutes` too: the
project's budget for the ticket's effort (KIT-645). A pin the runner cannot read is told once.

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

The health watch does not page from the incidents file yet. It learns the runner when the first lane
moves.

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
| acceptance | Bash, Read, and the look tool: it builds the app it checks | the built-in sandbox | the project's list | 180 min, 8 GB |

Every kind's disk ceiling is 20 GB. With `session_tools` set, code, fix and conflict also get their
own five ticket tools ("The session's own tracker tools", below), and the rules they are given name
each one: no skill loads from the workspace to tell them.

**A kind may carry its own steps** (`rules`, KIT-708). They go into the run's rules file, after its
brief. The acceptance check's steps are there: fetch the head, build, one look run per check block,
judge, one result block, and the second look. So a request's text carries only facts, fenced as data.

**The look tool** (KIT-708) is the acceptance kind's alone. The run's `mcp.json` starts it from the
engine's own folder, isolated, with `--config-dir` set to `look_config_dir`. The acceptance job writes
the one config naming the run's workspace there before it asks. The program starts the tool outside
Bash's sandbox, so the tool starts its own no-network sandbox (`docs/LOOK-TOOL.md`). Its one tool,
`mcp__look__look_run`, is allowed by its exact name.

**One command-line builder** (KIT-708). `line()` in the adapter spells every option the program is
given, in one order. The runner's command line, the tech lead's turn and the one-shot writer all use
it. Each names its own options and values. The builder has no option for a mode that skips the checks.

What no kind's data can say, because the loader refuses it:

- **A mode that skips the checks.** The only value is `deny-unlisted`: what no rule allows is refused.
- **A file tool allowed by name.** Each is allowed by the workspace's path only.
- **Bash inside our own sandbox,** or a kind without Bash outside it.
- **Less than the four walls:** the store, the pins, the keys and the sessions' own state.
- **An environment name the adapter has no source for,** so no API key, proxy or address can pass.
- **The push token in a kind that does no ticket's work,** or in one whose Bash the built-in
  sandbox does not hold. A kind names the token and git's helper for it together, or neither.
- **A review tool outside the reviewer's read-only set.** The selftest checks the review kind
  against the same allow list the old lane's probe uses, and the plan kind against the planner's.
- **An approval from a run, for any kind but one that follows the coding it judges.** The review
  kind says `approval: {from_run: code}`. Every other kind is approved by the owner's moves.

The files sit behind the `hooks-change` label: a change to one changes what a session may reach.

**The runner never starts a session from kinds the kit no longer has (KIT-880).** It reads the
files once, when it starts, and keeps a hash of the bytes it read. Before each start and resume
it reads the folder again and compares, with no network call. When they differ, nothing starts:
the request waits, and the runner says why. A restart loads the new files. A folder it cannot read
is never taken as unchanged. Stage E's `verify` counts `session-kinds/` too, so a merge that
changed only these files moves the clone. Where the runner's job is installed, it counts the
kit's guards in `.claude/hooks/` as well (KIT-889): the guard launcher runs them from the clone on
every tool call.

Good: a merge changes `code.json` alone. `verify` says the clone is behind, and a runner still
holding the old file holds every start until it restarts.
Not: the runner starts sessions behind the old walls until some script changes too.

## What a session is given

- **Nothing from its workspace.** No settings, hooks, tool servers or `CLAUDE.md` load from it.
- **The project's instructions,** read from its default branch by the runner, handed in as a file.
- **A plan run's brief** comes before them, in the same file: how to plan, what one document to
  answer with, and that the idea is its ticket (`scripts/pipeline_plan_brief.py`, KIT-705).
- **The ticket,** as the runner read it, fenced as data. A resume's text is fenced the same way.
- **A review: its facts and its instructions, and no ticket (KIT-644).** The review job resolved the
  basis it judges against, and the ticket may have been edited since, so no read of it is given.
  The prompt holds the review's facts, the request's text, line by line, so a finding can cite a
  diff line. The closing tag carries a mark the text does not hold, and a forged one is escaped.
  The rules hold the review's instructions, from `scripts/pipeline_review_brief.py`, the same words
  the old lane's review ticket uses. A change to a guarded path runs on `guarded_model`.
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
  a home, a temp folder, the language, the sign-in token, and two switches. Code, fix and conflict
  also get the push token and git's helper for it ("The push token", below).
- **No tracker sign-in.** No tracker key is in its environment, its `mcp.json` lists no tracker
  server, and every tracker key file is walled from its file tools and from Bash. With
  `session_tools` set, a code, fix or conflict session gets five tracker tools of its own instead.
- **An acceptance check gets the look tool,** and no other tool server (KIT-708, above).

Good: a review session's Grep reaches only its workspace, and the system itself stops it writing.
Not: a review session is given Bash, or a file tool allowed by its bare name.

**A workspace `.env` that names a variable the program reads is refused.** An API key there would
move the lane from the plan's usage window to a card, and a base address or a proxy would send its
traffic elsewhere. The runner checks the dotenv files a loader reads by itself (`.env`, `.env.local`
and the like) before every start and resume. It names the file and the variable, never the value.
`.env.example` is documentation, and is not read.

## The push token

A code, fix or conflict session pushes its own branch and opens its own pull request (KIT-886). It
uses the sessions' push token: the code host's token that sessions on the old dispatcher hold
today. Taking the token out of sessions, with the runner pushing for them, is KIT-37.

- **Where it lives.** One file, named by `push_token_file`. It is the role account's own, mode
  0600, and holds one GitHub token and nothing else. The runner refuses it inside the work or home
  folder, or inside a git working tree.
- **How it gets there (KIT-907).** The installer owns the setting: `PUSH_TOKEN_FILE` in `runner.conf`
  (unset, it is `push-token` in `RUNNER_DIR`). The file is made by one step, run by a person at a
  terminal and refused inside a Claude session:

      python3 scripts/pipeline_runner_setup.py push-token

  Make the token on the code host first, with an end date. The step asks for the token at a hidden
  prompt, then for the day it ends (`YYYY-MM-DD`, after today and within five years). It judges both
  before it asks for your password. Then the role account writes the file, mode 0600, and the date is
  filed in the health watch's `CREDENTIAL_EXPIRIES` as `runner-push-token`, which is where its yearly
  credential warning reads it. The token goes to the role account on standard input only: it is in no
  command line, no output and no log. Run it again to replace a token: the new date takes the old one's
  place. A token that never ends gets the day you will review it, so the warning still comes.
- **Its end date.** The parts list records the token as a stored key that ends when it was made
  (`pipeline-parts.json`), so `pipeline_watch.py dates` lists it, `missing` until a date is on record.
  The runner's `check` has a `push-token` row. A token file with no end date on record is NOT MEASURED,
  never current. An ended token, a file others can read and a link are FAILED. No file yet is no fault
  while no code, fix or conflict lane is on, and FAILED once one is. A path you installed by hand stays:
  with `PUSH_TOKEN_FILE` unset in `runner.conf`, `apply` keeps it. Set it to replace the path.
  `PUSH_TOKEN_FILE=off` writes the setting off, and a rerun that leaves the key out keeps it off (KIT-919).
- **Who reads it.** The runner, at each start or resume of a code, fix or conflict run, and never
  for another kind. A file it cannot read, or one that holds anything but one GitHub token, holds the
  request. The answer says why, never what the file holds.
- **Who gets it.** Code, fix and conflict, under `GH_TOKEN`, where git's helper and
  `scripts/gh_fallback.py` read it. Review, plan and acceptance never do: their data names neither
  the token nor git's helper. The loader refuses either name in a kind that does no ticket's work.
- **git's helper.** The same environment gives git one credential helper. First it clears every
  helper git would read from its other settings, the keychain's among them. Then it hands git the
  token for github.com, and for no other host. No command line holds the token.
- **Its walls.** The file is walled from every kind, the three that hold the token included, as
  each key file is. The token adds no host: Bash still reaches only the project's `allowed_hosts`.
- **The guards still hold.** In these runs the kit's guard refuses a push to main, a bare force
  push and `gh pr merge`, as in any session. The guard launcher's selftest proves each one.
- **Keep it out of the keychain.** The file is the token's only home. A session's Bash could ask
  the account's keychain itself. The probe fails if git's stored helpers hand an acceptance session
  a GitHub password.

**What the guard does not catch.** The guard knows command shapes. A merge sent straight to the
code host's API, with `curl` or Python, is not one of them. A token that can push a branch can
merge a green pull request that way. Only the default branch's own rules can stop that account
from merging. Nothing in the runner can, until KIT-37 takes the token out of the session.

Good: `push_token_file` names `~/.pipeline-runner/push-token`, mode 0600, holding the token alone.
A code session runs `git push -u origin HEAD`, then `scripts/gh_fallback.py pr-create`.
Not: `push_token_file` names the tracker key's file. Every code start waits, and its answer says
the file does not hold one GitHub token. Nothing reaches a session.

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
| Walls: its tools are only its kind's; nothing in the workspace loads; no read outside, of the store, the pins, the key, the push token's file or another session's transcript; the guard refuses a `.env` read and write; no write outside, nor into the sessions' home, by a file tool or Bash; Bash reaches no unlisted host and no inbox socket; every name in Bash's environment that looks like a key is proven useless in the same run; `push-token`, one row a kind: the token present by name for code, fix and conflict, absent for the rest; in acceptance, git's stored helpers hand it no GitHub password; in a push kind, the guard refuses a push to main, a bare force push and a merge | PASS |
| Work: the instructions arrive; reads, searches and writes inside work; Bash writes its workspace and temp folder; `git status` and a commit; a listed host is reached; in a push kind, git's helper hands git the push token | WORKS, or the lane cannot move |
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
not a wider network. A coding session pushes with git and opens its pull request through the kit's
plain HTTPS fallback, `scripts/gh_fallback.py`, with the push token (see "The push token"). The
probe measures the TLS of both, so the `gh` row stays information.

Before any session, the probe asks the program for its version and its help. If the help lacks an
option the command line uses, the probe stops and names it: a program that meets an unknown option
exits 1 before it writes anything. On 18 Nov 2026 the role account's own copy, 2.1.247, failed every
session this way, because `--permission-prompts` arrived in 2.1.259. Every session that fails
leaves the program's own last words in the results.

A row the session never tried is NOT MEASURED, never PASS. A refusal counts as the guard's only
when its own words are seen. Exit 0 means every wall held; 1 a wall failed; 2 some wall was not
measured; 3 it could not run.

Its results name the kit commit it ran at (KIT-916): the root-owned copy's own marker, `.engine-commit`, or
git's answer in a checkout. Run it from the copy the runner runs from (`ENGINE_DIR`), with
`--out <RUNNER_DIR>/probe`: the live-run tool reads its last result there ("Proving a lane live").

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

## The writer's key

The session writer's key lets a coding session's ticket tools write as the session writer. It also lets
the runner move a coding ticket into its working state. It belongs to the tracker user in
`session_tools.writer_user_id`. Every reader that trusts a session's words trusts that user (KIT-933).

**Where it comes from.** `WRITER_KEY_SOURCE` in runner.conf picks one of three. The installer writes it
into `session_tools.key_source`. Left out, the source you installed stays.

| Source | Who keeps it working | What the runner does |
|---|---|---|
| `file` (the default) | A person, with `writer-key`, before its end date. | Reads the key file at each use. |
| `other-program` | Another program on the role account. It renews the key in its own JSON settings file. | Reads the key from that file at each use. It never renews it. |
| `renew` | The runner itself. | Renews the key 12 hours before it ends. |

**From another program's settings.** While the previous dispatcher is installed, it holds the app user's
key and renews it about once a day. Point the runner at its settings file:

    WRITER_KEY_SOURCE=other-program
    WRITER_KEY_FROM=~/<that program's folder>/<its settings file>.json
    WRITER_KEY_FROM_FIELD=<the JSON key that holds the access token>

- The runner finds that JSON key at any depth of the file. Every place that holds it must hold the same
  key. Two different keys are refused, never guessed between.
- The runner only reads the file. Each renewal hands back a new renewal token, so two renewers would
  clash, and the runner never renews this key.
- The file must be the role account's, and nobody else may change it. Its program's own mode, such as
  0644, is left as it is.
- The file is walled from every session, like each key file.

**Renewed by the runner.** Once that program is stopped for good:

1. Run `python3 scripts/pipeline_runner_setup.py writer-client` at a terminal. Give the app's client id and
   secret at the hidden prompt, as `id:secret`. It asks for the day you will review the secret.
2. In runner.conf, set `WRITER_KEY_SOURCE=renew`. Keep `WRITER_KEY_FROM`, and set
   `WRITER_RENEWAL_FROM_FIELD` to the JSON key that holds that program's renewal token.
3. Run the installer's `apply`. Its `writer-key` row waits for the runner's first word.

The runner's next pass takes the renewal token from that file, once, and renews at once. From then on it
uses its own files, mode 0600, beside the writer's key: `WRITER_RENEWAL_FILE` (default
`writer-renewal-key`) and `WRITER_CLIENT_FILE` (default `writer-client`). No person copies a key.

How Linear renews a key (its OAuth 2.0 guide, "Refresh an access token"):

- One form POST to `https://api.linear.app/oauth/token`: `grant_type=refresh_token`, the renewal token,
  and the app's client id and secret.
- The answer holds a new key, a new renewal token and `expires_in`. A key lives 24 hours.
- A renewal token ends 30 days after it was issued. Each renewal issues a new one.
- An answer that was lost can be fetched again with the same renewal token for 30 minutes.

The runner writes the new renewal token first, then the key, then the key's end. A stop between two of
them never leaves a used renewal token behind. After a failure it tries again every five minutes, inside
that 30-minute window.

**What it says.** Each pass may update `<store>/writer-key.json`. It holds the source, the state, when the
key last answered as the session writer, the name it answered with, and for a renewed key when it ends.
It never holds a key. The runner asks the key who it is every 30 minutes.

- The runner's `check`, in its `writer-key` row, says the source and when the key ends.
- The live tool's writer row says the same.
- The health watch's `writer-key` kind reads a copy of that file, made by the watcher's copier
  (`WRITER_KEY_STATUS` in watcher.conf; the one command sets `WRITER_KEY_STATUS_FILE`). It reaches Slack
  when the key could not be read or renewed, or answers as another user. It also does when the key has
  not answered for three hours, ends within six hours, or the runner has said nothing for three hours.
- A key that cannot be used is told once in the runner's log, and cleared when it answers again.

A renewed key always ends within a day: that is Linear's 24 hours. So for a renewed key, "close to its
end" means within six hours, which is two missed renewal windows.

**When it fails.**

| It says | What a person does |
|---|---|
| Linear refused the renewal token | It ended, was revoked, or another program used it first. Authorize the app again, as the app, then run `writer-renewal` with the new renewal token. Meanwhile, `WRITER_KEY_SOURCE=file` and `writer-key` keep sessions writing. |
| Linear refused the app's client details | The secret was changed. Run `writer-client` with the current ones. |
| the key could not be read from where it is kept | `check` names the file and why. For `other-program`, start that program again, or fix `WRITER_KEY_FROM_FIELD`. |
| the tracker refused the key | For `other-program`, that program stopped renewing. Start it again, or move to `renew`. For `file`, write a new key. |
| the key answers as another user | The source holds another user's key. Fix the file, or the key name. |
| the runner has not said how the key stands | The runner's job is not running. Its installer's `check` says why. |

**One writer user.** Each reader that trusts a session's words must name the session writer:

- the notifier's `DISPATCHER_AGENT_USER_ID`;
- the acceptance check's `AGENT_USER_ID`;
- the finding filer's `agent_user_id`;
- the bridge's `AGENT_USER_NAME`, by display name.

The `writer-user` row of `check` compares each with `writer_user_id`. It fails, naming the one that
differs. The bridge's name is compared with the name the key answered with, once the runner has one.

**Removing the previous dispatcher.** Switch to `renew` first, and wait until `check` says the runner
renewed the key. Then remove the program. Nothing reads its file any more, so the key keeps working. Take
`runner-writer-key` out of CREDENTIAL_EXPIRIES: that date was for a key a person wrote. The parts list still
says a writer's key ends on a date set when it is made, which is true of the `file` source. So the one
command asks for that date on each run, and `dates` lists it as missing: press Enter, since a key that is
renewed has no date to give.

Good: `check` says the writer's key is renewed by the runner itself, answered 4 minutes ago, and ends tomorrow at 03:12.
Not: a person copying the key into `writer-key` each morning. It ends within a day, and nobody is warned before it does.

## The key files

The runner holds three key files, and two more for a renewed writer's key (KIT-933). Each is the role
account's own, mode 0600, one key and nothing else. One installer step writes each, the way it writes the
push token (KIT-907, KIT-928):

| Step | File | Who reads it | Its end date is filed as |
|---|---|---|---|
| `push-token` | `PUSH_TOKEN_FILE` | The code, fix and conflict sessions, through git's helper | `runner-push-token` |
| `tracker-key` | `tracker-key` in `RUNNER_DIR` | The runner, for its own tracker reads | `runner-tracker-key` |
| `writer-key` | `WRITER_KEY_FILE` (unset: the `session_tools.key_file` you installed, else `writer-key` in `RUNNER_DIR`) | The runner, to move a ticket, and the session's own tracker tools | `runner-writer-key` |
| `writer-client` | `WRITER_CLIENT_FILE` (unset: `writer-client` in `RUNNER_DIR`) | The runner, to renew the writer's key (`renew` only) | `runner-writer-client`, the day you review it |
| `writer-renewal` | `WRITER_RENEWAL_FILE` (unset: `writer-renewal-key` in `RUNNER_DIR`) | The runner, to renew the writer's key (`renew` only) | nothing: the runner renews it |

Run each one at a terminal, never in a Claude session (it is refused there, and blocked with no terminal):

    python3 scripts/pipeline_runner_setup.py tracker-key
    python3 scripts/pipeline_runner_setup.py writer-key

`writer-key` is refused while `WRITER_KEY_SOURCE` is `other-program` or `renew`: a key written then would
never be the one used. `writer-renewal` asks no end date. You need it only when Linear refused the
runner's renewal token ("The writer's key", above).

Make the key first. The step asks for it at a hidden prompt, then for the day it ends. A key that never
ends gets the day you will review it, so the warning still comes. It judges both before it asks for
your password, writes the file as the role account, and files the date for the health watch. The key
is in no command line, no output and no log. Run it again to replace a key.

`check` has a row for each step. It says the file's owner, mode and presence in words, never its
text. A file with no end date on record, or one it cannot read, is NOT MEASURED. An ended key, a file
others can read and a link are FAILED. No file yet is no fault, except the writer's key when a code,
fix or conflict lane is on. The installer does not choose which tracker user the writer is: that is
`session_tools.writer_user_id`, yours.

Good: `check` says `writer-key` is present, the role account's own and closed to group and others, and ends in 200 days.
Not: a key file that others can read. The row fails and says the mode, and the runner will not read it.

## The coding lane

A coding session runs through the runner when its project's `lanes.code` is on (KIT-645). That one
setting switches a project, and setting it back to false switches it back.

1. **The queue asks.** The queue's starter decides as before. The bridge does the starting: for a
   repository its `runner_lane` wires to a runner project whose code lane is on, its one hand-over
   writes a code request to the runner. Nothing is delegated in the tracker. A plan's piece the
   queue starts goes the same way.
2. **The runner judges it** with the queue's own reader and judge, strictly: a named approver, or
   no start (see "What it does").
3. **It moves the ticket into its working state** as the program starts: delivery.json's
   `linear.stateIds.working`, with the session writer's key, checked as the writer's and never an
   owner's. So the ticket's history says the run began, and the move never reads as an approval.
   This is the runner's one write to the tracker. Not taken, nothing is left, and the request waits.
4. **The pin carries the budget, the mode and the criteria.** The program gets `--max-turns` and
   `--max-budget-usd` from it, and the run is held to its `maxMinutes`. The kit's guard reads the
   pin beside the lane marker: its rules are added to the lane's, which still hold (contract
   section 3). That guard change is the owner's to apply, as every hook change is.
5. **The session uses its five tools** for its ticket, and has no other way to write to the tracker.
   It pushes its branch and opens its pull request with the push token (see "The push token").
6. **The bridge reads the answer** each pass. A start gets the comment naming who approved it. A
   refusal ends the bridge's pass loud once, in the runner's words. So does a request with no
   answer in 30 minutes. A ticket refused for one approval is asked again only on a newer one.

To switch a project on, the runner's settings need `session_tools`, `queue_config` and
`push_token_file`, and the bridge's `runner_lane` names the repository's runner project. The fix
driver's own `RUNNER_LANE_REPOS` names that project too (KIT-765), and the runner's `fix` and
`conflict` lanes send its fixes and conflict fixes there (KIT-708).

**Not on this lane yet.**

- **The tech lead's own start.** On a switched project, the tech lead refuses `start` and `retry`
  in Slack, in words, and the queue lets go of a piece the plan's limit stopped. The runner starts
  a ticket only on its approver's move. The runner's own way to start, retry and resume a piece
  must land before any real project's coding lane is switched on, or the owner loses `start` there.

Good: the owner moves ENG-12 into Todo on a switched project. The queue asks the runner, ENG-12
moves to In Progress as the session writer, and the session's comments come through its own tools.
Not: the owner types `start ENG-12` in Slack on a switched project. The tech lead says to move it
into Todo instead, and nothing is handed to the old dispatcher.

## The planning lane

The planner job (`scripts/pipeline_plan_poller.py`) is the first caller (KIT-705). When the owner
moves an idea to Plan it, the planner asks the runner for one `plan` start on the idea itself.

- **The switch is the runner's.** A repository plans here when `lanes.plan` is on for the runner's
  project whose committed `delivery.json` names it. The planner's `runner_settings`
  (`RUNNER_SETTINGS` in `stage-a.conf`) only says where this file is. Empty, every repository plans
  on the old dispatcher.
- **No planning ticket, no routing label.** The request names the idea. Its text opens with three
  lines, `Planning-run trigger:`, `Idea:` and `Repository:`, then any earlier attempts and the
  owner's note. The session gets that text and the idea, each fenced as data.
- **The owner's move still decides.** The runner checks the idea's own history for his newest move
  into an approved plan state. So the team's Plan it state must be in the project's
  `approval_states.plan`. Until it is, the planner starts nothing on this lane, and says why.
- **The same plan document.** The session ends with the one fenced block it always did. The
  planner reads the run's record and final message, then runs the plan executor unchanged, pinned
  to the idea. The plan's receipt names the idea.
- **Notes and pages land on the idea.** Nothing is moved, because no ticket was made.
- **The caps count from the store.** A day's runs and re-runs are the planner's own requests here,
  added to the old lane's. A store the planner cannot read starts nothing.
- **Past the planner's time-out,** a request still waiting is taken back, and a run still going is
  asked to stop, once. A request the runner refuses ends the attempt with the runner's reason.

The old lane's probe sign-off, its version check and its routing stop do not hold this lane:
nothing here is routed. Settings the planner cannot read start nothing on either lane.

**The way back.** Turn `lanes.plan` off. A run already asked finishes on the runner, and the planner
still reads it back. Empty `RUNNER_SETTINGS` only once no run there is in flight: the day's caps
still count the runner's runs from the planner's own records, but a run whose record was lost is
found again only through the store.

Good: `"lanes": {"plan": true}` for the drill repository. Its next idea becomes one plan run, and
the plan lands on the idea.
Not: a planning ticket for that idea on the work team. On this lane none is made.

## Settings

One JSON file, which the installer writes from `runner.conf` (see "Installing it"). Its own example is
`runner.example.json`. `~/` means the account's own home.

| Key | What it is | Default |
| --- | --- | --- |
| `enabled` | Takes requests and resumes runs when true. Off, it still finishes runs already started. | `false` |
| `store_dir` | The runner's store. Outside every git working tree, mode 0700. | required |
| `work_dir` | Where workspaces go. | required |
| `session_home` | The sessions' own home folder. Holds nothing of the account's setup. | required |
| `claude_bin` | The program's full path: 2.1.259 or later, because the command line uses `--permission-prompts`. A copy the sessions' account cannot change, never that account's own. | required |
| `model` | The model for every session, but a guarded review. | `sonnet` |
| `guarded_model` | The model for a review whose request says `guarded`: a change to a guarded path (KIT-418, KIT-644). | `opus` |
| `token_env` | The NAME of the variable that holds the sign-in token. | `CLAUDE_WRITER_OAUTH_TOKEN` |
| `linear_key_file` | The tracker key, in a file of this account's, mode 0600. | required |
| `push_token_file` | The sessions' push token: one GitHub token, in a file of this account's, mode 0600, outside the work and home folders and every git working tree. The installer writes it from `PUSH_TOKEN_FILE`, and `push-token` makes the file. See "The push token". No code, fix or conflict lane may be on without it. | `<RUNNER_DIR>/push-token` |
| `owner_user_ids` | The owner's tracker user ids. Only his moves approve. | required |
| `dispatcher_user_ids` | The old dispatcher's own users. Never an approver. | required |
| `poll_seconds` | Time between passes. | `5` |
| `stop_grace_seconds` | After a stop, how long before the session is killed. | `30` |
| `max_starts_per_ticket` | A runaway's limit on starts of one ticket and kind. | `20` |
| `max_review_starts_per_ticket` | The same limit for reviews: above a full loop of parts and re-reviews (KIT-644). | `100` |
| `hold` | The watcher's hold file (contract section 20). Set, nothing starts. | none |
| `limits` | `wall_minutes`, `memory_mb`, `disk_mb`, `check_seconds` (how often trees and folders are measured) and `room` (the floor). See "Its limits". | above |
| `session_tools` | `key_file`, the session writer's tracker key, and `writer_user_id`, the user it must belong to, never an owner. `key_source`, where the key comes from: see "The writer's key". See "The session's own tracker tools". Absent, no session gets tracker tools, and no code, fix or conflict lane may be on. | none |
| `queue_config` | The queue's settings file, whose reader and judge approve each code start. No code lane may be on without it. | none |
| `look_config_dir` | The folder the acceptance job writes each check's look config into: its own `look_config_dir`. The acceptance kind's look tool is started on it. Needed while any project's acceptance lane is on. Never inside the other three folders, nor in a git working tree. | none |
| `projects` | Each project: `repo` (the runner's own clone), `base`, `lanes`, `approval_states` per kind, `allowed_hosts`. | required |

The four folders must not sit inside one another.

**From runner.conf** (KIT-919). You never edit `runner.json` for these. The installer writes them from
`runner.conf`:

- `SESSION_TOOLS_KEY_FILE` and `SESSION_TOOLS_WRITER_USER_ID` become `session_tools`.
- `WRITER_KEY_SOURCE`, with `WRITER_KEY_FROM`, `WRITER_KEY_FROM_FIELD`, `WRITER_RENEWAL_FROM_FIELD`,
  `WRITER_RENEWAL_FILE` and `WRITER_CLIENT_FILE`, becomes `session_tools.key_source` ("The writer's key").
  `file` is no source at all, and left out, the installed source stays.
- `QUEUE_CONFIG` becomes `queue_config`, and `LOOK_CONFIG_DIR` becomes `look_config_dir`.
- `PUSH_TOKEN_FILE` becomes `push_token_file` (see "The push token"). Left out, it keeps what is
  installed, and else is `push-token` in `RUNNER_DIR`.
- `APPROVAL_STATES_<KEY>` (`code:<state id>, plan:<state id>`) and `ALLOWED_HOSTS_<KEY>` become each
  project's `approval_states` and `allowed_hosts`. `<KEY>` is the project's key in capitals, with `-`
  written as `_`.

A key you set decides, and `off` writes the off itself. A key you leave out keeps what is installed:
your own value, or an off you set.

Good: `ALLOWED_HOSTS_APP=github.com, api.github.com, registry.npmjs.org`. App's sessions may reach npm too.
Not: editing `allowed_hosts` in `runner.json`. Use runner.conf, so a rerun writes the same thing.

**Each project's lanes.** `lanes` holds `true` or `false` for each kind of session: `code`, `fix`,
`conflict`, `review`, `plan` and `acceptance`. A kind left out is off, and so is a project with no
`lanes`. The runner refuses a start or resume for a lane that is off, before it makes or reads
anything, and says so in the request's answer. Its own resume of an interrupted run on such a lane
is refused too, and the run's record says `auto_resume: refused`. A stop is always taken. A lane that
is on needs its approved states in `approval_states`: `fix` and `conflict` use the `code` states,
and `review` needs none, since it follows its code run.
`enabled` stays the runner's own switch: off, it takes nothing, whatever the lanes say.

Good: `"lanes": {"review": true}` for the drill repository. Only reviews of it go through the runner.
Not: a review request for a project whose `review` lane is off. It is refused: "the review lane is
off for project app".

**It says which lanes it takes** (KIT-708). Each pass writes `<store_dir>/lanes.json` from its own
checked settings, when they change: whether it is on, each project's lanes that are on, its work
folder and `look_config_dir`. The acceptance job, the fix driver and the catch-up job read it. A lane it
does not take, or a runner that has written nothing yet, is the job's old road. A file that cannot be
read is "could not tell": the job sends nothing and says so, and never reads it as off. The catch-up
job then leaves no team out, and fires, so a lost delegation is still re-sent.

Good: the owner sets `"acceptance": true` for project app. The next acceptance check of app's
repository is asked of the runner. He sets it back to false, and the next check files its ticket.
Not: a job that keeps asking the runner for a lane the runner no longer takes.

## Installing it

The one command installs it when this Mac has a `runner.conf` (copy `runner.conf.example`). Its
installer also runs alone:

```bash
python3 scripts/pipeline_runner_setup.py check          # reads, changes nothing, asks nothing
echo '{"schema": "runner-answers/1", "consent": "yes"}' | python3 scripts/pipeline_runner_setup.py apply
python3 scripts/pipeline_runner_setup.py push-token     # the owner's step: the push token and its end date
```

`check` and `apply` print one JSON document of rows on standard output (`runner-setup-rows/1`), whatever its exit,
and every word for a person on standard error, as the notifier's installer does.

- **Every lane off.** It writes `<RUNNER_DIR>/runner.json` as the role account, and gives a new
  project every lane off. Switching one on is the owner's yes, per project, and this installer never
  does it. So it writes only the keys it owns, from `runner.conf`. A lane you switched on, its approved
  states and every other key you set are kept as installed. A file that is not JSON is never written
  over. The keys it carries for you are in "From runner.conf" under Settings.
- **Its copy of the kit** (KIT-919). The runner runs from `ENGINE_DIR` (default
  `/usr/local/pipeline-runner/kit`). That is a copy of the kit at the commit Stage E's clone is at, and
  every file and folder in it is root's, closed to group and others. `check` compares the copy file by
  file with that commit. It also runs the runner's own check of the copy, for the role account and for
  you. When the clone moves, `check` says the copy is behind, and the same `apply` refreshes it. Nothing
  else writes the copy: not the role account, and not a session.
  - The files come from this checkout's own git objects at that commit, never from the role's clone.
    Run the installer from a checkout that has fetched it.
  - Only a commit on `origin/main` is copied, and the copy never moves back to an older commit.
  - A small root program writes the files beside the old copy and swaps the two. Then the copy is read
    back. A copy that cannot be read is NOT MEASURED, never current.
  - A job that runs from the copy is stopped before the swap, so no session's guard comes from a copy
    half moved. The `job` row loads it again. A refresh that fails loads it again at once, and says so.
- **Its job.** One LaunchDaemon as the role account, kept alive, running the runner from its copy of the
  kit. Stage E's `run` finds it there, stops it around a move of the clone and loads it again, still
  on the old copy. The copy moves only with this installer's `apply`, which then loads the job again.
  The job file says `Disabled`, so it starts at boot only after `launchctl enable`, which only an
  install that switches it on runs.
- **Its switch and its ceiling.** `enabled` is the part's own switch. Once installed it is kept: on
  stays on, and off is never switched back on by the installer. To switch it on, set `enabled` to true
  in `runner.json`. A first install counts its first pass: the requests waiting in its store and its
  unfinished runs. At or below its ceiling in `pipeline-parts.json` (0), it is switched on and loaded.
  Above it, or with a ceiling it cannot read, it is installed and left off. The document's `left_off`
  says why.
- **It asks nothing.** `apply` takes its answers through a pipe from the program that runs it, never a
  terminal, a file or an argument. Every sudo call is `sudo -n`. With no live sudo session, `check`
  says NOT MEASURED and `apply` exits 5. The one command holds that session. Inside a Claude session,
  `apply` is refused before it reads anything.

`check` reports the job: its file, whether it starts at boot, and the kit commit launchd runs. It
reports the clone's commit, whether its runner scripts are this checkout's, whether the copy matches
that commit, and each project's lanes as installed.

Good: a fresh Mac. `apply` writes every lane off, switches the runner on and loads it. It starts nothing.
Not: a store with requests left from before. The runner is left off, and the line says how many wait.

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

```bash
npm run test:writer-key
```

The writer key's selftest (KIT-933) reads the key from another program's settings file in three
shapes, and refuses every bad one in words. It renews against a stand-in of Linear's token endpoint
that takes each renewal token once, three days running, with the other program removed. It checks the
order the files are written in, and that no status or word holds a key.

## Proving a lane live

One script proves one lane on one test project, from off to on and back to off (KIT-916). You run
it, in your own terminal. It refuses a model's environment and root, and asks for your Mac password
once.

```bash
python3 scripts/pipeline_runner_live.py check --lane code --project <key> --ticket <ID>   # changes nothing
python3 scripts/pipeline_runner_live.py run   --lane code --project <key> --ticket <ID>   # one typed yes
python3 scripts/pipeline_runner_live.py off   --lane code --project <key>                 # only switches off
```

The lanes are `code`, `fix`, `review`, `plan` and `acceptance`. The project is a key of the runner's
settings, and the ticket is the one its chain is about: the ticket to code, the coded ticket to fix or
review, the idea to plan, or the piece to check.

1. **Check.** Sixteen rows, each OK, MISSING with what to do, or NOT NEEDED for this lane. Anything it
   cannot read is UNKNOWN, never OK, and stops the run. The rows: the runner installed and on (its
   installer's own `check`, the push token's end date among its rows); its job running from a copy the role
   account cannot change (its root-owned copy, KIT-919); its job running main's head (the commit its job
   file names);
   the project's clone, HTTPS, reached; `RUNNER_STORE` for the queue; each key file the role account's,
   mode 0600 (another program's settings file the writer's key comes from: closed to writes by others);
   a dry-run push with the push token; the writer key answering as its user, with where it comes from
   and when it ends, as the runner's own word on it says (KIT-933); the probe passing
   at a commit with main's session kinds; `delivery.json` on main passing the kit's validator; the
   project's `docs/SESSION-BRIEF.md` naming the ticket tools; now inside the working window; the lane
   off; the settings with it on taken by the runner's own loader; no other approved ticket waiting; and
   the ticket ready.
2. **Show and ask.** It says what it will switch, then waits for you to type `yes`.
3. **Switch on.** It sets `lanes.<lane>` for that one project, as the role account, and waits until the
   runner says so in its store.
4. **Wait.** It tells you your one step, if the lane has one (the coding lane: move the ticket into
   Todo). Then it reads the store once every 20 seconds, up to the lane's time limit (`--minutes`).
5. **Switch off.** The same setting, back to false, confirmed by the runner. It watches three minutes
   that nothing of the lane starts after. A failure, Ctrl-C or a closed terminal switches it off first.
6. **Compare and print.** The run record, the tracker's history, the pull request and the ledger, where
   the lane's proof asks. The approval's time is checked against the ticket's state history itself, so
   an approval dated by an earlier change in the same history entry disagrees (KIT-934). Then one result
   block to post on the proof's ticket, with what did not work.

No key reaches the script, its output or a command line. Each key file is looked at with `stat`. The
dry run hands the push token to git as a session's git gets it, and the writer key is asked who it is by
the runner's own code, both inside the role account's own processes. The tracker is read by the runner's
own reader, as the role account. GitHub is read with your own `gh` login.

Exits: 0 passed and off · 1 a step failed or did not agree, and off · 2 arguments · 3 refused · 4 not
ready, nothing changed · 5 no sudo · 6 the lane may still be on: run the `off` line it prints · 10 not yes.

Good: `check` says every row OK. `run`, `yes`, move the ticket into Todo. The result says PASSED.
Not: the lane switched on by hand in `runner.json` for a proof. Nothing then checks it, or switches it back.

## When it refuses

Each refusal says why, in the request's answer (`<store_dir>/taken/<id>.json`).

| It says | What to do |
| --- | --- |
| the ticket's newest move was not the owner's | The owner moves the ticket himself. |
| the queue's judge does not admit ENG-12 (a reason) | The judge's own reason, from the queue's pool: the owner moves it, or it is not ready. |
| the queue's judge admitted ENG-12 by its limits, with no named approver | An agent-filed finding: the owner's move into the ready state starts it. |
| the ticket could not be moved to its working state | Nothing to do: nothing was made, the request waits, and the next pass tries again. |
| a code lane that is on needs session_tools, queue_config or push_token_file | Settings: set all three before switching a project's code lane on. |
| `<ticket>` was not coded by a code run of this runner's | The ticket's coding ran on the old dispatcher, so no approval is recorded for its review. Review it by hand, or move its project's coding to the runner first. |
| a review's start says its tier | The review job's request said neither `standard` nor `guarded`: a fault in that job. |
| a workspace is there already | For a code start: the ticket's earlier code run keeps its workspace for its fixes, so remove it before a new start. For a review, plan or acceptance start: no run of the runner's left that folder, so it is never removed for you. Look at what it is first. |
| an earlier run is still going or is to be resumed | Wait. A review, plan or acceptance start can follow once that run has ended. |
| the project has no committed delivery.json | The project is not set up for the pipeline. |
| `<name>` is not set | The sign-in token's variable is missing from the job's environment. |
| the tracker could not be read | Nothing to do: the request waits, and the next pass tries again. |
| `<n>` move(s) in the ticket's history have no time of their own in its state history | The request waits, and each pass reads it again. A move counts only at the time its state history gives it (contract §1), never at an earlier change Linear kept in the same history entry. If it stays, start the work from a fresh ticket. |
| the push token file could not be read, can be read by others, or does not hold one GitHub token | Fix the file `push_token_file` names: this account's, mode 0600, one GitHub token. The request waits and starts on a later pass. |
| the machine is below its floor | Free disk or memory. The request waits; it starts when there is room. |
| the workspace's `.env` names a variable the program reads | Take the variable out of that file, or out of the project's committed copy. |
| the engine's folder could be changed by this account | The runner must run from its root-owned copy of the kit. Run the installer's `check`: its `engine` row says what is wrong, and `apply` makes the copy again. The request waits until then. |
| the session kinds could not be loaded | A kind's data file is missing or wrong: `python3 scripts/pipeline_session_kinds.py --check` says which. |
| the kit's session kinds changed after this runner loaded them | The kit moved after the runner started. Restart the runner: it loads them, and the request starts. |
| the kit's session kinds could not be read to compare | The folder, or a kind's file in it, cannot be read (a link, say). `python3 scripts/pipeline_session_kinds.py --check` says which. The request waits. |

The runner refuses to start from inside a Claude session, or as root.

## Not proven yet

- That a session outlives a restart of the runner under launchd. The selftest proves it for a runner
  killed with its whole process group. One live check is owed.
- That the program says a lost session in the words the runner looks for: "No conversation found",
  as an error result or as the last line of its errors. The tech lead's turn relies on the same
  words. The selftest's stand-in prints them. One live check is owed.
- That one sign-in serves the runner and the tech lead together (KIT-629).
- That a real push signs in with the push token from inside the built-in sandbox. The adapter's
  selftest runs git's own `git credential fill` against its helper, and the probe asks it as the
  role account. The first live run (KIT-882) pushes for real, after the owner re-runs the probe.
- Each kind's walls as the role account: the probe above has not been run live yet. Until it
  passes, no real session goes through the runner. It also measures what is still open here: that
  sandboxed Bash may write its temp folder, that a commit works in a workspace whose git folder sits
  in the runner's clone, and whether the code host's command-line tool gets through TLS.
- That our own sandbox lets the real program run for a review or plan session. The selftest runs
  the profile on a Mac with a stand-in. The probe runs the real program in it.
- That the acceptance kind's look tool can start its own sandbox in an acceptance session. It is
  a tool server, started outside Bash's sandbox, so nothing should nest. It joined that kind with
  KIT-708. The probe does not start it yet, so one live check of an acceptance run is owed before
  the acceptance lane moves.
- That a real review runs through the runner on the owner's Mac, and that its comment matches the
  old lane's (KIT-644's last criterion). It needs the runner installed (KIT-859), and a ticket
  coded through the runner (KIT-645), since a review follows only the runner's own code run.
- That a real session calls its own tracker tools, and that Linear takes their comments. Their
  selftest runs the real server over stdio against a stand-in tracker. One real ticket from the
  owner's yes to a reviewed pull request on the coding lane is the first live run, on the owner's
  Mac, once the runner is installed.
- That Linear takes the runner's move as the session writer, and that the queue's judge reads a
  real board inside a pass. The selftest runs the queue's own reader over a stand-in board.
- How the owner's answer to a session's question reaches the work again on this lane. The
  notifier pages the question as today. Taking the answer back to the session by resume is
  KIT-707's.
- That a plan run through the runner files a plan live. The planner's selftest runs every case on
  both lanes against a stand-in runner over a real store. The first live run is its own ticket
  (KIT-887), once the runner is installed.
- That the runner's own renewal of the writer's key works against Linear's real token endpoint
  (KIT-933). The selftest renews against a stand-in that keeps the rules Linear documents. The first
  real renewal is the one after `WRITER_KEY_SOURCE=renew`, and `check` says how it went.
- That the key read from another program's settings file keeps answering for two days running, with no
  person writing a key (KIT-933's second criterion). The owner runs the live check on two days in a row.
