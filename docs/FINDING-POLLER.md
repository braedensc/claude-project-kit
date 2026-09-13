# The finding poller — filing a session's findings on a local-daemon backend

KIT-96 gave a session a safe way to file a follow-up finding: it **requests** one and a
credential-holding executor creates it. This doc is the **local-daemon half** of that.

## Why there are two executors

A session must never create a ticket itself (it could fake an approval one API call later).
So it asks; something that holds the tracker key creates the ticket. *Where that key-holder
lives depends on the dispatch backend:*

| Backend | Session hands over a finding by… | The key-holder that files it |
|---|---|---|
| `github-actions` | a `ticket-create` request in its safe-outputs file (§8) | the `pipeline-safe-outputs.yml` CI job |
| `local-daemon` (Cyrus) | a `pipeline-finding/1` **comment** on its own ticket | **`scripts/pipeline_finding_poller.py`**, run by the role account |

On a local-daemon backend the session holds the tracker tools directly, so there is no
credential-free CI job to hide behind — the tool-fence (the PreToolUse guard) stops the
session creating a ticket, and this poller, running as the dispatcher's role account
**outside** any session, does the creating. Same seam as `pipeline_review_poller.py`.

## What a session writes

One comment on its own ticket, containing a fenced block:

````
```json
{"schema": "pipeline-finding/1", "title": "one line", "body": "…markdown…"}
```
````

Nothing else — no create, no label, no state. Posting the comment is a plain `save_comment`
a session already may do; creating the ticket is not, and the guard blocks it.

## What the poller forces (none of it the session's to choose)

For each finding, one `issueCreate` in the **same team** as the source ticket, with:

- **state = that team's Backlog** (intake). Never `ready`/started/`review`/done.
- **label = `provenance:agent`**, applied by the poller. A session can request no
  provenance — the poller strips every protected class (`provenance:*`, `agent:*`,
  `blocked:*`, `hooks-change`) from anything a session names.
- **subscriber = the configured owner**, so it reaches a person. **No assignee** — the
  ticket is never assigned to the session's identity.
- a **provenance line** in the body naming the source ticket.

It then records the source comment id in its seen-set and posts a `Filed as <ID>` receipt on
the source ticket. Its **only** Linear writes are that `issueCreate` and that
`commentCreate`. It trusts a finding **only** from the configured agent user, so an outside
commenter cannot inject one.

## Which comments a pass reads

The finding **is** the comment, so the window is keyed on the comment's own `createdAt`.
It is not keyed on the ticket's `updatedAt`, and that distinction is the whole of it: a
finding written on a ticket nobody touches afterwards falls outside a ticket-shaped window,
and a finding that goes unread is not delayed, it is **lost**.

Each team carries a **watermark** in the state dir — the instant up to which that team's
comments have been read *and resolved*. A pass asks for everything newer, less a small
overlap for clock skew (`watermark_overlap_minutes`, default 15). So:

- A daemon that was down for a week asks for **the week**, not for the last few hours.
- `lookback_hours` (default 72) is the **cold-start window only**: what a team with no
  watermark yet asks for on its very first pass.
- A watermark never moves past work the pass left behind. Anything over a flood cap,
  anything the wall clock cut off, and any team whose comment pages ran past the page bound
  holds that team's watermark where it was. **Capped is left, never dropped.**

If the watermark file is lost or damaged, the pass falls back to the cold-start window and
says so in the log. Nothing is filed twice — the seen-set still holds — but findings older
than that window are not re-asked for until the file is repaired or removed.

## Safety limits

- **Flood guard:** at most `max_per_source` findings per source ticket per run (default 3,
  §8's cap) and `max_per_run` across a pass. Extras are left for the next pass and named in
  the log — never silently dropped (§13).
- **Dedup: the seen-set is the authority**, and the only one. It is keyed by source comment
  id, written atomically, written *through* the moment each ticket is created, and a corrupt
  one refuses the run rather than re-filing. The `Filed as` receipt is a record for a
  person, **not** a backstop: it is written with the poller's own key, so its author is that
  key's user and not the agent user. A backstop that looked for a receipt "from the agent"
  matched nothing on a real deployment, so it was removed rather than left reading as a
  second line of defence that was not there.
  **The operational consequence:** the state dir *is* the dedup record. A seen-set that is
  deleted, or restored from a machine that filed different findings, can file the same
  finding twice. Back up the state dir with the rest of the role account; never "reset" it
  to clear a problem. A pass that finds no seen-set says so on its first log line.
- **Heartbeat:** every terminal path writes one — including a config or credential failure,
  which is the path that used to miss it. A stale heartbeat means *not running*; a fresh one
  with a non-`ok` result means *ran and could not do it* (§13). A `--dry-run` writes no
  heartbeat at all, so an operator's dry run cannot forge proof that the daemon is alive.
- **Off by default:** the poller runs only where an operator installs it and points it at a
  config. Absent it, a `pipeline-finding/1` comment is just a comment a person reads.

## Reading the state dir

| File | What it says |
| --- | --- |
| `seen.json` | every source comment already filed. **The dedup authority** — do not delete |
| `watermarks.json` | how far each team has been read and resolved |
| `heartbeat.json` | when the last pass ran, and what it decided |

`heartbeat.json` carries the same fields as the review poller's, so one monitoring rule
reads every Stage E daemon: `result` is `ok`, `error` or `usage`, beside `exit_code`,
`started_at`, `ended_at`, and this poller's `filed` / `skipped` counts.

Exit codes: **0** the pass ran (even if it filed nothing) · **1** the pass could not
complete · **2** bad arguments, an unreadable or invalid config, or a missing credential.

## Enabling it (operator)

The poller is a kit script; the operator runs it as the role account under a system
LaunchDaemon, exactly like the review poller. It needs:

1. A `provenance:agent` label in the workspace (workspace-scoped), in each team it files into.
2. A config file (`pipeline_finding_poller.py --example-config` prints the shape) naming the
   teams, the owner's user id, the agent user id, and the **name** of the env var holding the
   Linear key — never the key value.
3. The Linear key in the role account's own env file (`<home>/.stage-e/env`, mode 600), never
   in the dispatcher's env file.
4. A LaunchDaemon running `pipeline_finding_poller.py scan --config <path>` on an interval,
   `RunAtLoad`, no `KeepAlive` — one pass then exit.

Prove it with `--dry-run` (reads and reports, writes nothing) before enabling. The machine-
specific runbook lives outside this repo with the other operator runbooks.
