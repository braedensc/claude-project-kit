# Two settings that are right by luck: write them down

A value that is right by luck reads exactly like a value that is right by choice. These two are
both right on the machine they were found on, for a reason nobody wrote down. Neither goes red when
it stops being right. Read this when you set up a machine, a repository, or take over from someone.

Write what you find in your **private** ops repo. Never in this repo: it is a public template, and
a figure or a login in it is a deployment fact.

## 1. The spend ceiling

The one ceiling that survives a bug in this system is the one set outside it
([AUTONOMY.md](AUTONOMY.md), *The backstop*). Nothing in the kit records that it was set, what it
is, or when anyone last looked. Which ceiling applies depends on the lane a session runs on:

| Lane | What stops a runaway | Where it is set |
|---|---|---|
| A Claude subscription (the dispatcher's token, `claude setup-token`) | The plan's usage window. A runaway uses up the window and stops. It does not bill, **unless** the plan lets use continue past the window ("extra usage"). | Your Anthropic account, on the plan |
| An API key | The workspace's monthly spend limit. | The Anthropic Console, on the workspace the key was made in |

Write down, once, then again each time you look:

- the lane each part runs on (the dispatcher, the reviewer, the tech lead's writer);
- where the ceiling is set, so the next reader does not go looking;
- the figure, if there is one, or "extra usage is off" if there is not;
- the date you checked it, and who looks next. Every quarter is enough.

What it is **not**: it is account-wide and coarse. It is a circuit breaker, not isolation, and not a
budget for one session. When it trips, everything stops at once: the workers, the reviews and the
tech lead. Nobody should mistake it for a per-session limit. The delegation lane has none, by an
accepted decision (KIT-114).

Good: `spend ceiling: subscription lane; extra usage off; checked 2026-11-10; next look February`.
Not that: `spend ceiling: set` with no date, no place and no figure.

## 2. Who a page reaches

Two Actions variables decide who an alert reaches. Both are **unset** on a fresh repository, and
that is fine until it is not.

| Variable | Used by | If it is unset |
|---|---|---|
| `ALERT_PAGE_TO` | the health alert and the other alert workflows | the repository owner, **only when the owner is a person** |
| `PR_CONFLICT_PAGE_TO` | the conflict monitor | the PR's author when a person, else a person who owns the repository |

The fallback is right when the owner of the repository is a person. It is **wrong** when:

- the repository belongs to an organisation. An @mention of an organisation notifies nobody;
- the pull requests are opened by a bot, so the author is not a person;
- there is a second owner, and the page should reach someone else.

Then the run does not look delivered. It **fails**, and the page says *Nobody was paged*. That is the
right behaviour. The first person to meet it will meet it during an incident, so settle it before.

Set a variable in Settings, Secrets and variables, Actions, Variables. It is a login, not a secret.

For each repository you set up, copy this into your ops repo and fill it in:

```
repo: <owner/name>
alerts:    ALERT_PAGE_TO = <login>          (or: fallback on purpose, the owner <login> is a person)
conflicts: PR_CONFLICT_PAGE_TO = <login>    (or: fallback on purpose, the owner <login> is a person)
checked on: YYYY-MM-DD
```

Set both on the first organisation-owned repository, the first bot-authored one, and the first one
with a second owner.

Good: `alerts: fallback on purpose, the owner is a person; checked 2026-11-10`, and a repository
that keeps a login in `ALERT_PAGE_TO`.
Not that: an organisation repository with neither set, which pages no one until the run fails.

## Why they are one guide

They are the same defect. Each is a setting whose current value is correct by the shape of one
deployment, and each breaks silently on the next machine, repository or owner. The kit does not
change the fallback: it is correct and has its own gate (`scripts/check_alert_pages.py`). It asks you
to write the choice down, so it stays a choice.
