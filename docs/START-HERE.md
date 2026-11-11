# Start here

*Written 2026-11-10 (KIT-589, a slice of KIT-59). For someone who has never set the kit up. It
points at the operator guides and does not repeat them. Every value in this repo is synthetic;
keep your own names, accounts and paths in a private repo, never here.*

## What the kit is

The kit is a GitHub template. It gives a project two things.

1. **Guardrails for Claude Code.** Hooks that block secret reads, pushes to `main`, self-merged
   pull requests and edits to the guards. Git pre-commit checks and CI back them up.
2. **An optional delivery pipeline.** A ticket becomes a coding session, then a pull request,
   then a review, then your merge. Nothing in it merges for you.

Part 1 works on its own. Part 2 is off until you add a `delivery.json` and install its jobs.

**What it is not.** It is not a hosted service. It runs on a computer you own. It does not merge,
approve or label for you. It does not make a bad ticket good.

Good: a person reads the pull request and merges it.
Not that: a session merges its own work.

## What you need first

| You need | Used for |
| --- | --- |
| A GitHub account, `git`, and the GitHub CLI `gh`, logged in | The repo, its pull requests and its checks |
| Python 3.9 or newer, and Node 20 or newer | The hooks, the installers and the secret scan |
| A Claude Code subscription | Every session the pipeline starts |
| For part 1: macOS or Linux | The hooks. Windows needs WSL2 |
| For part 2: a Mac that stays on | The installers write macOS launchd jobs. A Linux install of part 2 is not covered |
| For part 2: a Linear workspace | The ticket tracker the pipeline reads and writes |
| For part 2: a coding-session dispatcher | The program that turns a delegated ticket into a session. The kit does not ship one |
| For part 2: a Slack workspace | Pings and the chat. Optional, but most of the later parts need it |

**The dispatcher is the one thing this repo cannot give you.** `docs/STAGE-E-OPERATOR.md` ("On a
new machine, the dispatcher comes first") says what it must look like. Build it first.

## The order

Do part 1 and stop there if you only want the guardrails. Part 2 comes in the order below. Each
step needs the ones above it.

### Part 1 — the guardrails

1. **Make your repo.** Use the template, clone it, open Claude Code in it.
2. **Run the bootstrap.** Paste `BOOTSTRAP-PROMPT.md`. It asks you questions and fills in the
   blanks (`PLACEHOLDERS.md`).

**How you know it works.** `npm run test:hooks` passes. A try to edit on `main` is blocked with a
message that tells you to branch.
**What a failure looks like.** A hook that is missing a script blocks every tool. Put scripts in
place before the settings that call them (`docs/LESSONS.md`).

### Part 2 — the delivery pipeline

Run each installer in a terminal, as yourself. They refuse to run inside a Claude session, and
there is no override.

3. **Turn it on for the project.** Copy `delivery.example.json` to `delivery.json`, fill it in,
   and run `python3 scripts/check_delivery_config.py`. The `/setup-board` skill makes the
   matching Linear board. The formats are in `docs/PIPELINE-CONTRACT.md`.
   *Works:* the checker prints no errors. *Fails:* it names the key that is wrong.

4. **The dispatcher.** Build it by its own guide and run one coding session by hand. Stage E
   needs that first session to have happened.
   *Works:* a delegated ticket starts a session. *Fails:* nothing starts. Read
   `docs/SESSION-BRIEF.md` for what a session is told and what it can do.

5. **Stage E, the review layer.** Copy `stage-e.conf.example` to `stage-e.conf` and fill it in.
   Then run `python3 scripts/pipeline_stage_e_setup.py run` and repeat it until it stops asking.
   Guide: `docs/STAGE-E-OPERATOR.md`. It also installs the finding poller
   (`docs/FINDING-POLLER.md`), the catch-up poller (`docs/CATCH-UP.md`), the heartbeat monitor
   (`docs/HEARTBEAT-MONITOR.md`) and the conflict waker.
   *Works:* `python3 scripts/pipeline_stage_e_setup.py verify` exits 0.
   *Fails:* a row says FAILED or NOT MEASURED. Each row names what to do. A row that could
   not look never counts as a pass.

6. **The idea gate.** `python3 scripts/pipeline_install.py` pulls the kit, then runs Stage E's
   check and the idea gate's installer. Run it again after each stop. Guide:
   `docs/STAGE-A-OPERATOR.md`, section 5.
   *Works:* `python3 scripts/pipeline_stage_a_setup.py verify` exits 0, and a throwaway idea
   moved to **Plan it** becomes a plan.
   *Fails:* the planner says the routing is unknown. Nothing new starts until it is known.

7. **The notifier.** One short ping to you for each event that needs a person. Copy
   `notifier.conf.example` to `notifier.conf`, then run
   `python3 scripts/pipeline_notifier_setup.py run`. Guide: `docs/NOTIFIER-OPERATOR.md`.
   *Works:* `python3 scripts/pipeline_notifier_setup.py verify` exits 0 and a test ping arrives.
   *Fails:* a dead notifier does not ping you. Its heartbeat file and the monitor are how you
   find out.

8. **The chat lane, then the bridge.** The chat lane lets you mention the dispatcher's bot in
   Slack. The bridge lets a trusted member answer "yes" and act as you. Read the trusted-members
   rule first: anyone who can say yes has your reach. Guides: `docs/CHAT-LANE-OPERATOR.md`,
   `docs/BRIDGE-OPERATOR.md`. Commands:
   `python3 scripts/pipeline_chat_lane_setup.py verify` and
   `python3 scripts/pipeline_bridge_setup.py verify`.
   *Works:* both exit 0 and the bridge answers a test message.
   *Fails:* `UNKNOWN` on a fresh chat lane until its first live check. Run it again after.

9. **Cleanup and health.** The sweep removes what finished runs leave behind (`docs/SWEEP.md`,
   `python3 scripts/pipeline_sweep_setup.py run`). The health watch and the update review say
   what is out of date (`docs/UPDATE-OPERATOR.md`, `python3 scripts/pipeline_update.py review`).
   *Works:* the sweep's dry run lists only what you expect. `review` shows every part current.
   *Fails:* the sweep never loads itself. You read its dry run first.

10. **Optional, off by default.** The queue and workers that start approved work without you
    (`docs/QUEUE-OPERATOR.md`, `docs/AUTONOMY.md`). The watcher (`docs/WATCHER.md`). Leave these
    off until the parts above have run for a while.
    *Works:* for the queue, `python3 scripts/pipeline_queue_setup.py run --dry-run` ends with
    `DRY RUN: nothing was changed` and each row reads `ALREADY-DONE` or `WOULD-CHANGE`. For the
    watcher, `python3 scripts/pipeline_watcher_local.py run --config <watcher.json> --dry-run`
    prints what it sees and changes nothing.
    *Fails:* a row says `UNKNOWN` or `FAILED`, or the queue installer lists problems in your
    conf. `UNKNOWN` means it could not look, which is not a pass. The watcher has no scheduled
    job yet (`docs/WATCHER.md`), so it only runs when you start it.

## What only a person can do

No installer does these. Plan for them.

- Create the accounts and the Slack apps, and mint the tracker key and the GitHub token.
- Write down each credential's expiry. Nothing warns you before one lapses.
- Create the board's review and approval lanes. No installer adds them.
- Merge every pull request, including the one that turns plans on in a repository.
- Add the guard-change label when a pull request touches a guard. A session cannot.
- Type your login password when an installer asks, and sign each card it prints.
- Run the `launchctl` lines a card prints. The installers never load a job for you.

## When something looks wrong

Pipelines fail quietly. A check that never ran looks the same as a check that passed. The rule
behind that is in `docs/PIPELINE-CONTRACT.md` §13. Each operator guide has its own "What is
not proven" list: read it before you trust a part.

## What is not proven

- **That a stranger can follow this page and finish.** It was written from the guides, not by
  following them on a clean machine. KIT-59.
- **That the order is the only one that works.** It is the order each guide's "what you need
  first" implies. KIT-59.
- **Part 2 on Linux.** The installers write launchd jobs. No ticket yet.
- **A dispatcher guide the kit ships.** There is none here. KIT-54.
