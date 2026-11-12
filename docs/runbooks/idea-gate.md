# Turn on the idea gate: setup runbook

*Written 2026-11-11, from the installer at that date. The dense reference is `docs/STAGE-A-OPERATOR.md`.*

Start at section 1 and type the one command. This runbook turns on the idea gate. You move an idea to **Plan it** on your project's work team, and a sandboxed session turns it into a proposed epic you approve. Read `docs/guides/idea-gate.md` to know what each part does. Read `docs/lessons/idea-gate.md` to understand it rather than run it.

### Words used here

| Word | Meaning |
| --- | --- |
| Session runner | The program on this Mac that starts a sandboxed AI session when a ticket is handed to its agent. |
| Review jobs | The background jobs that review pull requests. The idea gate runs beside them, under the same account. |
| Work team | Your project's own team in the tracker. Ideas and their planning tickets live on it. |
| Idea | A ticket you write on the work team, describing something you want planned. You never hand it to the agent. |
| Plan it | A to-do state on the work team. Moving an idea there is the only way to start a planning run. |
| Planner job | A program that runs every five minutes under the role account. It turns your move into a planning ticket and reads the answer back. |
| Role account | The local account the background jobs run as. It is `ROLE_ACCOUNT` in `stage-a.conf`. |
| Planning setup | The part of the session runner's settings that runs a planning ticket, read-only. One per repository. |
| Probe | Two test tickets per repository that the installer files and reads, to prove where planning tickets go and what the session may use. |
| Guard file | A file in a repository whose changes need your guard-change label before they merge. `delivery.json` is one. |
| `CA-LANE` | The name of the last sign-off: you saw the idea gate plan ideas correctly. |

---

## Before you start

You need all of these.

- [ ] The review jobs are installed on this Mac (`stage-e.conf` exists).
- [ ] `gh` is logged in as you: `gh auth status` says so.
- [ ] Each repository you want planned has a committed `delivery.json` that names its work team, and the session runner has a coding entry for it.
- [ ] Nothing the session runner works on is In Progress in the tracker. The install may restart it.

---

## 1. Run the one command

Type this yourself, from the kit checkout you install from. The macOS Terminal app works. So does the Terminal tab in Claude's desktop app.

```bash
python3 scripts/pipeline_install.py
```

- Good: it prints `== 0. Where this is running` and then `A terminal, as yourself. Good.`
- Not that: `Stopped: this shell belongs to a Claude session`. You typed it after `!` inside Claude. Open a Terminal tab and type it there.

**Why it refuses a Claude session.** It places a background job that files tickets, and it rewrites the session runner's settings. A session that could run it could install its own supervisor. So it refuses whenever it sees a Claude session, and there is no override.

It works through the list below and stops at the first thing that needs you. **After any stop, run the same command again.** It carries on from where it stopped.

---

## 2. Answer what it asks

| It asks | Answer |
| --- | --- |
| Write these settings to `stage-a.conf`? | `y`. On a first run it works out most values from `stage-e.conf` and asks only for the rest. |
| Turn on re-runs of a failed plan? | Your choice, asked once. `y` lets a failed plan run again up to twice, told why it failed. It is saved as `AUTO_REPLAN`. |
| Your Mac password | Type it. Asked once, near the start. |
| Replace the skills in your home folder? | `y`. It keeps the planning skill level with the kit. |
| A question about updating the review jobs | `y`, when nothing is In Progress. It moves the review jobs onto the code you merged. |
| The change to the repository's `delivery.json`: open it now? | `y`. It opens the pull request as you. See section 3. |
| The change to the session runner's settings: type yes | `yes`, the whole word. It backs the file up, writes the planning setup and restarts the session runner. |
| Run the probe now? | `y`. It files test tickets on the work team and closes them afterwards. |
| Does the session or helper list hold any tool other than these: …? | Only asked when a tool list came back but could not be read. Read that list in the output above. `y` if any name in it is not one it names. That stops the probe. |
| Sign the probe with the helper's tools unmeasured? | Only asked when no helper list came back. `y` signs, and the record says the helper was not measured. `n` signs nothing. |
| Is the team's agent guidance empty, or silent about how to plan or build? | Look first. See section 4. |
| Your initials | Two to four letters. They sign the probe. |
| Start the planner job now? | `y`. From then on it checks Plan it every five minutes. |
| Run the routing drill now? | `y` when you have ten minutes. See section 5. |
| Add the idea gate's note to Plan it? | Only on an older install. `y`. It labels the state as the gate's own. |

**It never asks for a tracker key by default.** It uses the key the review jobs already store. That key is yours, so the test tickets are filed and handed over as you. With `LINEAR_KEY_FILE=.stage-a/env` in `stage-a.conf` it asks once, at a hidden prompt, for a key of the planner's own.

---

## 3. Merge the repository's pull request

**You leave the terminal twice during the install:** here, and for section 4. The installer prints the pull request's address, then waits.

`delivery.json` is a guard file. So:

1. Open the pull request it printed.
2. Add the guard-change label yourself, if the checks ask for it.
3. Wait for the checks to go green, then merge it.

- Good: back in the terminal, within 15 seconds it prints `merged.` and carries on.
- Not that: `still not merged after 30 minutes`. Nothing is lost. Merge it, then run the one command again. It finds the same pull request.

**Before it opens the pull request,** it checks the change against the repository's own copy of the schema, `schemas/delivery.schema.json`. The repository's checks use that copy, not the kit's.

- Good: it shows the change and asks `Open it now?`.
- Not that: `<repo>'s own delivery schema would reject the change`, then card `CA-SCHEMA`. Nothing was opened. In that repository, bring `schemas/delivery.schema.json` and the first section of `docs/PIPELINE-CONTRACT.md` up to the kit's. `/sync-kit` there does it. Merge that, then run the one command again.

---

## 4. Read the team's agent guidance

The installer cannot read this, so it asks you. The guidance reaches every session on the team, planners included.

| Where | What |
| --- | --- |
| The tracker | Settings, Teams, your work team, Agents |

- Good: empty, or nothing about how to plan or build. Answer `y`.
- Not that: instructions such as "open a pull request". Answer `n`, clear them in the tracker, then run the one command again.

---

## 5. Prove it: the drill, then three ideas

**The routing drill is automatic.** It makes the session runner refuse one planning ticket on purpose, and checks that planning stops. Then it puts the setting back, restarts the session runner and runs the probe again, which lets planning start. It takes about ten minutes and asks for `yes` first. Run it later from the kit checkout with:

```bash
python3 scripts/pipeline_stage_a_setup.py drill
```

It files its idea in the work team's Plan it state, as you, and waits until the idea reads back as your creation. Only then does it wake the planner job.

- Good: `the drill passed: planning stopped, then started again after the probe.`
- Not that: `the drill FAILED: the planner job filed the planning ticket … and did not stop planning`. Do not plan real ideas yet. Read the job's log (section "If something goes wrong").
- Not that: `the drill could not test anything: …`. The job filed no planning ticket, so nothing was tested, and planning was not stopped. When the job answered the idea, the drill quotes it. Fix what that names, then run the drill again.

**Then three ideas by hand, all on the work team.** The `CA-LANE` card lists them, and all three come before you sign:

1. an idea whose text carries a routing tag for one of your coding repositories. The planning ticket must show a removed-tag mark, carry only the routing label, and have no project;
2. an idea carrying the orchestrator label. Its planning ticket must show the same;
3. one real idea, end to end. Read the epic it files.

Then sign:

```bash
python3 scripts/pipeline_stage_a_setup.py attest CA-LANE --initials <your initials> --note "<what each planning ticket showed; the epic id>"
```

- Good: `recorded CA-LANE, signed by <you>.`
- Not that: `that is the placeholder`. Type your own initials.

Run the one command once more. Good: its last line reads `The idea gate is installed, running and proven.`

---

## What you do, day to day

1. Write an idea on the work team. One or two paragraphs, in your own words.
2. Move it to **Plan it**.
3. Wait. Within a few minutes a note names the planning ticket doing the work.
4. Read the plan on that ticket when it arrives. Read the duplicate-check lines first.
5. Approve by moving the **epic** to the state your project maps to ready. Then hand each child to the agent yourself, after the ones it waits on. Or say `approve <epic id>` to your tech lead, and it does both in order.

Never hand an idea you moved to Plan it to the agent. That starts a coding session on the idea as written.

To plan an idea again, move it out of Plan it and back in, or say `replan <idea id>` to your tech lead. A plan that stands for the idea blocks this until you approve or cancel it.

---

## Keep it running

**Run the one command again whenever something changed.** It puts right what it finds. The page for this is `docs/runbooks/after-you-merge.md`.

| After this | What the one command does |
| --- | --- |
| You merged a kit change | Pulls it, updates the review jobs after a yes, then the idea gate. |
| You upgraded the session runner | Runs the probe again. The planner job plans on no other version. |
| Planning stopped | Runs the probe again. That is what clears the stop. Then move the idea out of Plan it and back in. |
| You re-ran the session runner's own setup tool | Puts the planning setup back into its settings. That tool rebuilds the list and drops it. |
| A kit change altered the planning setup | Asks for a new probe. `verify` says the setup has changed since the probe. |

To check without changing anything:

```bash
python3 scripts/pipeline_stage_a_setup.py verify
```

- Good: `No drift: every step still measures as done.`
- Not that: any row other than `DONE` or `ALREADY-DONE`. It names what is left; run the one command.

To replay what earlier runs recorded, run the same line with `status` in place of `verify`.

**Optional settings in `stage-a.conf`,** then run the one command:

| Setting | What it does |
| --- | --- |
| `TICKET_MODEL` | The model planning tickets and approved pieces run on. Set it before you move the session runner's default model. |
| `HOLD_FILE` | The watcher's hold file. While it exists, no planning run starts. Only you clear it. |
| `MAX_RUNS_PER_DAY` | Planning runs a day across every team. Default 10. |
| `AUTO_REPLAN` | Whether a failed plan runs again by itself. Default no. |

---

## Switch it off

Disable it as well as stopping it. A job that is only stopped starts again at the next boot. `<label>` is `JOB_LABEL` in `stage-a.conf`.

```bash
sudo launchctl disable system/<label> && sudo launchctl bootout system/<label>
```

- Good: `verify` then reports the job as not started, and it stays that way after a reboot.
- Not that: `Could not find service`. Look for the real label with `sudo launchctl list | grep planner`.

To start it again, run the one command and answer `y` to "Start the planner job now?".

That stops new planning runs. To stop the lane entirely, also remove the planning setups from the session runner's settings and restart it.

---

## If something goes wrong

The planner job's own files, read as the role account (`<role account>` is `ROLE_ACCOUNT` in `stage-a.conf`):

```bash
sudo -u <role account> cat ~<role account>/.stage-a/state/heartbeat.json
sudo -u <role account> tail -40 ~<role account>/.stage-a/poller.log
```

- Good: `"result": "ok"` in the heartbeat, and log lines ending in a `plan-poller:` summary.
- Not that: `"result": "error"`. The log's `FAIL:` lines say why. A stale time means the job is not running.

While planning is stopped, `~<role account>/.stage-a/state/stop.json` exists and holds the evidence. The heartbeat monitor reports a stale or failing heartbeat, once it watches the job.

| What you see | What it means | What to do |
| --- | --- | --- |
| The one command stops with a card | One thing only you can do | Do what the card says, then run it again |
| `a dispatcher restart … did not finish` | The session runner may be stopped | Run the `sudo launchctl bootstrap` command it prints, then run it again |
| `the dispatcher was restarted, and it has not answered` | It did not answer within a minute of its restart | Run the `sudo launchctl print` command it prints. If that shows it running, wait a minute and run the one command again |
| `the dispatcher is not answering … so no probe ticket was filed` | The session runner is still starting, or stopped | Follow its lines. When it says launchd holds it, wait a minute and run it again. Do not `bootstrap` a running one: that only prints `Bootstrap failed: 5` |
| `own delivery schema would reject the change` | The repository's copy of the schema is older than the kit's | See section 3. Nothing was opened |
| `the session's tools were not measured` | The session log could not be read, and the session named no tools | Nothing was signed. Read the reason in brackets, fix it, then run it again |
| `the planning entry … has changed since the probe` | A kit change altered the brief, the label or the fence | Run the one command. It probes again |
| `the drill could not test anything` | The job filed no planning ticket during the drill | See section 5. Planning was not stopped |
| `the review installer printed a card` | The review jobs wait on you | Do what the card says. If only a card is left and the jobs run the current code, the idea gate carries on |
| `Stopped: interrupted.` | You pressed Ctrl-C. The step you stopped finished its own clean-up first | Run the one command again when you are ready |
| `the probe failed` | A test ticket did not reach the planning setup | The lines under it say where it went. Fix that, then run it again |
| `holds a tool the fence should have removed` | A planning session could use something it must not | Do not start the job. Keep the output and ask for a kit change |
| An idea sits in Plan it, no note | The job is not running, cannot reach the tracker, or planning is blocked | Read the heartbeat, then the log |
| `Planning was not started` | Someone other than you moved it | Move it out of Plan it and back in yourself |
| `this looks like live work` | The ticket is handed over, has a session, a parent, children or a pull request | Copy the idea into a new ticket and move that one |
| `Planning waits until tomorrow` | The day's runs are used | Nothing. It starts after midnight UTC |
| `This planning run was cancelled` | Its route could not be confirmed, so all planning stopped | Run the one command. It probes again |
| A plan already stands for the idea | An earlier run's epic is not cancelled | Approve it or cancel it, then plan again |
| A plan rejected by the readiness check | A child failed the check | The rejection names each child. Add detail and plan again |
| `the watcher has held new starts` | The watcher's hold file exists | Read why. Clear it yourself with `scripts/pipeline_hold.py clear` |

---

## What is not proven

- **A real idea carried through every stop with a person watching is not recorded.** The drill and the probe have run; a full plan end to end, watched, has not been written down (KIT-164).
- **A helper session's tools are measured only when the log shows a helper started.** Otherwise the record says so (KIT-261).
- **The wait for the session runner after a restart is one minute.** A slower start stops the step and says how to look (no ticket yet).
- **A planning session may be able to read files in the role account's home.** The probe checks with a harmless test file and prints what it found (KIT-162).
- **The planner's key and the review jobs' key are the same key by default.** Revoking it stops both (no ticket yet).
- **The planner job is installed disabled and enabled at your yes.** That this survives a reboot is from launchd's documentation, not yet seen (no ticket yet).
- **A planning ticket that misses both routes runs in the coding setup for a few seconds** before it is cancelled (KIT-184).
- **That a planning stop reaches you as a message** has not been seen on a real stop (no ticket yet).
- **The team's agent guidance is checked only when the probe runs** (KIT-193).
- **The routing note's rendered shape** is read from the session runner's source. The probe is where anyone sees one (KIT-164).
