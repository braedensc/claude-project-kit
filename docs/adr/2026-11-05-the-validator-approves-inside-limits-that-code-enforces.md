# The validator: approves in the owner's place, inside limits that code enforces

**Date:** 2026-11-05 · **Status:** Accepted (the owner, 6 Nov 2026: V1 to V10, recorded on KIT-510; they replace this note's trust ladder) · **Context:** KIT-510 and KIT-499, design note KIT-525, decisions KIT-530. Nothing here is built. It builds on the watcher's design note.

## Decision

The owner won't approve everything himself. Today his yes is the one authority an AI can't fake, so whatever stands in for it has to be just as hard to fool. An AI alone is not enough: it reads the same text that could carry an injection.

1. **Three layers, and all three must agree.** Hard limits in code. Evidence. An independent read-only agent. Anything missing, unsure or unreadable holds.
2. **It approves three things:** a plan (in place of `approve <plan>`), a piece starting, and a merge. A merge is GitHub's own auto-merge, switched on by a job. **No agent ever approves a PR review or runs a merge.**
3. **What it may approve is plain on/off settings.** One for plans, one for pieces, one for merges, with their size and file limits. There is no trust ladder, and nothing gains or loses trust by itself. The future UI shows the settings.
4. **A short list stays human.** Changes to the safety system, credentials, billing and accounts, production deploy settings and deleting data. Guard changes stay human always, fixed in code. The other four are on/off settings.
5. **Merges go one at a time, each tested on the latest main,** and the validator does it itself. It relies on no paid feature. A merge queue is optional, only where it is free.
6. **The agent chooses what a "no" does:** send the change back to the dev session, or escalate to the owner. If it believes a dev is compromised, that is an urgent escalation.
7. **It runs under the user's own login, with no new OS account, and posts its verdict from its own GitHub app.** This is what ports most simply to the dream app. It costs some independence, named below.
8. **Every approval is on the record,** with its reasons, in a file only the validator writes. The stand-up, the sprint recap and the watcher read it, and the watcher checks every merge against it.

## The three layers

**Layer 1: hard limits, in code.** Plain code with no model, recomputed at decision time from things a session can't write. Most of it exists already as `scripts/check_auto_approve.py` and `scripts/check_auto_merge.py`, built for the GitHub-hosted pipeline and not wired to today's local one (KIT-499). They check:

- the switches: that kind of approval is on;
- provenance: a plan approves only if the owner filed the idea; an agent-filed ticket only in its first generation (the autonomous-workers ADR, decision 2);
- size, and the files it may touch: nothing on `autonomy.riskPaths` or the human-only list below;
- the budget and window: the day's planned load, and no runaway stop firing;
- the send-back budget: Stage E's bounce budget, counted by code and never by the agent. Past it, the change goes to the owner;
- **the watcher is alive and has set no hold:** a stale watcher heartbeat or a hold means no approval (the watcher ADR, decision D8);
- one verdict per head: a push revokes everything and the item qualifies again from scratch.

**Layer 2: evidence.** What the pipeline already produces, read from the platform and not from anything a PR author can edit:

- every required check is terminal and green on the head;
- Stage E's review covers the whole change and shows nothing at or above the bar. A partial review, or one with withheld files, is not clean;
- for a plan's last piece, the acceptance check (KIT-302) passed;
- the merge state is not conflicting or unknown.

**Layer 3: one independent validator agent.** It is read-only. It is separate from the worker it judges: a different session, no shared thread, and the worker has no way to message it. Its input is a brief built by code from the ticket's criteria as they were at delegation (KIT-131), the evidence record and a fenced diff. The PR body and comments are not in it. It judges one thing code can't: does this diff do what the criteria ask, and nothing else?

When it is not satisfied, it chooses one of two:

- **Send it back to the dev session,** with its reason. The dev fixes and pushes, which is a new head and a new verdict. Code counts the send-backs against the bounce budget. This is the default.
- **Escalate to the owner,** only for a real problem the dev can't solve.
- If it **believes a dev is compromised** (a change that does what the criteria never asked, a session working outside its ticket, text that reads like an injection), it escalates **urgently**, and the watcher is told.

Its words are checked by code and kept as one plain line. It can only turn a pass into a hold. Layers 1 and 2 are not its to relax. A successful injection of the agent can therefore make it say yes to a change that already passed the tests, the review, the size and the file limits. That is the remaining risk.

## What it approves, and what no agent does

| Approves | How | Not |
|---|---|---|
| A plan | A tool call on the acting layer, recorded under the validator's own actor, never the owner's. It replaces `approve <plan>`. A switch, off by default. | A plan from an idea an agent filed, ever. Only the owner's own ideas qualify, as in layer 1. |
| A piece starting | The same, for a ready piece of an approved plan. A switch. | A piece on the human-only list. |
| A merge | A job asks GitHub to enable its own auto-merge. GitHub merges when branch protection is satisfied. A switch. | A merge by any session or job. No agent holds the capability, as today. |

No agent ever approves a PR review, applies `hooks-change` or any protected label, moves an epic out of intake by itself, or edits a rule it is judged by. This widens the old rule that an approval traces back to a thing a person did: it now traces back to a switch the owner turned on, and to a code-and-agent verdict for the one item.

## The human-only list

Each is a class. All but the first can be turned on by the owner as a plain setting.

| Class | Detected by | Can the owner switch it on? |
|---|---|---|
| **Changes to the safety system:** hooks, guards, CI and workflows, branch protection and rulesets, settings, the watcher, the validator's own limits | The grader floor in `scripts/check_grader_paths.py`, plus `riskPaths`. The `hooks-change` label is the owner's, as today. | **No.** A guard the system can approve for itself is theater. It is fixed in code, not in config. |
| Credentials: env files, keys, tokens, secrets stores | Paths, plus the secret scan | Yes |
| Billing, accounts, the plan or subscription | Paths and settings files named in `riskPaths` | Yes |
| Production deploy settings | The deploy workflow and its config | Yes |
| Deleting data: migrations that drop, bulk deletes, branch or release removal | Paths. **Not provable by path:** see below. | Yes, with the migrations path kept human |

## No trust ladder

An earlier version of this note had five rungs, with the system dropping a rung after a revert, a bug or a flag. The owner decided against it: no gaining or losing trust, just plain on/off settings for what the validator may approve. So the validator never changes its own authority, up or down.

What that gives up is the one automatic descent. A bad record no longer reduces authority by itself. What stands in its place is the watcher, which watches the approval rate and checks every merge against a record, and pauses on an anomaly; and the owner, who sees every approval and every hold in the stand-up and can turn a setting off at any time.

## Merging one at a time, on the latest main

On 5 Nov, #354 and #355 passed alone, merged 13 seconds apart, and turned main red together. A fast human missed it, and auto-merge would hit it. The rule: **each PR is tested on top of the latest main, and only one is in flight.**

The validator does this itself, and relies on no paid feature. It enables auto-merge on at most one PR. When that PR merges, the next gets main merged in, its checks run again on that head, and it qualifies again before auto-merge is switched on. The ruleset requires branches to be up to date. The union check can't carry this alone: it skips itself past 8 open PRs (KIT-524). A GitHub merge queue does the same job, and may be used where it is free. It also needs the required workflow to run on the queue's event, a workflow change only a person can land.

**The ruleset changes.** Tier 3's ruleset asks for at least one approving review. With no agent allowed to approve, and the owner stepped out, that can't be met. The validator's verdict becomes a required check posted by its own GitHub app, and the ruleset names that app as its source so nothing else can post it. Documented by GitHub, not tried here.

## Its audit

The validator writes a fields-only ledger, readable by others and written only by the validator: for each decision the ticket or PR, the head, each layer's verdict as a closed word, the settings in force, and the agent's reason as one plain line marked as the agent's words. A send-back, an escalation and a hold are rows too.

- **The stand-up** reads it, with no model between: one line in "done" for each approval ("approved and merged by the validator: the ticket, and why"), and each escalation as a blocker with its reason and the reply that clears it.
- **The sprint recap** (KIT-496) lists every approval of the week, the send-backs and the escalations.
- **The watcher** reads it against GitHub. It watches the approval rate. **It also checks every merge against a record:** a merge with no approval row is a pause and a page, and a row with no merge or hold is a page. That is how the watcher notices a merge the validator didn't make.

## Decisions

All ten are the owner's, decided on 6 Nov 2026 and recorded on KIT-510. Where he chose differently from my recommendation, it says so.

| | Decision | My recommendation | The owner's answer |
|---|---|---|---|
| **V1** | Its own account with a root-owned install, or the role account? | Its own account, and not the watcher's | **Different.** Whatever ports most simply to the dream app: it runs under the user's own login, with no new OS account, and its own GitHub app. |
| **V2** | May it approve plans, or only pieces and merges? | Pieces and merges first | It may approve plans too, if that switch is on. |
| **V3** | Drop "one approving review" and require the validator's verdict as a check from its own app? | Yes | Yes. |
| **V4** | One at a time: the validator serialises, or a merge queue? | The validator now, a queue if the plan allows | No reliance on a paid feature. The validator merges one at a time itself. A queue is optional where it is free. |
| **V5** | The human-only classes, with guard changes fixed in code? | Yes | The list stands. Guard changes stay fixed in code. The other four are easy on/off settings. |
| **V6** | The ladder's rungs, and where it starts? | Rung 1 for a week | **Different.** No trust ladder. Plain on/off settings for what it may approve, which the future UI exposes. |
| **V7** | One validator agent, or two? | One up to rung 2, two from rung 3 | One validator agent. |
| **V8** | What a "no" does? | Hold for the owner as a blocker, no auto-retry | **Different.** The agent chooses: send it back to the dev session, or escalate to the owner. It escalates only for real issues the dev can't solve. If it truly believes a dev is compromised, that is an urgent escalation. |
| **V9** | What drops a rung? | A revert, a bug within 7 days, or a watcher flag | **Different.** No gaining or losing trust. |
| **V10** | Is the approval trail in the stand-up and the recap? | Yes | Yes (taken as yes: he hasn't objected). Each approval is one line, each hold a blocker. |

## What could not be proved

1. **The validator's independence under the user's own login (V1).** The watcher can run as its own account in a root-owned folder. The validator, as the user's own login, cannot be made as hard to edit: a local session running as the owner could in principle write its settings or its files. What holds the line instead is the grader floor, which includes its limits; the GitHub check it posts from its own app, whose key is outside every worktree; and the watcher, which checks every merge against the ledger. None of this is tried.
2. **That a required check can be limited to one app as its source.** GitHub documents it for rulesets. Not tried.
3. **That the agent's yes is independent.** It is the same family of model as the workers, so it can share their blind spots. That is why it can never be the only layer.
4. **That the agent can tell a compromised dev from a mistaken one.** A wrong escalation costs the owner's attention, and a wrong send-back costs a bounce. The rates are unknown until it runs.
5. **That a path list catches "deleting data".** A migration that drops a table is just a file. The migrations path stays human, and the agent is a second look, not a guarantee.
6. **The false-hold rate.** Unknown until it runs on real work. A validator that holds everything trains the owner to switch it off.
7. **That the criteria snapshot (KIT-131) exists for every kind of ticket.** A ticket without one holds.
8. **What auto-merge records as the merging actor.** The watcher's cross-check needs it. Not tried.
9. **Throughput.** One full check run per merge is the price of one at a time. Unmeasured.

## Why

**The existing tiers kept.** `check_auto_approve.py` and `check_auto_merge.py` already hold the rule that the agent never merges and that inputs come from places a session can't write. They become layer 1 and part of layer 2. What they lack is the local wiring, the agent layer and the ledger.

**An agent alone rejected.** It reads the diff and the ticket, which are the text an injection rides in. It sits third and can only say no or send it back.

**An agent-approved review rejected.** An approving review from any agent identity makes the approval an agent's act. The check-and-ruleset route keeps the capability off every agent.

**A trust ladder dropped, at the owner's decision.** It would have had the system lower its own authority after a bad record. Plain settings are simpler to explain, to show in a UI and to port, and keep one rule: authority changes only when a person changes a setting.

**Guard changes behind a switch rejected.** The system approving changes to its own guards means one successful injection approves its own way past them.

## Verified

Nothing is built, so nothing is verified beyond what was read. I read the two existing gates and the autonomy doc's tier 2 and tier 3, the watcher's design note, the KIT-499 comment on #354 and #355, the autonomous-workers ADR, and the owner's decisions on KIT-510. The pieces to build: the local wiring of layers 1 and 2 with the ledger and the settings; the validator agent and its brief, with the send-back and escalation paths; the ruleset change and the serialiser; and the watcher's approval monitors (KIT-518).
