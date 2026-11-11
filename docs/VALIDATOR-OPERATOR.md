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
| `approve.merges` | It may ask GitHub to merge a pull request. | You merge each pull request. |
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

It is JSON, contract §23. Start from `validator-settings.example.json`, which has every switch off
and every limit `0`:

```json
{
  "schema": "pipeline-validator-settings/1",
  "approve": {"plans": false, "pieces": false, "merges": false},
  "limits": {"max_changed_lines": 0, "max_files": 0},
  "allow": {"credentials": false, "billing_and_accounts": false,
            "production_deploy": false, "deleting_data": false},
  "stage_e_login": null
}
```

Every key must be there. Each switch is `true` or `false`. Each limit is a whole number.
`stage_e_login` is Stage E's own bot login, or `null` until Stage E has one. Section 3 says where
it comes from.

Keep it in your own home, outside every repository. Good: `~/.validator/settings.json`, mode 600.
Not that: a file inside a checkout. A session can write there, so the file is not obeyed.

### When every switch reads off

Absent is every switch off. So is any file the validator cannot trust. It never means on:

- the file is not valid JSON, or gives a key twice;
- it names another schema, misses a key, or adds one;
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

It prints every switch, every limit and Stage E's login, and why when they are off. It changes
nothing.

Good: `read: the validator may approve only what is on below`. Not that: `EVERY SWITCH IS OFF
(untrusted): the settings file is inside a git working tree, where a session can write, so it is
not obeyed: every switch is off`. Move the file out of the repository.

Exit 0: read, or no file. Exit 1: a file is there and is not obeyed. Exit 2: the path is not
absolute.

### Turn a switch on

Edit the file by hand, then run `show`. Turn on one switch at a time. Turn it off the same way.
Nothing else changes a switch: the validator has no way to write this file.

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
your branch rules require, with this app as its only source, is a separate owner step.

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
| The switch | That kind of approval is on (section 1). | `switch-off` |
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
