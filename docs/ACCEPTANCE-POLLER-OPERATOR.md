# The acceptance poller: when a check may start

*Slices 1 to 5 (KIT-551, KIT-552, KIT-553, KIT-554 and KIT-555, part of KIT-324). They decide, read, sift, write up the verdict and give the owner's page its facts. Nothing runs them yet.*

The acceptance check looks at a finished plan's app in a real browser, before the last piece
merges. The poller is the job that will start it. This first slice answers one question for each
plan: **may a check start now, and has it already started for this exact code?**

## The rule

A check may start for a plan when all of these are true:

1. Acceptance is on for the plan's repository.
2. Every piece but one is Done.
3. That one piece, the last, has a pull request that is **open**, **green** and **reviewed**.

"The last" is the one piece still open, wherever it sits in the plan's list. Pieces merge in any order.

Anything else is a named reason, never a silent no.

| Word | Meaning |
|---|---|
| `start` | All three are true, and no check has started for this head. |
| `acceptance-off` | The repository has no acceptance section. |
| `no-pieces` | The plan lists no pieces. |
| `pieces-pending` | More than one piece is not Done yet. |
| `all-done` | Every piece is Done. Nothing is left to check before merge. |
| `no-pr` | The last piece has no pull request yet. |
| `pr-not-open` | Its pull request is closed or merged. |
| `checks-red`, `checks-pending`, `checks-unknown` | Its required checks are not green. |
| `review-pending`, `review-declined`, `review-unknown` | Stage E has not cleared it. |
| `already-started` | A check already started for this head. |
| `start-unconfirmed` | A check was about to start for this head, and the job stopped before saying it did. |
| `start-failed` | Starting it failed. |
| `could-not-tell` | A fact is missing or unreadable. |

**Good:** `checks-unknown`. The job says it could not read the checks.
**Not:** treating an unreadable check as green.

## One check per head

The ledger keeps one row for each plan and **pull-request head**. A head that is in it never starts
again on its own. A push gives a new head, which is a new row, and a new check. Only your request
(`check <epic>`, below) runs a head again, in place of its row.

The row is written **before** anything starts, with a temp file, a sync and a rename. If the job
stops between the write and the start, the row says `starting`. That row never starts again by
itself, and the next pass says `start-unconfirmed`. This is on purpose: a missed check is a
sentence in the morning, and a check run twice is a bill.

If the ledger exists and cannot be read, the whole pass stops. Starting from an empty ledger would
start every plan again.

A pass that can start things holds the ledger's lock file for its whole run. A second pass at the same
time does nothing and says so, so two passes can never start the same head. The read-only command takes no lock.

## Reading what the check found

After a check runs, the poller reads two things and puts them together.

- **The session's result**: one block, `pipeline-acceptance/1` (contract section 21).
- **The look tool's own record**: what actually ran in the browser, and what failed. The tool writes it
  where the session cannot.

The poller trusts the record for what ran and what failed mechanically. It trusts the session only for
what it judged on screen. The answer is one of three words.

| Answer | When |
|---|---|
| `found-nothing` | The session found nothing, and the record confirms every run it names passed. |
| `found-problems` | The session listed problems. Or the record has a failed run the session did not report. |
| `could-not-check` | Anything below. It is never read as a pass. |

`could-not-check` comes from any of these:

- The block is missing, does not parse, names another schema, fails the schema, is about another plan or
  another head, or repeats a problem id.
- The look tool's record folder cannot be read, holds a malformed record, or holds no record at all.
- A run could not run, or a run crashed, and the session said it found nothing.
- The session says it ran a check that the record does not hold.
- The session says it could not check, and gives its reason.

A run that failed, or could not run, and was then **done again and passed**, is cleared: the newest
record for that run wins. A record that says it crashed, failed or could not run but names **no run** is
different. A later run cannot be tied to it, so it is not cleared. It blocks a `found-nothing` until a
person clears the plan's look-tool records. This is on purpose: the safe side of "never a pass".

**Good:** `could-not-check: the look tool has no record for this plan`.
**Not:** a session that says "all passed" with nothing recorded.

Where the two disagree, the answer says so, by name: *the session says it ran home/desktop/dark; the look
tool's record holds no such run.*

A record the tool marked as the runner's alone, or as a base copy, is **mechanical**. A record marked
`session-code-in-runner`, or with no mark, is **not mechanical**: the answer says so, because the pass
then rests partly on code the session could change.

The session's own words (a problem's `what`, a `reason`) come back as they are. They are data. Whoever
puts them in a comment, a page or a request cleans them first (slices 3 and 4).

## Try it

The command is read-only. It writes nothing.

```
python3 scripts/pipeline_acceptance_poller.py decide --facts facts.json --ledger ledger.json
```

`facts.json` is fields only: ticket ids, a repository name, piece states, a pull-request number and
head, and closed words for the checks and the review. No ticket or pull-request text goes in it, and
none comes out. Its shape is `pipeline-acceptance-facts/1`; a later slice writes it from the
tracker and GitHub.

```json
{"schema": "pipeline-acceptance-facts/1", "plans": [
  {"plan": "ENG-100", "repo": "acme/app", "acceptance": true,
   "pieces": [{"id": "ENG-101", "state": "done"},
              {"id": "ENG-102", "state": "review",
               "pr": {"number": 7, "head": "<40 hex characters>", "open": true,
                      "checks": "green", "review": "reviewed"}}]}]}
```

## Test it

```
npm run test:acceptance-poller
npm run test:acceptance-run
```

## The live pass: `run` (KIT-571)

`scripts/pipeline_acceptance_run.py` is the job that runs all of this for real. One pass, then exit, as the role
account. The installer (`pipeline_acceptance_setup.py`, below) gives it its config and its LaunchDaemon; until a person
runs it, nothing runs it.

```
python3 scripts/pipeline_acceptance_run.py run --config ~/.stage-e/acceptance.json --dry-run   # read it first
python3 scripts/pipeline_acceptance_run.py run --config ~/.stage-e/acceptance.json
```

**One pass does four things, in order.**

1. **It reads the facts.** The plans you approved, from the Slack bridge's own record of your approval. For each,
   the tracker gives its pieces and the pull requests GitHub's integration linked to them. GitHub gives the last
   piece's pull request, its full head and the check runs on it, and whether the repository's `delivery.json` has an
   `acceptance` section. Stage E's own record says whether that head was reviewed. Never the status file: it reports,
   and nothing may decide from it what to run.
2. **It starts a check** for each plan that may start, at most `max_starts_per_pass` (default 2) a pass. The ledger row
   goes on disk first. Then it files the acceptance ticket, not handed over yet. It writes the check's own look-tool
   config. Only then does it hand the ticket to the agent, which starts the session.
3. **It collects** each started check. The session's final message is read from its agent session, never from a
   comment. The ticket's description must be unchanged since it was filed. The result is judged as above, each
   follow-up request is posted on the plan's epic (contract §25), then the verdict, once per head. Then the ticket is
   closed, and its look config removed.
4. **It stops a check past its time limit** (`time_limit_minutes`, default 90). Its look config goes first, so the look
   tool refuses any further run. Its ticket moves to the team's canceled state. Its verdict is `could-not-check`, and
   says why.

**Each check has its own look config.** One server line in the acceptance entry's MCP config serves every check:
`pipeline_look.py --config-dir <look_config_dir>`. The pass writes `<look_config_dir>/<ticket>.json` before the
session starts, naming the session's worktree (`<workspace_dir>/<ticket>`), the plan, and the check's own record
folder (`<look_record_dir>/<ticket>`). The look tool takes the one config naming the folder it was started in. The
session cannot write that folder, and cannot choose the folder the dispatcher started it in.

**Good:** `TOD-100    started: OPS-12, delegated, for acme/app#7 at aaaaaaaaaaaa`.
**Not:** a check started from the status file. A session can write a ticket's title and labels; the status file only
reports them.

**The second pass is not wired yet.** Every problem goes on as one the second pass could not judge: it is filed,
marked "not confirmed by the second pass", and a high one still holds the plan. The session is told to try to disprove
each problem itself before it reports it.

**After the merge, a check of the default branch.** A plan whose pieces all merged before any check ran gets one, at
the head the default branch is at then. The bridge waits for a verdict before it closes a plan, so one must come. No
step from you.

**When you ask: `check <epic>`.** The Slack bridge posts a request on the epic (contract §26) when you send `check
<epic>` and say yes. The next pass runs the plan's check again, even at a head it checked before:

- after the merge, of the default branch at its head then;
- before it, of the last piece's pull request, at the head it was checked at. A head not checked yet is left to the
  usual rule: it starts once green and reviewed.

The re-run takes the place of the earlier check's row once that check is over and its ticket is closed. A request
that has to wait (the starts a pass may make, the ledger's lock, GitHub, a ticket not closed yet) is tried again at
the next pass, never dropped. Never while one of the plan's checks is running.

**Good:** `check TOD-100` after a could-not-check on a head nothing changed since. It runs again.
**Not:** sending it while the check runs. It waits, then runs once more after that check ends.

**Follow-ups wait for the finding poller.** The finding poller files only what the dispatcher's agent writes, so it
does not yet read the requests this pass posts. Until it does, they sit on the epic, marked, for a person to file.

### The config

| Key | Meaning |
|---|---|
| `state_dir` | The run state and heartbeat. Default `~/.stage-e/state`. |
| `ledger` | The poller's ledger. Default `<state_dir>/acceptance-ledger.json`, where the notifier reads it. |
| `bridge_state` | The Slack bridge's state file. Default `<state_dir>/bridge-state.json`. |
| `review_state_dir` | Stage E's state directory. Default `<state_dir>`. |
| `linear_key_env`, `github_token_env` | Names of the variables holding the owner's tracker key and a GitHub token. Never the values. |
| `team_key` | The team acceptance tickets are filed in. |
| `agent_user_id` | The dispatcher agent's tracker user id. |
| `entries` | `{"owner/repo": {"workspace_dir": "/abs"}}`: the dispatcher's `acceptance-<repo>` entry, and where its worktrees go. |
| `look_base_config` | The look tool's config, without `worktree`, `plan` or `record_dir`. |
| `look_config_dir`, `look_record_dir` | Where each check's config goes, and the look tool's record root. Both outside every worktree. |
| `time_limit_minutes` | Default 90. |
| `required_checks` | `{"owner/repo": [names]}`. Absent for a repository: every check run on the head must pass. |
| `max_starts_per_pass`, `run_timeout_seconds` | Defaults 2 and 300. |

Every problem in the config is named at once.

### Exit codes

| Exit | Meaning |
|---|---|
| 0 | Ran. A dry run, and a pass that found another pass running, are 0 too, and say so. |
| 1 | Could not do something it will retry: the tracker or GitHub could not be read. |
| 2 | Config or usage, or a missing credential. Nothing was touched. |
| 3 | Ran, and something could not be read or done. Each is named on a `NOT DONE:` line. |
| 4 | Ran past its wall clock. |

Every pass but a dry run leaves `<state_dir>/acceptance-heartbeat.json`: when, the exit, and the summary.

## The second pass, and what gets filed

The session lists the problems it saw. Before any is filed, a second pass tries to show each one is
**not real**.

- A problem the second pass **disproves** is not filed. It is listed in the verdict with its reason.
- A problem it **confirms** is filed.
- A problem it **could not judge** (the second pass failed, or gave no usable answer) is **not**
  disproved. It is filed, marked "not confirmed by the second pass". A broken checker never loses a real
  problem, and the mark says it was not checked.

**Severity is a proposal.** The session proposes. The second pass may lower it and never raise it. Only a
problem that survives and is still `high` holds the plan open.

**Each survivor becomes one request** for the finding path (`pipeline-finding/1`), never a ticket made
here. At most **three** per run, worst first. The rest are listed by id in the verdict, not dropped. A
request asks for no size, no track and no label, so the new ticket waits for a person.

A problem name used twice is **one** problem, at the worse severity. A problem that is not the shape the
contract gives (a severity that is not high, medium or low, a missing field) is listed as **malformed**,
not filed, and never stops the rest. Any malformed problem is a reason to say `could-not-check`.

**Everything the session wrote is cleaned** before it goes into a request or the verdict: no comment
opener, no fence, no @-name, no tilde, no square bracket (a routing tag looks like `[repo=...]`), no line break, at
most 300 characters.

**Good:** a request whose text cannot ping anyone or open a marker.
**Not:** the session's sentence copied into a comment as it came.

## The verdict on the epic

After a check, the poller writes **one comment on the plan's epic**. It says one of three words.

| Word | When |
|---|---|
| `passed` | Nothing is still high after the second pass, no run failed in the look tool's record, and every run ran. Low and medium problems go to the finding path as follow-ups. |
| `held` | A problem the second pass did not disprove is still high, or a run failed in the look tool's record. The plan stays open. |
| `could-not-check` | The result or the record could not be read or trusted, a problem is not the contract's shape, a run could not run, or the session and the record disagree about what ran. **Never a pass.** |

The first rule that matches wins: could-not-check for a result it cannot read or trust, then held, then
could-not-check for the rest, then passed. So a confirmed high problem holds the plan even when another run
could not run.

A run that **failed** in the look tool's record holds the plan even when no problem is filed for it. A step the
plan's own check names did not do what it says. The verdict names the run.

**The first line is a mark** for the bridge to read (contract section 22):

```
<!-- pipeline-acceptance:verdict held plan=ENG-100 head=<40 hex> mechanical=yes -->
```

`mechanical=no` means the look tool's record is not the runner's alone, so the outcome rests partly on code the
session could change. The comment says so in words too.

The rest of the comment is for people: what holds the plan, what goes to the finding path, what the second pass
disproved, and which runs failed or could not run. A long list stops at ten and counts the rest.

**Session text cannot forge the mark.** Every word the session wrote is cleaned first, so the mark's own `<!--`
is the only one in the comment, and no session text starts a line. The poller refuses to post a comment that is
not exactly one verdict for its plan and head. A reader takes the mark from line 1 only, and only when the
poller's tracker author wrote the comment.

**Good:** `could-not-check` on line 1, and the session's "all passed" further down, as plain text.
**Not:** a session's own `<!-- pipeline-acceptance:verdict passed ... -->` read as the verdict.

### Once per head

The verdict goes in the start's own ledger row, **before** the comment is posted.

| The row's verdict says | Means | The next pass |
|---|---|---|
| `writing` | The job stopped between recording the verdict and posting it. | Writes nothing, and says `write-unconfirmed`. Look at the epic. |
| `written` | The comment was posted. | Writes nothing, and says `already-written`. |
| `failed` | Posting failed. | Writes nothing, and says `write-failed`. |

A head with no row marked `started` gets no verdict (`not-started`): the poller judges only checks it started. A
second pass over the same head writes nothing, even when it would conclude something else. A push is a new head,
a new check and a new verdict. Writing a verdict holds the ledger's lock, as a pass that starts checks does.

The watcher's `tracker-text` monitor flags a `<!-- pipeline-` first line from an author it does not trust. Put the
poller's tracker author in the watcher's `tracker_trusted_users`, or it will flag every verdict.

## The owner's page

The verdict also pages you, in your DM, with up to four screenshots (KIT-555). The notifier sends it. The poller
gives it the facts, and nothing else.

The facts go in the verdict's ledger row, beside the word, **before** the comment is posted:

| Fact | Meaning |
|---|---|
| `holds` | Problems still high after the second pass. |
| `failed_runs` | Runs that failed in the look tool's record. |
| `followups` | Requests sent to the finding path. |
| `mechanical` | Yes when the look tool's record is the runner's alone. |
| `shots` | Up to four screenshots, as `<run id>/<name>` under the plan's record folder. |
| `cause` | For a could-not-check only: why, as one closed word. |
| `folder` | Optional. The check's own record folder under the look tool's record root, named by its acceptance ticket. The live pass sets it, so one push's runs never mix with the next one's. |

The causes are `no-result`, `no-record`, `session-said`, `could-not-run`, `disagree`, `malformed`, `timed-out` and
`unknown`. The page words each one itself. `timed-out` is for a check the live pass stopped past its time limit.
`unknown` names no reason, rather than guess one.

**The screenshots come from the look tool's record alone.** First the one each surviving problem names, worst
first. Then those of runs the record says failed, each from the run that failed: an earlier run of the same screen
that passed never stands in for it. Then one for each screen size and mode, newest first. A problem names its
screenshot by its step name. The poller only looks that name up among what the record kept. The session's words
never become a path.

**No session text.** Counts, flags and closed words only. Facts out of shape are refused before anything is written.

**Good:** `{"holds": 1, "failed_runs": 0, "followups": 1, "mechanical": true, "shots": ["001-20261110T120000Z/home--phone--dark--home.png"]}`.
**Not:** a problem's own sentence in the page. It is in the verdict comment, neutralized, and nowhere else.

The notifier's guide says how the page reads (`docs/NOTIFIER-OPERATOR.md`, *The acceptance check's page*).

## The installer: `pipeline_acceptance_setup.py` (KIT-326)

One command puts the acceptance check on a Mac. A person runs it, in a terminal. Every path and name comes from a conf
file, so a new machine installs the same way. Nothing in the kit is a fact of one Mac.

```
cp acceptance.conf.example acceptance.conf && chmod 600 acceptance.conf    # then edit it
python3 scripts/pipeline_acceptance_setup.py run --dry-run    # read it first; changes nothing
python3 scripts/pipeline_acceptance_setup.py run              # the only command that changes anything
python3 scripts/pipeline_acceptance_setup.py verify           # read-only, any time
```

**It puts five things in place, and `verify` checks each as its own row.**

| Row | What it is | It holds when |
|---|---|---|
| `browser` | Playwright (one exact version) and its Chromium, installed as root at the conf's two folders | Both folders are root's, and nothing in them is writable by group or others |
| `profile` | The look tool's seatbelt profile, `templates/sandbox/look.sb` | The role account's clone holds this checkout's copy, and `/usr/bin/sandbox-exec` is there |
| `mcp-config` | The acceptance session's MCP config: the look tool once, started with `--config-dir` | The file is exact, root:wheel, and not writable by others |
| `entry` | One `acceptance-<repo>` entry per repository, in the dispatcher's config | The entry is there as the conf produces it |
| `job` | The live pass's LaunchDaemon plist | The plist is installed and current |

Around them are four more rows: `preflight` (the clone, the env file's two names and the three selftests), `config` (the
live pass's config and the look tool's base config, mode 600), `dry-run` (the live pass once, starting nothing) and
`enable` (the job is loaded and runs this plist).

**What it does not do.** It never loads the job (card CK-A1: you read the dry run, then load it). It never restarts the
dispatcher. It never reads a credential, only the names of the two variables. A session cannot run it: under a model `run`
is refused before the conf is read, and `verify` only reads.

**The entry is copied from the repository's coding entry.** The clone, the branch, the workspace folder and the fence
(`disallowedTools`) are the coding entry's, because the acceptance session builds the app the way a coding session does.
It routes by a name only it answers to, `acceptance-<repo>`, and has no URL, team key or project key. It is refused when
another entry's name could answer to the same tag, since a session joined to two entries runs under only the tools both
deny. The other entries are never edited. The dispatcher's config is copied into the role account's home first, and it
is read as facts (it holds tracker tokens, and no token is printed).

**The browser is installed with the install scripts off.** npm runs as root with `--ignore-scripts`, so nothing in
Playwright's dependencies runs as root. The Chromium bundle comes from `playwright install` alone. Then both folders go to
root and close to group and others. A session that could write the browser could say any check passed.

**Good:** `verify` ends `DONE — every row holds`, and the five rows above read `ALREADY-DONE`.
**Not:** a browser folder you can write to. The `browser` row names the first file that is, and stops.

**Known gap.** Stage E's installer finds "the entry that manages a repository" among the dispatcher's entries and skips its
own `reviews-` entries and the idea gate's, but not these. Today it takes the first match in file order, which is the
coding entry, since these are added last. Teach it to skip `acceptance-` entries too if that order ever changes.

## What comes next

The bridge's gate, which waits for the verdict before it closes a plan, is its own piece (KIT-325). The finding poller
does not yet file the follow-ups this pass posts (KIT-607).
