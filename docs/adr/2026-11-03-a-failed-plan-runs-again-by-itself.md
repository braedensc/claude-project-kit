# A failed plan runs again by itself, told why

**Date:** 2026-11-03 · **Status:** Accepted (owner, 2026-11-02, decision 2 on KIT-275) ·
**Context:** KIT-374, KIT-375, KIT-376; `scripts/pipeline_plan_poller.py`

## Decision

When the owner switches it on (`AUTO_REPLAN`, asked once by the Stage A installer), the
planner job runs a move's planning again by itself, up to twice, when the run ended in a
way another try can fix. Those endings are:

- a plan refused for its shape or readiness (`cut-off`, `schema`, `readiness-gate`);
- no plan, or no answer at all;
- a usage or rate limit, two hours later;
- a time-out, once its session reads ended. If that session left a real final message,
  that message is filed instead, with no new session.

Each re-run's planning ticket carries an `<untrusted-earlier-attempts>` block before the
idea's fence. It names every earlier run of the move, newest first, and how each ended: the
job's own fixed words for a time-out, an empty answer or a limit, and the executor's own
cleaned reasons for a refusal. A filing given up on with nothing filed runs the executor
once more, an hour later, over the same message.

Three rules come with it, in the same change:

- **One pass at a time.** A pass holds `<state_dir>/poller.lock`.
- **No run over a standing plan.** A move whose idea already has a plan that stands gets
  one note and no run. A plan stands when an epic carries one of the idea's runs' receipts,
  was filed by the owner's key and is not canceled.
- **Awake time.** The four-hour time-out counts only time the machine was awake.

## Why

- **The owner asked for it.** His words on 2 Nov: "make sure the retry knows why it failed
  so we're not just duplicating the mistake". So the reasons travel, and the first line
  asks for a different approach. Without them a re-run is the same run paid for twice.
- **The reasons never decide.** A refusal's reasons are session-influenced text: the gate
  quotes child titles and values. Only the verdict's class and kind, and the session's
  ending, decide whether a re-run happens (`rerun_cause`). The reasons are quoted as data,
  cleaned by `sanitize_text`, at most 2000 characters each, and every line inside the block
  opens with the job's own words or a quote mark. So no line can pass for a trigger line,
  an `Idea:` line or a fence.
- **Never re-run what a person did, or what signals an attack.** Questions, a stop, a
  partial filing, a retarget, a disallowed request kind (now a refusal kind of its own,
  `request-kind`), a credential's shape and a config error all stop the move for good. So
  does an idea whose text changed since its first run. A re-run is a paid session started
  with no person's gesture, so the list of what may start one is closed.
- **One run per idea at a time still holds.** A running re-run is in flight. An owed one is
  written to a ledger (`<state_dir>/replans.json`) as `owed` before its ticket exists, and
  a newer owner move drops it. Before filing, the job checks the idea again: still in Plan
  it, not live work, no standing plan, the same newest move, and under
  `AUTO_REPLAN_PER_DAY`. An unreadable ledger re-runs nothing and pages.
- **You hear the result, not the retries** (decision 7). A re-run starting is a note on the
  idea. With re-runs on, the executor's own page is held (`--defer-mark`). The job pages
  once, on the last run's planning ticket, when the move ends without a plan, and names
  every run. Until KIT-338 gives the planner a mark of its own, that page carries the
  executor's mark (`planning-rejected` or `planning-no-output`), which the notifier already
  reads from the owner's key.
- **Caps stop a runaway, not a normal day.** Two re-runs a move, twenty a day (two a move,
  times `MAX_RUNS_PER_DAY`'s ten), counted apart from new runs. Re-runs share the per-pass
  limit of three with new runs.
- **Rejected: a re-run as a new move.** Moving the idea out of Plan it and back in, as
  `replan` does, would read as the owner's gesture. It would also lose the move's history,
  which is what the next run needs to be told.

## Verified

- New selftest cases fail on main's code and pass here:
  - the lock: two passes against one state folder file one planning ticket;
  - a standing plan, for a held move and a new one;
  - awake time;
  - every re-run ending, and every never-re-run ending, a run cancelled mid-flight among
    them;
  - each check before filing, the ledger, and the final page;
  - a third run that names both earlier attempts, newest first.
- The bridge's own `run_idea` reads a re-run's ticket as a run of its idea, so its `run
  open` and `plan filed` refusals cover re-runs.
- The poller, the executor and the Stage A installer each pass their selftest on Python
  3.13 and 3.9.
- **Not yet proven live:** a re-run on the real dispatcher, and the notifier paging the
  planner's final page.
