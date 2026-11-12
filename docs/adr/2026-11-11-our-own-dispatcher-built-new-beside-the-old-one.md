# Our own dispatcher, built new beside the old one

**Date:** 2026-11-11 · **Status:** Accepted (the owner, 12 Nov 2026 UTC: his six answers on KIT-701, then
"Yes, build beside"). Nothing is built yet. · **Context:** KIT-641, under KIT-503. The plan is KIT-701's design,
its first section winning. The walls rest on KIT-630's live run.

## Decision

1. **We build our own dispatcher, new.** It is written in the kit's standard-library Python. We do not fork the
   third-party dispatcher the pipeline uses today (Cyrus).
2. **It is one always-on job, the runner.** It runs the official `claude` program headless, one process per
   session. Other jobs ask it to start, resume or stop a session.
3. **It is built beside the old dispatcher, in new files.** The old one keeps doing everything until a lane moves.
   Every lane sits behind a setting that is off.
4. **Nothing moves without the owner's yes,** one lane and one project at a time. A drill repository goes first.
   The kit and the first project move last. Each move has a one-setting way back.
5. **Tickets stay in Linear for now.** A tracker on the machine is planned as a choice beside Linear. It is not
   decided, and nothing of it is approved to start.

## The owner's six decisions

| # | Decision | His answer | Why |
| --- | --- | --- | --- |
| 1 | Fork the third-party dispatcher, or build new | Build new | A fork would be mostly deletion, in code we do not know. It would bring Node and npm into an engine whose rule is standard-library Python. |
| 2 | Where tickets live | Linear, for now. Later the app supports both: a tracker on the machine, or Linear | A tracker on the Mac would cost Linear's board, phone app and cloud copy before the app's window can stand in. Most of the install win comes from the dispatcher, not from leaving Linear. |
| 3 | Where this sits against the first project building itself | The dispatcher lands first. The first project runs on Linear with our own dispatcher | His rule: everything that changes the system lands before the first project is switched on. |
| 4 | One OS account for sessions and jobs, or two | One for the first build, made easy to split | Nothing new to install. A session starts in one function, so a second account is added in one place. |
| 5 | The copy of the tickets that leaves the Mac | Waits. Linear keeps the cloud copy | With tickets in Linear, a dead disk loses no ticket. It returns with the tracker on the machine. |
| 6 | Linear afterwards | Keep it, as one of two supported kinds | It is the way back during any move. Two kinds keep one tracker module honest: an interface with one implementation is tested by nothing. |

His requirement with decision 2: the pipeline behaves the same with Linear on or off. Only where tickets, the board
and the backup live differs.

## Build new, not fork

**What a fork would have kept:** a workspace maker, a model picker, a file of sessions in flight, a stop hook and a
sandbox settings builder. The kit has its own of each, or the pin replaces it.

**What a fork would have cut out first:** the web server and its tunnel, the Linear agent-session transport, the
Agent SDK runner, the remote-config routes and the chat transports. That is most of it.

**What building new buys:**

- Every command is a list of arguments. Branch names come from a fixed pattern. Nothing goes through a shell.
- No listener. Requests arrive as files in the runner's own store.
- Ticket text reaches a session only as fenced data, from the pin's snapshot. Nothing expands over it.
- A short fixed environment per session, not the dispatcher's whole one.
- Limits on time, memory and disk are part of every run.
- No upstream to track.

The third-party dispatcher's private security findings are tracked in KIT-34 and stay out of the kit. That report to
its authors is still owed whichever way this went.

## What does each job afterwards

| Job | Today, by the third-party dispatcher | Afterwards, by the runner |
| --- | --- | --- |
| Trigger | Linear's agent-session events reach its web server through a public address | A request file in the runner's own store, written by one of five jobs. No listener and no open port |
| Authority | A person's delegation in Linear | The runner checks each request against the tracker's history: who approved the ticket, and whether this caller may ask for this kind. The caller's word is never the authority |
| Workspace | A git worktree per ticket | A fresh worktree in a folder named for the ticket. Every git command is a list of arguments, and the branch name comes from a fixed pattern |
| Session start | The Agent SDK, inside the dispatcher's own process | The official `claude` program, headless, in its own process group. One command line, built in one place, with a short fixed environment |
| Confinement | Settings and hooks load from the worktree | Nothing loads from the workspace. Guards run from a path the session cannot write. Bash runs in the built-in sandbox, and file tools are held by rules (below) |
| Resume | A reply in the ticket's thread becomes a new prompt | A resume request names the run. The runner resumes that session with text code writes. A session that died is resumed once |
| Cost record | A log per session, which the usage job reads | One run record per run, in the runner's store: session id, exit, reason, time, cost and turns. The usage job reads it |
| Per-session tools | Its default tools and tool servers | Each kind lists its tools. Later, five tools tied to the session's own run, and no tracker sign-in (KIT-706) |
| Limits | None of its own on time, memory or disk | Every run is held to time, memory and disk limits from settings. A runaway's whole process tree is stopped (KIT-643) |

## The runner, and why

- **One always-on job, under the role account.** It owns every session's process. Under launchd on this Mac; under
  the app's supervisor later. Never in a window, because a window can be closed.
- **Why always on.** The other jobs are short and run on a clock. A session runs for hours. Something must stay to
  hold its limits and record its end.
- **Why the official program, headless.** The kit already runs it that way twice, the tech lead's turn and the
  conflict fixer, with exact command lines pinned by tests. It needs no SDK and no npm.
- **Why one process per session.** A crash ends one session. A limit applies to one process tree. A restart of the
  runner ends none: each session writes its result to a file the runner finds again.
- **Each kind of session is plain data** (KIT-704). One small adapter turns it into the command line. A second
  model provider would be a second adapter. It is not built now.

## How a session gets its instructions, with nothing loaded from the workspace

| What it is given | From | Can the session change it? |
| --- | --- | --- |
| The program | One pinned version for the role account | No |
| Settings and guards | The engine's own folder, outside every workspace: root-owned on this Mac | No |
| Its tools | The kind's list, in a file the runner writes outside the workspace | No |
| The project's instructions | The project's default branch, read by the runner and handed in with `--append-system-prompt-file` | Only by a merged pull request |
| The ticket | The pin's snapshot, fenced as untrusted data | No |
| Settings, hooks, tool servers and `CLAUDE.md` in its working copy | Not loaded: `--setting-sources ""`, `--strict-mcp-config` and the runner's own `--settings` file | n/a |

## The walls, from KIT-630's live run

The owner ran it on 11 Nov at 23:53 US Eastern, as his own account, on Claude Code 2.1.287.

| Row | What it found | What we do |
| --- | --- | --- |
| r0, r1, r1c | With project settings on, the worktree's hook, settings, tool server and `CLAUDE.md` all load. With the three flags above, nothing loads, and outside instructions arrive | Every kind runs with the three flags |
| r2k | The kit's own guard, run from a copy outside the repository, refused an edit on `main` | Guards run from a path the session cannot write |
| r2 | Printed PASS, but for the wrong reason: the program's own read-before-edit rule refused, not the stand-in guard | **Not proof.** r2k covers the need. KIT-704 owes the fixed judge |
| r3, r3b | The built-in sandbox held Bash: no write outside, an unlisted host refused. Without a deny-read list, Bash read the outside folder | Every kind with Bash carries a deny-read list |
| r3c | NOT MEASURED: the model declined the commands | It does not weaken r3. In r4a a file tool wrote to the same folder, so r3's refused write was the sandbox's doing |
| r4a | File tools allowed by bare name read and wrote outside the workspace, sandbox on | **No kind allows a file tool by bare name** |
| r4b, r4c | Not allowed, or allowed with deny rules for the outside folder: refused | File tools are allowed by path only, or held by deny rules |
| r5a | Our own macOS sandbox around the whole program held the file tools | A kind with no Bash can run inside it |
| r5b | **FAIL:** inside both sandboxes Bash did not start (`sandbox_apply: Operation not permitted`) | **The two cannot be combined.** A kind that needs Bash uses the built-in sandbox, with deny rules for its file tools. A kind with no Bash (review, probably plan) can run inside our own sandbox |

**Decision 4 stands on this.** File tools can be held by rules (r4b, r4c) and by our own sandbox (r5a). So one
account is enough for the first build. If the role account's rows disagree, two accounts become the wall.

**Still owed, all in KIT-704:** the same rows as the role account; the fixed judge for r2; and a new row, file
tools allowed by path, for the workspace only. No lane moves before they pass.

## The order, with the way back

Phase 1 is approved: the dispatcher.

| Step | What moves | What still runs as today | The way back |
| --- | --- | --- | --- |
| A. Foundations | Nothing. The runner and its store (KIT-642), its limits (KIT-643) and the session kinds with their live probe (KIT-704) are built and tested beside the live system | Everything | Nothing to undo |
| B. Reviews | Review sessions run through the runner (KIT-644) | Tickets, approvals, planning, coding | One setting per project |
| C. Planning | Planning sessions (KIT-705) | Tickets, approvals, coding | One setting per project |
| D. Coding and fixes | The session's own five tools (KIT-706); coding sessions with a pin (KIT-645); a fix by resume of the same session (KIT-707) | Tickets, comments, states, labels, approvals | One setting per project |
| E. The last one | The acceptance check's session; a conflict on a dispatched pull request by resume (KIT-708) | The same | One setting |
| F. Removal | The old dispatcher and its host pieces leave the Mac, by a script the owner runs (KIT-646) | Tickets, in Linear | Rebuild from the host guide. Slow: this is the point of no easy return |

Each step moves one lane for one project, on the owner's yes: a drill repository first, the kit and the first
project last.

Phase 2 is planned, not decided: a tracker on the machine as a choice beside Linear (KIT-703, KIT-495, KIT-709 to
KIT-716). Tickets would move project by project, by import, with the kit's own last. It starts when the app's window
gives the owner a board, and on a fresh yes from him.

## Contract changes, and the order they land in

1. **A "Run record" section,** new, with the runner (KIT-642). It holds the request and the record of each run.
2. **Section 3: the pin and the lane marker together,** with the runner (KIT-642). The runner writes a pin for each
   session it starts. The marker says "this account is the pipeline's"; the pin says "which ticket". A guard that
   reads the runner's pin under the marker is a lane's own change, behind its setting. Until then the marker's rules
   hold as written.
3. **Section 9: the dispatcher state counted from the runner's runs,** when coding sessions move (KIT-645).
4. **With the tracker, in phase 2:** the `tracker.kind` setting and a "Tracker" section.

Two pieces that each add "the next section" always collide, so each new section lands in its own pull request, one
after the other.

## Verified

- **The walls:** KIT-630's live run, above. Thirteen rows, run once, as the owner's account. The role account's rows
  are not run yet.
- **The headless program:** the kit runs it today in the tech lead's turn and the conflict fixer, each with its command
  line pinned by its selftest.
- **The dispatcher's surface:** the KIT-701 plan read one version of its published packages, and nothing was run.
  The version on this Mac is three releases older.

**Not proven, each with an owner:**

- That a session outlives a restart of the runner under launchd. KIT-642's selftest, then one live check.
- That one sign-in serves the runner and the tech lead. KIT-629.
- Whether the built-in sandbox needs a certificate, and whether the code host's command-line tool works inside it.
  KIT-704.
- The size of the build. It is counted in pieces, not measured in time or usage.
