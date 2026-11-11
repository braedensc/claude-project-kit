# The validator

The validator approves in your place, inside limits that code enforces. It may approve three
things: a plan, a piece starting, and a merge. Three layers must all agree first: hard limits in
code, the evidence, and one independent read-only agent. Anything missing, unsure or unreadable
holds. The design is `docs/adr/2026-11-05-the-validator-approves-inside-limits-that-code-enforces.md`.

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
