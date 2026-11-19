# The validator

The validator approves in your place, inside limits that code enforces. It may approve three
things: a plan, a piece starting, and a merge. Three layers must all agree first: hard limits in
code, the evidence, and one independent read-only agent. Anything missing, unsure or unreadable
holds. The design is `docs/adr/2026-11-05-the-validator-approves-inside-limits-that-code-enforces.md`.

## 1. The switches

What the validator may approve is plain on/off settings, in one file. There is no trust ladder.
Nothing gains or loses trust by itself: a switch changes only when you change it.

| Setting | On means | Off means |
|---|---|---|
| `approve.plans` | It may approve a plan, in place of `approve <plan>`. | You approve each plan. |
| `approve.pieces` | It may approve a ready piece of an approved plan starting. | You start each piece. |
| `approve.merges` | It may ask GitHub to merge a pull request, in a repository in `merge_repos`. | You merge each pull request. |
| `merge_repos` | The repositories where merges may be approved, as `owner/name`. | `[]`: none. No merge is approved anywhere. |
| `limits.max_changed_lines` | The most lines added plus removed it may approve. | `0`: nothing passes. |
| `limits.max_files` | The most files changed it may approve. | `0`: nothing passes. |
| `allow.credentials` | It may approve a change to env files, keys, tokens or secrets stores. | Those stay yours. |
| `allow.billing_and_accounts` | It may approve a change to billing, accounts, or the plan. | Those stay yours. |
| `allow.production_deploy` | It may approve a change to production deploy settings. | Those stay yours. |
| `allow.deleting_data` | It may approve a change that deletes data. | Those stay yours. |

**The safety system is never a switch.** Changes to hooks, guards, CI and workflows, branch
protection and rulesets, settings, the watcher and the validator's own limits always stay yours.
That is fixed in code. A settings file that names one, even switched off, is refused.

Good: `"merges": false`, and you merge by hand. Not that: `"hooks": true` — that file is refused,
and every switch reads off.

No agent ever merges, approves a review, or applies a protected label. A merge is GitHub's own
auto-merge, which a job switches on.

### The file

It is JSON, contract §23. Start from `validator-settings.example.json`, which has every switch off,
every limit `0` and no repository listed:

```json
{
  "schema": "pipeline-validator-settings/2",
  "approve": {"plans": false, "pieces": false, "merges": false},
  "limits": {"max_changed_lines": 0, "max_files": 0},
  "allow": {"credentials": false, "billing_and_accounts": false,
            "production_deploy": false, "deleting_data": false},
  "stage_e_login": null,
  "merge_repos": []
}
```

Every key must be there. Each switch is `true` or `false`. Each limit is a whole number.
`stage_e_login` is Stage E's own bot login, or `null` until Stage E has one. Section 3 says where
it comes from. `merge_repos` lists repositories, `[]` for none.

Keep it in your own home, outside every repository. Good: `~/.validator/settings.json`, mode 600.
Not that: a file inside a checkout. A session can write there, so the file is not obeyed.

The install (section 12) writes this file for you, once, with every switch off. It never writes it
again, so no update can turn a switch on.

### When every switch reads off

Absent is every switch off. So is any file the validator cannot trust. It never means on:

- the file is not valid JSON, or gives a key twice;
- it names another schema, misses a key, or adds one;
- it is the first version, `pipeline-validator-settings/1`, from before `merge_repos`. Write it
  again as `/2`, with the list;
- `merge_repos` has an entry that is not `owner/name` in lower case, names a repository twice,
  or names more than 100;
- a switch is not `true` or `false`, a limit is out of range, or `stage_e_login` is not a bot
  login;
- it names a part of the safety system;
- it cannot be read, or is larger than a settings file;
- it is inside a git working tree, is a link, is writable by group or other, is owned by another
  account, or sits in or under a folder others can write.

Each says why, in words.

### Check what it reads

```bash
python3 scripts/pipeline_validator_settings.py show --settings-file ~/.validator/settings.json
```

It prints every switch, every limit, Stage E's login and the repositories for merges, and why when
they are off. It changes nothing.

Good: `read: the validator may approve only what is on below`. Not that: `EVERY SWITCH IS OFF
(untrusted): the settings file is inside a git working tree, where a session can write, so it is
not obeyed: every switch is off`. Move the file out of the repository.

Exit 0: read, or no file. Exit 1: a file is there and is not obeyed. Exit 2: the path is not
absolute.

### Turn a switch on

Turn on one switch at a time, then run `show` and check its line says `on`:

- **Merges:** run `merges on --repo OWNER/NAME` (section 2). It sets the ruleset, then lists the
  repository and turns `approve.merges` on, last.
- **Plans, pieces, and the four classes:** edit the file by hand. Set the one key to `true`.
- **The size limits:** edit the file by hand. Set each to the most you will let through.

Turn one off the same way. The validator itself has no way to write this file. Only you change
it, by hand or with your own scripts, each after your typed yes.

Good: `approve.merges` on through `merges on`, then `show` says `approve merges: on`. Not that: two
switches on in one edit. When something goes wrong, you cannot tell which one did it.

### Turn every switch off at once

One command, in a terminal, as yourself. It reads no conf, and it is refused in an agent's
environment:

```bash
python3 scripts/pipeline_validator_setup.py off
```

It names each switch that is on and asks for one typed `yes`. Then every `approve` and `allow`
switch goes off, and it reads the file back. Your limits, Stage E's login and the repository list
stay, so each switch comes back with one edit. A file it does not obey already reads as every
switch off; `off` replaces it with one that has every switch off.

The job keeps running and says `OFF:` on every pass. A pull request already in flight may still
merge: GitHub's own auto-merge stays on for it. `off` prints the `merges off --repo` command for
each listed repository, which turns that off too.

Good: `Done: every switch in ~/.validator/settings.json is off, read back`. Not that: deleting the
settings file. Every switch reads off, but the next install writes a fresh file, and your limits
and list are gone.

### List a repository for merges

The merges switch counts only in the repositories you list. A merge needs both: `approve.merges`
on, and its repository in `merge_repos`. Anything else holds, with a closed reason.

1. Add the repository to `merge_repos`, as `owner/name` in lower case.
2. Run `show`. Check the line `repositories for merges:` names it.
3. To stop merges there, take it off the list. The other repositories keep theirs.

`merges on` and `merges off` do this for you, with the ruleset and the repository's settings to
match (section 2, *Turn merges on for one project*). Use them rather than editing the list.

Good: `"merge_repos": ["acme/app"]`, with `"merges": true`. Merges in `acme/app` may be approved.
Not that: `"merge_repos": ["Acme/App", "acme/app"]`. Capitals and a repository twice each make
every switch read off.

The list alone approves nothing. With `"merges": false`, no merge is approved, listed or not.
List each repository once, and at most 100. Write it in lower case, even when GitHub shows
capitals. GitHub's names ignore case, so `acme/app` covers a pull request in `Acme/App`.

### What is built

The merge path (section 9), the piece pass (section 10) and the plan pass (section 11) decide, and
the install runs all three (section 12).

## 2. The ledger

Every decision the validator makes is one row in its ledger: what it decided, about what, and
why. Only the validator writes it. The stand-up and the sprint recap read it (section 7, "In
the stand-up"). The watcher checks every merge against it (`docs/WATCHER.md`, the
`approval-rate` monitor). Nothing writes rows yet: the pieces that decide come next. The format
is contract §24; the code is `scripts/pipeline_validator_ledger.py`.

| A row says | For example |
|---|---|
| What was decided, about what | a `merge`, of `acme/app#12` at its head |
| The decision | `approved`, `sent-back`, `escalated`, `escalated-urgent` or `held` |
| Each layer's verdict | hard limits `pass`, evidence `pass`, agent `pass` |
| Which switches were on, and the size limits | `approve-merges`; 400 lines, 20 files |
| Why, when it held, escalated or sent back | closed words, such as `risk-path` or `send-back-budget-spent` |
| The agent's reason, in its own words | `The diff does what the criteria ask, and nothing else.` |

**One verdict per head.** A row is never changed. A new push to a pull request is a new head,
and gets a new row.

**An approval is whole.** All three layers passed, its own switch was on, and code gave no
reason to hold. A row that says otherwise is refused, and nothing is written.

**Code's reasons are closed words.** A hold from the limits or the evidence says why in code's
own words, never the agent's. The agent's reason is kept apart, as its words.

**The agent's words are quoted, never obeyed.** Its reason is one line of at most 300 characters,
with nothing hidden in it. It is the only text in the ledger.

**A ledger it cannot trust approves nothing.** It must be a plain file outside every repository,
that only you can write. Anything else gives no rows, and says why. Good: no ledger yet, which
means nothing has been decided. Not that: `the ledger is inside a git working tree, where a
session can write, so no approval stands`. The install sets where it lives: `LEDGER_FILE` in
`validator.conf` (section 12).

### The check on the pull request

The ledger's decision is also a check on the pull request, named `Validator`, posted by the
validator's own GitHub app. The code is `scripts/pipeline_validator_check.py`. Making it a check
your branch rules require, with this app as its only source, is your step: see *Make the check
required*, below.

- `success` only when the ledger records an approval of that pull request at that exact head.
- `failure` for any other decision for that head.
- No check at all for a head with no decision. A push is a new head, so it waits to be judged.

Its summary is each layer's verdict, in fixed words. It never quotes the pull request or the
agent.

A ledger it cannot read posts nothing, and says why. A post GitHub refuses is a hold, said in
words. Good: `nothing posted: the validator's app is not installed on acme/app`. Not that: a
green check while the ledger could not be read.

**The app's key** is a file only you can read, outside every repository. The validator's config
names the app's id and the environment variable that holds the key file's path. Only `openssl`
reads the key, and nothing prints the path or the key.

**See what it would post,** from the ledger alone. It sends nothing:

```bash
python3 scripts/pipeline_validator_check.py show --ledger-file ~/.validator/ledger.json \
  --repo OWNER/NAME --pr 12 --head FULL_SHA
```

Exit 0: it would post, or there is nothing to post yet. Exit 1: held. Exit 2: the ledger path is
not absolute.

### Make the check required

One script, run once by you in a terminal. It makes the validator's GitHub app, and makes its
check one the ruleset requires. It is refused in an agent's environment.

```bash
python3 scripts/pipeline_validator_app.py run --repo OWNER/NAME --key-file ~/.validator/app.pem
```

It checks what is done, shows what it will change, and asks for one typed `yes`. Then:

1. It opens GitHub's page to create the app. You click GitHub's own Create button. The app may
   write check runs. For the merge path (section 9), it may also turn GitHub's own auto-merge on
   or off, and ask GitHub to update a pull request's branch. It never merges, approves or labels.
2. It writes the app's key to the file you named, mode 600. The key is never printed. The
   app's id goes in `~/.validator-app/state.json`.
3. It opens GitHub's page to install the app. Pick the repository. It waits until GitHub shows
   the app there.
4. It changes the ruleset on the default branch, with your own `gh` login: `Validator` becomes
   a required check with this app as its only source, and a pull request needs 0 approving
   reviews instead of 1.

Step 4 removes a person's review as a gate. After it, a merge waits for the `Validator`
check, which only the validator's own app can post.

It reads the ruleset back. If GitHub does not keep the app as the check's source, it puts the
ruleset back as it was and says so: a check anything could post is not one to require. A
second run changes nothing and says `Nothing to do: every piece holds`.

Good: `required check     DONE     ruleset 7 requires Validator from app 4242`. Not that: a
required `Validator` check with no source app. Then any GitHub app on the repository could
post it.

It needs one active ruleset on the default branch that already requires status checks
(`docs/AUTONOMY.md`, *Set up the ruleset FIRST*). With more than one, name it with
`--ruleset ID`. To check later, read-only: `python3 scripts/pipeline_validator_app.py verify
--repo OWNER/NAME`.

Exit 0: done, or every piece holds. 1: a piece does not hold, or a step failed after your yes.
2: the command line needs fixing. 3: refused. 4: something could not be checked. 10: you did
not type yes. With 2, 3, 4 or 10, nothing changed.

### Turn merges on for one project

One command per project, after `run`. It is the switch: run it in a terminal, as yourself. It is
refused in an agent's environment, before it reads anything.

```bash
python3 scripts/pipeline_validator_app.py merges on --repo OWNER/NAME
```

It checks first, and changes nothing yet. It reads four things: the validator's app on the
repository, the default branch's ruleset, the repository's merge settings, and your settings file
(`~/.validator/settings.json`, or name it with `--settings-file`).

It warns when nothing would merge yet, and still asks:

- no decision in the validator's ledger in the last 7 days, so its job may not be running;
- no `stage_e_login` in your settings, so no review is trusted. It checks that the login is
  set, not that Stage E is running: the heartbeat monitor watches that.

Then it shows each change in plain words, and asks for one typed `yes`. It changes, in this
order, and reads each back:

1. **The ruleset.** `Validator` is required, from the validator's app only. Branches must be up
   to date before merging. A pull request needs no person's review: no approving review, no code
   owner's review, no approval of the last push. Only repository admins may bypass it, and only
   for pull requests. It says which other bypasses this removes.
2. **The repository.** Auto-merge and squash merging are allowed.
3. **The switch, last.** `approve.merges` goes on, and the repository goes into `merge_repos`.

The switch goes on last. If a step fails, the switch is still off, and it says so. Run `merges
on` again to finish, or `merges off` to undo.

Dropping the review rule removes a person's review as a gate. After it, a merge waits for the
`Validator` check, which only the validator's own app can post. To merge a pull request the
validator holds, merge it yourself on GitHub, as an admin. GitHub records that bypass, and the
watcher checks every merge against the ledger.

Good: `settings       DONE     merges on, and acme/app listed`, after `Checked again:`. Not that:
listing the repository by hand while the ruleset still needs a person's review. Then nothing
merges on its own.

The admin role is role 5 in GitHub's API. GitHub uses that number but does not document it. After
the first `merges on`, open the ruleset's page on GitHub once. Its bypass list should say
*Repository admin*, for pull requests only.

A second run changes nothing and says `Nothing to do`.

**Classic branch protection.** If only classic branch protection guards the default branch, it
says so, and nothing changes. Move the protection to a ruleset first (`docs/AUTONOMY.md`).

### Turn merges off for one project

```bash
python3 scripts/pipeline_validator_app.py merges off --repo OWNER/NAME
```

It undoes `on` in the safe order, after one typed `yes`:

1. The repository comes off `merge_repos`, so nothing new is approved there. `approve.merges`
   stays as it is, for your other projects.
2. Auto-merge is turned off on each open pull request there that has it on.
3. The ruleset's parts that `on` sets go back as `on` found them, from
   `~/.validator-app/state.json`. Any other change made to the ruleset since stays.

Step 3 waits while a pull request could still merge on its own: if auto-merge cannot be turned
off, or the pull requests cannot be read. Step 1 always runs first. `off` leaves the repository's
auto-merge and squash settings as they are.

Good: `pull requests  DONE     no open pull request has auto-merge on`. Not that: putting the
ruleset back by hand while a pull request still has auto-merge on. It could merge with no check.

Exit 0: done, or every piece holds. 1: a piece is blocked, or a step failed after your yes.
2: the command line needs fixing. 3: refused. 4: something could not be checked. 10: you did
not type yes.

## 3. The evidence

The evidence is the second layer. It reads what the pipeline already made, from GitHub, for one
pull request at one head. It answers `yes` or `no`, with short fixed reasons. It never quotes the
pull request. The code is `scripts/pipeline_validator_evidence.py`.

`yes` needs all of these, at the head it was asked about:

| What | `yes` means | Some reasons for `no` |
|---|---|---|
| The head | GitHub's head is that exact commit. A push makes old evidence count for nothing. | `head-moved` |
| Checks | Every check is finished and green, and every check the branch requires has run. A check is known by its workflow and name. A required check counts only from the app your branch rules name as its source; one of the same name from another app is not it. | `check-pending`, `check-failed`, `check-missing` |
| Stage E's review | A full review of this exact head, never edited, from Stage E's own bot login, named in your settings. | `review-login-unset`, `review-login-shared`, `review-missing`, `review-stale`, `review-partial`, `review-declined`, `review-edited` |
| Its findings | Nothing at or above the bar. The bar is `medium` unless set. | `review-finding-at-bar`, `review-unreadable` |
| Acceptance | For a plan's last piece, the acceptance check passed. | `acceptance-none`, `acceptance-not-passed` |
| The merge state | Open, mergeable, up to date with its base, not a draft. | `merge-conflicting`, `merge-unknown`, `merge-behind`, `merge-draft` |

Anything it cannot read is `no`, said with its own reason. Good: `review-stale` after a push, until
Stage E reviews the new head. Not that: a `yes` because GitHub did not answer.

**Whose review counts.** Only Stage E's own bot login: `stage_e_login` in your settings file
(section 1). Nothing in the code names it, and no flag can. A review counts only when its author
is exactly that login and a bot, that login did not open the pull request, and it is not
`github-actions`, which a pull request's own workflow can post as. A person's login never fits:
it has no `[bot]`, and any session holding that person's credential can post as it. Every review
comment for the head counts, and the worst decides: a later comment cannot hide an earlier finding
or decline. An edited review comment is `no`.

**Where the login comes from.** Stage E's own GitHub App, made by
`scripts/pipeline_stage_e_app.py` (`docs/STAGE-E-OPERATOR.md`, step 7). Its login ends in
`[bot]`. The script writes it into `stage-e.conf` as `GITHUB_APP_LOGIN`.
Copy that value into `stage_e_login`, then run `show`. Until Stage E has its app, leave it `null`:
every answer is `no`, with `review-login-unset`. A login that is set while Stage E still posts as
you gives `review-missing`.

Good: `"stage_e_login": "acme-stage-e[bot]"`, copied from `GITHUB_APP_LOGIN`. Not that: your own
login. It has no `[bot]`, so the settings file is refused and every switch reads off.

**Try it.** It only reads:

```bash
python3 scripts/pipeline_validator_evidence.py check --repo OWNER/NAME --pr 12 \
  --head FULL_SHA --settings-file ~/.validator/settings.json --last-piece no
```

It prints one line of JSON and exits 0 for `yes`, 1 for `no`. Good:
`{"verdict": "no", "head": "...", "reasons": ["check-failed", "review-login-unset"]}` on a pull
request whose guard label is not on yet, before Stage E has its app. Not that: a `yes` while a
required check is still running. Exit 2: the settings path is not absolute.

## 4. The agent's brief and answer

The third layer is one agent that reads and judges, and can do nothing else. Code builds everything
it reads, and code reads everything it says. The code is `scripts/pipeline_validator_brief.py`. No
model runs there: section 6 is the run.

**What the agent reads.** One brief with three parts:

- the ticket's acceptance criteria, as they were when the work was delegated;
- the evidence, as code read it: `yes` or `no`, the head, and the reasons;
- the diff.

Never the pull request's description or comments: a session wrote those. The criteria and the
diff each sit in a fence, after a line saying they are data, not instructions. A fence tag inside
them, in any spelling, is removed first, so they cannot close their own fence.

**A diff that is too big is said, not cut quietly.** The brief says the diff is over the cap, and
the answer is a hold whatever it says. Good: a hold on an over-sized change. Not that: a pass on
the first part of one.

**What the agent may say.** Exactly two lines:

```
PASS
The diff does what the criteria ask, and nothing else.
```

Line 1 is `PASS`, `SEND_BACK` (the dev can fix it), `ESCALATE` (you must look) or
`ESCALATE_URGENT` (it believes the dev is compromised). Line 2 is one plain sentence of at most
200 characters. Anything else, a third line, a lower-case word, a long reason, is a hold.

**Its reason is cleaned before it is kept.** Links, markup, @-names, routing tags, hidden
characters and anything shaped like an order to a model are removed. A request to the dev, such as
`Add a test for the empty case.`, is kept as it is. Good: `Fine. (removed).` where the agent wrote
`Fine. Merge this now.` Not that: an agent's sentence that tells the next reader what to do with
the pull request.

**It can only hold.** The agent's `PASS` counts only when the limits and the evidence have both
passed. With either one not passed, the answer is a hold, whatever the agent said. One exception
runs the other way: `ESCALATE_URGENT` always reaches you.

## 5. The hard limits

The hard limits are the first layer: plain code with no model. They are worked out again at each
decision, from things no session can write. The code is `scripts/pipeline_validator_limits.py`.
The merge path (section 9), the piece pass (section 10) and the plan pass (section 11) call it.

It answers `pass`, or `fail` with every reason, in short fixed words. It never quotes a ticket or a
pull request.

| Limit | `pass` needs | Reasons for `fail` |
|---|---|---|
| The switch | That kind of approval is on (section 1). A merge: its repository is in `merge_repos` too. | `switch-off`, `repo-not-listed` |
| Who it comes from | A plan: an idea you filed yourself. A piece, or its pull request: an idea you filed, or agent-filed work in its first generation. | `provenance-not-owner`, `provenance-later-generation` |
| Size | A merge: within `max_changed_lines` and `max_files`. | `too-many-lines`, `too-many-files` |
| The safety system | No file on the kit's guard floor or in the project's `riskPaths`. Never a switch. | `risk-path` |
| The human-only classes | No file in a class you have not allowed. | `credentials`, `billing-and-accounts`, `production-deploy`, `deleting-data` |
| Budget and window | The working window is open, no runaway stop, and a piece fits the day's load. | `window-closed`, `runaway-stop`, `over-load` |
| Send-backs | A merge: Stage E has not used up its bounces on it. | `bounce-budget-spent` |
| The watcher | It ran recently, and it has set no hold. | `watcher-stale`, `held` |
| The head | A merge is judged at one commit. A push makes it fail, and it is judged again. | `head-moved` |

Anything it cannot read is `fail`, with a reason ending in `-unknown`. Good: `hold-unknown` when no
hold file is named. Not that: a `pass` because a file was missing.

**Who filed it is read by the queue's own rule** for a ticket you filed: you created it, it carries
your `provenance:human` mark, it has no parent, and it is not a session's finding. A ticket you
only moved into Todo, or a plan you only approved, is not one you filed. Good: a plan from an idea
you wrote yourself. Not that: a plan from an agent's finding you moved to Plan it.

**It counts nothing itself.** Each fact comes from the part that owns it: the load and the stops
from the queue, the window from the working window, the bounces from Stage E's own ledger, and
the hold from the watcher's hold file. No number is kept twice.

**The human-only classes, by path.** Each is matched on a file's path, in any case:

- credentials: `.env` files, keys and certificates, and anything named `secret` or `credential`;
- billing and accounts: anything named `billing`, `payment`, `stripe`, `subscription`, `invoice`
  or `account`;
- production deploy: anything named `deploy`, Docker files, hosting files such as `fly.toml` or
  `vercel.json`, and Terraform;
- deleting data: migrations and `.sql` files.

They are wide on purpose: a file that only looks like one holds, and you decide. Deleting data
cannot be proved by a path, so the agent reads the diff too. Your project's own sensitive paths go
in its `riskPaths`, where they always stay yours.

Good: a pull request that changes `src/app/views.py` passes, with the merges switch on. Not that:
one that touches `.github/workflows/ci.yml` passing. It fails with `risk-path`, whatever is on.

## 6. The agent's run

The agent is one run of the `claude` CLI. It reads the brief from section 4 and answers in two
lines. It can do nothing else. The code is `scripts/pipeline_validator_run.py`. The merge path
(section 9), the piece pass (section 10) and the plan pass (section 11) call it, from the
validator's own job (section 12).

**What it can do.** Read the brief, and answer. It has no tools, so it cannot read a file, run a
command, or reach the tracker or GitHub. It loads none of your settings, hooks, plugins or skills.
Each run is a new conversation that is never saved. No session can resume it or send it a message.

**Where it runs.** As you, under your own login, like your other jobs. It makes no new account and
uses no `sudo`. Its home and working folder are one fresh, empty folder, removed when the run ends,
however it ends. A `CLAUDE.md` in that folder or any folder above it stops the run: your own
instructions are never loaded into it.

**What it uses.** The `claude setup-token` token your other jobs use, by its variable's name. Only a
setup token is sent: an API key would bill every call. No GitHub token and no tracker key reach it.

**Its settings.** One answer, a spend cap and a time limit:

| Setting | Default | What it is |
|---|---|---|
| `agent_bin` | none | The claude CLI's full path, as `command -v claude` prints it. |
| `agent_model` | `sonnet` | The model's name or alias. |
| `agent_token_env` | `CLAUDE_WRITER_OAUTH_TOKEN` | The name of the variable that holds the token. |
| `agent_timeout_seconds` | `300` | How long one run may take: 30 to 900. |
| `agent_max_budget_usd` | `1.0` | The spend cap for one run: above 0, at most 5. |

A setting that is not valid holds every run, and names the setting.

**When it cannot answer, it holds.** A run that times out, cannot start, exits with an error, or
gives no answer is a hold. Each is said in fixed words, never in the CLI's own. It never retries:
one brief is one run.

Good: `the agent took longer than 300 seconds`, and the change waits. Not that: an approval because
the agent did not answer.

**Only its own job starts it.** It is refused in an agent's environment: a session that could start
the validator's agent could choose what it is shown. It has no command line that runs the model,
and its selftest checks that no other script names it.

Good: the validator's own job, started by your scheduler, as you. Not that: a session running the
agent to see what it would say.

## 7. What a no does

When the validator does not approve, code decides what happens next, from the agent's answer and
the layers. The code is `scripts/pipeline_validator_route.py`. The merge path (section 9), the
piece pass (section 10) and the plan pass (section 11) call it.

| What happened | What it records | What happens next |
|---|---|---|
| The agent sends it back, and Stage E has send-backs left | `sent-back` | Stage E re-prompts the dev session, from the same budget a review's send-back uses. |
| The agent sends it back, and none are left | `escalated`, `send-back-budget-spent` | It waits for you. |
| The agent sends back a plan or a piece | `escalated`, `send-back-no-session` | It waits for you: there is no dev session yet. |
| The agent escalates | `escalated` | It waits for you, with the agent's reason. |
| The agent believes the dev session is compromised | `escalated-urgent` | It waits for you, marked urgent, and the watcher pages you. |
| A limit or the evidence holds it for good | `held`, with closed words such as `risk-path` | It waits for you. |
| The agent gave an answer that could not be used | `held`, with a word such as `answer-not-two-lines` | It waits for you. |

**A hold that settles by itself writes nothing.** Checks still running, a review not in yet, the
working window closed, a fact it could not read, an agent run that failed before it judged (a
timeout, a CLI that would not start): it waits, and a later pass judges again. A pull
request's head gets one verdict, so a wait never spends it. Good: no row while checks run. Not
that: a `held` row for a check still running, which would leave that head unable to be approved.

**A send-back goes through Stage E.** The validator never re-prompts anyone itself. It writes the
`sent-back` row, and Stage E's bounce driver re-prompts the session that opened the pull request,
from its own ledger. So a send-back counts against the bounce budget Stage E already keeps: there
is no second count. The agent's reason reaches the session as one line marked as its words, inside
the re-prompt's data fence, never as an order. One row buys one re-prompt. The session's next push
is a new head, reviewed and judged again from the start.

To turn it on, add the ledger's absolute path to the bounce driver's config as
`validator_ledger_file` (`docs/STAGE-E-OPERATOR.md`). The driver must be able to read the file. A
path it cannot read stops it, said on each pull request, rather than missing a send-back quietly.

Good: `validator_ledger_file` names the ledger, and a send-back arrives in the session's thread.
Not that: no `validator_ledger_file`. Then a `sent-back` row waits, and nothing re-prompts the
session.

**What you see.** Each escalation and each hold that waits for you is a blocker, with its reason in
plain words and the reply that clears it: `approve <plan>` for a plan, `start <piece>` for a
piece. A pull request is yours to merge or close on GitHub. The agent's own words are shown apart,
as its words, never as the report's.

### In the stand-up

The daily stand-up and the sprint recap say the validator's decisions, built by code from the
ledger (KIT-545, `docs/STANDUP.md`). Each approval since the last stand-up is one line. Each
escalation and each hold that still waits for you is a blocker, an urgent one first and marked
urgent. The recap lists the week's approvals, send-backs, escalations and holds.

To turn it on:

1. Set `STANDUP_VALIDATOR_LEDGER` in `bridge.conf` to the ledger's absolute path.
2. Run the bridge installer's `standup` command (`docs/BRIDGE-OPERATOR.md`). It shows the file it
   writes, asks one yes, and reads it back.

The bridge runs as the role account, so that account must be able to read the ledger. A ledger
it cannot read is said in the stand-up: *The validator's record could not be read, so no
approval is claimed.* It is never shown as none.

Good: one line per approval, and the agent's reason quoted under it as its words. Not that: a
quiet stand-up while the record cannot be read.

**An urgent one pages you through the watcher,** which reads the ledger on its own account. The
validator sends nothing itself, so it never assumes a page. It says `paged` only once the watcher's
status shows that escalation flagged, `not paged yet` until then, and `could not page` while the
watcher is stale, unreadable, off or blind. The blocker stands either way. Good: `not paged yet:
the watcher has not flagged this urgent escalation, so it stays your blocker until it does`. Not
that: taking an urgent escalation as paged because it was written down.

**Your `Validator` check.** Stage E's bounce driver never treats a red `Validator` check as
something the dev session must fix, whatever your list of checks that wait on a person says. Its
verdict is the validator's: a send-back has its own path, and an escalation or a hold is yours.

## 8. The facts it reads

Before it judges anything, the validator reads the facts about it once: who filed the idea, which
files change, how big the change is, whether the window is open, and so on. The code is
`scripts/pipeline_validator_facts.py`. It only reads. The merge path (section 9), the piece pass
(section 10) and the plan pass (section 11) call it.

Each fact comes from the part of the kit that owns it, through that part's own reader:

| Fact | Where it comes from |
|---|---|
| The ticket a pull request belongs to | The tracker's own link from the ticket to the pull request, never the branch name. |
| Who filed the idea | The piece, then its plan, then the planning run, then the idea, judged by the queue's own rule for a ticket you filed. |
| The files and the size | GitHub. A renamed file counts under both names. |
| Your project's risk paths, and its send-back budget | `delivery.json` on the default branch. |
| Send-backs spent | Stage E's own ledger. |
| The window and the runaway stops | The queue's own config and its own stop checks. |
| The watcher and its hold | The watcher's status file, and the hold file. |
| The last piece of a plan, and its acceptance check | The tracker, and the acceptance check's own record at this head. |
| The criteria | Stage E's snapshot of them, taken when the work was delegated. |
| The diff | GitHub. |

**A fact it could not read holds the item.** It is said by name, with the reason, and never read
as clear, empty or zero. So a source that is down stops approvals instead of letting one through.

Good: `ticket: the tracker records no one ticket for the pull request`, and the pull request waits.
Not that: a pull request approved because its ticket could not be read.

**Its sources are yours to point it at.** Its config names the tracker key's variable (a read-only
key), the work teams, the queue's config, Stage E's ledger and snapshot folder, the acceptance
ledger, and the watcher's status and hold files. One left empty is a source it does not read, and
every fact from it is said as unread. The install sets them, from `validator.conf` (section 12).

**Read one pull request's facts yourself.** It prints the facts as fields, with the criteria and the
diff as sizes only, and changes nothing:

```bash
python3 scripts/pipeline_validator_facts.py show --config ~/.validator/config.json \
  --repo OWNER/NAME --pr 12
```

Exit 0: every fact was read. Exit 1: at least one was not, and `unread` says which. Exit 2: the
config could not be read or is not valid.

**What it does not read yet.** Agent-filed work's generation is the queue's to certify, from its own
verdict, and is not read here: such work's provenance is said as unread, so it waits for you.

## 9. The merge path

The validator's own job turns on GitHub's own auto-merge for one pull request at a time, each on
the latest main. It never merges, approves a review or labels: GitHub merges, once its own rules
pass. The code is `scripts/pipeline_validator_merge.py`. The validator's own job runs one pass every
few minutes, once installed (section 12).

**What one pass does.**

1. It reads the switches. Off, or no repository in `merge_repos`: it stops and says `OFF`.
2. It reads the watcher's hold. A hold, or a hold file it cannot read: it stops and says `HELD`.
3. It reads the ledger, and every open pull request in the listed repositories. One it cannot read
   whole stops it.
4. One in flight. A pull request with auto-merge on waits for GitHub to merge it. While one is on,
   nothing else is turned on.
5. The rest, oldest first. Each is judged by its facts, the hard limits, the evidence and, last,
   one agent run.

| What it finds | What it does |
|---|---|
| A hold no pass can settle: the safety system, a human-only class, a declined review | A `held` row and its check. It waits for you. |
| Behind main, and nothing else | GitHub's own update-branch at that head, and the pass ends. The new head is judged from scratch. |
| Checks running or red, a review not in yet or stale, a fact not read | Nothing written. It waits, and says why. |
| Both layers pass | One agent run, its row and its check. For an approval, auto-merge on at exactly that head. |
| Auto-merge on, but no approval at its head | Turned off if the validator turned it on. Left alone, and said, if someone else did. |
| Approved and in flight, but now behind main | Its auto-merge goes off first, then GitHub's own update-branch. The new head is judged from scratch before anything turns auto-merge on again. |

At most one agent run, and one pull request turned on, per pass.

**What is written down.** The ledger holds one verdict per head: an approval, a send-back, an
escalation, or a hold no pass can settle. A wait spends no verdict. So a skip (a draft, a pull
request into another branch), a wait, an update and turning auto-merge off write no row. Each pass
says each of them, and its output is their record.

**It acts as its own app.** It reads with your `gh` login, and writes only as the validator's app.
A merge GitHub makes for the app is the app's, so the watcher checks it against the ledger. A merge
under your login would be yours, and the watcher would not check it.

**The app needs two more permissions.** GitHub asks `Contents: write` and `Pull requests: write` of
an app that turns auto-merge on or updates a branch. An app made by `run` from now on has them. For
one made before:

1. Open the app's settings on GitHub. Under Permissions, set Contents and Pull requests to Read and
   write. Save.
2. Accept the new permissions on the app's installation.

Good: `acme/app#12: turn GitHub's own auto-merge on at 0a1b2c3d4e5f`. Not that: `could not turn
GitHub's own auto-merge on ... FORBIDDEN`.

**The stale-review limit (KIT-792).** Updating a pull request moves its head, so its review is stale
until Stage E reviews the new head. Stage E re-reviews a moved head once per pull request. One
updated a second time waits, and each pass says why. It is never approved on a stale review.

**Its config** is `~/.validator/config.json`: `settings_file`, `ledger_file`, the `merge` section
(`review_bar`, `merge_method`, `max_pages`), and the `facts`, `agent` and `check` sections. The
install writes it from `validator.conf` (section 12). Change the conf, then run the install
again: an edit to the config by hand is put back on the next install. To see the shape:

```bash
python3 scripts/pipeline_validator_merge.py --example-config
```

**Try one pass that writes nothing.** It reads and judges, runs no agent, and changes nothing. Run
it in a terminal, as yourself: it is refused in an agent's environment. The install's own
rehearsal runs the same pass this way, with your env file read in first.

```bash
python3 scripts/pipeline_validator_merge.py run --config ~/.validator/config.json --dry-run
```

**A merge queue is optional.** The job needs no paid feature: it keeps one pull request in flight,
and updates the next itself. Where GitHub's merge queue is free, a repository may use it instead.
Its required workflows must then also run on the `merge_group` event. That is a workflow change
only a person can land.

Exit 0: the pass ran and said what it did, or that it was off or held. 1: a write failed. 2: the
config needs fixing, or this is an agent's environment. 3: something could not be read.

## 10. A piece starting

A piece of a plan you approved waits for your `start` when it was added to the plan, or changed,
after your approval. With the pieces switch on, the validator may give that start in your place
(KIT-543). The code is `scripts/pipeline_validator_pieces.py`. The validator's own job runs
one pass every few minutes, once installed (section 12).

**What one pass does.**

1. It reads the switches. The pieces switch off: it stops and says `OFF`.
2. It reads the watcher's hold. A hold, or a hold file it cannot read: it stops and says `HELD`.
3. It runs the queue's own pass, with the validator's read-only tracker key, and lists the pieces
   that wait only for your `start`. Nothing else is judged.
4. Each, oldest ticket first: its facts, the hard limits, then the agent.

| What it finds | What it does |
|---|---|
| A hold no pass can settle: the safety system, a human-only class, an idea you did not file | A `held` row, once. It waits for you. An unchanged hold is not written again. |
| A fact not read, or the window closed | Nothing written. It waits, and says why. |
| The limits pass | One agent run and its row. A send-back becomes an escalation: a piece has no dev session yet. |
| A piece the agent already judged, unchanged since | It waits for you. No second run, so the pieces after it get their turn. |
| A piece edited while the agent judged it | Nothing written. A later pass judges it as it stands. |

At most one agent run per pass. "Unchanged" is judged by the piece's own history, as the queue
read it: a row newer than the piece's last change is about the piece as it stands. It never starts a piece itself. The queue's starter does, from the
ledger (`docs/QUEUE-OPERATOR.md`, "When the validator approves a piece's start").

**The agent's second brief.** A piece has no diff yet, so the agent judges the ticket as written. Its
brief holds two fenced blocks, each marked as data: the criteria of the piece's plan, and the
piece's own text. The question is whether the ticket asks for what the criteria ask, and nothing
else that needs you. The answer's grammar is the same two lines. A ticket over the cap is a hold,
as an over-sized diff is. The code is `build_ticket_brief` in `scripts/pipeline_validator_brief.py`.
A plan uses the same brief, with its pieces against its idea's criteria.

**Its config** is the validator's one config, `~/.validator/config.json`: `settings_file`,
`ledger_file`, and the `facts` and `agent` sections. The facts section names the queue's config
and the read-only tracker key's variable.

**Try one pass that writes nothing.** It reads and judges, runs no agent, and changes nothing. Run
it in a terminal, as yourself: it is refused in an agent's environment.

```bash
python3 scripts/pipeline_validator_pieces.py run --config ~/.validator/config.json --dry-run
```

Good: `TOD-12: the validator approved its start.`, then the queue's next start says so on the
ticket. Not that: a piece changed after the validator's approval starting on that approval.

Exit 0: the pass ran and said what it did, or that it was off or held. 1: a ledger row could not
be written. 2: the config needs fixing, or this is an agent's environment. 3: something could not
be read.

## 11. A plan

With the plans switch on, the validator may approve a plan in place of your `approve <plan>`
(KIT-544). It judges and records. The bridge approves the plan, on its next pass. The code is
`scripts/pipeline_validator_plans.py` and `scripts/pipeline_bridge_plans.py`. The validator's own
job runs one pass every few minutes, after the piece pass, once installed (section 12).

**Which plans it judges.** Only a plan whose `approve` question is open: one the bridge asked you
about, that you have not answered. It reads those questions from the bridge's state, the file the
queue's reader names (`reader.bridge_state_file`). A plan you approved, declined or were never
asked about is not judged. Nor is one that has left the backlog.

**What one pass does.**

1. It reads the switches. The plans switch off: it stops and says `OFF`.
2. It reads the watcher's hold. A hold, or a hold file it cannot read: it stops and says `HELD`.
3. It reads the bridge's open questions. A state it cannot read: it stops, and says so.
4. Each plan, oldest ticket first: its facts, the hard limits, then the agent.

| What it finds | What it does |
|---|---|
| An idea you did not file | Never approved, whatever the switches. It waits for you, and says why. |
| Pieces that touch the safety system, or a human-only class you have not allowed | A `held` row, once. It waits for you. An unchanged hold is not written again. |
| A fact not read, or the window closed | Nothing written. It waits, and says why. |
| The limits pass | One agent run and its row. A send-back becomes an escalation: a plan has no dev session yet. |
| A plan it already decided, unchanged since, under the same switches | No second run. The plans after it get their turn. |
| A plan whose last change can't be read | Nothing written, and no run. It waits, and says so. |
| A plan edited while the agent judged it | Nothing written. A later pass judges it as it stands. |

At most one agent run per pass. A row carries the moment before the plan's last read: after the
agent's run, it is read once more. A change to the plan, its pieces or its idea after that moment
needs a new decision, and so does a question asked after it.

**The agent's brief.** Your idea's own text, and the plan with every piece as written. Each sits in
its own fence, marked as data. The question is whether the plan asks for what your idea asks, and
nothing else that needs you. The code is `build_ticket_brief` in `scripts/pipeline_validator_brief.py`.

**How the bridge approves it.** On each pass, after your own messages, the bridge reads the
validator's newest decision about each plan with an open question. An `approved` one counts only
when all of these hold:

- the plans switch is still on, in the settings the queue's reader names
  (`reader.validator_settings_file`), and the ledger it names can be trusted;
- the switches on now are the ones it decided under. Turn one on or off, and it decides again;
- the plan, its pieces and its idea have not changed since the validator read them, and it
  decided after you were asked;
- its idea is yours: filed with your key, marked `provenance:human`, with no parent;
- every check your own `yes` gets passes.

Then the bridge approves the plan as the validator's, never yours. Its record says
`approved_by: validator`. The plan's comment, the reply where it asked you and each piece's start
comment name the validator, and no person.

**Your choice always stands.** Your own answer is read first. A plan you declined stays declined.
When the bridge can't act on the validator's approval, it says why where it asked you, once, and
your question stays open. You can still approve a plan yourself at any time. You can pause or cancel a plan the validator
approved, as any other: `pause <plan>` or `cancel <plan>`.

**A hold** is a blocker in the stand-up, with the reply that clears it: `approve <plan>`.

**Its config** is the validator's one config, `~/.validator/config.json`, as the piece pass reads
it. Both of the queue's validator files empty, or the plans switch off: your `approve` decides, as
before.

**Try one pass that writes nothing.** It reads and judges, runs no agent, and changes nothing. Run
it in a terminal, as yourself: it is refused in an agent's environment.

```bash
python3 scripts/pipeline_validator_plans.py run --config ~/.validator/config.json --dry-run
```

Good: `TOD-10: the validator approved it.`, then in your DM: *The validator approved TOD-10 in your
place.* Not that: a plan approved from an idea an agent filed.

Exit 0: the pass ran and said what it did, or that it was off or held. 1: a ledger row could not
be written. 2: the config needs fixing, or this is an agent's environment. 3: something could not
be read: the hold, the ledger, or the bridge's questions.

## 12. Install it

The validator's job runs under your own login: one LaunchAgent, as you, every few minutes. It makes
no account and runs no `sudo`. After a fresh install every switch is off, and the job says so on
every pass. The code is `scripts/pipeline_validator_setup.py`.

### Before you start

Three things come first. The installer checks each, and names the card when one is missing.

1. **The validator's own app and its key file** (section 2, *Make the check required*). Card
   `CK-V1`. The installer reads the app's id from that script's record, `~/.validator-app/state.json`.
2. **The watcher, with its hold file** (`docs/WATCHER.md`). Every pass reads that hold.
3. **An env file**, mode 600, with two lines: a read-only tracker key and a token from
   `claude setup-token`, each under the name `validator.conf` gives. Card `CK-V2`.

### The conf

```bash
cp validator.conf.example validator.conf && chmod 600 validator.conf
$EDITOR validator.conf
```

`HOLD_FILE` and `WATCHER_STATUS` are required: the watcher's own two files, outside your home. Every
other key has a default. No value in the conf is a secret. The keys go in the env file, and the
app's key stays in its own file. A value shaped like a credential is refused. Every bad value is
named in one pass.

Good: `HOLD_FILE=/var/claw-watcher/hold.json`, the watcher's own. Not that: a hold file in your home.
You could remove it, so it holds nothing, and the conf is refused.

### Run it

```bash
python3 scripts/pipeline_validator_setup.py run --dry-run   # read it first: changes nothing
python3 scripts/pipeline_validator_setup.py run             # shows, asks one typed yes, applies
```

`run` looks, shows what it will change, and asks for one typed `yes`. Then it applies each row in
order and looks again. A row only you can clear stops it and prints its card. A second run changes
nothing and says `Nothing to change: every row holds.`

What it builds, all yours:

| File | What it is |
|---|---|
| `~/.validator/settings.json` | The switches. Written once, when absent, with every switch off. Never written again. |
| `~/.validator/config.json` | The job's config, from `validator.conf`: the settings, the ledger, the hold and the facts' sources. |
| `~/Library/LaunchAgents/<JOB_LABEL>.plist` | The job: each pass reads your env file, then runs the merge path, the piece pass and the plan pass. |
| `~/.validator/validator.log` | What each pass said. While every switch is off: `OFF: the merges switch is off ...`. |
| `~/.validator/last-pass.json` | When the last pass ended, and its exit code. |
| `~/.validator/setup.json` | One fact: the job has been on. A job that was on and is not loaded now, you paused. |

Its rows, in order:

| Row | Holds when | Stops on |
|---|---|---|
| `preflight` | macOS; this is a main checkout, never a linked worktree; Python, `claude` and `gh` run | anything missing, named |
| `folder` | `~/.validator` is yours, outside every repository, and nobody else can write it | a folder others can write: you run `chmod 700` |
| `settings` | written with every switch off, or kept as you left it | never: a file it cannot obey reads as every switch off, and is said |
| `ledger` | its folder is yours, and its own reader trusts it | a ledger it cannot trust |
| `app` | the app's id is recorded, and its key file is only yours | card `CK-V1`, or a key file others can read |
| `env-file` | it is yours, mode 600, and sets each name | card `CK-V2`, or a file others can read |
| `hold` | the job can read the watcher's hold | a hold it cannot read: every pass would hold |
| `config` | the job's own loader accepts it | a value it would refuse |
| `job` | the job file is current | a job file at that label that runs something else |
| `enable` | loaded, running this job file, and its last pass exited 0 | card `CK-V3`: left off, or paused |

**The key is never opened.** The `app` row looks at the key file's mode and owner, and nothing
else. The job's `openssl` is the only reader.

**No value leaves the env file.** The `env-file` row asks a shell that reads the file whether each
name is set. Only `NAME=set` or `NAME=unset` comes back.

### When the job is switched on

`pipeline-parts.json` gives the validator's part a first-pass ceiling: the most things its first
pass may decide with nobody reading the number first. It is 0.

Before it switches the job on, the installer rehearses the job's own command with `--dry-run`. It
counts the `[dry-run]` lines: what a real pass would record, post or turn on.

- **At or below 0,** it enables and loads the job, then waits for its first pass and judges it. A
  pass that has not finished in time is NOT MEASURED, never "on".
- **Above 0,** the job stays installed, disabled and off. One line says why, for example `left off:
  its first pass would decide 2 things, above its ceiling of 0`. Read the rehearsal's lines, then
  switch it on yourself with card `CK-V3`.
- **A rehearsal that does not exit 0,** or a ceiling it cannot read, also leaves it off.

With every switch off, the first pass decides nothing, so a fresh install switches the job on.

Good: `enable  DONE  switched it on ... its first pass exited 0 and said: OFF: the merges switch is
off`. Not that: turning `merges` on before the first install. The rehearsal then finds work, and
the job stays off until you read it.

### Pause it, and resume it

To pause the job, so that a new login does not start it either:

```bash
launchctl bootout gui/$(id -u)/local.validator
launchctl disable gui/$(id -u)/local.validator
```

Use your `JOB_LABEL` if you changed it. A paused job stays paused: neither `run` nor `apply` loads
it again. The `enable` row says `you paused it` and names card `CK-V3`, which resumes it.

To stop approvals and keep the job running, use `off` instead (section 1). The job then says `OFF:`
on every pass.

### For a program: `check` and `apply`

The one command (`scripts/pipeline_install.py`, step 3h) runs this when the Mac has a
`validator.conf`. It asks for no Mac password for it.

- **`check`** prints one JSON document of rows on stdout and its words on stderr. It changes,
  records and asks nothing.
- **`apply`** reads its answers from a pipe on stdin: `{"schema": "validator-answers/1", "consent":
  "yes"}`. A terminal, a file or an argument is refused. Anything but `"consent": "yes"` changes
  nothing and exits 10.

In Manual the one command runs `run --dry-run`, then `run` on your yes, which asks its own typed yes.

### Under a model

`run`, `apply` and `off` are refused in an agent's environment, before the conf is read. `apply` is
refused before its stdin is read. There is no override. `check`, `verify`, `run --dry-run` and
`card` still work. They never read the env file under a model, so that row says NOT MEASURED, and
they exit 4.

Exit 0: every row holds. 1: a row failed. 2: the conf or the command line needs fixing. 3: refused.
4: a row could not measure itself. 10: a row would change something, a card is yours, or you did
not type yes.

### What its independence costs under your own login

You chose this (decision V1 in the design): no new account, so it ports simply. The cost is
plain. A session runs as you too. So it could, in principle, edit what the job reads: the settings
file, the config, the job file, or this checkout's scripts. The watcher runs as its own account in a
folder only root can write. The validator cannot be made that hard to edit.

What holds the line instead:

- **The safety floor is code.** No switch approves a change to hooks, guards, workflows, settings or
  the validator's own limits. A settings file that names one is refused.
- **The check comes from its own app.** Your branch rules accept `Validator` only from that app. Its
  key is outside every repository.
- **The watcher checks every merge** against the ledger, from its own account, and pauses new work
  on a merge with no approval.
- **The job runs a main checkout only.** Sessions work in linked worktrees. The job's code changes
  when you pull, never when a session edits a worktree.
- **A settings file a session could have written is not obeyed:** one in a repository, a link, or
  one in a folder others can write.

Good: you read each approval in the stand-up, and turn a switch off when one surprises you. Not
that: trusting the switches alone. Anything that can act as you can edit them.

### Who else reads the ledger

The watcher's approval check (`VALIDATOR_LEDGER` in `watcher.conf`) and the stand-up
(`STANDUP_VALIDATOR_LEDGER` in `bridge.conf`) read the ledger from their own accounts. A Mac's home
folder lets no other account in, so a ledger under it is out of their reach. The `ledger` row says
which folder stops them.

To let them read it, name a `LEDGER_FILE` in a folder of yours that they can reach. Then name the
same path in their settings. The ledger itself is written readable by all, and only you can write
it.

### What is not proven

- **Live launchd calls in your own domain.** The selftest drives a fake launchd. No live install
  has run yet.
- **That launchd's print of a LaunchAgent lists its arguments and interval** the way the notifier's
  installer reads them for its daemon. The `enable` row compares them.
- **That `gh` and `claude` find your logins under launchd.** A LaunchAgent runs in your login
  session, so your keychain is open while you are logged in. Logged out, the job does not run.
- **A copy of the ledger for other accounts,** written by root, as the watcher's copier does for the
  usage file. Not built: until it is, choose the ledger's folder as above.
