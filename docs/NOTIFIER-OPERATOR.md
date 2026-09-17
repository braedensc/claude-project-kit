# The human-action notifier — operator steps

The tracker notifies you about everything the same way. The few events that need a
decision drown in bookkeeping. The notifier separates them: one short ping per event, to
one private Slack channel.

**Mechanism** ships in this kit as tested scripts. **Activation** is yours, at your own
terminal. No session installs, loads or edits it. This file is generic on purpose: no
account names, ids or paths from a real deployment. Keep the filled-in copy in your
private runbook.

| Script | What it is | Selftest |
|---|---|---|
| `scripts/pipeline_notify_local.py` | the notifier: one pass, then exit | `npm run test:notifier` |
| `scripts/pipeline_notifier_setup.py` | its installer | `npm run test:notifier-setup` |

Design: `docs/adr/2026-09-06-human-action-notifier-and-reply-relay.md`.

**It needs Stage E first.** The notifier runs as the dispatcher's role account, beside the
Stage E daemons. It uses that account's clone of this kit and that account's env file,
which must already hold the tracker key. Install Stage E before this
(`docs/STAGE-E-OPERATOR.md`).

---

## What it does

One pass every few minutes, as the role account:

1. It reads recent comments on the tickets of the teams you name.
2. It looks for an escalation mark on a comment's **first line**. The pipeline already
   writes these marks. A mark anywhere else is skipped, never paged.
3. For each new mark, it posts one message: what happened, the ticket id, its title, and a
   link. Nothing else.
4. For `agent:blocked` and `agent:needs-human`, it also adds that label to the ticket.

| Mark | Who writes it | The ping | Label added |
|---|---|---|---|
| `epic-awaiting-approval` | the plan executor | a plan is ready to approve | none |
| `planning-needs-input` | the plan executor | planning needs your input | `agent:blocked` |
| `planning-no-output` | the plan executor | a planning run produced nothing | `agent:blocked` |
| `planning-rejected` | the plan executor | a plan was refused | none |
| `agent:blocked` | a stopped session | a session needs a decision | `agent:blocked` |
| `agent:needs-human` | a stopped session | a session is terminal until you act | `agent:needs-human` |

**The four planning marks count only from the executor's author ids** (`EXECUTOR_ACTOR_IDS`).
A session that can comment could otherwise forge one. A planning mark from anyone else is
skipped, never paged.

**Each event pings once.** The notifier keeps a seen-set. A restart or a missed interval
re-sends nothing.

## What it never does

- It never merges, approves, or labels a pull request.
- It never moves a ticket, creates a ticket, or posts a comment. Its one tracker write is
  adding one of the two labels above.
- It never carries the comment text off the tracker. The ping holds the ticket id, the
  title and a link. The question stays in the tracker, where you are signed in.
- It reads nothing from the channel. A reply there reaches nobody. Answer in the tracker
  (KIT-118).
- It runs no model and starts no session.

---

## The private channel, and why

**On the free plan, channel membership is the only access control.** Anyone who can join
the channel reads every ping. A ping names a ticket and its title. So the channel is
private, and holds you and the notifier's bot.

Good: a second Slack account in the same workspace cannot find the channel.
Not: a public channel "that nobody else knows about".

## A separate Slack app, and the variable-name rule

**The notifier gets its own Slack app and its own token.** Never the dispatcher's.

The dispatcher has its own Slack lane. It reads a token named `SLACK_BOT_TOKEN` from the
dispatcher's environment. That environment is copied into every session. If the notifier
used that app, a token leaked from a session could post a ping that looks exactly like
the notifier's. Two apps, two tokens: a leaked chat token cannot impersonate a notifier
ping. (Owner decision, 2026-09-17.)

**The notifier's token lives under a different name: `NOTIFIER_SLACK_BOT_TOKEN`.** The
installer refuses `CHAT_TOKEN_ENV=SLACK_BOT_TOKEN`. A different name makes the separation
structural: the two values can never be the same variable by accident.

Good: `CHAT_TOKEN_ENV=NOTIFIER_SLACK_BOT_TOKEN`, and a bot token from the notifier's own app.
Not: the dispatcher's bot token, under any name.

## Where the token lives

In the role account's own env file, `~/.stage-e/env`, at mode 600, beside the tracker key.
The same three rules the notifier states when the token is missing:

1. **In the notifier's own env file**, under the role account's home, mode 600.
2. **Never in the dispatcher's env file.** It is copied unscrubbed into every session.
3. **Never under the dispatcher's state root.** What a session can read there is unmeasured.

Set `DISPATCHER_ENV_FILE` and `DISPATCHER_STATE_ROOT` in `notifier.conf`, and the installer
checks rules 2 and 3 against real paths. Left empty, it says in preflight that it did not
check them.

---

## The installer: one command, repeated

```sh
cp notifier.conf.example notifier.conf && chmod 600 notifier.conf
$EDITOR notifier.conf                                   # seven values, none of them secret
python3 scripts/pipeline_notifier_setup.py run --dry-run   # read it first
python3 scripts/pipeline_notifier_setup.py run             # the only command that changes anything
```

`run` does everything a computer can do, in order. When it reaches something only you can
do, it prints a numbered card and exits 10. Do that one thing. Run the same command again.
It checks your work and carries on.

| Command | What it is |
|---|---|
| `run` | do everything possible; stop at the first card |
| `run --dry-run` | the same pass with nothing applied. Names what would change. Asks for nothing |
| `status` | replays the ledger. Probes nothing |
| `verify` | re-measures every step. Changes nothing, records nothing, asks for no credential |
| `card CK-N1` | print a card, at any time |
| `attest A-PRIVATE-CHANNEL --initials YOUR-INITIALS --note "..."` | record something no computer can check. `YOUR-INITIALS` is refused as typed |

**The labels step reads the tracker key from YOUR shell,** for that one command. It never
reads the role account's file. Export it first:

```sh
read -rs STAGE_E_LINEAR_API_KEY && export STAGE_E_LINEAR_API_KEY
```

Not set, the labels row says **NOT MEASURED** and names this command.

**Your login password is asked for once,** at the start of `run`, `run --dry-run` and
`verify`. Declined, the command stops at exit 5 having done nothing.

### The steps

| Step | What it does | What stops it |
|---|---|---|
| `preflight` | reads the conf, finds the role account's home, checks the notifier is in that account's clone, and runs the notifier's own selftest | any problem, all listed at once |
| `slack-app` | waits for your sign-off that the app is separate and the channel private | card `CK-N1` |
| `credentials` | checks the token in the role account's env file. Its shape is judged in that account's shell; the value never reaches the installer. Missing, `run` asks at a hidden prompt and writes it, mode 600, keeping every other line | no terminal: card `CK-N2`. No tracker key: a failure naming the Stage E installer |
| `labels` | finds `agent:blocked` and `agent:needs-human` by exact name, workspace-scoped, and looks up `self` | a missing label fails and names `/setup-board`. The installer never creates one |
| `config` | writes the notifier's config, mode 600, after the notifier's **own loader** accepts it | a composition the notifier would refuse fails here, before anything is written |
| `job` | installs `/Library/LaunchDaemons/<JOB_LABEL>.plist`. **Does not load it** | — |
| `dry-run` | runs the notifier once, as the role account, through the job's own command, with `--dry-run` | exit 1, 2 or 4 fails, with the notifier's own words |
| `enable` | checks whether you loaded the job | card `CK-N3` |
| `first-ping` | waits for your sign-off on one live test | card `CK-N4` |
| `handover` | prints what is on, what is off, and what is not proven, each with a ticket id | — |

**Two sign-offs are bound to what they signed.** `A-PRIVATE-CHANNEL` records the channel
id. Change the channel and `CK-N1` comes back. `A-FIRST-PING` records the config the job
ran under. Change the config and `CK-N4` comes back.

### Under a model

`run` and `attest` refuse. There is no override flag. `run --dry-run`, `verify`, `status`
and `card` still work, and under a model they:

- ask for nothing, and acquire no password;
- attempt nothing that needs `sudo`. Those rows say **NOT MEASURED**, exit 4;
- make no tracker request, with any key;
- record nothing.

The refusal reads environment variables, so it is tamper-evident, not tamper-proof. It
stops accidents and makes the deliberate act visible. The boundaries are the self-protected
hook and the role account's file permissions.

---

## Dry run first

Run `run --dry-run` before `run`. Every row says **ALREADY-DONE**, **WOULD-CHANGE**,
**BLOCKED-ON-HUMAN**, **UNKNOWN** or **FAILED**, with its reason. Not measured is not
passed.

Then `run`. Its `dry-run` step is the first time you see the notifier work. It runs exactly
the job's command, plus `--dry-run`:

```sh
sudo -u <role-account> -H /bin/sh -c 'cd / && set -a; . "$HOME/.stage-e/env"; set +a; exec /usr/bin/python3 "$HOME/.stage-e/kit/scripts/pipeline_notify_local.py" run --config "$HOME/.stage-e/notifier.json" --dry-run'
```

It sends nothing and labels nothing. It still reads the tracker, so it needs both values.

| Exit | Meaning | The installer |
|---|---|---|
| 0 | ran; the summary says what it would send, or says "nothing to do" | done |
| 3 | ran, and declined something it names: a title withheld for carrying a credential shape, or events over the per-pass cap | done — read what it declined |
| 1 | could not do it: the tracker could not be read, or an unexpected error | failed |
| 2 | config or usage: often a token that is not set | failed |
| 4 | ran out of time | failed |

A `run` that already passed this step does not repeat it. It reads the notifier's latest
heartbeat instead, so a loaded job's real heartbeat is not replaced by a rehearsal's.

## Turn it on — card CK-N3

The installer never loads the job. You do:

```sh
sudo launchctl bootstrap system /Library/LaunchDaemons/<JOB_LABEL>.plist
sudo launchctl print system/<JOB_LABEL>
python3 scripts/pipeline_notifier_setup.py run
```

Changed the plist while it was loaded? Unload it first:
`sudo launchctl bootout system/<JOB_LABEL>`. Wait a few seconds before loading again. A load
straight after an unload can answer `Input/output error`; wait and repeat it.

To pause the notifier, unload it. A later `run` does not load it again.

## The throwaway live test — card CK-N4

One ticket, one mark, one ping.

1. Create a throwaway ticket on a team in `TEAM_KEYS`.
2. Post a comment whose **first line** is exactly:
   `<!-- pipeline-escalation: agent:blocked -->`
   Anything may follow below it.
3. Wait one interval, and a little more.
4. In the private channel: **one** ping naming that ticket. Open its link.
   Good: it opens that ticket.
5. On the ticket: the `agent:blocked` label is there.
6. Remove the label. Cancel the ticket.
7. Sign it off: `attest A-FIRST-PING --initials <yours> --note "<ticket>: pinged, link opened, label landed"`.

**Do not use a planning mark for this test.** The four planning marks count only from the
executor's author ids, so this test cannot exercise them. The first real planning run is
their test.

**No ping?** Open the comment. If the editor stored `<!--` as `&lt;!--`, the notifier was
right not to page: a neutralised mark never pages. Post the comment through the tracker's
API instead, from your own shell. Find the ticket's internal id, then post:

```sh
read -rs STAGE_E_LINEAR_API_KEY && export STAGE_E_LINEAR_API_KEY
curl -s https://api.linear.app/graphql \
  -H "Authorization: $STAGE_E_LINEAR_API_KEY" -H "Content-Type: application/json" \
  --data '{"query":"query($t:String!,$n:Float!){issues(filter:{team:{key:{eq:$t}},number:{eq:$n}}){nodes{id}}}","variables":{"t":"<TEAM>","n":<NUMBER>}}'
curl -s https://api.linear.app/graphql \
  -H "Authorization: $STAGE_E_LINEAR_API_KEY" -H "Content-Type: application/json" \
  --data '{"query":"mutation($i:String!,$b:String!){commentCreate(input:{issueId:$i,body:$b}){success}}","variables":{"i":"<ID FROM ABOVE>","b":"<!-- pipeline-escalation: agent:blocked -->\nnotifier live test"}}'
```

Still nothing? Read `~/.stage-e/notifier.log` as the role account, and its heartbeat,
`~/.stage-e/state/notifier-heartbeat.json`. A stale `at` means NOT RUNNING. A fresh one with
a non-zero `exit` means RAN AND COULD NOT, and its `summary` says why.

---

## Status and verify

`status` is as true as the last `run` that recorded. It probes nothing. `verify` measures.
Run `verify` after any merge that moves the role account's clone, and whenever `status`
looks better than the channel feels.

`verify` reads the notifier's latest heartbeat. A latest pass that exited 1, 2 or 4 is a
failed `dry-run` row, with the notifier's summary. That is the only place a dead notifier
shows up, and only when you run it.

---

## What is not proven

- **Nothing watches the notifier's own heartbeat.** A notifier that stops is silent. `verify`
  reads the heartbeat only when a person runs it. (KIT-156)
- **A stopped Stage E daemon pages nobody.** The heartbeat monitor comments with Stage E's
  tracker key, usually your own, and the tracker does not notify you of your own comment. The daemon-health page
  through this channel is not built. (KIT-156)
- **What a session can reach with the dispatcher's own Slack token.** If the dispatcher's
  Slack lane is turned on, its token is in every session's environment. The notifier's
  separate app keeps that token from posting as the notifier. Nothing stops a session with a
  shell from copying that token into a pull request body or a ticket comment: no fence
  closes that. (KIT-157)
- **A session can read the token file through the dispatcher's own tools.** Sessions run as
  the role account. A dispatcher may give sessions tools that run outside the session
  sandbox, in the dispatcher's own process. Where it does, any file that account can read —
  this env file included — is readable by a session through a tool server the dispatcher
  provides, whatever the sandbox denies. Moving the file does not help: every daemon runs as
  the same account. (KIT-162)
- **The Stage E installer rewrites the env file whole** when it stores or replaces a
  credential. That drops the notifier's token line. Every notifier pass then exits 2, and only
  `verify` here shows it. Re-run this installer after any Stage E credential change.
  (KIT-171)
- **Whether `chat:write` alone posts to a private channel** the bot was invited to. The
  notifier is built for that one scope. The throwaway test settles it. (KIT-173)
- **Whether the tracker's editor keeps `<!--` intact** on a comment's first line. The API
  path above sidesteps it. (KIT-173)
- **Whether the comment window is really newest-first.** The notifier asks for it, and names
  any ticket whose window came back full, so a wrong answer is visible, not silent.
  (KIT-173)
- **The four planning marks have not paged live.** The throwaway test cannot exercise them.
  (KIT-173)
