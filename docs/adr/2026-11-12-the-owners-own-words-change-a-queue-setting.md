# The owner's own words change a queue setting, through one write path

- **Status:** Proposed, 12 Nov 2026 (KIT-700). The owner chose the copy carried at install on 12 Nov.
- **Date:** 2026-11-12

## Context

The owner asked that the tech lead carry out any adjustment he asks for, with its own tools. The queue's limits live
in queue.conf, his file in his checkout. The jobs and the tech lead run as the role account, which can neither read
nor write it. The only change path before this was the sprint review's `apply` reply (KIT-577), and it lasted only
until the next install rewrote the queue's config from queue.conf.

Every acting tool before this one only asks: its action waits for the owner's yes to a question the bridge asks.

## Decision

1. **His request is the yes.** `queue_setting` records a change, and the bridge makes it after the turn only when the
   message that started the turn is the owner's, as Slack says, and asks for it in one sentence: a verb such as set,
   lower or raise, the setting, and the value. A question, a negation, or a number in a time, a date or a percentage
   asks for nothing. The one write path checks his words itself as well. A change the model read in a ticket, a pull
   request or a session's words is never in his message, so it is refused.
2. **Three settings.** `PLANNED_LOAD`, `DAY_STOP` and `LANES_PER_REPO`. Who may start work, teams, repositories,
   paths, accounts and jobs stay his own edit. The runaway stop stays: it can be raised, never removed.
3. **One write path.** `scripts/pipeline_queue_settings.py` writes every change: the tech lead's, and the sprint
   review's. The queue's own loader judges the new settings first; a value it refuses is never written.
4. **A copy, carried at install.** The installer keeps a copy of queue.conf beside the queue's config, with a base line.
   The write path changes the copy and the config, reads both back, and appends a ledger row. The next install writes
   each change into his queue.conf, unless he changed that setting too. The role account never writes his file.

## Consequences

- A change takes effect at the queue's next pass and survives the next install.
- The words "lower the planned load to 30" are enough; a separate yes is not asked.
- The ledger and the stand-up make every change visible. A change he did not mean is undone the same way.
- Rejected: moving queue.conf into the role account's folder. It is smaller, but anything running as that account
  could then rewrite every key, owner ids included, and the next install would keep it.
