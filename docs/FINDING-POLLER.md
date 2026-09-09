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

It then posts a `Filed as <ID>` receipt on the source comment (a human-visible record and a
dedup backstop) and records the source comment id in its seen-set. Its **only** Linear
writes are that `issueCreate` and that `commentCreate`. It trusts a finding **only** from the
configured agent user, so an outside commenter cannot inject one.

## Safety limits

- **Flood guard:** at most `max_per_source` findings per source ticket per run (default 3,
  §8's cap) and `max_per_run` across a pass. Extras are left for the next pass and named in
  the log — never silently dropped (§13).
- **Dedup:** the seen-set (keyed by source comment id, atomic, corrupt ⇒ refuse) plus the
  receipt backstop, so a finding is filed once even across a lost seen-set.
- **Heartbeat:** every pass writes one, so "the poller is dead" and "it ran and filed
  nothing" are distinguishable without a log (§13).
- **Off by default:** the poller runs only where an operator installs it and points it at a
  config. Absent it, a `pipeline-finding/1` comment is just a comment a person reads.

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
