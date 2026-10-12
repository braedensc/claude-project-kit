# Slack bridge — operator steps

*Written 2026-10-10 (KIT-221); updated 2026-10-10 (KIT-222, planning an idea; KIT-223,
approving a plan). Design: `docs/adr/2026-10-10-slack-bridge-acts-for-the-owner.md`.*

The bridge lets a trusted member of your Slack workspace act as you, from the chat. Someone
asks it about a ticket, it asks its own question, and a member's "yes" is the go-ahead. It
checks with Slack itself who said yes.

**It does three things, and only once you switch it on.** With `ACT=yes`:

- a verified "yes" to `plan <ticket id>` moves that idea into Plan it, as you, and the
  planner starts on it;
- a verified "yes" to `approve <epic id>` moves the epic to Todo. Then it hands the children
  to the agent, as you, in the order the plan needs them;
- a verified member's reply under a "session stopped and needs a decision" ping goes to that
  session's own thread, as your answer, and the session carries on (KIT-224).

With `ACT=no`, the default, it asks and records, and nothing in the tracker changes.

Every file here is generic. Keep your filled-in values in your private runbook.

---

## What you need first

- **Stage E installed.** The bridge runs as the same role account, from that account's clone
  of this kit, and reuses the tracker key Stage E already stores (`docs/STAGE-E-OPERATOR.md`).
- **A private Slack channel** where you talk to the chat bot (`docs/CHAT-LANE-OPERATOR.md`).
  The bridge reads that channel.
- **The trusted-members rule.** Anyone in the workspace can say "yes" with your reach. Admit
  only people you fully trust, as full members, with two-factor sign-in.

### What runs `sudo`, and what you run

- **`token`, `install` and `verify` run `sudo` themselves.** Each asks for your
  administrator password once, at the start. Then it reads and writes as the role account,
  and `install --apply` writes the job's file into `/Library/LaunchDaemons` as root.
  `verify` also asks launchd, as root, whether the job is loaded.
- **You run the `launchctl` lines that start and stop the job.** Cards CK-B2 and CK-B3
  print them. The installer never runs them.
- **`compose` and `card` need no password.** They read nothing on the machine.

---

## Step 1 — Fill in the settings file

```sh
cp bridge.conf.example bridge.conf && chmod 600 bridge.conf && ${EDITOR:-nano} bridge.conf
```

Seven values are required: the role account, its kit clone, the channel id, your tracker
user id, the team keys, a job label, and the bridge bot's member ID (Step 2). None of them is
secret. Set `DISPATCHER_ENV_FILE` too, so `token` can refuse the chat bot's own token.
It must be a different file from `ENV_FILE`. The dispatcher hands its env file to every
session it starts, so the bridge's token must never go there. The installer refuses the
same file.

Then read what it builds:

```sh
python3 scripts/pipeline_bridge_setup.py compose
```

Good: three pieces print, the app manifest, the job's config and its command.
Not that: `CONF PROBLEMS`. Fix every line it names, then run it again.

---

## Step 2 — Create the bridge's own Slack app

```sh
python3 scripts/pipeline_bridge_setup.py card CK-B1
```

Follow the card. Create the app from the manifest `compose` printed, install it to the
workspace, and invite it to each channel with `/invite @<app name>`. Then open the app's
profile in the channel, choose **Copy member ID**, and put it in the conf as `BOT_USER_ID`.
The job refuses to run with any token that answers to a different bot.

Good: the app shows in the channel's member list.
Not that: reusing the chat bot's app, or the notifier's. The bridge needs its own bot identity.
Otherwise the chat bot could post a question that looks like the bridge's.

**Don't:** give the app more scopes than the manifest lists. It needs three:
`groups:history`, `users:read` and `chat:write`.

---

## Step 3 — Put the token in place

Run this in a terminal you type into. It asks for the token at a hidden prompt.

```sh
python3 scripts/pipeline_bridge_setup.py token
```

Good: `wrote BRIDGE_SLACK_BOT_TOKEN (… characters) into …/env, mode 600`. The token itself is
never printed.
Not that: `REFUSED: that token is already in the env file under …`. You pasted another app's
token. Copy the bridge app's own.
Not that: `REFUSED: … is the same file as DISPATCHER_ENV_FILE`. The two settings name one
file, perhaps through a link. Point `ENV_FILE` at the role account's own env file.

---

## Step 4 — Install the config and the job

```sh
python3 scripts/pipeline_bridge_setup.py install
python3 scripts/pipeline_bridge_setup.py install --apply
```

The first prints what it would write. The second writes the job's config, as the role
account, and its LaunchDaemon file, as root. It never starts the job. The job is installed
turned off, so a restart before Step 5 does not start it either.

Good: `wrote …/bridge.json` and `wrote /Library/LaunchDaemons/<label>.plist (installed
turned off, not loaded)`, then card CK-B2.
Not that: `UNKNOWN: the home of … could not be read`. The role account is missing or locked.

---

## Step 5 — Start the job

```sh
python3 scripts/pipeline_bridge_setup.py card CK-B2
```

Run the two `sudo launchctl` lines it prints, in order. The `enable` line comes first: the
job is installed turned off, and it will not load until that line turns it on.

Good: no output from either line.
Not that: `Bootstrap failed: 5`. Check that the `enable` line ran. If it did, the job is
already loaded, or was unloaded a moment ago: go to Step 6. If `verify` says it is not
loaded, wait ten seconds and run both lines again.

---

## Step 6 — Check it

Wait one interval (a minute by default), then:

```sh
python3 scripts/pipeline_bridge_setup.py verify
```

| Check | Passes when |
|---|---|
| `config` | the job's config is the composed one |
| `credentials` | the env file names both the bridge's token and the tracker key (by name; no value is read into the command) |
| `plist` | the LaunchDaemon file is the composed one |
| `dry-run` | the job's own dry run, as the role account, exits 0. If it fails, the row shows the job's last line, or its error |
| `loaded` | launchd holds the job |
| `heartbeat` | the job's last real pass was recent and exited 0 |

Good: `Good: the bridge is installed, loaded and passing.` and exit 0.
Not that: `heartbeat FAILED`. Read the job's log, beside its config (`bridge.log`).

**The first pass answers nothing.** It records every message already in the channel as seen,
so an old request is never answered late.

---

## Step 7 — Try it

In the channel, type one line: `plan <ticket id>`, for a ticket on one of your teams.

Good: within a minute the bridge replies in that thread, *Plan this?*, with the ticket's
title, state, team and who filed it.
Not that: no reply after two minutes. Run `verify`; the `heartbeat` row says what the last
pass did.

**Post a request as a new message.** Each pass reads the last seven days of the channel,
and it finds a thread through the thread's first message. A request posted as a reply in a
thread whose first message is older than seven days is not read, and nothing says so. Post
it as a new message in the channel instead.

Reply `yes` in that thread.

Good, with `ACT=no`: *Confirmed by @you. Acting is switched off here, so … was not changed.*
Not that: *I can't take that as an answer …*. The reply names why: a guest account, an edited
message, a reply with a file, or a member not on `ALLOWED_SLACK_USERS`.

---

## Step 8 — Let it plan

When you have watched it ask and confirm, set `ACT=yes` in `bridge.conf`, then:

```sh
python3 scripts/pipeline_bridge_setup.py install --apply
```

The job reads its config every pass, so there is nothing to reload. A question asked before
the change is closed when answered, since its text promised something else. Ask again.

Try it on a small idea: `plan <ticket id>`, then `yes` in the thread.

Good: *Moved … to Plan it for @you. The planner starts on it within a few minutes.* The
ticket gets one comment naming who said yes.
Not that: *… looks like live work …*. The ticket is delegated, has a session, a parent,
children, a pull request, or a `provenance:*` or `agent:*` label. The bridge refuses exactly
what the planner would.

A ticket already in Plan it is refused before the bridge asks.

Before it moves anything, the bridge reads the ticket again. One already in Plan it is said
and left alone. One that moved to another team is refused. One whose title, text, team,
labels or state changed since the question moves nothing: the bridge closes the question and
asks you to ask again. It acts at most 10 times an hour.

If the comment naming who said yes cannot be written, the move still stands. The thread says
so, and every pass tries the comment again. Until it lands, each pass exits 1 and `verify`
shows the job failing.

---

## Step 9 — Let it start an approved plan

Set `AGENT_USER_NAME` in `bridge.conf` to the dispatcher's app user, spelled as the tracker
shows it. Leave `EPIC_WIP=1` at first, so one child runs at a time. Then:

```sh
python3 scripts/pipeline_bridge_setup.py install --apply
```

When the idea gate has filed a plan, say `approve <epic id>`. The bridge lists the children in
the order they will start. Reply `yes` in that thread.

Good: *Approved … for @you.* Then *Started … (1 of 4).* The epic and each child get one
comment naming who approved.
Not that: *I won't ask about approving …*. The epic must carry `provenance:agent`, and every
child `provenance:epic`. The bridge approves only what the idea gate filed.

From then on, each pass starts the next child once the tickets it waits on are Done. Each
start is said in the thread. When nothing can start, it says so once, and why.

- It never hands over the epic itself. If someone else does, it stops, because the children
  would stack on the epic's branch.
- It never hands over a child twice. A child you reopen is yours to hand over again.
- It hands over only what you saw. The question records each child's title, text, labels,
  project and what it waits on. If any of that changes before your yes, nothing is
  approved and it asks you to ask again. A child added or changed after your yes is held
  and said once. Coding sessions can write tickets, so this keeps their text from starting
  work as you. Hand it over yourself if the change is yours.
- It refuses an epic where the epic or a child carries the `orchestrator` or `graphite`
  label. Those make the dispatcher hand out or stack children itself.
- It says when a hold needs you: a child outside the plan is at work, or a child it handed
  over has not started after two hours.
- A child someone already handed over counts toward `EPIC_WIP`. So does a child marked
  Done while its session still runs, until the session ends. The bridge says so once.
- Your yes approves the terms the question stated. If `ACT`, `EPIC_WIP`, `READY_STATE` or
  `AGENT_USER_NAME` changed since it asked, nothing is approved. Ask again.
- If a comment naming who approved cannot be written, the approval or hand-over still
  stands. The thread says so, and every pass tries the comment again. Until it lands, each
  pass exits 1.
- Move the epic back to Backlog to pause. Move it to Todo and it goes on.
- Cancel the epic to stop for good.

Each child counts toward the 10 actions an hour. Merging each pull request stays yours.

---

## Step 10 — Answer a stuck session from Slack

The notifier pings you when a session stops with a question. Once this step is done, a reply
under that ping is passed to the session as your answer. The notifier's own reply relay stays
off: the bridge replaces it, with the same rules and a check with Slack on who replied.

1. Invite the bridge's app to the notifier's pings channel.
2. Add that channel's id to `CHANNEL_IDS` in `bridge.conf`, after the chat channel's.
3. In `bridge.conf`, set `NOTIFIER_STATE_DIR` to the `STATE_DIR` in `notifier.conf`. Both
   default to `~/.stage-e/state`. The bridge reads the notifier's pings file there.
4. In `bridge.conf`, set `NOTIFIER_BOT_USER_ID` to the notifier app's member ID. Copy it from
   the app's profile in Slack, as you did for `BOT_USER_ID`.
5. Set `BRIDGE_PINGS=yes` in `notifier.conf`, and run the notifier installer:

```sh
python3 scripts/pipeline_notifier_setup.py run
```

6. Then the bridge's install again:

```sh
python3 scripts/pipeline_bridge_setup.py install --apply
```

7. After the notifier's next pass, check the bridge:

```sh
python3 scripts/pipeline_bridge_setup.py verify
```

Good: the `pings` row reads `ALREADY-DONE`. The notifier writes that file on its first pass
with `BRIDGE_PINGS=yes`.
Not that: `pings BLOCKED no pings file at …`. Either `BRIDGE_PINGS` is still `no`, or the
two state dirs differ. Fix step 3 or step 5.

Try it on the next real ping: reply under it, in its thread.

Good: *Sent to … 's session as @you's answer.* The session's thread in the tracker gets one
comment, naming you, with your words.
Not that: *I can't pass that on to …*. It says why: a guest or a bot, an edited or long
message, a credential in it, the hourly limit, a ticket on a team not in `TEAM_KEYS`, or a
session thread that is gone.

A ping sent before `BRIDGE_PINGS=yes` was never recorded, so no reply under it can be passed
on. With `NOTIFIER_BOT_USER_ID` set, the bridge says so in that thread. Answer those in the
tracker. Without it, the bridge cannot tell the notifier's pings apart, and its log says on
every pass that such replies are not checked.

It passes on at most 6 answers an hour, and never one longer than 1,000 characters: a long
answer belongs in the tracker. Angle brackets are removed, so nothing you paste can turn into
a mark. If the tracker never confirms a post, it says so in the thread and never sends that
answer twice.

---

## Who can answer, and what is refused

The bridge checks every answer with Slack's own records. It refuses:

- a guest, a bot or an app account, a deactivated account, one from another workspace, or
  one Slack names no workspace for;
- a bot post, an edited message, a message with a file, or any special message type;
- a "yes" to a question it did not ask, in another thread, or older than the question;
- a bare "yes" when two questions are open in one thread (it asks for `yes <ticket id>`);
- a bare "yes" when anything but the bridge or a member posted in the thread after the
  question (a bot, an app, Slackbot or a guest), since a look-alike question could have
  misled you (it asks for `yes <ticket id>`);
- any answer to a question that was edited or deleted after it was posted (it closes the
  question);
- a "yes" sent more than 24 hours after the question. A "yes" sent in time still counts if
  the Mac was asleep and handles it later.

A message the bridge cannot handle three passes running is given up on, said once, and
passed over, so one bad message cannot stall it.

A request (`plan <ticket id>` or `approve <epic id>`) can come from anyone, the chat bot
included. Asking is harmless; only the answer acts. It looks up at most 20 requests an hour,
found or not, and asks one question per ticket at a time. Past that limit it refuses requests
without reading them, and says so once an hour. A request from the chat bot credits "the chat
bot", with no mention.

---

## Turning it off

```sh
python3 scripts/pipeline_bridge_setup.py card CK-B3
```

Run the two lines it prints.

Good: `launchctl print system/<label>` says it could not find the service.
Not that: deleting the plist and stopping there. launchd keeps a loaded job until it is booted
out.

To turn it back on, follow card CK-B2 again. Its `enable` line undoes the `disable` here.

---

## What a deployment accepts when it turns this on

- **Any full member acts with your reach.** That is the access rule, by design.
- **One approval starts paid sessions.** A single "yes" hands over every child in turn, at
  most `EPIC_WIP` at once. Move the epic back to Backlog to pause it.
- **The bridge's token and records sit in the dispatcher's account,** beside your tracker key.
  Any session holding the dispatcher's tool server can read them (KIT-162). With the token,
  someone could read the channel and post as the bridge. Such a post binds nothing: only
  questions in the bridge's own records count, an edited question is refused, and a bare
  "yes" after a post the bridge has no record of is refused too. Today no session can write
  the records, which is what keeps them from being forged.

---

## What is not proven

- That a live Slack marks guests and apps in `users.info` as its documentation says. The tests
  use the documented fields (KIT-227).
- That `groups:history` alone reads a private channel's history and threads. A missing scope
  stops the pass with a named failure (KIT-227).
- That a "yes" sent while the Mac sleeps is acted on after it wakes. The tests cover it; no
  live run has (KIT-227).
- That an idea the chat bot filed, moved by the bridge with your key, enters its history
  at once as your move, so the planner accepts it. The evidence says another actor's change
  is recorded at once; this case is untested (KIT-227).
- That handing a child to the agent with your key starts a session, as the planner's
  hand-over at creation does. The dispatcher checks who handed it over; the tests cannot
  (KIT-227).
- That reading an epic with 30 children, every pass, fits the tracker's query and rate
  limits. The key is shared with Stage E. A refusal stops the pass with a named failure
  (KIT-227).
- That a session's status reads as ended once it stops. A session left running holds its
  child's slot after Done, and the thread says so once (KIT-227).
- That an answer passed on by the bridge resumes the session as the notifier's relay did.
  The route is the one proven on 2026-09-08; this caller is not (KIT-227).
