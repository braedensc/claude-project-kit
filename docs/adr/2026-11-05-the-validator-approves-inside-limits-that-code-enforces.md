# The validator: approves in the owner's place, inside limits that code enforces

**Date:** 2026-11-05 · **Status:** Proposed · **Context:** KIT-510 and KIT-499, design note KIT-525. Nothing here is built. It builds on the watcher's design note, which comes first.

## Decision

The owner won't approve everything himself. Today his yes is the one authority an AI can't fake, so whatever stands in for it has to be just as hard to fool. An AI alone is not enough: it reads the same text that could carry an injection.

1. **Three layers, and all three must agree.** Hard limits in code. Evidence. An independent read-only agent. Anything missing, unsure or unreadable holds. A hold goes to the owner as a blocker, never to a retry.
2. **It approves three things:** a plan (in place of `approve <plan>`), a piece starting, and a merge. A merge is GitHub's own auto-merge, switched on by a job. **No agent ever approves a PR review or runs a merge.**
3. **A short list stays human.** Changes to the safety system, credentials, billing and accounts, production deploy settings and deleting data. Guard changes stay human always, and no switch turns that off.
4. **Authority climbs a ladder, one rung at a time.** The owner climbs it, with a script. The system may only drop a rung.
5. **Merges go one at a time, each tested on the latest main.** #354 and #355 were each green alone and turned main red together.
6. **Every approval is on the record,** with its reasons, in a file only the validator writes. The stand-up, the sprint recap and the watcher read it, and the watcher checks every merge against it.

## The three layers

**Layer 1: hard limits, in code.** Plain code with no model, recomputed at decision time from things a session can't write. Most of it exists already as `scripts/check_auto_approve.py` and `scripts/check_auto_merge.py`, built for the GitHub-hosted pipeline and not wired to today's local one (KIT-499). They check:

- provenance: a plan approves only if the owner filed the idea; an agent-filed ticket only in its first generation (the autonomous-workers ADR, decision 2);
- size, and the files it may touch: nothing on `autonomy.riskPaths` or the human-only list below;
- the budget and window: the day's planned load, and no runaway stop firing;
- zero bounces, as today: a PR that needed fixing goes to a person;
- **the watcher is alive and has set no hold:** a stale watcher heartbeat or a hold means no approval, which is the watcher ADR's decision D8;
- one verdict per head: a push revokes everything and the item qualifies again from scratch.

**Layer 2: evidence.** What the pipeline already produces, read from the platform and not from anything a PR author can edit:

- every required check is terminal and green on the head;
- Stage E's review covers the whole change and shows nothing at or above the bar. A partial review, or one with withheld files, is not clean (as of 5 Nov, a withheld file is a gap, not a pass);
- for a plan's last piece, the acceptance check (KIT-302) passed;
- the merge state is not conflicting or unknown.

**Layer 3: an independent validator agent.** It is read-only and says only yes or no, with reasons. It is separate from the worker it judges: a different session, a different account, no shared thread, and the worker has no way to message it. Its input is a brief built by code from the ticket's criteria as they were at delegation (KIT-131), the evidence record and a fenced diff. The PR body and comments are not in it. It judges one thing code can't: does this diff do what the criteria ask, and nothing else? Its words are checked by code and kept as one plain line. A "no" is final for that head, and the worker can't ask again.

It can only turn a pass into a hold. Layers 1 and 2 are not its to relax. A successful injection of the agent can therefore make it say yes to a change that already passed the tests, the review, the size and the file limits. That is the remaining risk, and the ladder below is how it is kept small.

## What it approves, and what no agent does

| Approves | How | Not |
|---|---|---|
| A plan | A tool call on the acting layer, recorded under the validator's own actor, never the owner's. It replaces `approve <plan>`. | A plan from an idea an agent filed, at any rung. Only the owner's own ideas qualify, as in layer 1. |
| A piece starting | The same, for a ready piece of an approved plan. | A piece on the human-only list. |
| A merge | A job asks GitHub to enable its own auto-merge. GitHub merges when branch protection is satisfied. | A merge by any session or job. No agent holds the capability, as today. |

No agent ever approves a PR review, applies `hooks-change` or any protected label, moves an epic out of intake by itself, or edits a rule it is judged by. This widens the old rule that an approval traces back to a thing a person did: it now traces back to a class the owner switched on, and to a code-and-agent verdict for the one item.

## The human-only list

Each is a class. A class can be switched on by the owner, class by class, **except the first**.

| Class | Detected by | Can the owner switch it on? |
|---|---|---|
| **Changes to the safety system:** hooks, guards, CI and workflows, branch protection and rulesets, settings, the watcher, the validator's own limits | The grader floor in `scripts/check_grader_paths.py`, plus `riskPaths`. The `hooks-change` label is the owner's, as today. | **No.** A guard the system can approve for itself is theater. It is fixed in code, not in config. |
| Credentials: env files, keys, tokens, secrets stores | Paths, plus the secret scan | Yes |
| Billing, accounts, the plan or subscription | Paths and settings files named in `riskPaths` | Yes |
| Production deploy settings | The deploy workflow and its config | Yes |
| Deleting data: migrations that drop, bulk deletes, branch or release removal | Paths. **Not provable by path:** see below. | Yes, with the migrations path kept human |

## The trust ladder

Each rung is a config switch the owner flips with one tested script. A rung includes the rungs below it.

| Rung | The validator may approve |
|---|---|
| 0 | Nothing. The owner approves everything (today). |
| 1 | Documents, tests and small fixes of at most a few lines, in the owner's own approved plans, touching no risk path |
| 2 | Any piece of an owner-approved plan within the size limit, touching no risk path |
| 3 | Agent-filed first-generation follow-ups within their caps |
| 4 | A plan itself, for an idea the owner filed |

**The system may only go down.** A revert of an auto-merged change, a bug filed against one within a week, a watcher flag on the validator, or a run of its own "no"s drops one rung and pages the owner. Climbing is the owner's alone, and the stand-up says which rung the record would justify.

## Merging one at a time, on the latest main

On 5 Nov, #354 and #355 passed alone, merged 13 seconds apart, and turned main red together. A fast human missed it, and auto-merge would hit it. The rule: **each PR is tested on top of the latest main, and only one is in flight.**

1. **A GitHub merge queue** does exactly this: each entry is tested on main plus the entries ahead. It needs the required workflow to run on the queue's event, which is a workflow change only a person can land. I could not check that this repository's plan offers one.
2. **The validator serialises.** It enables auto-merge on at most one PR. When that PR merges, the next gets main merged in, its checks run again on that head, and it qualifies again before auto-merge is switched on. The ruleset requires branches to be up to date. The union check can't carry this alone: it skips itself past 8 open PRs (KIT-524).

Recommended: option 2 now, option 1 when available. It costs one full check run per merge, which is the point.

**The ruleset changes.** Today's tier 3 asks for at least one approving review. With no agent allowed to approve, and the owner stepped out, that can't be met. The validator's verdict becomes a required check posted by its own app, and the ruleset names that app as its source so nothing else can post it. Documented by GitHub, not tried here.

## Its audit

The validator writes a fields-only ledger, readable by others and writable only by its own account: for each decision the ticket or PR, the head, each layer's verdict as a closed word, the rung it acted under, and the agent's reason as one plain line marked as the agent's words. A hold is a row too.

- **The stand-up** reads it, with no model between: one line in "done" for each approval ("approved and merged by the validator: the ticket, and why"), and each hold as a blocker with its reason and the reply that clears it.
- **The sprint recap** (KIT-496) lists every approval of the week, the rung, and the holds.
- **The watcher** reads it first-hand against GitHub. It watches the approval rate. **It also checks every merge against a record:** a merge with no approval row is a pause and a page, and a row with no merge or hold is a page. That is how the watcher notices a merge the validator didn't make.

## Decisions for the owner

Each has my recommendation. Nothing is built.

| | Decision | Recommendation |
|---|---|---|
| **V1** | The validator runs under its own account, with a root-owned install like the watcher's, or as the role account? | Its own account, and not the watcher's: the watcher must be able to pause it. |
| **V2** | May it approve plans, or only pieces and merges? | Pieces and merges first. Plans are the last rung. |
| **V3** | Drop "one approving review" from the ruleset and require the validator's verdict as a check posted by its own app? | Yes. Nothing else can meet it. |
| **V4** | One at a time: the validator serialises, or a merge queue? | The validator now, a queue if the plan allows it. |
| **V5** | The human-only classes, with guard changes fixed in code and not switchable? | Yes. The other four classes switchable one by one. |
| **V6** | The ladder's rungs, and where it starts? | Rung 1, on the first project, for a week. |
| **V7** | One validator agent, or two that must both say yes? | One up to rung 2. Two from rung 3, from different prompts. |
| **V8** | What a "no" does? | Hold for the owner as a blocker with the reason. No auto-retry. |
| **V9** | What drops a rung, and for how long? | A revert, a bug filed within 7 days, or a watcher flag. Back up only by the owner. |
| **V10** | Is the approval trail shown in the stand-up and the recap? | Yes: every approval is one line, every hold is a blocker. |

## What could not be proved

1. **That a merge queue exists for this repository.** I believe a personal-account repository may not have one. Not checked.
2. **That a required check can be limited to one app as its source.** GitHub documents it for rulesets. Not tried.
3. **That the agent's yes is independent.** It is the same family of model as the workers, so it can share their blind spots. That is why it can never be the only layer.
4. **That a path list catches "deleting data".** A migration that drops a table is just a file. The migrations path stays human, and the agent is a second look, not a guarantee.
5. **The false-hold rate.** Unknown until it runs on real work. A validator that holds everything trains the owner to approve it all.
6. **That the criteria snapshot (KIT-131) exists for every kind of ticket.** A ticket without one holds.
7. **What auto-merge records as the merging actor.** The watcher's cross-check needs it. Not tried.
8. **Throughput.** One full check run per merge is the price of one at a time. Unmeasured.

## Why

**The existing tiers kept.** `check_auto_approve.py` and `check_auto_merge.py` already hold the rule that the agent never merges and that inputs come from places a session can't write. They become layer 1 and part of layer 2. What they lack is the local wiring, the agent layer, the ledger and the ladder.

**An agent alone rejected.** It reads the diff and the ticket, which are the text an injection rides in. It sits third and can only say no.

**An agent-approved review rejected.** An approving review from any agent identity makes the approval an agent's act. The check-and-ruleset route keeps the capability off every agent.

**A system that climbs its own ladder rejected.** Descent is safe to automate, because it removes authority. Ascent adds it, and a successful injection could aim for it.

**Guard changes behind a switch rejected.** The system approving changes to its own guards means one successful injection approves its own way past them.

## Verified

Nothing is built, so nothing is verified beyond what was read. I read the two existing gates and the autonomy doc's tier 2 and tier 3, the watcher's design note, the KIT-499 comment on #354 and #355, and the autonomous-workers ADR. The pieces to build, once the owner answers: the local wiring of layer 1 and 2 with the ledger; the validator agent and its brief; the ruleset change and the serialiser; the ladder script; and the watcher's approval monitors (KIT-518).
