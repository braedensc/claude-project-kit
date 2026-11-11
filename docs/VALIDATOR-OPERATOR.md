# The validator

The validator approves in your place, inside limits that code enforces. It may approve three
things: a plan, a piece starting, and a merge. Three layers must all agree first: hard limits in
code, the evidence, and one independent read-only agent. Anything missing, unsure or unreadable
holds. The design is `docs/adr/2026-11-05-the-validator-approves-inside-limits-that-code-enforces.md`.

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
