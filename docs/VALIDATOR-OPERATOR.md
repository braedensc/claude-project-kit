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

Edit the file by hand, then run `show`. Turn on one switch at a time. Turn it off the same way.
Nothing else changes a switch: the validator has no way to write this file.

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

### Until the rest is built

This guide grows as the validator is built, one section per piece. Until the pieces that decide
are built, nothing is approved for you.

## 2. The ledger

Every decision the validator makes is one row in its ledger: what it decided, about what, and
why. Only the validator writes it. The stand-up, the weekly recap and the watcher will read it,
and the watcher will check every merge against it. Nothing writes rows yet: the pieces that
decide come next. The format is contract §24; the code is `scripts/pipeline_validator_ledger.py`.

| A row says | For example |
|---|---|
| What was decided, about what | a `merge`, of `acme/app#12` at its head |
| The decision | `approved`, `sent-back`, `escalated`, `escalated-urgent` or `held` |
| Each layer's verdict | hard limits `pass`, evidence `pass`, agent `pass` |
| Which switches were on, and the size limits | `approve-merges`; 400 lines, 20 files |
| The agent's reason, in its own words | `The diff does what the criteria ask, and nothing else.` |

**One verdict per head.** A row is never changed. A new push to a pull request is a new head,
and gets a new row.

**An approval is whole.** All three layers passed, and its own switch was on. A row that says
otherwise is refused, and nothing is written.

**The agent's words are quoted, never obeyed.** Its reason is one line of at most 300 characters,
with nothing hidden in it. It is the only text in the ledger.

**A ledger it cannot trust approves nothing.** It must be a plain file in your own home, outside
every repository, that only you can write. Anything else gives no rows, and says why. Good: no
ledger yet, which means nothing has been decided. Not that: `the ledger is inside a git working
tree, where a session can write, so no approval stands`. The install will set where it lives.

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
   write check runs, and nothing else.
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
- no `stage_e_login` in your settings, so no review is trusted.

Then it shows each change in plain words, and asks for one typed `yes`. It changes, in this
order, and reads each back:

1. **The ruleset.** `Validator` is required, from the validator's app only. Branches must be up
   to date before merging. A pull request needs no approving review. Only repository admins may
   bypass it, and only for pull requests. It says which other bypasses this removes.
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
3. The ruleset's four parts go back as `on` found them, from `~/.validator-app/state.json`. Any
   other change made to the ruleset since stays.

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
| Checks | Every check is finished and green, and every check the branch requires has run. A check is known by its workflow and name. | `check-pending`, `check-failed`, `check-missing` |
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
model runs here: running the agent is its own piece.

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
Nothing calls it yet: the merge path and the approvals are their own pieces.

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
