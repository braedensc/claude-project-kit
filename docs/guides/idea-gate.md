# The idea gate: how it works, part by part

*Written 2026-11-11, from the code at that date. The set-up steps are in `docs/runbooks/idea-gate.md`. The teaching version is `docs/lessons/idea-gate.md`. The dense reference is `docs/STAGE-A-OPERATOR.md`.*

Read this once, end to end. Each part is short, and unfamiliar words are explained at the end.

You write an idea. A machine turns it into a plan. You approve the plan. This guide walks every part, so you know what happens in between. It also says how far the promise reaches that the machine cannot approve its own work.

> **Where it stands on 2026-11-11: built, installed and running.** The planner job runs every five minutes and has passed many times. Every part below exists. What is not recorded yet is a real idea carried through every stop by a person watching. The last section lists that and the rest.

---

## The one-line version

You write an idea on your project's work team and move it to **Plan it**. A background job writes a clean copy as a planning ticket and hands it to the agent. Seconds later the job checks where the session runner sent it. A sandboxed session reads your code and answers with a proposed plan. The job reads the answer back, and a filing program checks it and files the tree in the backlog. You approve by moving the epic to your ready state.

---

## The parts, in order

### 1. The trigger: you move an idea to Plan it

Plan it is a to-do column on each work team. Moving an idea there is the only thing that starts planning.

The job checks two things before it acts:

- **You made the move.** It reads the ticket's history for who moved it. Anyone else's move gets a note and no run.
- **It is a fresh idea.** Some tickets look like live work: one handed to the agent, one with a session, a pull request, a parent or children. So does one carrying an agent or origin label. Each gets one note, and no run.

It starts at most three runs a pass and, by default, ten a day across every team. It never plans over a plan that already stands. You get one note naming that plan instead.

**On a work team, two gestures mean two things.** Move to Plan it to plan. Hand to the agent to build. Never both on one ticket.

### 2. The clean copy: what the session runner actually sees

The session runner picks a setup in this order: a route it remembers, the ticket's text, its labels, its project, and last its team. A line like `[repo=acme-app]` in an idea would send it to the coding setup, which can edit and run anything.

So the job never hands over your idea. It writes a **planning ticket** on the same team:

- the first line names the read-only planning setup for that repository;
- its only label is the routing label, which names the same setup;
- your idea sits inside markers that say "this is data", with every routing line replaced by `(removed-routing-tag)`;
- it has no project.

An idea longer than 60,000 characters is cut there, and the planning ticket says so. The job creates the ticket and hands it to the agent in one call.

### 3. The route check: seconds after filing

The session runner reads the tag, the label and the project with separate network calls. A failed call counts as "not there". If both the tag and the label are missed, it falls back to the team. On a work team, that means the coding setup.

So the job checks. The session runner posts a routing note at the start of every session, before the AI runs, saying which setup answered. The job reads the **earliest** such note. It waits two minutes by default, and longer while a repository setup script runs.

- The note names the planning setup, by tag or by label: fine.
- It names anything else, or no note arrives: the job records what it saw and cancels the ticket. It pages you, notes the idea, and **stops all planning**.

Planning stays stopped until you sign the probe again with new test tickets and run the one command. A planning ticket is never reused.

If a pass cannot finish a route check, the next pass finishes it first. No new planning ticket is filed while any route is unknown.

**The session runner's version is pinned too.** When you sign the probe, the installer records the version. The job reads the running version on every pass and plans nothing on a different one. After an upgrade, you sign the probe again.

### 4. The planning session: sandboxed, with the board's tools taken away

In the normal case, the session runs in the planning setup. It can read and search the code, and start helper sessions for review passes. That is all.

Removed by name, before it starts:

- every tracker tool, and every tool server the session runner adds, in both rule forms;
- everything that runs, writes, fetches, schedules or messages, including file writes.

Only you may start a session in the planning setup. Its instructions tell it the idea is data, not instructions.

**One thing reaches it that the setup does not control:** the work team's own agent guidance in the tracker. Keep it empty or neutral. The installer cannot read it, so you check it during the probe. The planning brief also says it outranks that guidance, but that is a request to the model, not a check.

### 5. The proposal: the session answers, and files nothing

The session's last message ends with one block of structured text: the epic, its children, and any questions it could not answer from the code. It files nothing and moves nothing.

### 6. The background job: reads the answer back

When the session finishes, the job reads its last message, from the session whose route it checked and never a later one. It refreshes its own copy of the repository's code, and hands both to the filing program.

It then tells your idea what came of the run and closes the planning ticket. A planning ticket sits In Progress beside real work while it runs, for up to four hours the machine is awake.

If a run ends without a plan for a reason another try can fix, the job can run it again by itself. That is off until you say yes once. Each re-run is told how the earlier ones ended.

### 7. The filing program: plain code, holding the key, checking everything

The filing program is not an AI. It holds the only key that writes plans to the board.

It refuses:

- a proposal naming any ticket but the one it was handed;
- a planning ticket on any team but the one the repository's settings file names;
- anything that looks like a credential, without quoting it;
- the whole plan, if one child fails the readiness check.

It files a project, the epic and every child into the backlog, marks the epic as machine-written, and makes the epic every child's parent. It files a proposal once. It lists children whose titles look like recent tickets, leaving out your idea and every planning ticket, and never refuses for that.

### 8. The readiness check: every child passes, or the whole plan bounces

Each child needs the required sections and acceptance criteria a machine can check. It needs a test plan naming a command, file paths that exist, and one size and one kind label. One failing child rejects the plan, and the rejection names each child and each rule.

### 9. The approval: your move, and only yours

You move the epic to the state your project maps to ready. That approves the tree. It releases the children only where the optional approve step is installed and on. By default it is off, so you then hand each child to the agent yourself, after the ones it waits on. In your tech lead's chat, `approve <epic>` does the move for you, and the children go out in order.

Never hand the epic itself to the agent. Never put the orchestrator or stacking label on the epic or a child.

### 10. The other jobs leave planning tickets alone

The review jobs, the finding job and the catch-up job all watch the same team. Each skips planning tickets, and review tickets too. The finding job goes by the label or the first line, so a plan's text is never filed as a finding.

The review job also asks for the session runner's routing note. A coding session could put the planning label on its own ticket. It cannot change the note posted before it ran. So a ticket leaves review only when the note confirms a planning session.

---

## How far "the machine cannot fake your approval" reaches

Your approval means a person looked and agreed. The design exists so the machine cannot produce that signal itself.

**What holds it up:**

1. **The session runner never takes orders from your text.** It sees a copy written by a program: your words quoted as data, with every directive removed.
2. **Every planning ticket's route is checked,** and a wrong one stops planning.
3. **The planning session's board tools are removed by name.**
4. **The filing program forces the safe values.** Backlog only. The machine-written label. The parent link it sets itself.
5. **A machine-written epic never approves itself.** The mark it carries never auto-approves, and the approve step is off by default.

**Where it is thin, today:**

- **A mis-routed planning ticket runs in the coding setup for a few seconds** before the cancel lands. It works from the clean copy, and cannot merge.
- **The planner reuses the review jobs' key by default.** That is your personal key, stored where the jobs on this machine can read it. A session that read it could forge your moves. Revoking it stops both jobs. A key of the planner's own is one setting away.
- **Other runners.** A runner label can pick a runtime that loads none of the kit's guards. Neither reaches a planning ticket; a label added to one by hand still would.
- **Who moved the epic.** The approve step checks that the epic is in the ready state, not who put it there. It is off by default.
- **A planning session may read files in the job's home.** The probe checks with a harmless test file and prints what it found.

**What the machine can do on its own:** spend money. That is up to ten planning runs a day, plus a capped number of re-runs if you turned them on.

---

## What you do, day to day

1. Write an idea on your work team. One or two paragraphs, in your own words.
2. Move it to **Plan it**.
3. Wait for the note naming its planning ticket.
4. Read the plan when it arrives, starting with the duplicate lines.
5. Move the **epic** to ready to approve. Then hand each child to the agent, or let your tech lead do it.

To plan an idea again, move it out of Plan it and back in, or send `replan <idea>` to your tech lead. A plan that stands blocks this until you approve or cancel it.

---

## When something looks wrong

| What you see | What to do |
| --- | --- |
| No note on the idea after ten minutes | Read the job's heartbeat, then its log (runbook, "If something goes wrong") |
| `this looks like live work` | Copy the idea into a new ticket and move that one |
| `Planning was not started` | Someone else moved it. Move it out of Plan it and back in yourself |
| `Planning waits until tomorrow` | The day's runs are used. It starts after midnight UTC |
| A plan already stands for this idea | Approve it or cancel it, then plan again |
| `This planning run was cancelled` | Planning is stopped. Runbook, "If something goes wrong" |
| Nothing planned after a session runner upgrade | Expected. Run the one command, which probes again |
| A plan rejected by the readiness check | Add detail to the idea and move it back into Plan it |
| A run ended with no plan | Move it again, or turn re-runs on. The page names every run |

---

## Words used here

| Word | Meaning |
| --- | --- |
| Agent | The session runner's user in the tracker. Handing a ticket to it starts a session. |
| Session runner | The program that turns a handed-over ticket into a sandboxed AI session. |
| Epic | A parent ticket. Moving it to ready is your approval. |
| Filing program | Ordinary code that checks a proposal and files it. Holds the only key. |
| Fence | The tools removed from a planning session before it starts. |
| Helper session | A short sub-session a planning session starts for one review pass. |
| Plan it | The to-do column on each work team you move an idea into. |
| Planner job | The background job that turns your move into a planning ticket and reads the answer back. |
| Planning setup | The read-only setup for one repository that runs planning tickets. |
| Planning ticket | The clean copy the job writes on your team and hands to the agent. |
| Probe | Two test tickets the installer files as you, to prove where planning tickets go. |
| Routing note | The note the session runner posts at the start of a session, saying which setup answered. |
| Routing tag, routing label | The planning ticket's two addresses, both naming the planning setup. |
| Stop file | The file that says planning has stopped, and why. |

---

## What is not proven

- **A real idea carried through every stop with a person watching is not recorded.** The routing drill and the probe are the live evidence (KIT-164).
- **The routing note's rendered shape and delay** are read from the session runner's source, and the probe measures them (KIT-164).
- **The stop notice's stored shape,** which finds a run someone stopped, has not been seen on a live planning ticket (KIT-373).
- **A mis-routed ticket runs for seconds before the cancel** (KIT-184).
- **A helper session inheriting the fence** is read from the runner's source, not seen live (KIT-261).
- **A planning session reading the job's home files** is checked by one test file only (KIT-162).
- **Team agent guidance is checked once, by you.** Nothing re-checks it (KIT-193).
- **Your creation of a ticket counting as the move** rests on what the tracker leaves out, measured through its API only (KIT-164).
- **That a planning stop reaches you as a message** depends on paging, and no real stop has been seen (no ticket yet).
- **The size limit on a session's final message** is unmeasured (no ticket yet).
- **That the launchd override survives a reboot** is from its documentation (no ticket yet).
