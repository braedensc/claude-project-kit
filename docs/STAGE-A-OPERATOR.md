# Stage A — the idea gate, for the operator

The sibling of `docs/STAGE-E-OPERATOR.md`, pointed at planning instead of review. It says
what each part is, what starts it, what it refuses, and what only a person can do. Every
value here is synthetic; a deployment's real names live in its own operator notes, never
in this repository.

**Off unless installed.** Nothing in this document runs in a repository that has no
`delivery.json` and no installed job. The kit ships the mechanism; a person installs it.

---

## 1. What the gate is

You have an idea. You want it turned into an epic and child tickets a session could pick
up, without writing them yourself, and without a machine filing tickets nobody approved.

There is no separate planning team. **Planning happens on each project's own work team**,
beside its real work. The gate is four parts and one gesture:

| Part | What it is | Who runs it |
|---|---|---|
| **Plan it** | A to-do state on each work team. Moving an idea into it is the only thing that starts planning. | you |
| The **planner job** (`scripts/pipeline_plan_poller.py`) | A scheduled program under the executor's own account. It turns your move into a clean planning ticket, checks where the dispatcher sent it, and reads the answer back. | a system daemon |
| The **planning session** | A sandboxed session the dispatcher starts from that ticket, in the repository's **planning entry**: no tracker tool, no shell, no way to write a file. | the dispatcher |
| The **executor** (`scripts/pipeline_plan_executor.py`) | The one party holding a tracker credential. It checks the proposal, runs the readiness gate on every child, and files the tree in the backlog. | the planner job |

**The gesture: you move an idea to Plan it.** Two gestures, two meanings, on a work team:

- Move a ticket to **Plan it** to plan it.
- Delegate a ticket to the agent to **build** it.

Never both on one ticket.

## 2. What happens, in order

1. You write an idea on the project's work team and move it to **Plan it**.
2. The planner job's next pass reads the ticket's history and checks that **you** made the
   move. It also checks the ticket is a fresh idea, not live work (section 4).
3. The job writes a **planning ticket on the same team**:
   - the first line is the repository's planning tag, `[repo=stage-a-planning-<repository>]`;
   - your idea is quoted inside `<untrusted-idea-data>` markers, with every routing
     directive removed;
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
9. The job tells the idea where the result is, and closes the planning ticket.
10. **You approve.** Moving the epic to the state the project maps to `ready` is the one
    gesture that releases the tree. Then you delegate each child yourself.

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
  3. you are paged: the planning ticket gets an `agent:needs-human` mark the notifier reads;
  4. the idea gets a note;
  5. **all planning stops**, on every team.

  The stop survives restarts. Only signing the probe again, then running the installer,
  clears it. The job never reuses a planning ticket.

**The dispatcher's version is pinned.** An upgrade could change how tags and labels route.
The job reads the dispatcher's version from its own `/version` route on every pass, and
starts no run on a version the probe was not signed on. After an upgrade: probe, sign, run
the installer.

**What is left:** a planning ticket that misses both routes runs in the coding setup for the
seconds before the cancel lands. It works from the clean copy, its working copy is deleted,
and it cannot merge.

## 4. What each part refuses

**The planner job**
- Starts nothing unless the owner made the move, read from the ticket's own history.
- Starts nothing on **live work**, with one note and no run: a ticket that is delegated, has
  an agent session, carries a `provenance:*` or `agent:*` label, has a parent or children, or
  has a pull request attached.
- Starts at most a few runs a pass, and at most `MAX_RUNS_PER_DAY` a day across every team.
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

**Other jobs on the same team** — the review poller's discovery, the criteria snapshot and
the finding poller — skip planning tickets, by their label or their opening tag.

**The installer** (`scripts/pipeline_stage_a_setup.py`)
- Refuses to run its mutating commands in an agent environment.
- Refuses two repositories whose `delivery.json` name one team.
- Never creates a team, and never writes the dispatcher's config: it composes the planning
  entries and prints them.
- Never loads the job.

## 5. Installing it

One command, run repeatedly by a person in a terminal, until it stops asking:

```bash
python3 scripts/pipeline_stage_a_setup.py run
```

It reads `stage-a.conf` (copy `stage-a.conf.example`), reports every bad value in one pass,
and stops at the first row that needs you. `status` replays what earlier passes recorded;
`verify` re-measures everything, including the dispatcher's version, and changes nothing;
`card <id>` prints one checkpoint.

Before you start, each repository in `PLANNED_REPOS` needs a committed `delivery.json` whose
`linear.teamKey` names its work team, and the dispatcher needs a coding entry for it.

What the installer builds on each work team: a **Plan it** state (to-do type) and the
repository's **routing label**. It reports which started state a running ticket lands in.

What you supply, and why it is yours:

| Checkpoint | What you do |
|---|---|
| `CA-DELIVERY` | Turn the plan kind on in each planned repository's committed `delivery.json`, by a pull request you merge. |
| `CA-ENTRY` | Paste the composed planning entries into the dispatcher's config and restart it. Check each: every tracker server named twice (`mcp__<server>` and `mcp__<server>__*`), `Bash`, `Write` and `Edit` denied, one routing label equal to its own name, no `teamKeys`, no `allowedTools`, and you as the only allowed user. |
| `CA-PROBE` | For each repository, hand the agent one probe ticket with the tag and the label, and one with the label only. Read both sessions' tool lists and a helper's. Read the work team's agent guidance: the API cannot. Sign with `--ticket` for each probe ticket; the installer reads the routing notes itself, and records the dispatcher's version. |
| `CA-HANDOVER` | Run the planning procedure by hand once, before anything is automatic. |
| `CA-EXECUTOR` | Load the job. The next run measures it. |
| `CA-LANE` | Plan one idea carrying a routing tag and one carrying a prompt-type label. Run the routing drill: make the dispatcher refuse a planning session, and see planning stop until you sign the probe again. Then plan one real idea end to end. |

## 6. Reading the job

Everything the job owns sits under the executor account's home:

| File | What it tells you |
|---|---|
| `~/.stage-a/state/heartbeat.json` | That a pass **finished**, and what it decided: `ok`, `error`, `usage` or `timeout`. Stale means not running; fresh and not `ok` means it ran and could not do it. A pass that is blocked from planning reports `error`, and its log says why. |
| `~/.stage-a/state/stop.json` | Present only while planning is stopped. It holds the evidence: the ticket, the session, the routing note or its absence, and the times. |
| `~/.stage-a/state/seen.json` | Which moves have been handled, each planning run's routing verdict, and runs per day. A cache: the tracker is the authority. |
| `~/.stage-a/poller.log` | What each pass did, one line per decision. |
| `~/.stage-a/poller.json` | The job's config, written by the installer. It holds the probe the job is pinned to, and no credential. |
| `~/.stage-a/kit` | The code the job runs. The installer fast-forwards it. |
| `~/.stage-a/planned/<owner>__<name>` | One checkout per planned repository, for the readiness gate. |

Exit codes: `0` the pass ran, `1` something will be retried, was given up on, or planning is
blocked, and it says so, `2` config or credential, nothing was touched, `4` the pass hit its
wall clock.

## 7. What is not proven

- **Nothing here has run live.** The routing note's exact rendered shape and its delay after
  delegation are read from the dispatcher's source (0.2.69). The probe measures both.
- **A misrouted ticket runs for seconds.** The check catches it; nothing prevents it.
- **Team agent guidance reaches planners.** It cannot be read through the API. The probe
  asks a person to check it, and nothing re-checks it later.
- **The tracker's limit on a session's final message is unmeasured.** A twenty-child plan
  measures about 50,000 characters. An overlong one arrives as "no plan", never a partial one.
- **A helper session inherits the fence, per the runner's source.** The probe is where a
  person sees the helper's own tool list.
- **The duplicate check compares titles, not intent.**
- **Nothing watches the job's heartbeat automatically** unless the heartbeat monitor is set up.
