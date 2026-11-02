# The acceptance check: a dispatcher session looks at the finished feature through a locked browser tool, before the last piece merges

- **Status:** Accepted (owner, 2026-11-01, in chat; KIT-302). Built in parts: KIT-322 (this
  record, `delivery.json` `acceptance` in §1 and the browser check block in §15), KIT-331
  (the readiness rules and the planner's instructions), KIT-323 (the look tool), KIT-324 (the
  poller and its result block), KIT-325 (the bridge gate), KIT-326 (the installer step), and
  each project's own fake backend.
- **Date:** 2026-11-02
- **Builds on:** `2026-09-05-stage-e-under-a-delegation-bound-dispatcher.md` (sessions start
  from tickets, in the dispatcher's sandbox, and only there) and
  `2026-10-30-one-dm-tech-lead-voice.md` (what reaches the owner, and how).

## Context

The first feature to go through the whole pipeline, dark mode (2026-10-30), was planned, coded,
reviewed and merged in seven pieces. Every stage passed it. Then the tech lead asked the owner
to check it by hand. A hand check found nine problems that had shipped, and each was obvious in
a screenshot. Three reasons:

- No stage runs the app. Coding sessions run unit tests, lint and build. Stage E reads the diff.
  The tickets' "Manual" test steps were done by nobody.
- No stage looks at the whole feature. Each pull request is reviewed alone.
- The project deploys on every merge, so a problem found after the last merge is already live.

Facts measured on 2026-10-31 (KIT-302 research):

- Chromium cannot start inside a dispatched session's sandbox. It needs `mach-register` for
  `org.chromium.Chromium.MachPortRendezvousServer.*`, and no Claude Code sandbox setting grants
  it.
- Two seatbelt lines grant exactly that, and nothing else.
- Playwright can serve a built app from its folder (`page.route`) with no socket at all.
- `allowLocalBinding` would open every loopback service on the machine to the sandbox. It is
  never an option.

## Decision

**1. The check is a dispatcher session, started from a ticket.** No job starts an agent session
as the owner (the Stage E record). An *acceptance ticket*, delegated to the agent, carries the
plan, the pull-request head to build and the plan's checks.

**2. The browser is a tool with its own sandbox, not the session's shell.** An owner-installed
MCP server, the *look tool*, is listed in the acceptance entry's MCP config. That file is
root-owned and outside every worktree. The tool runs node, Playwright and the browser under the
kit's own seatbelt profile:

- no network;
- the role home's secrets unreadable;
- reads limited to the build folder, the project's `node_modules`, node and the browser bundle;
- writes only to a fresh temp folder;
- the two Chromium lines.

The browser bundle is installed ahead of time, root-owned. The project's fake backend runs
inside the same profile.

**3. The session builds, and the tool only renders.** The session builds the app in its own
worktree, under its normal sandbox, which allows the package registry. The tool never fetches.

**4. The tool keeps its own record.** It writes what ran and what failed outside the worktree.
The poller trusts that record for the mechanics, and the session only for what it judged on
screen. A skeptic pass tries to disprove each reported problem before anything is filed.

**5. The check runs before the last piece merges.** It starts when every other piece is Done
and the last piece's pull request is green and reviewed. It builds that pull request's head,
which is the whole feature.

**6. Severity decides, and only high holds.** Confirmed problems are filed as follow-ups through
the finding path. A high-severity one holds the plan open, and the owner hears why in the DM.

**7. A budget of about $25 per plan, until it is measured.** The dispatcher's lane has no hard
spend cap (KIT-114, accepted). So the cap is enforced two ways: the tool refuses runs past a
per-plan count, and the poller stops the ticket past a time limit. Both say so when they
trigger, and telemetry records the real cost.

**8. Off unless a project opts in.** A `delivery.json` without an `acceptance` section behaves
exactly as before. The section also tells planning to write checks a machine can run.

## Consequences

- A project that opts in owes a fake backend and its scenarios. That module belongs in its
  `autonomy.riskPaths`, so a session cannot change the fake it is judged against unseen.
- Plans get a `## How we will know it works` section with check blocks, and the readiness gate
  wants one on each UI ticket.
- One more session per plan, and one more owner install step (KIT-326).
- The look tool, its runner and its profile are guarded files.
- Not covered: native apps, and anything a fake backend cannot stand in for. Those plans say
  `No browser check:` and why.
