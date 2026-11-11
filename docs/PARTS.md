# Every part of the pipeline

*Written by `python3 scripts/pipeline_parts.py render` from `pipeline-parts.json`. Do not edit this page by hand: CI fails when it differs from the list.*

Each part once, in install order: what it does for you, its state in the kit, what it needs first, and where its guide is. A part comes after every part it needs.

## The parts

| Part | What it does | State | Needs first | Check | Apply | Guide |
|---|---|---|---|---|---|---|
| **Safety checks on every action a session takes** (`guards`) | Checks every tool call a Claude session makes and refuses the risky ones: edits on main, stacked branches, merges, self-approval, protected labels, secret files and edits to the guards. | live | nothing | none | none | [.claude/hooks/README.md](../.claude/hooks/README.md) |
| **A check on every commit, and a scan for secrets** (`commit-check`) | Refuses a commit on main, a commit to a forbidden path, or a commit that holds a secret. The same scan runs first in CI. | live | nothing | none | none | [docs/SECURITY.md](SECURITY.md) |
| **Checks on every pull request, and nothing merges red** (`kit-checks`) | Runs every test battery and the secret scan on each pull request, and fails a stacked one. Branch protection makes the required checks block a merge. | live | nothing | none | none | [docs/TESTING.md](TESTING.md) |
| **The project's settings file, which switches the pipeline on** (`project-settings`) | One committed file per project turns the pipeline on there; absent means off and quiet. A readiness gate refuses a ticket that lacks the required sections. | live | nothing | `python3 scripts/check_delivery_config.py` | none | [docs/PIPELINE-CONTRACT.md](PIPELINE-CONTRACT.md) |
| **Turning a fresh copy of the kit into a project** (`project-template`) | An interview fills the project's stack details, moves the workflow templates into place and opens a pull request. | live | nothing | none | none | [templates/README.md](../templates/README.md) |
| **Written procedures a session runs as slash commands** (`skills`) | Nine procedures such as taking a ticket to a pull request or shipping one. The sync keeps your home folder's copy level with the kit's. | live | nothing | `python3 scripts/sync_user_skills.py` | `python3 scripts/sync_user_skills.py --apply` | none yet |
| **The always-on machine that runs the sessions** (`host`) | A separate account on an always-on Mac, the program that turns a delegated ticket into a session, its front door, its public address and its port block. | designed only | nothing | none | none | none yet |
| **The mark that tells the safety checks a session is the pipeline's** (`lane-marker`) | A root-owned empty file that makes the pipeline-only checks apply to every session the host's runner starts. | live | Safety checks on every action a session takes, The always-on machine that runs the sessions | none | none | [.claude/hooks/README.md](../.claude/hooks/README.md) |
| **Coding sessions that take a ticket to a pull request** (`coding-sessions`) | The host's runner starts a Claude session on a delegated ticket in its own worktree, under the checks. It opens a pull request and never merges. | live | The always-on machine that runs the sessions, The mark that tells the safety checks a session is the pipeline's, The project's settings file, which switches the pipeline on, Written procedures a session runs as slash commands | none | none | [docs/SESSION-BRIEF.md](SESSION-BRIEF.md) |
| **The tracker's columns and labels** (`tracker-board`) | The columns and labels every job matches by name or id. The review and planning installers add their own. | live | The project's settings file, which switches the pipeline on | none | none | [.claude/skills/setup-board/SKILL.md](../.claude/skills/setup-board/SKILL.md) |
| **Code review of every pull request a session opens, and the fix rounds** (`review`) | Files one review per pull request a session opened, hands it to a read-only reviewer, and posts one comment. When review, CI or a conflict needs a fix, it asks the coding session, a capped number of times. | live | The always-on machine that runs the sessions, The mark that tells the safety checks a session is the pipeline's, Coding sessions that take a ticket to a pull request, The tracker's columns and labels | `python3 scripts/pipeline_stage_e_setup.py verify --conf stage-e.conf` | `python3 scripts/pipeline_stage_e_setup.py run --conf stage-e.conf` | [docs/STAGE-E-OPERATOR.md](STAGE-E-OPERATOR.md) |
| **Side findings a session leaves become backlog tickets** (`findings`) | Turns a structured finding a session leaves on its own ticket into a backlog ticket marked as agent-filed. It never moves a ticket to ready. | live | Code review of every pull request a session opens, and the fix rounds | `python3 scripts/pipeline_stage_e_setup.py verify --conf stage-e.conf` | `python3 scripts/pipeline_stage_e_setup.py run --conf stage-e.conf` | [docs/FINDING-POLLER.md](FINDING-POLLER.md) |
| **Re-sends a hand-off the session runner missed** (`catch-up`) | If the machine slept past the tracker's retries and a hand-off was lost, it prompts that ticket once more, in its own thread. | live | Code review of every pull request a session opens, and the fix rounds | `python3 scripts/pipeline_stage_e_setup.py verify --conf stage-e.conf` | `python3 scripts/pipeline_stage_e_setup.py run --conf stage-e.conf` | [docs/CATCH-UP.md](CATCH-UP.md) |
| **One file of where every plan stands** (`status`) | Every 15 minutes it writes one file of facts on every plan: pieces, pull requests, checks, reviews and merges. The tech lead and the stand-up read it. | live | Code review of every pull request a session opens, and the fix rounds | `python3 scripts/pipeline_stage_e_setup.py verify --conf stage-e.conf` | `python3 scripts/pipeline_stage_e_setup.py run --conf stage-e.conf` | none yet |
| **Says when one of the pipeline's jobs stops** (`job-health`) | Reads each job's heartbeat and comments on one chosen ticket when health changes; paging turns that into a message. | live | Code review of every pull request a session opens, and the fix rounds, One file of where every plan stands | `python3 scripts/pipeline_stage_e_setup.py verify --conf stage-e.conf` | `python3 scripts/pipeline_stage_e_setup.py run --conf stage-e.conf` | [docs/HEARTBEAT-MONITOR.md](HEARTBEAT-MONITOR.md) |
| **Reviews post under their own login, not yours** (`review-identity`) | Gives review comments an app identity of their own, so a review proves who wrote it. | built, off by default | Code review of every pull request a session opens, and the fix rounds | `python3 scripts/pipeline_stage_e_app.py verify --conf stage-e.conf` | none | [docs/STAGE-E-OPERATOR.md](STAGE-E-OPERATOR.md) |
| **Fixes your pull requests when main moves under them** (`conflict-fixer`) | Every five minutes it starts a capped Claude session in your own worktree to merge main into a pull request that conflicts, and push. | live | nothing | `python3 scripts/pipeline_conflict_waker_setup.py run --dry-run --conf conflict-waker.conf` | `python3 scripts/pipeline_conflict_waker_setup.py run --conf conflict-waker.conf` | [docs/STAGE-E-OPERATOR.md](STAGE-E-OPERATOR.md) |
| **Turns an idea into a reviewed plan of tickets** (`idea-gate`) | When you move an idea to Plan it, a planning session drafts a plan; the planner files a project, an epic and tickets that pass the readiness gate, into the backlog only. | live | Code review of every pull request a session opens, and the fix rounds, The project's settings file, which switches the pipeline on | `python3 scripts/pipeline_stage_a_setup.py run --dry-run --conf stage-a.conf` | `python3 scripts/pipeline_stage_a_setup.py run --conf stage-a.conf` | [docs/STAGE-A-OPERATOR.md](STAGE-A-OPERATOR.md) |
| **Your tech lead: a chat that answers you and acts on your yes** (`tech-lead`) | Reads your direct messages, answers with one headless Claude run that can only read, and acts in the tracker only after your typed yes. It also sends a short morning and afternoon report. | live | Code review of every pull request a session opens, and the fix rounds, One file of where every plan stands | `python3 scripts/pipeline_bridge_setup.py verify --conf bridge.conf` | `python3 scripts/pipeline_bridge_setup.py install --apply --conf bridge.conf` | [docs/BRIDGE-OPERATOR.md](BRIDGE-OPERATOR.md) |
| **Answers your messages in seconds, not at the next minute** (`instant-replies`) | Holds the chat app's live connection open, so a direct message is picked up within seconds. The minute job stays as the catch-up. | built, off by default | Your tech lead: a chat that answers you and acts on your yes | `python3 scripts/pipeline_bridge_setup.py verify --conf bridge.conf` | `python3 scripts/pipeline_bridge_setup.py push --conf bridge.conf` | [docs/BRIDGE-OPERATOR.md](BRIDGE-OPERATOR.md) |
| **Messages you when only a person can act** (`paging`) | Sends you one short message when a person is needed: a blocked ticket, spent fix rounds, a health incident, a declined review, a pull request ready to merge. | live | Code review of every pull request a session opens, and the fix rounds, Your tech lead: a chat that answers you and acts on your yes | `python3 scripts/pipeline_notifier_setup.py run --dry-run --conf notifier.conf` | `python3 scripts/pipeline_notifier_setup.py run --conf notifier.conf` | [docs/NOTIFIER-OPERATOR.md](NOTIFIER-OPERATOR.md) |
| **The session runner's own chat channel, kept switched off** (`chat-lane`) | Composes and checks the pieces that keep the runner's built-in chat channel off and its port closed. The tech lead no longer lives there. | built, off by default | The always-on machine that runs the sessions, Messages you when only a person can act | `python3 scripts/pipeline_chat_lane_setup.py verify --conf chat-lane.conf` | `python3 scripts/pipeline_chat_lane_setup.py merge --apply --conf chat-lane.conf` | [docs/CHAT-LANE-OPERATOR.md](CHAT-LANE-OPERATOR.md) |
| **Removes what finished sessions left behind** (`cleanup`) | Deletes what finished runs left (worktree, ticket folder, session and temp folders) and trims job logs. It only reads the tracker. | built, off by default | Code review of every pull request a session opens, and the fix rounds | `python3 scripts/pipeline_sweep_setup.py run --dry-run --conf sweep.conf` | `python3 scripts/pipeline_sweep_setup.py run --conf sweep.conf` | [docs/SWEEP.md](SWEEP.md) |
| **Starts approved work on its own, inside a working window and a budget** (`work-queue`) | Plain code decides which approved tickets may start, in what order and how many at once. A usage file paces it; a morning job proposes the day's lanes. | built, off by default | Code review of every pull request a session opens, and the fix rounds, Your tech lead: a chat that answers you and acts on your yes | `python3 scripts/pipeline_queue_setup.py run --dry-run --conf queue.conf` | `python3 scripts/pipeline_queue_setup.py run --conf queue.conf` | [docs/QUEUE-OPERATOR.md](QUEUE-OPERATOR.md) |
| **An independent auditor that can pause new work** (`watcher`) | Plain code under its own account flags spend over a limit, too many runs, or labels added by the wrong login. Its one power is to write a reason into the hold file, which pauses new starts. | built, off by default | Code review of every pull request a session opens, and the fix rounds | `python3 scripts/pipeline_watcher_setup.py check --conf watcher.conf` | `python3 scripts/pipeline_watcher_setup.py run --conf watcher.conf` | [docs/WATCHER.md](WATCHER.md) |
| **Tries the finished app before a plan's last piece merges** (`acceptance-check`) | Opens a finished feature in a real browser in a locked, no-network sandbox, tries to disprove each problem, and posts one verdict per head. | built, off by default | Code review of every pull request a session opens, and the fix rounds, Turns an idea into a reviewed plan of tickets | none | none | [docs/ACCEPTANCE-POLLER-OPERATOR.md](ACCEPTANCE-POLLER-OPERATOR.md) |
| **Approves and merges in your place, inside hard limits** (`validator`) | Would approve a plan, a piece or a merge in your place, only inside switches and size limits you set. Its evidence reader, settings, ledger and brief are built; nothing runs it. | built, off by default | Code review of every pull request a session opens, and the fix rounds, Reviews post under their own login, not yours | none | none | [docs/VALIDATOR-OPERATOR.md](VALIDATOR-OPERATOR.md) |
| **Runs the pipeline on hosted runners instead of this machine** (`hosted-pipeline`) | Another way to run ticket sessions, review and fix rounds: as workflows in the project instead of jobs on a Mac. | built, off by default | The project's settings file, which switches the pipeline on | none | none | [templates/README.md](../templates/README.md) |
| **Lets work start and safe changes merge without you, in narrow cases** (`autonomy`) | Tier gates move a ticket to ready or ask the code host to merge it, only in narrow cases. Telemetry turns session reports into a dashboard and a weekly review that only proposes. | built, off by default | The project's settings file, which switches the pipeline on, Code review of every pull request a session opens, and the fix rounds | none | none | [docs/AUTONOMY.md](AUTONOMY.md) |
| **An hourly check of what nothing else watches** (`health-check`) | Every hour it checks hooks, updates due, the session runner and the public address, disk, temp space and memory, and tells you only when an answer changes. | live | Messages you when only a person can act | `python3 scripts/pipeline_watch.py --conf update.conf run --dry-run` | `python3 scripts/pipeline_watch.py --conf update.conf install` | [docs/UPDATE-OPERATOR.md](UPDATE-OPERATOR.md) |
| **Says what on this machine is behind, and updates the session runner** (`update-review`) | Compares the session runner, chosen tools and the kit checkout with their newest versions. After a typed yes it can update the session runner, and only that. | live | The always-on machine that runs the sessions | `python3 scripts/pipeline_update.py --conf update.conf review` | `python3 scripts/pipeline_update.py --conf update.conf update dispatcher` | [docs/UPDATE-OPERATOR.md](UPDATE-OPERATOR.md) |
| **The one command that brings this machine up to date** (`one-command`) | Pulls the kit, levels the skills, then runs the review, cleanup and planning installers in order, stopping only where a person must act. | live | Written procedures a session runs as slash commands, Code review of every pull request a session opens, and the fix rounds, Removes what finished sessions left behind, Turns an idea into a reviewed plan of tickets | none | `python3 scripts/pipeline_install.py` | [docs/START-HERE.md](START-HERE.md) |

## Documents still to write

### No guide yet (3)

- **Written procedures a session runs as slash commands** (`skills`): Each skill's own page is its reference; no guide covers all nine yet.
- **The always-on machine that runs the sessions** (`host`): A page on what a correct host is, with no product named, is in review.
- **One file of where every plan stands** (`status`): Only a paragraph in the review guide describes it.

### No lesson yet (32)

- **Safety checks on every action a session takes** (`guards`): The kit has no lessons yet: teaching pages live outside it, with this install's names.
- **A check on every commit, and a scan for secrets** (`commit-check`): The kit has no lessons yet: teaching pages live outside it, with this install's names.
- **Checks on every pull request, and nothing merges red** (`kit-checks`): The kit has no lessons yet: teaching pages live outside it, with this install's names.
- **The project's settings file, which switches the pipeline on** (`project-settings`): The kit has no lessons yet: teaching pages live outside it, with this install's names.
- **Turning a fresh copy of the kit into a project** (`project-template`): The kit has no lessons yet: teaching pages live outside it, with this install's names.
- **Written procedures a session runs as slash commands** (`skills`): The kit has no lessons yet: teaching pages live outside it, with this install's names.
- **The always-on machine that runs the sessions** (`host`): The kit has no lessons yet: teaching pages live outside it, with this install's names.
- **The mark that tells the safety checks a session is the pipeline's** (`lane-marker`): The kit has no lessons yet: teaching pages live outside it, with this install's names.
- **Coding sessions that take a ticket to a pull request** (`coding-sessions`): The kit has no lessons yet: teaching pages live outside it, with this install's names.
- **The tracker's columns and labels** (`tracker-board`): The kit has no lessons yet: teaching pages live outside it, with this install's names.
- **Code review of every pull request a session opens, and the fix rounds** (`review`): The kit has no lessons yet: teaching pages live outside it, with this install's names.
- **Side findings a session leaves become backlog tickets** (`findings`): The kit has no lessons yet: teaching pages live outside it, with this install's names.
- **Re-sends a hand-off the session runner missed** (`catch-up`): The kit has no lessons yet: teaching pages live outside it, with this install's names.
- **One file of where every plan stands** (`status`): The kit has no lessons yet: teaching pages live outside it, with this install's names.
- **Says when one of the pipeline's jobs stops** (`job-health`): The kit has no lessons yet: teaching pages live outside it, with this install's names.
- **Reviews post under their own login, not yours** (`review-identity`): The kit has no lessons yet: teaching pages live outside it, with this install's names.
- **Fixes your pull requests when main moves under them** (`conflict-fixer`): The kit has no lessons yet: teaching pages live outside it, with this install's names.
- **Turns an idea into a reviewed plan of tickets** (`idea-gate`): The kit has no lessons yet: teaching pages live outside it, with this install's names.
- **Your tech lead: a chat that answers you and acts on your yes** (`tech-lead`): The kit has no lessons yet: teaching pages live outside it, with this install's names.
- **Answers your messages in seconds, not at the next minute** (`instant-replies`): The kit has no lessons yet: teaching pages live outside it, with this install's names.
- **Messages you when only a person can act** (`paging`): The kit has no lessons yet: teaching pages live outside it, with this install's names.
- **The session runner's own chat channel, kept switched off** (`chat-lane`): The kit has no lessons yet: teaching pages live outside it, with this install's names.
- **Removes what finished sessions left behind** (`cleanup`): The kit has no lessons yet: teaching pages live outside it, with this install's names.
- **Starts approved work on its own, inside a working window and a budget** (`work-queue`): The kit has no lessons yet: teaching pages live outside it, with this install's names.
- **An independent auditor that can pause new work** (`watcher`): The kit has no lessons yet: teaching pages live outside it, with this install's names.
- **Tries the finished app before a plan's last piece merges** (`acceptance-check`): The kit has no lessons yet: teaching pages live outside it, with this install's names.
- **Approves and merges in your place, inside hard limits** (`validator`): The kit has no lessons yet: teaching pages live outside it, with this install's names.
- **Runs the pipeline on hosted runners instead of this machine** (`hosted-pipeline`): The kit has no lessons yet: teaching pages live outside it, with this install's names.
- **Lets work start and safe changes merge without you, in narrow cases** (`autonomy`): The kit has no lessons yet: teaching pages live outside it, with this install's names.
- **An hourly check of what nothing else watches** (`health-check`): The kit has no lessons yet: teaching pages live outside it, with this install's names.
- **Says what on this machine is behind, and updates the session runner** (`update-review`): The kit has no lessons yet: teaching pages live outside it, with this install's names.
- **The one command that brings this machine up to date** (`one-command`): The kit has no lessons yet: teaching pages live outside it, with this install's names.

### No runbook yet (32)

- **Safety checks on every action a session takes** (`guards`): No runbook for 'a check blocked me, what now'; the guide is a dense reference.
- **A check on every commit, and a scan for secrets** (`commit-check`): Its guide holds the set-up steps; no separate runbook yet.
- **Checks on every pull request, and nothing merges red** (`kit-checks`): Its guide holds the set-up steps; no separate runbook yet.
- **The project's settings file, which switches the pipeline on** (`project-settings`): Its guide holds the set-up steps; no separate runbook yet.
- **Turning a fresh copy of the kit into a project** (`project-template`): Its guide holds the set-up steps; no separate runbook yet.
- **Written procedures a session runs as slash commands** (`skills`): Its guide holds the set-up steps; no separate runbook yet.
- **The always-on machine that runs the sessions** (`host`): Its guide holds the set-up steps; no separate runbook yet.
- **The mark that tells the safety checks a session is the pipeline's** (`lane-marker`): Its guide holds the set-up steps; no separate runbook yet.
- **Coding sessions that take a ticket to a pull request** (`coding-sessions`): Its guide holds the set-up steps; no separate runbook yet.
- **The tracker's columns and labels** (`tracker-board`): Its guide holds the set-up steps; no separate runbook yet.
- **Code review of every pull request a session opens, and the fix rounds** (`review`): Its guide holds the set-up steps; no separate runbook yet.
- **Side findings a session leaves become backlog tickets** (`findings`): Its guide holds the set-up steps; no separate runbook yet.
- **Re-sends a hand-off the session runner missed** (`catch-up`): Its guide holds the set-up steps; no separate runbook yet.
- **One file of where every plan stands** (`status`): Its guide holds the set-up steps; no separate runbook yet.
- **Says when one of the pipeline's jobs stops** (`job-health`): Its guide holds the set-up steps; no separate runbook yet.
- **Reviews post under their own login, not yours** (`review-identity`): Its guide holds the set-up steps; no separate runbook yet.
- **Fixes your pull requests when main moves under them** (`conflict-fixer`): Its guide holds the set-up steps; no separate runbook yet.
- **Turns an idea into a reviewed plan of tickets** (`idea-gate`): Its guide holds the set-up steps; no separate runbook yet.
- **Your tech lead: a chat that answers you and acts on your yes** (`tech-lead`): Its guide holds the set-up steps; no separate runbook yet.
- **Answers your messages in seconds, not at the next minute** (`instant-replies`): Its guide holds the set-up steps; no separate runbook yet.
- **Messages you when only a person can act** (`paging`): Its guide holds the set-up steps; no separate runbook yet.
- **The session runner's own chat channel, kept switched off** (`chat-lane`): Its guide holds the set-up steps; no separate runbook yet.
- **Removes what finished sessions left behind** (`cleanup`): Its guide holds the set-up steps; no separate runbook yet.
- **Starts approved work on its own, inside a working window and a budget** (`work-queue`): Its guide holds the set-up steps; no separate runbook yet.
- **An independent auditor that can pause new work** (`watcher`): Its guide holds the set-up steps; no separate runbook yet.
- **Tries the finished app before a plan's last piece merges** (`acceptance-check`): Its guide holds the set-up steps; no separate runbook yet.
- **Approves and merges in your place, inside hard limits** (`validator`): Its guide holds the set-up steps; no separate runbook yet.
- **Runs the pipeline on hosted runners instead of this machine** (`hosted-pipeline`): Its guide holds the set-up steps; no separate runbook yet.
- **Lets work start and safe changes merge without you, in narrow cases** (`autonomy`): Its guide holds the set-up steps; no separate runbook yet.
- **An hourly check of what nothing else watches** (`health-check`): Its guide holds the set-up steps; no separate runbook yet.
- **Says what on this machine is behind, and updates the session runner** (`update-review`): Its guide holds the set-up steps; no separate runbook yet.
- **The one command that brings this machine up to date** (`one-command`): Its guide holds the set-up steps; no separate runbook yet.

## What only a person does

Each step is marked: *only a person* can do it, or it is *a sign-off a machine could fake*.

- **Checks on every pull request, and nothing merges red**: Turn on branch protection for main, with the required checks and admins included. *only a person.*
- **The project's settings file, which switches the pipeline on**: Copy the example settings file into the project, fill it in, and merge it. *only a person.*
- **Turning a fresh copy of the kit into a project**: Paste the bootstrap prompt into a Claude session in the new project and answer its questions. *only a person.*
- **The always-on machine that runs the sessions**: Build the host: the pipeline's own account, the session runner, its front door and its public address. *only a person.*
- **The always-on machine that runs the sessions**: Sign in to the tracker, the code host and the chat app, and make each key the pipeline needs. *only a person.*
- **The mark that tells the safety checks a session is the pipeline's** (card CK-10): As an administrator, write the empty root-owned marker file the card names. *only a person.*
- **Coding sessions that take a ticket to a pull request**: Add the project to the session runner's settings and start one coding session by hand. *only a person.*
- **The tracker's columns and labels**: Run the board skill in a Claude session, or make the columns and labels by hand. *only a person.*
- **Code review of every pull request a session opens, and the fix rounds** (card CK-1): Merge the pull requests that carry the review layer. *only a person.*
- **Code review of every pull request a session opens, and the fix rounds** (card CK-2): Paste the two credentials at a terminal. *only a person.*
- **Code review of every pull request a session opens, and the fix rounds** (card CK-4): Add the session runner's agent to each work team. *only a person.*
- **Code review of every pull request a session opens, and the fix rounds** (card CK-5): Read the dry run's count before anything is turned on. *a sign-off a machine could fake.*
- **Code review of every pull request a session opens, and the fix rounds** (card CK-6): Name the required checks the code host would not report. *a sign-off a machine could fake.*
- **Code review of every pull request a session opens, and the fix rounds** (card CK-7): Watch one real ticket become a reviewed pull request. *a sign-off a machine could fake.*
- **Re-sends a hand-off the session runner missed** (card CK-11): Read the catch-up job's first count before it is loaded. *a sign-off a machine could fake.*
- **One file of where every plan stands** (card CK-12): Make the status job's dry run read the tracker. *only a person.*
- **Says when one of the pipeline's jobs stops** (card CK-9): Name the ticket the job-health check comments on. *a sign-off a machine could fake.*
- **Reviews post under their own login, not yours**: Click the code host's own Create and Install buttons for the app; nothing else may make one. *only a person.*
- **Fixes your pull requests when main moves under them** (card CK-W1): Read the conflict fixer's dry run before it is turned on. *a sign-off a machine could fake.*
- **Turns an idea into a reviewed plan of tickets** (card CA-DELIVERY): Merge the pull request that switches plans on in each planned repository. *only a person.*
- **Turns an idea into a reviewed plan of tickets** (card CA-ENTRY): Say yes to the planning setup in the session runner's settings. *a sign-off a machine could fake.*
- **Turns an idea into a reviewed plan of tickets** (card CA-PROBE): Prove where planning tickets go, and what a planning session holds. *a sign-off a machine could fake.*
- **Turns an idea into a reviewed plan of tickets** (card CA-EXECUTOR): Start the planner job. *a sign-off a machine could fake.*
- **Turns an idea into a reviewed plan of tickets** (card CA-LANE): Prove the lane on two test ideas and one real idea. *only a person.*
- **Your tech lead: a chat that answers you and acts on your yes** (card CK-B1): Create the tech lead's own chat app from the printed manifest. *only a person.*
- **Your tech lead: a chat that answers you and acts on your yes** (card CK-B2): Load the tech lead's job. *a sign-off a machine could fake.*
- **Your tech lead: a chat that answers you and acts on your yes** (card CK-B4): Prove the tech lead live with one message. *a sign-off a machine could fake.*
- **Answers your messages in seconds, not at the next minute** (card CK-B9): Turn instant replies on in the chat app's settings, then load the job. *a sign-off a machine could fake.*
- **Messages you when only a person can act** (card CK-N1): Create the paging chat app and a private channel, unless pages go to direct messages. *only a person.*
- **Messages you when only a person can act** (card CK-N2): Paste the paging app's token at a terminal. *only a person.*
- **Messages you when only a person can act** (card CK-N3): Load the paging job. *a sign-off a machine could fake.*
- **Messages you when only a person can act** (card CK-N4): Watch one throwaway escalation become a message. *a sign-off a machine could fake.*
- **The session runner's own chat channel, kept switched off** (card CK-C4): Prove the runner's port is closed, from a second device. *only a person.*
- **The session runner's own chat channel, kept switched off** (card CK-C5): Restart the session runner, only when it is idle. *a sign-off a machine could fake.*
- **The session runner's own chat channel, kept switched off** (card CK-C6): Turn the chat channel off. *a sign-off a machine could fake.*
- **Removes what finished sessions left behind** (card CK-S1): Read the cleanup's dry run, then load the job. *a sign-off a machine could fake.*
- **Starts approved work on its own, inside a working window and a budget** (card CK-Q1): Point the tech lead's settings at the queue. *a sign-off a machine could fake.*
- **Starts approved work on its own, inside a working window and a budget** (card CK-Q2): Load the usage job and the morning job. *a sign-off a machine could fake.*
- **An independent auditor that can pause new work**: Make the watcher's keys read-only where you create them; the installer cannot see a key's scope. *only a person.*
- **An independent auditor that can pause new work**: Clear the hold yourself, in a terminal, when the watcher pauses new work. *only a person.*
- **Approves and merges in your place, inside hard limits**: Turn on each switch by hand, one at a time, in the settings file. *only a person.*
- **Runs the pipeline on hosted runners instead of this machine**: Move the pipeline workflow templates into the project, and make the app or token they use. *only a person.*
- **An hourly check of what nothing else watches**: Make a private alert repository and copy the alert workflow into it. *only a person.*

## What is not proven

- The list was checked against the kit's code, not against a running machine. No ticket yet.
- No part has a first-pass ceiling yet: every `ceiling` is empty. No ticket yet.
