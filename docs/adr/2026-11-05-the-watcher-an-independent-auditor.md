# The watcher: an independent auditor that can stop the system and never act

**Date:** 2026-11-05 · **Status:** Proposed · **Context:** KIT-509 (master roadmap 3d), design note KIT-513. Nothing here is built. Nothing past the first piece should be built before the owner answers the decisions below.

## Decision

The owner wants to be a casual observer of the system working. Once he steps out of the loop, the watcher stands where his eyes were. It must exist, and be on, before the validator (KIT-510) approves anything.

1. **Monitors are plain code.** They read signals the system already writes, judge them against limits of their own, and record what they saw. No model sits between a signal and a stop.
2. **An agent only explains.** A later piece reads what the monitors flagged, with read tools only, and says in plain words what happened. It reads text an attacker could have written, so it is never in the path of a stop. It can add to a page. It cannot lower a flag, clear a flag or clear a pause.
3. **It is independent of what it watches.** Its own account, its own credentials, its own limits, and its code and rules in a place no worker can write. Nothing a worker can send reaches it.
4. **Its one power is a hold.** It can pause new starts and approvals. It cannot revert, close, relabel, merge or fix. Only the owner clears a hold.
5. **Each finding has one of three outcomes:** record, page, or pause and page. The table under "What pages urgently" says which.
6. **It is built in six pieces, in this order:** (1) the monitors, KIT-514; (2) its own account and install, KIT-517; (3) the hold, KIT-515; (4) the page, KIT-519; (5) the judge, KIT-516; (6) the monitors that wait on a producer, KIT-518. The table's last column uses these numbers.

## What it watches, and where each signal lives today

A *first-hand* signal comes from a platform the watcher reads with its own key: GitHub or the tracker. A *second-hand* signal is a file a worker-side job wrote. Whoever controls that job could falsify the file, so a second-hand reading can only under-report. A finding says which kind it rests on, and a stale file makes the monitor blind.

| Signal | Does it exist today? | Where it lives | Kind | Piece |
|---|---|---|---|---|
| Spend and per-piece cost spikes | Yes | The usage file (§17): dollars per day and per piece, runs, and runs that signed in with a key. The dispatcher's session logs sit under it. | Second-hand | 1 |
| Runaway starts | Yes | Runs per day and per piece in the usage file. The day plan's `stops` (§18) are the queue's own view of the same. | Second-hand | 1 |
| A protected label applied by someone not trusted | Yes | GitHub's issue-events feed holds the actor of every `labeled` event. CI's *Hooks change guard* already judges who applied `hooks-change` on one pull request. | First-hand | 1 |
| A forged pipeline marker | Partly | Readers already ignore a marker from the wrong author (the conflict loop, the notifier, the plan executor). Nothing counts the attempts. GitHub's comment feed carries the author and the author's association. | First-hand | 1 (GitHub), 6 (tracker) |
| Prompt-injection shapes in tickets, comments and pull request text | Defended, not recorded | The untrusted-ticket-data fence and the hidden-character clean-up defend. Nothing records an attempt. The text is in GitHub's feeds and the tracker. | First-hand | 1 (GitHub), 6 (tracker) |
| Guard blocks, and repeated blocks | **No** | `pre-tool-use.py` blocks with exit 2 and records nothing (KIT-11). For a dispatched session the block's text may sit in the dispatcher's log. That is unverified. | None yet | 6 |
| Egress the allowlist refused | **Unconfirmed** | The proxy is documented to log a `BLOCKED <host>` line. No one has confirmed the marker in a real log (KIT-88). | None yet | 6 |
| A session working outside its ticket's files | Partly | Planned pieces carry a declared footprint in the queue. Tickets in general don't. A pull request's changed files are on GitHub. | First-hand, but half the comparison is missing | 6 |
| The validator's approval rate | **No** | The validator doesn't exist. It will have to write an approval record. | None yet | 6 |
| The watcher's own health | To build | Its heartbeat and status file, and a check of its own files against a manifest. | Own | 1, 2 |

A monitor with no producer ships as `off`, with its reason. The status file says how many monitors are on. A watcher that watches five of ten says so, so it never claims cover it lacks.

## Independence

The threat is a worker that read an injected instruction. A worker is a dispatched session as the role account (inside a sandbox) or a local session as the owner. It can write its worktree and post comments as the owner. It can do whatever its tools and the owner's tokens allow. The watcher must still see it, still stop it, and be unable to be told to stop looking.

| Property | How | What it does not cover |
|---|---|---|
| **Its own account.** No uid shared with the role account or the owner. | A dedicated service account runs the job. The Stage E ADR already accepts that the poller and the sessions share a uid, so the sandbox's home deny-read is the whole boundary there. The watcher can't rest on that. | The Mac is one failure domain. A sleeping Mac runs nothing, and the silence looks like health. Only an off-box check closes that (decision D7). |
| **Code and rules the workers can't edit.** | A root-owned install folder, copied from a reviewed commit, with a manifest of every file's hash. The watcher checks its own files against the manifest on each pass. A mismatch makes it blind and loud. Changing a rule takes `sudo` at a terminal, like the self-protected hooks. | It holds only as long as the owner's `sudo` does. A session running as the owner with `sudo` could defeat it. The pass also refuses a rules file that sits in a git working tree or is writable by group or other, and says so. |
| **Its own credentials, read-only.** | A GitHub token with read access, and a Linear API key with only the Read permission (documented by Linear, not tried here). Held only in the watcher account's own env file. | A read-only key still reads everything the key's owner can. |
| **No way in.** | It listens on nothing. It takes no message, socket, webhook or mention. It reads text from tickets and pull requests only to count shapes in it, and never quotes it. | A worker can still flood the feeds. Every read is bounded in pages and in text length. |
| **Its own limits.** | Spend, cost and start limits live in the watcher's rules, above the queue's own stops, so the queue's configuration can't raise them. They are a second, wider net. | A limit set too high is silent. The defaults are decision D5. |
| **Its own page path.** | A channel that doesn't go through the bridge or the notifier, which are worker-side (KIT-519, decision D2). | Until it exists, a page rides the health watch's route, and says so. |
| **First-hand where it can.** | Labels, comments and pull request text come straight from GitHub. Second-hand files are marked as such. | A worker with the owner's token can delete a comment between two passes. See "What could not be proved". |

The files the watcher needs from the worker side (the usage file and the day plan) must reach it as a read-only copy it can read and no worker can write. How that copy gets there is part of KIT-517.

## Pausing

Pausing is a later piece (KIT-515). The design:

- **A hold file** only the watcher's account can write. Fields only: which monitor, when, and a reason word from a closed list.
- **Every job that starts work reads it first:** the queue, the planner, the bounce driver, the catch-up job, direct asks and, later, the validator. A malformed hold holds. An absent hold is no hold. A reader that can't read the hold fails closed.
- **Only the owner clears it,** through one tested script that shows the reasons, asks one yes, clears it and reads it back. The watcher never clears one, and neither does the judge.
- **A deeper pause is optional (decision D3).** The dispatcher's pause switch (KIT-49) keeps answering the webhook while Cyrus is stopped. The watcher would run it through a narrow `sudo` rule that allows that one helper and nothing else. It could pause. It could never resume.
- **A blind watcher holds the least that keeps the owner safe.** My recommendation: it holds autonomous approvals and agent-filed starts once the owner has stepped out, because those are what no person is watching. It does not stop work the owner himself started. The validator would read the watcher's heartbeat as one of its hard limits.

## What pages urgently

A monitor's rule gives each finding a level. The first piece computes it and writes it; nothing acts on it until the hold exists.

| Level | What | Why |
|---|---|---|
| **Pause and page** | A protected label (`hooks-change`, `agent:*`, `blocked:*`, `provenance:*`) applied by someone not on the trusted list. A run that signed in with an API key. Day spend or a single piece's cost over the watcher's own hard limit. Starts or runs over its own cap. Its own files not matching the manifest. A monitor that gates approvals going blind. | Something got through, or money is moving, or the watcher can't trust itself. |
| **Page** | A forged marker. A prompt-injection shape in pull request or ticket text. Later: a guard blocking the same shape of action across sessions, an egress refusal of a host not seen before, a session outside its ticket's files that touched a guarded path. | An attempt, which readers already ignored. The owner should know, and a repeat can raise it to a pause. |
| **Record** | One guard block the session recovered from. One refusal of a known host. A shape match in text the owner wrote. | Most blocks are the system working. A single one is noise. Recurrence is the signal. |

A page says which monitor, how many, since when and what the owner can do. It carries no ticket, comment or pull request text.

**The judge never sits in this table.** A level comes from a rule. The judge's words are added to a page afterwards.

## How it fails (§13)

Each monitor ends in one of four states: `clear` (it says what it asked and what the answer was), `flagged`, `blind` (it could not establish the answer), or `off` (not configured, or no producer yet, with the reason). A silent green is not `clear`. A blind monitor is never `clear`, and a blind pass exits non-zero.

The status file is the watcher's heartbeat, in the health watch's words (`findings`, `firing`). Two other things read it: the health watch, hourly, and the validator once it exists. A watcher that stops writing reads as stale, and that is the dead-man signal.

## Decisions for the owner

Nothing below is built. Each has my recommendation.

| | Decision | Recommendation |
|---|---|---|
| **D1** | A dedicated account and a root-owned install, or the role account? | A dedicated account. The role account's sessions share its uid. |
| **D2** | The urgent channel: its own Slack app and DM, a GitHub mention, a phone push service, or two of them. It needs a new credential, owner-only. | Two routes: its own Slack app to his DM, and a GitHub issue in the private alert repository. Neither goes through a worker-side job. |
| **D3** | How far a pause reaches: new starts and approvals only, or also the dispatcher's pause switch through a narrow `sudo` rule. | Starts and approvals first. Add the switch once the hold has run clean for a week. |
| **D4** | Who clears a pause. | The owner, with a script. Never the watcher. Never the judge. Never a chat message. |
| **D5** | The watcher's own limits: day spend, per-piece cost, runs per day, runs per piece. | Set from a week of measured spend, then rounded up to roughly twice a normal day. The numbers are his. |
| **D6** | Can the watcher's rules change from chat? | No. A terminal with `sudo`, one script. |
| **D7** | An off-box dead-man's switch, such as a free heartbeat service that pages if the watcher stops. | Yes, once the owner is away for days at a time. It adds a third party and one credential. |
| **D8** | What a blind watcher holds (see "Pausing"). | Autonomous approvals and agent-filed starts, and nothing else. |

## What could not be proved

I could not read the dispatcher's home or logs from this account, and nothing was built, so each of these is open:

1. **The dispatcher's log format.** Whether a blocked tool call or a refused host appears there in a form a monitor can read. KIT-88's measurement was never run.
2. **That the machine's permissions allow it.** That the watcher's account can read a read-only copy of the role account's files, and that no worker can write that copy. This is macOS account and ACL behaviour, and it must be tried on the Mac.
3. **That a root-owned folder resists a session on the owner's account.** It holds only as long as `sudo` does.
4. **Where a worker with the owner's token can hide.** It can delete a comment between two passes. GitHub's label events stay in the timeline and name the actor. A comment's trace may not. The watcher's interval is the only mitigation, and I have not measured what GitHub keeps.
5. **That Linear's history shows every label change.** An earlier finding (the creator's quick early changes never enter the history) says it may not.
6. **That shape matching catches an injection.** It catches the lazy ones. A careful one evades it. The watcher is a tripwire, not a filter. The gates are the code in the validator's hard limits and the owner's own list of human-only changes.
7. **The false-positive rate.** Unknown until the monitors run on real traffic. A watcher that cries wolf trains the owner to ignore it. The first piece's job is to find out, with nothing acting on it.
8. **Linear's read-only key.** Linear's documentation describes an API key with only the Read permission. I have not tried one.
9. **A guard-block producer.** One needs a change to `pre-tool-use.py`, which is self-protected. A person applies it.

## Why

**The role account rejected.** The heartbeat monitor and the notifier run as the role account because they only report. The watcher's job is to catch what a worker did, so a worker's uid is the wrong place to put it. The Stage E ADR already records that those sessions and jobs share a uid.

**A model-first watcher rejected.** A model that reads the same text as the worker can be steered by the same injection. It also costs tokens on every pass, from a budget the workers draw on. Plain code runs on every pass, gives the same answer each time, and can't be talked into anything. The judge is added after, and can't remove a flag.

**Hooks as the watcher rejected.** Hooks run inside each session, see one session at a time, and share its machine. Their Bash guards are advisory (`npm run test:bypass` records what a respelling gets past). They are the first net, and the watcher is the second.

**A watcher that can fix rejected.** Reverting, closing or relabelling would give an injected worker a lever on the system's own safety. Stopping never needs the watcher to do what an attacker would want done.

**Rules deployed by merge rejected.** Code comes from reviewed pull requests. The rules and the install do not deploy themselves. A merge that changed a limit would be a path for the next injection. The owner's `sudo` is the gate.

**The queue's own stops kept.** They hold new starts on their own already (§18). The watcher's limits sit above them and read the same files. It adds the cases where the queue's number is wrong or its configuration was changed.

## Verified

Nothing is built, so nothing is verified beyond what was read. I read each source named in the table in this repository: the hooks, the usage file and day plan contracts, the status job, the heartbeat monitor and the health watch, the conflict loop's marker readers, and the Stage E ADR's account of the shared uid. The table says which signals exist today and which don't. The first piece (KIT-514) is where the claims turn into tests: a selftest that fails on every monitor that goes blind.
