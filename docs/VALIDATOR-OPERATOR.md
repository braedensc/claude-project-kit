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
            "production_deploy": false, "deleting_data": false}
}
```

Every key must be there. Each switch is `true` or `false`. Each limit is a whole number.

Keep it in your own home, outside every repository. Good: `~/.validator/settings.json`, mode 600.
Not that: a file inside a checkout. A session can write there, so the file is not obeyed.

### When every switch reads off

Absent is every switch off. So is any file the validator cannot trust. It never means on:

- the file is not valid JSON, or gives a key twice;
- it names another schema, misses a key, or adds one;
- a switch is not `true` or `false`, or a limit is out of range;
- it names a part of the safety system;
- it cannot be read, or is larger than a settings file;
- it is inside a git working tree, is a link, is writable by group or other, is owned by another
  account, or sits in or under a folder others can write.

Each says why, in words.

### Check what it reads

```bash
python3 scripts/pipeline_validator_settings.py show --settings-file ~/.validator/settings.json
```

It prints every switch and every limit, and why when they are off. It changes nothing.

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

## 3. The evidence

The evidence is the second layer. It reads what the pipeline already made, from GitHub, for one
pull request at one head. It answers `yes` or `no`, with short fixed reasons. It never quotes the
pull request. The code is `scripts/pipeline_validator_evidence.py`.

`yes` needs all of these, at the head it was asked about:

| What | `yes` means | Some reasons for `no` |
|---|---|---|
| The head | GitHub's head is that exact commit. A push makes old evidence count for nothing. | `head-moved` |
| Checks | Every check is finished and green, and every check the branch requires has run. A check is known by its workflow and name. | `check-pending`, `check-failed`, `check-missing` |
| Stage E's review | A full review of this exact head, never edited, from a bot login no session can use. | `review-login-shared`, `review-missing`, `review-stale`, `review-partial`, `review-declined`, `review-edited` |
| Its findings | Nothing at or above the bar. The bar is `medium` unless set. | `review-finding-at-bar`, `review-unreadable` |
| Acceptance | For a plan's last piece, the acceptance check passed. | `acceptance-none`, `acceptance-not-passed` |
| The merge state | Open, mergeable, up to date with its base, not a draft. | `merge-conflicting`, `merge-unknown`, `merge-behind`, `merge-draft` |

Anything it cannot read is `no`, said with its own reason. Good: `review-stale` after a push, until
Stage E reviews the new head. Not that: a `yes` because GitHub did not answer.

**Whose review counts.** A review counts only from a bot login you name, that is not the pull
request's author, and is not `github-actions`. A person's login is one any session holding that
person's credential can post as, so a review from one is `review-login-shared`. Today Stage E posts
as your own login, so the evidence answers `no` until Stage E posts from a login of its own
(KIT-313). Every review comment for the head counts, and the worst decides: a later comment cannot
hide an earlier finding or decline. An edited review comment is `no`. With no login named, the
answer is `no`.

Good: `review-login-shared` while Stage E posts as your own login. Not that: a `yes` from a review
a session could have posted.

**Try it.** It only reads:

```bash
python3 scripts/pipeline_validator_evidence.py check --repo OWNER/NAME --pr 12 \
  --head FULL_SHA --review-author YOUR_LOGIN --last-piece no
```

It prints one line of JSON and exits 0 for `yes`, 1 for `no`. Good:
`{"verdict": "no", "head": "...", "reasons": ["check-failed", "review-login-shared"]}` on a pull
request whose guard label is not on yet. Not that: a `yes` while a required check is still running.
