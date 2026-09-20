# Stage A — the idea gate, for the operator

The sibling of `docs/STAGE-E-OPERATOR.md`, pointed at planning instead of review. It says
what each part is, what starts it, what it refuses, and what only a person can do. Every
value here is synthetic; a deployment's real names live in its own operator notes, never
in this repository.

**Off unless installed.** Nothing in this document runs in a repository that has no
`delivery.json` and no installed job. The kit ships the mechanism; a person installs it.

---

## 1. What the gate is

You have an idea. You want it decomposed into an epic and child tickets a session could
pick up, without decomposing it yourself, and without a machine filing tickets nobody
approved.

The gate is four parts and one gesture:

| Part | What it is | Who runs it |
|---|---|---|
| The **Planning team** | A tracker team that holds ideas, and the planning runs made from them. No code lands here. | you file ideas |
| The **planner job** (`scripts/pipeline_plan_poller.py`) | A scheduled program under the executor's own account. It turns your gesture into a delegated planning ticket, and reads the answer back. | a system daemon |
| The **planning session** | A sandboxed session the dispatcher starts from that ticket, under a tool fence that leaves it no tracker tool, no shell and no way to write a file. | the dispatcher |
| The **executor** (`scripts/pipeline_plan_executor.py`) | The one party holding a tracker credential. It validates the proposal, runs the readiness gate on every child, and files the tree in the backlog. | the planner job |

**The gesture: you move an idea into the Plan it state.** That is all. You never delegate
an idea, and you never hand one to the agent.

## 2. What happens, in order

1. You write an idea on the Planning team and move it to **Plan it**.
2. The planner job's next pass reads the ticket's history, and checks that **you** made
   that move. A move by anyone else, or by an integration, is refused with a note.
3. The job writes a **planning ticket**: your idea's text quoted inside
   `<untrusted-idea-data>` markers with every routing directive removed, no labels, no
   project, and one routing tag of the job's own naming the Planning entry. It creates
   and delegates that ticket in a single call.
4. The dispatcher starts a **planning session** on it. The session reads the real code,
   runs the planning passes, and ends its final message with one fenced JSON block: the
   proposed epic and its children.
5. The job reads that final message back through the tracker, updates its checkout of the
   planned repository to the default branch, and runs the **executor** with the planning
   ticket pinned.
6. The executor validates the proposal whole, runs the readiness gate on every child, and
   either files the tree — epic and children in the backlog, `provenance:agent` on the
   epic, `provenance:epic` on each child, the epic's own id as every child's parent — or
   refuses it and says why on the planning ticket.
7. The job tells the idea where the result is and closes the planning ticket.
8. **You approve.** Moving the epic to the state the project maps to `ready` is the one
   gesture that releases the tree. Nothing else does.

## 3. Why the idea is never the ticket that is delegated

The dispatcher decides which repository entry runs a ticket by reading its **description
first**: a `[repo=…]` tag matches an entry by URL tail, name or id, and starts a session
in every entry it matches. Labels come next, then projects, and the team last. A label can
also select a prompt type whose tool list **replaces** the entry's fence, and a runner tag
or label can select a runner that loads no guard at all.

An idea's text is exactly the kind of text those routes read. So the gate never shows it
to the router. The planning ticket is written by the job, and the job refuses to file one
whose description carries any directive but its own.

What that leaves: someone with tracker access adding a runner label to a planning ticket
and delegating it again. Only users on the Planning entry's allowed list can start a
session, and that list is the owner.

## 4. What each part refuses

**The planner job**
- Starts nothing unless the owner made the move, read from the ticket's own history.
- Creates no planning ticket whose description carries a second routing directive.
- Writes exactly three kinds of change: a planning ticket, a note, and closing a planning
  ticket it created. It never edits an idea, never labels anything, never touches a pull
  request.
- Files nothing from a session it did not start. A ticket handed to the agent directly
  gets one warning and no filing.
- Stops the pass rather than guess when its own record cannot be read.

**The planning session**
- Holds `Read`, `Grep`, `Glob` and helper sessions, and nothing else. No tracker server, no
  shell, no `Write`, no `Edit`.
- Cannot file, move, label or comment on any ticket. Its proposal is a message.

**The executor**
- Creates in the backlog only, and issues no state-move call at all.
- Refuses the whole tree if one child fails the readiness gate — a partial epic reads as
  decomposed and is not.
- Refuses a proposal naming any ticket but the one pinned by the job.
- Refuses text shaped like a credential, and quotes none of it.
- Files a proposal once: the epic carries a receipt, and a second run on the same proposal
  files nothing.

**The installer** (`scripts/pipeline_stage_a_setup.py`)
- Refuses to run its mutating commands in an agent environment.
- Never writes the dispatcher's config: it composes the Planning entry and prints it.
- Never loads the job. Installing and starting are different acts, and the second is the
  moment the gate can first write to the board.

## 5. Installing it

One command, run repeatedly by a person in a terminal, until it stops asking:

```bash
python3 scripts/pipeline_stage_a_setup.py run
```

It reads `stage-a.conf` (copy `stage-a.conf.example`), reports every bad value in one
pass, and stops at the first row that needs you. `status` replays what earlier passes
recorded; `verify` re-measures everything and changes nothing; `card <id>` prints one
checkpoint.

What you supply, and why it is yours:

| Checkpoint | What you do |
|---|---|
| `CA-DELIVERY` | Turn the plan kind on in the planned repository's committed `delivery.json`, by a pull request you merge. That file is the switch that lets a proposal become tickets. |
| `CA-ENTRY` | Paste the composed Planning entry into the dispatcher's own config and restart it. A program that wrote its own fence would be choosing its own supervision. Check before you paste: every tracker server named twice (`mcp__<server>` and `mcp__<server>__*`), `Bash`, `Write` and `Edit` all denied, no `allowedTools` key, a repository path, a base branch, a workspace directory, a workspace id, and you as the only allowed user. |
| `CA-PROBE` | Watch a live planning session, and a helper it starts, list every tool they hold. A fence is a list in a file until someone sees a session obey it. |
| `CA-HANDOVER` | Run the planning procedure by hand once, before anything is automatic. |
| `CA-EXECUTOR` | Load the job. The next run measures it: launchd for whether it is loaded, its own heartbeat for whether it works. |
| `CA-LANE` | Move one idea carrying a routing tag, and one carrying a prompt-type label, into Plan it, and read the planning tickets the job writes. Then plan one real idea end to end. |

## 6. Reading the job

Everything the job owns sits under the executor account's home:

| File | What it tells you |
|---|---|
| `~/.stage-a/state/heartbeat.json` | That a pass **finished**, and what it decided: `ok`, `error`, `usage` or `timeout`. A stale timestamp means not running; a fresh one with a non-`ok` result means it ran and could not do it. Those have opposite remedies. |
| `~/.stage-a/state/seen.json` | Which triggers have been handled, and the state of each planning run. A cache: the tracker is the authority, and a lost file costs a lookup, never a duplicate. |
| `~/.stage-a/poller.log` | What each pass did, one line per decision. |
| `~/.stage-a/poller.json` | The job's config. Names the variable each credential lives in; holds none. |
| `~/.stage-a/kit` | The code the job runs. The installer fast-forwards it. |
| `~/.stage-a/planned` | The checkout of the planned repository the readiness gate reads. |

Exit codes: `0` the pass ran, `1` something will be retried or was given up on and says
so, `2` config or credential — nothing was touched, `4` the pass hit its wall clock.

## 7. What is not proven

- **The tracker's limit on a session's final message is unmeasured.** A twenty-child plan
  with realistic bodies measures about 50,000 characters end to end, and the caps allow a
  much larger one. If the tracker refuses an overlong body, the dispatcher posts nothing,
  and the gate sees a session that finished with no plan — a visible note, never a partial
  tree. Record the size of the first real plan.
- **Whether a helper session inherits the fence** is settled by reading the runner's
  source, and confirmed only by the live probe a person runs (`CA-PROBE`).
- **The duplicate check is gone from the planning passes** and its replacement is named on
  its own ticket. Until then, a plan is not checked against existing tickets, and says so.
- **Nothing watches the job's heartbeat automatically.** The installer's `enable` step
  reads it when you run the installer; between runs, nothing does.
