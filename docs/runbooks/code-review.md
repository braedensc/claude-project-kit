# Turn on code review and its jobs: setup runbook

*Written 2026-11-11, from the installer at that date. The dense reference is `docs/STAGE-E-OPERATOR.md`.*

Start at section 1 and copy the settings file. This runbook turns on review of every pull request a session opens, the fix rounds and six smaller jobs. Read `docs/guides/code-review.md` to know what each part does. Read `docs/lessons/code-review.md` to understand it rather than run it.

### Words used here

| Word | Meaning |
| --- | --- |
| Session runner | The program on this Mac that starts a sandboxed AI session when a ticket is handed to its agent. |
| Role account | The local account the background jobs run as. It is `ROLE_ACCOUNT` in `stage-e.conf`. |
| Review job | Files a review ticket per pull request and posts one comment. Runs every 120 seconds by default. |
| Fix-round job | Sends findings back to the coding session and hands the ticket over. Runs every 120 seconds by default. |
| Side-findings job | Files a session's out-of-scope findings as backlog tickets. Runs every 300 seconds. |
| Catch-up job | Re-sends a hand-off the session runner missed. Runs every 900 seconds. |
| Status job | Writes one file of where every plan stands. Runs every 900 seconds. |
| Heartbeat monitor | Reads the other jobs' heartbeats and comments on one ticket. Runs every 1800 seconds. |
| Conflict fixer | A job under your own login that fixes your local pull requests when they conflict. |
| Work team | Your project's own team in the tracker. Review tickets are filed on it. |
| Card | A numbered stop, such as `CK-5`. The installer prints what to do, then waits. |
| `<label>` | A job's launchd name. The prefix is `DISPATCHER_SERVICE` in `stage-e.conf`. |

---

## Before you start

You need all of these.

- [ ] The session runner is installed, running and answering on this Mac.
- [ ] `gh` is logged in as you: `gh auth status` says so.
- [ ] Each work team exists in the tracker, and the session runner's agent is a member.
- [ ] Each reviewed repository has a committed `delivery.json` if you want fix rounds. Without one, you get review comments only.
- [ ] Nothing the session runner works on is In Progress. The first install restarts it.
- [ ] You have two credentials ready: a tracker key and a GitHub token. The installer asks for both at a hidden prompt.

---

## 1. Copy the settings and fill them in

Type this yourself, in a terminal, from the kit checkout you install from. The macOS Terminal app works. So does the Terminal tab in Claude's desktop app.

```bash
cp stage-e.conf.example stage-e.conf && chmod 600 stage-e.conf
```

- Good: `stage-e.conf` now exists beside the example.
- Not that: `cp: stage-e.conf: Permission denied`. You are in the wrong folder. Change to the kit checkout.

Open `stage-e.conf` in your editor. The keys that must be right:

| Key | What to put |
| --- | --- |
| `ROLE_ACCOUNT` | The local account the session runner runs as. |
| `DISPATCHER_SERVICE` | The session runner's launchd name. The job names start with it. |
| `DISPATCHER_CONFIG` | The path to the session runner's own settings file. |
| `KIT_REPO_URL` | The address of the kit repository the jobs run from. |
| `MANAGED_TEAM_KEYS` | The tracker team keys of your work teams, comma separated. No default. |
| `REVIEW_REPOS` | The repositories to review, as `owner/name`. |
| `OWNER_LINEAR_EMAIL` | Your tracker email. Review tickets are handed over as you. |
| `HEARTBEAT_MONITOR_TICKET` | A ticket nobody delegates, which stays open. Or `off`. |

The rest have working defaults. `POLL_INTERVAL_SECONDS` and `BOUNCE_INTERVAL_SECONDS` are 120. `FINDING_INTERVAL_SECONDS` is 300. `CATCHUP_INTERVAL_SECONDS` and `STATUS_INTERVAL_SECONDS` are 900. `MONITOR_INTERVAL_SECONDS` is 1800.

You can decline a job by name: `CATCHUP_INTERVAL_SECONDS=off`, `STATUS_INTERVAL_SECONDS=off`, `HEARTBEAT_MONITOR_TICKET=off` or `CONFLICT_WAKER_CONF=off`.

---

## 2. Rehearse first

A dry run changes nothing: not a file, not a job, not a tracker object.

```bash
python3 scripts/pipeline_stage_e_setup.py run --dry-run
```

- Good: a table of steps, each `DONE`, `ALREADY-DONE` or `WOULD-CHANGE`. Under `dispatcher-entry`, `WOULD-CHANGE` means a session runner restart is coming.
- Not that: `Stopped: this shell belongs to a Claude session`. You typed it after `!` inside Claude. Open a Terminal tab and type it there.

**Why it refuses a Claude session.** It installs background jobs and rewrites the session runner's settings. A session that could run it could install its own supervisor. There is no override.

---

## 3. Run the one command

Run it when no coding session is in flight. Writing the review settings restarts the session runner, and a restart ends every session mid-work.

```bash
python3 scripts/pipeline_stage_e_setup.py run
```

It does every step a computer can do, in order, and stops at the first card only a person can clear. It has 16 steps. Your Mac password is asked once for the system jobs.

- Good: it prints a numbered card and exits 10. Do what the card says, then run the same command again. It carries on.
- Not that: `FAILED`. The row names what failed. Fix it, then run the same command again.

To keep this installer level with the idea gate and the skills, run `python3 scripts/pipeline_install.py` instead. It runs this installer only when something is outstanding and you say yes.

### The cards you may meet, roughly in order

| Card | What it wants from you |
| --- | --- |
| `CK-1` | Merge the pull requests that carry the review layer. You add the guard-change label yourself. |
| `CK-2` | Paste the tracker key and the GitHub token at a hidden prompt. |
| `CK-4` | Add the session runner's agent to the work team it names. |
| `CK-13` | Let the reviews in flight finish, then run again. The session runner restarts on a routing change. |
| `CK-5` | Read the dry-run count, then sign it. It is the number of paid reviews about to start. |
| `CK-6` | Name the required checks, if GitHub would not tell the job. |
| `CK-7` | Watch one real ticket become a reviewed pull request, then probe the fence. |
| `CK-11` | Read the catch-up job's first count, then sign it. |
| `CK-12` | Only if the status job's dry run could not read the tracker. |
| `CK-9` | Name the ticket the heartbeat monitor comments on. |
| `CK-8` | Read the conflict fixer's dry run, then sign it. |
| `CK-10` | Write the root-owned lane marker. |

Print any card in full at any time:

```bash
python3 scripts/pipeline_stage_e_setup.py card CK-5
```

- Good: the card's title, why it exists and the exact steps.
- Not that: `unknown card`. Cards are `CK-` and a number from the table.

---

## 4. Sign the review count

The first real pass opens a review ticket for every eligible pull request it finds. Each one is a paid session and a public comment. The count is the only gate on that bill.

Read the count the installer printed. Subtract any `NOT REVIEWED` line, since a declined pull request costs no session. If the number is too big, put one repository in `REVIEW_REPOS` and run again.

```bash
python3 scripts/pipeline_stage_e_setup.py attest A-DRY-RUN --initials <your initials> --note "count read: <N>"
```

- Good: `recorded: A-DRY-RUN` then your initials, the time and your note. The next run loads the jobs and waits for their heartbeats.
- Not that: `is the placeholder this file prints, not a person`. Type your own two to four letters, and the real count.

---

## 5. Watch one real ticket end to end

Card `CK-7` is the proof that the chain works. Delegate one real ticket and leave the loop alone for a full cycle. Watch for these, in order:

1. A worktree for the coding ticket, and a pull request.
2. A review ticket on the same work team, with a label `reviews-<repo name>`, handed to the agent.
3. A reply on it carrying one fenced `pipeline-review/1` block.
4. One comment on the pull request, with a basis line.
5. The review ticket closed, and only its own worktree gone.

Then probe the fence, as the card describes. The sign-off is tied to the exact fence you probed. Change the fence later and the card comes back.

- Good: after you sign, the next run ends with the handover step done.
- Not that: a review comment from a reviewer that ran a command. Do not sign. Keep the output and ask for a kit change.

---

## 6. Optional: reviews under their own login

This is built and off. Without it, reviews post under your login, which any session on this Mac can also use. Run it as yourself.

```bash
python3 scripts/pipeline_stage_e_app.py run --conf stage-e.conf --test-pr <owner>/<name>#<number>
```

It asks for one typed `yes`. It opens GitHub's page to create the app. You click GitHub's own Create button. Then it opens the install page, where you pick the reviewed repositories. It posts one test comment as the app and reads it back.

- Good: it adds `GITHUB_APP_ID`, `GITHUB_APP_LOGIN` and `GITHUB_APP_KEY_FILE` to `stage-e.conf`. Run the main installer once more.
- Not that: `github-app ALREADY-DONE OFF by conf`. Reviews still post under your login. The three keys are empty.

Check it any time, read-only:

```bash
python3 scripts/pipeline_stage_e_app.py verify --conf stage-e.conf
```

- Good: it reports the key, the app, each installation and the test comment as present.
- Not that: a missing installation on a repository. Install the app there on GitHub, then verify again.

---

## What you do, day to day

1. Put the ticket id in every branch name you want reviewed.
2. Write acceptance criteria as checklist items under a `## Acceptance criteria` heading.
3. Delegate the ticket. Read the review comment, including the summary of a clean one.
4. When the ticket reaches your approval column, read the pull request, then merge or do not.
5. Once a day, look at the monitor's heartbeat. Silence on its ticket is not health.

You merge. No job here can.

---

## Keep it running

**Run the one command again whenever something changed.** It puts right what it finds. The page for this is `docs/runbooks/after-you-merge.md`.

| After this | What the one command does |
| --- | --- |
| You merged a kit change | Moves the role account's clone, stopping the jobs around it, then reloads them. |
| You changed `stage-e.conf` | Rewrites the job configs and reloads what changed. |
| The session runner was upgraded | Asks for the routing probe again. Reviews pause until you sign it. |
| A review was paused after a stop | Offers the routing probe. Only a sign-off dated after the stop clears it. |
| You re-ran the session runner's own setup tool | Puts the review entries back into its settings. |

To check without changing anything:

```bash
python3 scripts/pipeline_stage_e_setup.py verify
```

- Good: every row is `DONE` or `ALREADY-DONE`, and it exits 0.
- Not that: any other row. It names what is left. Run the one command.

`verify` re-measures the machine. `status` only replays what earlier runs recorded, so a drifted machine can still read clean there. Trust `verify`.

---

## Switch it off

Disable a job as well as stopping it. A job that is only stopped starts again at the next boot. Each system job has its own label. They end in `stage-e-poller`, `-bounce`, `-finding`, `-catchup`, `-status` and `-monitor`.

```bash
sudo launchctl disable system/<label> && sudo launchctl bootout system/<label>
```

- Good: `verify` reports the job as not started, and it stays that way after a reboot.
- Not that: `Could not find service`. Look for the real label with `sudo launchctl list | grep stage-e`.

Turn off the monitor too if you turn off the rest. A monitor left running pages you about a shutdown you made on purpose.

Your conflict fixer is a login job, so it has no `sudo`:

```bash
python3 scripts/pipeline_conflict_waker_setup.py status
```

- Good: it prints the last heartbeat, fresh or `STALE`.
- Not that: `NOT RUNNING`. Run `python3 scripts/pipeline_conflict_waker_setup.py run`, or set `CONFLICT_WAKER_CONF=off` to decline it.

---

## If something goes wrong

Each job's own files, read as the role account. `<role account>` is `ROLE_ACCOUNT` in `stage-e.conf`.

```bash
sudo -u <role account> cat ~<role account>/.stage-e/state/heartbeat.json
sudo -u <role account> tail -40 ~<role account>/.stage-e/poller.log
```

- Good: `"result": "ok"` in the heartbeat, and a recent time.
- Not that: `"result": "error"`. The log's `FAIL:` lines say why. A stale time means the job is not running.

The other jobs keep their files beside it, under `~<role account>/.stage-e/`:

| Job | Heartbeat | Log |
| --- | --- | --- |
| Review job | `state/heartbeat.json` | `poller.log` |
| Fix-round job | `state/bounce-heartbeat.json` | `bounce.log` |
| Side-findings job | `finding/heartbeat.json` | `finding/poller.log` |
| Catch-up job | `catchup/heartbeat.json` | `catchup/poller.log` |
| Status job | `status/pipeline-status.json` (its `written_at`) | `status/job.log` |
| Heartbeat monitor | `state/monitor-heartbeat.json` | `monitor.log` |

Two files you must not delete. The fix-round job's ledger is its budget record. The side-findings job's `seen.json` is its record of what it has filed. Deleting either can repeat work.

| What you see | What it means | What to do |
| --- | --- | --- |
| The one command stops with a card | One thing only you can do | Do what the card says, then run it again |
| No review comment after ten minutes | The review job is not running, or reviews are paused | Read its heartbeat, then its log |
| "reviews are paused" on a pull request | The route check failed, or the session runner's version changed | Run the one command. It offers the routing probe. Sign it |
| `A JOB THAT RUNS FROM THE CLONE IS OFF` | A job was stopped for a code move and not loaded again | Run the `bootstrap` line it prints, or the `disable` line to keep it off |
| `code FAILED … launchd still holds it` | A job would not stop | Run the `bootout` line it prints, then the one command |
| A pull request gets "will NOT be reviewed" | The branch names no ticket | Rename the branch to carry the ticket id |
| "NOT reviewed" with a reason | No criteria, or criteria under the wrong heading | Fix the ticket. The job reviews again when the criteria change |
| No fix round after a serious finding | The repository has no `delivery.json`, or the review is of an older commit | Add the file, or wait for the re-review |
| `agent:needs-human` on a ticket | The budget is spent, or no push followed a fix round for six hours | Read the pull request and decide |
| A pull request stays labelled `conflict` | Nothing answered the fix request | Check the conflict fixer's heartbeat. Is your login active? |
| The conflict page says "Nobody was paged" | An organisation repository with a bot author and no page recipient | Set the `PR_CONFLICT_PAGE_TO` Actions variable to your login |
| The conflict fixer's heartbeat is `STALE` | You were logged out, or the job stopped | Log in. Then run its `verify` |
| A delegated ticket never started | The Mac slept past the tracker's retries | Wait for the catch-up job (7 hours 30 minutes), or re-delegate |
| The catch-up job wants a signed count | Its first pass would start every old unanswered hand-off | Read the list, remove ones you do not want, then sign |
| The tech lead gives stale plan facts | The status job stopped, or its source was unreadable | Read the file's `written_at`, then the job's log |
| A page that a job is stale | The monitor judged it | Read that job's heartbeat. Stale means not running; `error` means ran and could not |
| `N review tickets could not be closed` | A review ticket will not close, so a worktree stays | Close the ticket by hand in the tracker |
| Your login still shows on reviews | The app step is off or its token failed | Run the app's `verify` |

---

## What is not proven

- **A spent budget, with its label and both comments,** has run only in tests (no ticket yet).
- **A fix round caused by a red check** rather than a finding has run only in tests (no ticket yet).
- **The fallback fix ticket** has never been needed live (no ticket yet).
- **The criteria snapshot pass** has not been watched live (no ticket yet).
- **Either conflict lane** has never fired on a real pull request (no ticket yet).
- **A real side finding turned into a real ticket** has not been seen (no ticket yet).
- **A real lost hand-off recovered by the catch-up job** has not been seen (KIT-268).
- **The app step against a live app** has not been run since it was built (no ticket yet).
- **A watcher off this Mac** does not exist, so a sleeping Mac pages nobody (KIT-45).
- **Whether the jobs come back after a reboot** is from launchd's documentation, not seen here (no ticket yet).
