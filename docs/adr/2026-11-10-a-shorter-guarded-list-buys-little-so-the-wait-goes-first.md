# A shorter guarded list buys little, so the label's wait goes first

**Date:** 2026-11-10 · **Status:** Proposed. The owner decides the three questions at the end. · **Context:** KIT-587, for KIT-317. Builds on [The grader floor gates what a change can do, not what the file is called](2026-08-25-grader-floor-gates-supervision-not-names.md).

## The question

Nearly every pipeline PR needs the owner's `hooks-change` label, a second pass of CI, and then his merge. When almost everything needs the label, it stops saying "look closely at this one". KIT-317 asked for a shorter guarded list.

## What the history says

Measured on `main`, from 10 Oct to 10 Nov: **248 commits. 242 of them touched a guarded path.** That is 98%. The label already says nothing, because it is on everything.

Which kind of guarded file did a commit touch? One commit can touch several kinds.

| Kind of guarded file | Commits that touched it |
|---|---|
| The daemons: `pipeline_*.py` that are not installers or the bridge | 115 |
| Installers and owner tools (`*_setup`, `install`, `update`, `sweep`, `model_switch`) | 92 |
| The bridge family (`bridge*`, `tech_lead_*`, `standup`, `voice`) | 84 |
| Workflows (`.github/workflows/**`, `templates/workflows/pipeline-*`) | 50 |
| Graders (`scripts/check_*.py`) | 32 |
| Hooks, settings and the other fixed floor files | 19 |

The same split by commits that touched **only one** kind: bridge 47, daemons 43, installers 25, hooks and floor 8, graders 5. That is 128. The other **114 touch two or more kinds**, for example an installer, its selftest and a CI step in one PR.

Two facts matter:

- The list is not the lever. The label is needed because most PRs are made of several guarded things at once.
- `.github/workflows/ci.yml` alone was touched by 41 commits. Each new selftest adds a CI step. That label is right: it changes what CI runs.

## Options

| | What changes | Commits freed (of 242) | Risk |
|---|---|---|---|
| **A. Keep it** | Nothing | 0 | None. The label stays noise. |
| **B. Strict leaves** | Ten files that only report: `usage_runs`, `usage_local`, `cleanup_probe`, `ws_client`, `day_planner`, `notify_events`, `status_local`, `standup`, `morning`, `day_plan` | 6 (2%) | Not worth a rule. And not free of risk: `status_local` is imported by the queue's starter and the heartbeat monitor, so a wrong status could steer a guarded job. |
| **C. Daemon families out by name** | 31 files leave: the bridge modules except `bridge_setup`, `tech_lead_*`, `standup`, `morning`, the day plan, the usage and status jobs, `voice`, `look`, `window`, `acceptance_poller` | 36 (15%) | Real. These run with the bridge's and the chat's credentials. A bad edit can add an action and merge with only Stage E and the owner's merge. |
| **D. C plus a tripwire** | As C, and the label is still required when a PR to those files adds a new power in its added lines: a new process start, a network write, `launchctl` or `sudo`, a tracker mutation, a protected label name | About 36, less the PRs that trip it | Medium. It sees an added power, not a new use of an old one. |
| **E. Cut the wait, not the list** | The label gate gets its own workflow, so a label re-judges only that gate | 0 labels, but about fifteen minutes less wait on every labeled PR | None. The gate is unchanged. **Done in this PR.** |
| **F. Split the bridge by authority** | The part that acts (the four tracker writes, the Slack post, the tech lead's process start) stays guarded. The part that decides wording and plans becomes data, or runs in a process with no credentials | The bridge's 84 touches, less the PRs that also touch a kind that stays guarded | Medium: a real refactor, the same shape as KIT-312, but cut by authority and not by size |
| **G. The merge is the acknowledgement** | Drop the label for the daemons and require Stage E's review comment instead | Most | Not possible. Sessions post to GitHub as the owner, so a comment cannot be told from a forged one. |

## Why the bridge cannot just leave the list

KIT-312 split the bridge into modules by concern, to make each one reviewable. That did not change what runs where: every module is imported into one process that holds the Slack and tracker credentials. A file that a guarded job imports runs with that job's authority. So moving a bridge module off the list helps only if what it can do is data (words, plans, text) or runs somewhere with no credentials. Before any part leaves it needs:

1. The four tracker writes and the Slack post in modules of their own, with the selftest's pins on them kept.
2. The entry script's list of allowed requests (what a chat message may start) in those same guarded modules.
3. The wording and planning modules to return values and call nothing that acts.

## My pick

**Do E now. It is in this PR.** It costs nothing in safety and saves the longest part of the owner's wait.

Then **do not shorten the list by name (B, C) yet.** Option C frees 15% for a real loss of supervision. If the owner wants fewer labels, take **F first**: it makes the bridge safer to review and then makes C honest. If he wants a smaller list before that, **D** is the cheapest way to keep the label meaning "this adds a power".

The label cost is also a build-phase cost. It falls when the churn on the daemons falls after the MVP.

Good: a label on a PR that changes who can approve, or what CI runs.
Not that: a label on a change to a Slack sentence.

## What this PR changes

`Hooks change guard` moves from the last job of `ci.yml` to `.github/workflows/hooks-change-guard.yml`. The job name is the same, so the required status check is the same. It still judges the PR against the default branch, still runs the default branch's copy of `check_grader_paths.py`, and still reads who applied the label. `ci.yml` no longer runs on `labeled` or `unlabeled`, so a label no longer re-runs every battery. The secret-scan selftest reads the gate from its new file.

## Questions for the owner

1. **Yes or no: keep E.** (Merge this PR. It needs your `hooks-change` label once, as it edits workflows.)
2. **Which of B, C, D or F, or none?** My pick: none of B and C, F next, D only if you want fewer labels before F.
3. **Start F for the bridge?** If yes, KIT-317's follow-up PR changes `FLOOR` and `UNGATED` only after F, with a selftest case for each moved file.
