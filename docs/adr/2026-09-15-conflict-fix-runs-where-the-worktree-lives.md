# A conflict fix runs where the worktree lives — GitHub keeps the clock

**Date:** 2026-09-15 · **Status:** Accepted

## Context

Thirteen parallel local sessions each opened a PR, drove it green and ended their turn.
Five merged; the other eight went `CONFLICTING`. `pr-conflict-monitor.yml` labeled and
commented on all eight correctly, and every one still waited for a person to wake its
session by hand (docs/LESSONS.md, 2026-09-15).

Nothing in the repo could close that loop:

- The Stop hook and its fix budget sample at turn-end only. They hold no timer.
- An idle local session has no wake path. The dispatcher's resume-on-comment exists only
  for dispatcher-owned sessions.
- The template's answer, an `@claude` comment for `claude.yml`, does not run here. It
  would also need a model credential in Actions and a PAT, because a
  `GITHUB_TOKEN`-authored comment or push starts no workflow. It would need `workflows`
  scope for any conflict under `.github/workflows/`. And it would run the model outside
  the PreToolUse hooks.

## Decision

Split the loop by what each side already holds.

1. **GitHub keeps the clock and the budget.** On a new conflict episode the monitor
   (`scripts/pr_conflict.py monitor`) posts a machine-readable fix **request**. It allows
   three per PR, lifetime. It escalates to the owner once when no **ack** arrives within
   15 minutes, no **result** within two hours, or a result arrives while GitHub still says
   `CONFLICTING`. A fork, or a spent budget, is paged and never requested.
2. **The machine that holds the worktree does the work.** A person runs a **waker**
   (`scripts/pr_conflict.py wake`) on a schedule. It claims a request for a branch one
   of its worktrees holds and runs `claude -p` there. The prompt is fixed: merge
   `origin/main`, resolve, push, watch CI, never merge or approve. It posts the result
   GitHub reports, not the one the session claims. The session runs under the repo's own
   hooks, with a spend cap and a timeout.
3. **Markers carry no authority.** Only the first line of a comment is parsed. Request,
   page and escalation markers count only from the Actions bot, so a comment cannot
   reset the budget. Ack and result markers count only from a writer, posted after the
   episode opened. An ack can delay a page by at most the result deadline. A result only
   ends the wait. Whether the conflict is gone is read from `mergeable`. Session output
   is neutralized before it is embedded.
4. **The waker refuses an agent environment** except `--dry-run`. A session that starts
   sessions spends money nobody approved.
5. **The monitor is honest under §13.** A PR whose mergeability never settles fails the
   run. A truncated listing is "could not tell", never "no conflicts".

The same change sweeps the `conflict` label off closed PRs and drafts. It also adds
`pr-union-check.yml`, a report-only union check. That one is reporting, not a new
authority, and needs no decision of its own.

## Consequences

- **The loop closes only while a waker runs and its machine is awake.** Otherwise the
  ack deadline turns the request into the same page the monitor sent before — slower by
  up to 15 minutes plus a tick, never silent.
- **A new remote-to-local path exists.** A bot comment can start a paid session on a
  person's machine. It is bounded by the three-request budget, the per-session spend cap,
  a fixed prompt whose only PR-author input is a branch name matched against a strict
  pattern, and the hooks the session runs under. `scripts/pr_conflict.py` is on the
  grader-path floor, so changing the prompt or the one label it writes needs a person's
  `hooks-change` label.
- A headless session can use only the tools its settings allow. A waker configured
  without them produces `failed` results, and the monitor pages — the misconfiguration
  is loud.
- Several conflicts at once run one session each, in their own worktrees. Two waker
  passes cannot double-start one PR on one machine (a per-PR lock, plus the ack re-read
  under it). Two machines holding the same branch are not coordinated.
- The template keeps its `@claude` handoff for projects that run `claude.yml`.
