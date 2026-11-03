# Stage A — the idea gate, for the operator

The sibling of `docs/STAGE-E-OPERATOR.md`, pointed at planning instead of review. It says
what each part is, what starts it, what it refuses, and what only a person can do. Every
value here is synthetic; a deployment's real names live in its own operator notes, never
in this repository.

**Off unless installed.** Nothing in this document runs in a repository that has no
`delivery.json` and no installed job. The kit ships the mechanism; a person installs it.

**Approving from Slack.** With the Slack bridge acting (`docs/BRIDGE-OPERATOR.md`, Step 9),
you approve in the chat instead: `approve <epic id>`, then a trusted member's `yes`. The
bridge moves the epic to Todo. It hands the children to the agent for you, one at a time
by default, each once the tickets it waits on are Done. It never hands over the epic
itself, and merging stays yours.

---

## 1. What the gate is

You have an idea. You want it turned into an epic and child tickets a session could pick
up, without writing them yourself, and without a machine filing tickets nobody approved.

There is no separate planning team. **Planning happens on each project's own work team**,
beside its real work. The gate is four parts and one gesture:

| Part | What it is | Who runs it |
|---|---|---|
| **Plan it** | A to-do state on each work team. Moving an idea into it is the only thing that starts planning. | you |
| The **planner job** (`scripts/pipeline_plan_poller.py`) | A scheduled program under a role account — usually the dispatcher's own, as the review jobs use. It turns your move into a clean planning ticket, checks where the dispatcher sent it, and reads the answer back. | a system daemon |
| The **planning session** | A sandboxed session the dispatcher starts from that ticket, in the repository's **planning entry**: no tracker tool, no shell, no way to write a file. | the dispatcher |
| The **executor** (`scripts/pipeline_plan_executor.py`) | The one party holding a tracker credential. It checks the proposal, runs the readiness gate on every child, and files the tree in the backlog. | the planner job |

The **dispatcher** is the program that turns a ticket handed to its agent user into a sandboxed session; "delegate to the agent" means hand a ticket to that user.

**The gesture: you put an idea in Plan it**, by moving it there or by writing it there. Two
gestures, two meanings, on a work team:

- Put a ticket in **Plan it** to plan it.
- Delegate a ticket to the agent to **build** it.

Never both on one ticket.

## 2. What happens, in order

1. You write an idea on the project's work team, in **Plan it** or moved there.
2. The planner job's next pass reads the ticket's history and checks that **you** made the
   move. When that history holds no state change at all, your creation of the ticket counts
   as the move, and the job checks that **you** created it. The tracker never records a
   change a ticket's creator makes in its first minutes, so a quick move leaves no trace
   (KIT-164). The job also checks the ticket is a fresh idea, not live work (section 4).
3. The job writes a **planning ticket on the same team**:
   - the first line is the repository's planning tag, `[repo=stage-a-planning-<repository>]`;
   - your idea is quoted inside `<untrusted-idea-data>` markers, with every routing
     directive and fence marker removed, including one spelt with an escape or a
     character reference;
   - its only label is the repository's **routing label**, of the same name;
   - it has no project.

   It creates the ticket and hands it to the agent in one call.
4. The dispatcher starts a **planning session** in the repository's planning entry.
5. **Seconds later, in the same pass,** the job reads the dispatcher's routing note on
   that session. It must name the planning entry, reached by the tag or the label. If it
   names anything else, or no note arrives, the job cancels the ticket, pages you, and
   stops all planning (section 3).
6. The session reads the real code and ends its final message with one fenced JSON block:
   the proposed epic and its children.
7. The job reads that message back, updates its checkout of the repository, and runs the
   **executor** with the planning ticket pinned.
8. The executor files the tree into the same team's backlog, or refuses it and says why.
   It files the epic as `provenance:agent`, every child as `provenance:epic`, and makes the
   epic every child's parent.
9. The job tells the idea what the result was and where it is, and closes the planning
   ticket (section 6 lists the results and how a session can end).
10. **You approve, then you release.** Move the epic to the state the project maps to
    `ready`. That approves the tree. It releases the children only where the approve step
    is installed and switched on (`docs/AUTONOMY.md`). That step is off by default, and then
    the move starts nothing. You release each child yourself, by delegating it to the
    agent, and a child only after the ones it depends on. The executor's summary says so
    too (KIT-216). **Never delegate the epic itself.** Its session would try to build the
    whole plan at once. And once the epic has a branch, the dispatcher cuts every child's
    worktree from it (0.2.69, `GitService.js:296-316`), so each child's pull request is
    stacked on the epic's. From 0.2.71 a child's finished session also wakes the epic's.
    **Never put the `orchestrator` or `graphite` label on the epic or a child.** From
    0.2.73 those prompts hand each child to the agent themselves, and a `graphite` child
    is cut from the branch of the ticket it waits on: a stack again.

## 3. Why routing is checked, and what happens when it goes wrong

The dispatcher picks which setup runs a ticket in this order: a route it remembers, the
`[repo=…]` tag, the routing label, the project, then the **team**. The tag, label and
project are each read by a separate network call, and a failed call counts as "none". The
team is read from the notification itself, so it always answers. On a work team, the team
answers "the coding setup".

So the planning entry claims **no team**. A planning ticket reaches it by its tag, or by its
label if the tag is missed. If both are missed, it runs in the coding setup. That is rare,
but it is the case this design is built to catch:

- **The routing check.** The dispatcher posts a note saying which setup answered, before
  the session's model starts. The job reads the earliest such note seconds after filing.
  The wait is 120 seconds by default, and longer while a repository setup script runs.
- **The circuit breaker.** A wrong note, or none, trips it:
  1. the evidence is written first, because cancelling deletes the session's working copy;
  2. the ticket is cancelled;
  3. you are paged: the planning ticket gets an `agent:needs-human` mark the notifier reads
     (the notifier scans only the teams in its `TEAM_KEYS`, so each planned repository's
     team must be listed there);
  4. the idea gets a note;
  5. **all planning stops**, on every team.

  The stop survives restarts. Only signing the probe again, with new probe tickets, then
  running the installer, clears it. The job never reuses a planning ticket.
- **Nothing new starts while a route is unknown.** A routing check a pass could not finish
  — a read that failed, a pass that ran out of time — is finished first on the next pass,
  before any new planning ticket is filed.

**The dispatcher's version is pinned.** An upgrade could change how tags and labels route.
The job reads the dispatcher's version from its own `/version` route on every pass, and
starts no run on a version the probe was not signed on. After an upgrade: probe, sign, run
the installer.

**What is left:** a planning ticket that misses both routes runs in the coding setup for the
seconds before the cancel lands. It works from the clean copy, its working copy is deleted,
and it cannot merge.

## 4. What each part refuses

**The planner job**
- Starts nothing unless the owner made the move, read from the ticket's own history. When
  that history holds no state change at all, the owner must have created the ticket instead.
  Any recorded state change, by anyone, means only a recorded move into Plan it counts.
- Says each refusal once per ticket, until the ticket's history changes. A new move gets a
  new answer.
- Never counts your creation of a ticket it already told has no move. That includes ideas
  an older version refused; each gets the note once more. Move it out of Plan it and back
  in.
- Starts nothing on **live work**, with one note and no run: a ticket that is delegated, has
  an agent session, carries a `provenance:*` or `agent:*` label, has a parent or children, or
  has a pull request attached.
- Starts at most a few runs a pass, and at most `MAX_RUNS_PER_DAY` a day across every team.
- Runs one planning run per idea at a time. A new move into Plan it while a run for that idea
  is still going waits, with one note on the idea. It starts when that run ends, if it is still
  the newest move.
- Runs one pass at a time. A pass holds a lock, `~/.stage-a/state/poller.lock`. A pass that
  finds it held says so in the log, writes nothing, not even its heartbeat, and exits 0.
- Never plans over a plan that stands. Before any move starts a run, new or held while an
  earlier run finished, the job looks for an epic filed from one of the idea's own planning
  runs: it carries that run's `Plan id:` line, was filed with your key, and is not canceled.
  If one stands, the idea gets one note naming it, as the bridge's `replan` says it, and no
  run starts. Approve that plan, or cancel it, before you plan the idea again.
- Starts nothing while planning is stopped, while the dispatcher's version is not the
  probe's, or while any team's Plan it state, routing label or cancel state is wrong. A Plan
  it typed **started** is the worst case: the dispatcher moves every session's ticket into the
  lowest started state, so every coding ticket could land there.
- Creates no planning ticket whose description carries a second routing directive.
- Writes exactly three kinds of change: a planning ticket, a note, and closing or cancelling
  a planning ticket it created. It never edits an idea, never labels an existing ticket, and
  never touches a pull request.
- Files nothing from a session it did not start. A session on an idea in Plan it that began
  after the move, or a second session on a planning ticket, gets one warning.

**The planning session**
- Holds `Read`, `Grep`, `Glob` and helper sessions, and nothing else.
- Cannot file, move, label or comment on any ticket. Its proposal is a message.

**The executor**
- Creates in the backlog only, and moves no ticket.
- Refuses a pinned ticket on any team but the one the repository's `delivery.json` names.
- Refuses the whole tree if one child fails the readiness gate.
- Refuses a proposal naming any ticket but the pinned one, and text shaped like a credential.
- Files a proposal once: the epic carries a receipt.
- Lists children whose titles look like recent tickets, leaving out the idea and every
  planning ticket.

**Other jobs on the same team** skip planning tickets. The finding poller skips one by its
label or its opening tag. The review poller's discovery and the criteria snapshot also need
the dispatcher's own routing note to confirm it: a coding session can write the label or
the tag on its own ticket, and must not be able to take itself out of review.

**The installer** (`scripts/pipeline_stage_a_setup.py`, run by `scripts/pipeline_install.py`)
- Refuses to run its mutating commands in an agent environment, and reads no key there.
- Refuses two repositories whose `delivery.json` name one team.
- Asks before every change a person should see, and only when a person is at the terminal.
  Writing the dispatcher's settings and running the drill need the word `yes`, typed.
- Never creates a team or an account. Never merges, approves or labels.
- Moves only tickets it filed itself: the probe tickets and the drill's idea.

## 5. Installing it

**One command, typed in a terminal, as yourself:**

```bash
python3 scripts/pipeline_install.py
```

Run it from the kit checkout you install from. It does everything a machine can do, and
stops only for what a person must do. Run it again after any stop: it carries on from there.

**Where to type it.** The macOS Terminal app works, and so does the Claude desktop app's
Terminal tab: neither sets the markers the installers refuse on (measured 2026-09-24). The
`!` prefix inside a Claude Code session does **not** work. That shell belongs to the
session, and the installers refuse it on purpose.

**Why it refuses a Claude session.** The idea gate stores a Linear key and places a
background job that files tickets. A session that could run the installer could install
its own supervisor. So the installer refuses whenever an agent marker is set, and there is
no override.

What it does, in order:

1. **The kit checkout.** On its default branch, with no uncommitted changes. It
   fast-forwards it, and starts itself again when that moved the code.
2. **The skills in your home folder.** If they differ from the kit's, it asks, then
   replaces them.
3. **Stage E**, when the machine has it (`stage-e.conf`). It runs the review installer's
   read-only check first. Only when something is outstanding does it warn you, ask, and run
   the review installer. That can restart the dispatcher. The check counts the role
   account's clone as behind when a merge changed a file that any job on it runs: the
   review jobs, the notifier or the Slack bridge.
4. **The idea gate:**
   - **Settings.** On a first run it writes `stage-a.conf` for you. It takes the
     dispatcher's account, settings file and service, the agent's name, the kit's URL and
     the reviewed repositories from `stage-e.conf`. Your user id comes from your key. It
     asks only for the rest.
   - **The board.** On each work team it creates **Plan it** (a to-do state) and the
     repository's **routing label**. It reports which started state a running ticket
     lands in.
   - **The plan kind.** If a planned repository's `delivery.json` does not switch plans on,
     it shows the change, opens the pull request **as you**, and waits for you to merge
     it. If the repository's checks ask for the guard-change label, you add it.

     Before it opens anything, it checks the change against the repository's **own** copy
     of the schema, `schemas/delivery.schema.json` on its default branch. That copy is what
     the repository's CI checks against. A copy from an older kit may not know
     `findingTicket`. Then the step stops before any pull request, with card `CA-SCHEMA`,
     and names the fields the copy does not know. Bring that copy and the section 1 rows
     of the repository's `docs/PIPELINE-CONTRACT.md` up to the kit's (`/sync-kit` there
     does it), merge, and run the one command again. A copy it cannot read stops the step
     as could not look, not as clean.
   - **The key.** By default the planner reuses the key the review jobs already store, so
     nothing is asked. With `LINEAR_KEY_FILE=.stage-a/env`, it asks once for a key of the
     planner's own.
   - **The job.** It clones the kit under the role account, writes the job's settings,
     and installs the job **disabled**, so not even a reboot starts it before you say so.
   - **The dispatcher's settings.** It composes one planning entry per repository and
     shows the change as a diff. It warns you which sessions a restart would cut off. After
     you type `yes`, it backs up the file, writes the entries, and restarts the dispatcher.
     Then it waits up to a minute for the dispatcher to **answer**, not just to be running.
     If it never answers, the step stops there and prints `sudo launchctl print
     system/<label>` to look first.
   - **The probe.** After a yes, it files two test tickets per repository, as you, and hands
     them to the agent. The planning setup's own instructions say what to do with a ticket
     of exactly the probe's title; the tickets' text only points there (KIT-215). It reads
     where the dispatcher sent them. It reads the tool list the session was given from the
     dispatcher's own session log, and a helper's from the session's answer. If the log
     cannot be read, the answer is used for both lists, and the probe says so. A tool
     outside the keep-set in any list that came back stops the probe, without asking,
     whatever the log says of a helper (KIT-261). Then it asks you **one** question: is the
     team's agent guidance empty, or silent about planning and building? The API cannot
     read it. It records the sign-off and closes the tickets.

     Two more questions come only when a list was not measured. If a list came back but
     could not be read, it asks about that list alone. If no helper list came back, it
     says the helper was not measured and asks whether to sign anyway. If a helper list
     came back but the log shows no helper started, that list is **not counted**, even
     as prose. It says so and asks the same question. The record says which lists were
     read, and how. A session list nothing measured is never signed.

     The same session's log also says whether a helper started and whether the test file
     was read. An older session's log never answers for it. If that log holds no call
     records at all, neither is measured. The probe says so, and the record gives that
     reason.
   - **The job, started.** After a yes, it enables the job in launchd, starts it, and reads
     its first heartbeat. Enabled, it starts again after a reboot.
   - **The routing drill**, if you want it now (or later: `pipeline_stage_a_setup.py
     drill`). It makes the dispatcher refuse one planning ticket and checks planning stops.
     Then it puts the setting back and probes again, which is what lets planning start.
     It files its idea straight into Plan it, as you, so your creation counts as the move.
     It wakes the job only once the idea reads back as your creation, the way the job
     reads it.

     A drill has three endings. **Passed:** planning stopped. **Failed:** the job filed a
     planning ticket and planning did not stop. **Could not test** (exit 4): the job filed
     no planning ticket, so nothing was tested. Then the drill prints the job's own answer,
     if it gave one. Fix what it names and run the drill again.

What is still yours, at most:

| When | What you do |
|---|---|
| Once | Type your Mac password. |
| First run, if the key is not stored yet | Paste your Linear key at a hidden prompt. |
| Each planned repository | Merge the pull request it opened (and add the guard-change label if the checks ask). |
| A repository whose schema copy is older than the kit's | Bring its schema and contract up to the kit's first (card `CA-SCHEMA`). |
| Before each change | Type `y`, or `yes` where it asks for the word. |
| The probe | Answer the agent-guidance question, and any question about a tool list it could not measure. |
| At the end | Plan two test ideas and one real idea, then sign `CA-LANE` (the card lists them). |

Each checkpoint card (`card <id>`) is what you see only when you answered no, or ran the
installer where it cannot ask. `status` replays what earlier passes recorded; `verify`
re-measures everything and changes nothing.

Before you start, each repository in `PLANNED_REPOS` needs a committed `delivery.json` whose
`linear.teamKey` names its work team, and the dispatcher needs a coding entry for it.

**Which model plans and builds.** The dispatcher runs a session on its one default model,
which is also the chat bot's, unless the ticket names another. A model set on a
repository's entry is never read.

- Set `TICKET_MODEL` in `stage-a.conf`, for example `TICKET_MODEL=opus`, then run the one
  command.
- Each planning ticket then carries a `[model=opus]` line under its first line. Each piece
  of an approved plan opens with one. Each runs on that model, whatever the default is.
- Set it **before** you move the default model, or planning and building move with it.
- Reviews carry their own model label, `MODEL_LABEL` in `stage-e.conf`.
- A piece filed before you set it has no line, and runs on the default. Moving the default
  with `scripts/pipeline_model_switch.py` tags those first (card CK-C8). A ticket you file
  and hand to the agent yourself is not tagged, and runs on the default unless it carries a
  model label.

**A failed plan, run again by itself.** Off until you say yes. The one command asks you once,
when `stage-a.conf` has no `AUTO_REPLAN` line, and writes your answer there. To change it
later, edit that line and run the one command again.

- **What is run again:** a plan the readiness gate or the schema refused, a cut-off
  proposal, a run with no plan or no answer, a usage or rate limit (two hours later), and
  a time-out.
- **How often:** up to `AUTO_REPLAN_TRIES` more runs for one move, 2 by default, so three in
  all. At most `AUTO_REPLAN_PER_DAY` re-runs start in a day, 20 by default. That is a
  runaway's limit, counted apart from `MAX_RUNS_PER_DAY`. A re-run shares the per-pass limit
  of three with new moves.
- **What the re-run is told.** Its planning ticket carries an `<untrusted-earlier-attempts>`
  block before the idea: every earlier run of the move, newest first, and how each ended.
  A refusal brings the executor's own reasons, cleaned. A time-out, an empty answer or a
  limit gets the job's fixed words. The first line says which attempt this is and asks for
  a different approach.
- **What is never run again:**
  - questions it asked you;
  - a run someone stopped;
  - an error that a wait cannot fix;
  - a partial filing;
  - a refusal for naming another ticket, for a request kind a plan may not carry, or for
    text shaped like a credential;
  - the plan kind switched off, a wrong team, or a config or key error;
  - an idea whose title or text you changed since its first run.
- **A time-out** waits for its session to read ended, for up to an hour. If the session
  then left a real final message, that message is filed, with no new session. If it is
  still going after the hour, nothing runs again, and you are paged.
- **A filing that failed three times**, with nothing filed, runs the executor once more an
  hour later, over the same message. No new session starts.
- **Before a re-run starts,** the job checks again. The idea must still be in Plan it, not be
  live work, and have no plan that stands, and today's re-runs must be under the limit. A
  failed check drops the re-run and pages you once. A newer move of yours, a `replan` from
  the bridge included, drops it with a note on the idea, and that move is planned instead.
  A re-run owed for more than a day is dropped, and you are paged.
- **What you hear.** A re-run starting adds a note to the idea, and pages no one. With
  re-runs on, the executor's own page for a refusal or an empty run is held back, and the
  job pages once, when the move ends without a plan. That page names every run and how
  each ended. It sits on the last run's planning ticket.
- **Its record** is `~/.stage-a/state/replans.json`. A re-run is written there as owed
  before its ticket exists. If that file cannot be read, nothing is run again, the run that
  would have been is paged, and each pass exits 1 until a person looks.

**If the dispatcher's own setup tool rebuilds its settings file,** the planning entries are
dropped with everything else it did not write. Run the one command again: it finds them
missing and puts them back.

**If you stop it with Ctrl-C,** the installer finishes what it was undoing first: the drill
puts the planning setup back, and an interrupted restart prints the command that starts the
dispatcher. The next run checks that the dispatcher is running before it does anything else.

**If it says the dispatcher is not answering,** it has already asked launchd. When launchd
holds the dispatcher, it is still starting: wait a minute and run the one command again.
Do not `bootstrap` it then; a loaded job answers `Bootstrap failed: 5: Input/output error`.
Only when launchd does not hold it does the installer print the `bootstrap` command.

**Stage E's own cards do not hold up the idea gate.** When the review installer's check finds
nothing left but a card for a person, and its jobs already run the current code, the one
command says so and carries on. Anything else from Stage E stops it: a check that failed or
could not measure, an update that stopped part way (it may have left the review jobs
unloaded), or a Ctrl-C. Run the one command again once that is cleared.

**To switch the job off for good,** disable it as well as stopping it, or the next boot
starts it again: `sudo launchctl disable system/<label>`, then
`sudo launchctl bootout system/<label>`.

## 6. Reading the job

Everything the job owns sits under the role account's home:

| File | What it tells you |
|---|---|
| `~/.stage-a/state/heartbeat.json` | That a pass **finished**, and what it decided: `ok`, `error`, `usage` or `timeout`. Stale means not running; fresh and not `ok` means it ran and could not do it. A pass that is blocked from planning reports `error`, and its log says why. |
| `~/.stage-a/state/stop.json` | Present only while planning is stopped. It holds the evidence: the ticket, the session, the routing note or its absence, and the times. |
| `~/.stage-a/state/seen.json` | Which moves have been handled, each planning run's routing verdict, runs per day, and each pass's `[wall, awake]` mark. A cache: the tracker is the authority. |
| `~/.stage-a/state/replans.json` | Only with re-runs on: each move's runs, how each ended, and any re-run owed, started or dropped, with re-runs per day. Unreadable stops every re-run, and the pass exits 1. |
| `~/.stage-a/state/poller.lock` | The pass's lock. One pass at a time holds it. |
| `~/.stage-a/poller.log` | What each pass did, one line per decision. |
| `~/.stage-a/poller.json` | The job's config, written by the installer. It holds the probe the job is pinned to, and no credential. |
| `~/.stage-a/kit` | The code the job runs. The installer fast-forwards it. |
| `~/.stage-a/planned/<owner>__<name>` | One checkout per planned repository, for the readiness gate. |

Exit codes: `0` the pass ran, `1` something will be retried, was given up on, or planning is
blocked, and it says so, `2` config or credential, nothing was touched, `4` the pass hit its
wall clock.

### What a planning run came to (KIT-373)

The executor writes what it did to a file the job names (`--verdict-file`), on every exit
Python sees; a run killed outright writes none, which reads as `could-not-tell`. The job
records a finished run's result in `seen.json` as `result`, and an errored attempt's as
`last_result`. Its note on the idea says which:

| `result` | The note on the idea |
|---|---|
| `filed` | a plan was filed, awaiting your approval |
| `questions` | the session asked you questions, and how many |
| `no-plan` | no plan came back, and how the session ended |
| `refused` | the plan was refused; the reasons are on the planning ticket |
| `errored` | the run is tried again, up to three times in all, then given up on. Kept as `last_result` until then |
| `could-not-tell` | the executor's file was missing, unreadable, or did not fit its exit code. The note names the three possible results instead. It is never read as filed |

The file also gives a `kind`: which rule refused a plan (`readiness-gate`, `cut-off`,
`schema`, `retarget`, `request-kind` for a request a plan may not carry, `credential-shape`,
`config`), what failed (`tracker`, `partial`,
`unconfirmed` when a create the tracker never confirmed may have landed, `config`, `input`,
`gate`, `unexpected`, or `unknown` when no exit recorded one), or what
else happened (`created`, `already-filed`, `asked`, `no-proposal`, `no-message`, `off`,
`dry-run`).

**How the session ended** is recorded beside it, as `ending`:

- `response`: it posted a final message.
- `error`, with a kind read from the error's text: `limit` (a usage or rate limit, such as
  *Request rejected (429)* or *You've reached your limit*), `billing`, `max-turns`, or
  `unknown`. Text the job cannot name is `unknown`, never a limit: a ticket number such as
  PROD-429, *Context limit reached*, or a full disk's quota is not one.
- `nothing`: it ended without a message.
- `stopped`: someone stopped it by closing or deleting its planning ticket. The ticket was
  found completed, canceled or deleted while the job still had it open, or its final
  message is the dispatcher's own notice (*Session stopped — PROD-101 was marked as Done
  or Canceled.*) and the ticket reads closed. The notice's words on an open ticket are an
  answer with no plan in it: a session can write them itself. When the stop is found by
  the state alone, the session's own ending is kept beside it, as `session`. A stopped run
  is never given to the executor, and its ticket is never moved. The idea is told once,
  in one of three ways:
  - nothing was filed, when no attempt filed anything or may have;
  - some or all of the plan was filed, when any attempt filed part of it, whatever came
    after: look for its project and epic before planning again;
  - whether anything was filed is not known, when an attempt's result could not be read,
    when one was cut off before then, when its last create was never confirmed
    (`unconfirmed`), or when the job rebuilt its lost record of the run: look for a project
    or an epic made for it, since such a run may leave no list. Planning the idea
    again starts a new planning ticket with its own plan id, so it finds nothing the
    stopped run made: delete what that run left first.

  Each note then says: if you have moved the idea back into Plan it since, that move
  starts its next run; if not, move it out and back in.
  - **A stop pages no one.** The note carries no mark, so the notifier does not page it.
    With re-runs off, the executor's mark is the only page a run gets. With them on, the
    job pages a move that ends without a plan, once, and a stop is never one of those.
  - **Only a closed or deleted ticket counts.** The session panel's Stop button leaves
    the ticket open. The run then reads as a session that ended without a plan.
  - **A ticket closed before its routing note** is a stop, not a misroute, only when it
    has no session at all. With a session, nothing confirms where it runs, so planning
    stops, and the page says the ticket was closed. The dispatcher posts its stop notice
    only for a session it has set up, just before the routing note, so a close while it
    makes the worktree or runs the setup hook gets no notice; and after a routing post
    that was lost, a model may still start. If you closed it yourself, look at the
    session, then sign the probe again to resume planning. A read that fails stops
    planning too.
  - **A close in the first seconds may be undone.** The dispatcher moves a ticket it starts
    work on to in progress, whatever its state, so the run may go ahead and file.
  - **A ticket whose state cannot be read** holds its result back for two passes, and on
    the third it is filed as it was before. A stop notice whose ticket's state stays
    unreadable is held the same way, and on the third pass it is a stop: never filed,
    never moved.
- **A run times out after four hours awake.** Each pass records how long the machine has
  been awake, so a run is never timed out for hours the Mac slept through. On a machine
  whose awake clock cannot be read, the wall clock counts, as before. The time-out note
  says which it used.
- **Every note that closes a run says what its attempts left.** A give-up or a refusal
  after an attempt that filed part of the plan says so and points to the list; after an
  attempt whose result is not known it says that. Either way it asks you to look for the
  epic on the work team before you plan the idea again.

## 7. What is not proven

- **Nothing here has run live.** The routing note's exact rendered shape and its delay after
  delegation are read from the dispatcher's source (0.2.69). The probe measures both. So
  are the stop notice's stored shape, and the state read with `trashed` that finds a
  stopped run (KIT-373): neither has been seen on a live planning ticket.
- **A misrouted ticket runs for seconds.** The check catches it; nothing prevents it.
- **Team agent guidance reaches planners.** It cannot be read through the API. The probe
  asks a person to check it, and nothing re-checks it later.
- **The tracker's limit on a session's final message is unmeasured.** A twenty-child plan
  measures about 50,000 characters. An overlong one arrives as "no plan", never a partial one.
- **A helper session inherits the fence, per the runner's source.** It is not measured
  live yet. The probe's steps now sit in the planning setup's own instructions, keyed on
  the probe's exact title, so a session that follows them starts a helper (KIT-215). That
  this happens on a live session is not yet seen. If the log shows no helper started, a
  helper list in the answer is not counted, and the record says so. A fenced tool named
  in it still stops the probe (KIT-261).
- **The duplicate check compares titles, not intent.**
- **Nothing watches the job's heartbeat automatically.** The heartbeat monitor does not know
  this job yet (KIT-192); `verify` is what reads it.
- **The planner's key is the review jobs' variable, by name.** Rename `LINEAR_KEY_ENV` in the
  review installer's settings and the planner goes on reading the old name: change it in
  `stage-a.conf` too.
- **Your creation counts as the move only because of what the tracker leaves out.** On
  2026-10-10, four tickets made through the API showed it: the creator's state change in
  the first minutes left no history entry, and another actor's change was recorded at
  once. Tickets made in the tracker's own app are not measured (KIT-164).
- **The installer's own new steps have not run live.** Writing the dispatcher's settings and
  restarting it reuse the review installer's code, which has. The pull request, the automatic
  probe, starting the job and the drill are proven only against synthetic fixtures.
- **Installed disabled, enabled at your yes.** That the override launchd records outlives a
  reboot is from its documentation, not yet seen on this kit's job.
- **The session log's tool list is read from the SDK's start-up message.** That it lists the
  tools after the fence removed them is read from the runner's source, not yet seen live. A
  list that still held a fenced tool would fail the probe loudly, never pass it.
- **The log's call records are read in the shape the runner's source writes** (0.2.69
  and 0.2.73). Whether a helper started, and whether the test file was read, come from
  them. That shape is not yet seen live. A log with no call records reads as not
  measured, with that reason, never as "no helper, no read" (KIT-261).
- **A planning session could read files in the role account's home**, if its file-reading
  tool reaches there. The probe leaves a harmless test file beside the planner's own files,
  asks the session to read it, and reports what came back. If the file's contents come
  back, that is "read", whatever the log shows: they cannot come back unread. "Not
  returned" counts only when the dispatcher's log shows the read was attempted. Without
  that, or when the log holds no call records or cannot be read, the result is "no
  answer". Tracked as KIT-162.
