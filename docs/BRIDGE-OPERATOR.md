# Slack bridge — operator steps

*Written 2026-10-10 (KIT-221). Design: `docs/adr/2026-10-10-slack-bridge-acts-for-the-owner.md`.*

The bridge lets a trusted member of your Slack workspace act as you, from the chat. Someone
asks it about a ticket, it asks its own question, and a member's "yes" is the go-ahead. It
checks with Slack itself who said yes.

**This first version acts on nothing.** It reads, checks, asks and records. A "yes" is
confirmed in the thread, and nothing in the tracker changes. Planning an idea, approving an
epic and answering a stuck session come next (KIT-222, KIT-223, KIT-224).

Every file here is generic. Keep your filled-in values in your private runbook.

---

## What you need first

- **Stage E installed.** The bridge runs as the same role account, from that account's clone
  of this kit, and reuses the tracker key Stage E already stores (`docs/STAGE-E-OPERATOR.md`).
- **A private Slack channel** where you talk to the chat bot (`docs/CHAT-LANE-OPERATOR.md`).
  The bridge reads that channel.
- **The trusted-members rule.** Anyone in the workspace can say "yes" with your reach. Admit
  only people you fully trust, as full members, with two-factor sign-in.

---

## Step 1 — Fill in the settings file

```sh
cp bridge.conf.example bridge.conf && chmod 600 bridge.conf && ${EDITOR:-nano} bridge.conf
```

Seven values are required: the role account, its kit clone, the channel id, your tracker
user id, the team keys, a job label, and the bridge bot's member ID (Step 2). None of them is
secret. Set `DISPATCHER_ENV_FILE` too, so `token` can refuse the chat bot's own token.

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

---

## Step 4 — Install the config and the job

```sh
python3 scripts/pipeline_bridge_setup.py install
python3 scripts/pipeline_bridge_setup.py install --apply
```

The first prints what it would write. The second writes the job's config, as the role
account, and its LaunchDaemon file, as root. It never starts the job.

Good: `wrote …/bridge.json` and `wrote /Library/LaunchDaemons/<label>.plist (not loaded)`,
then card CK-B2.
Not that: `UNKNOWN: the home of … could not be read`. The role account is missing or locked.

---

## Step 5 — Start the job

```sh
python3 scripts/pipeline_bridge_setup.py card CK-B2
```

Run the two `sudo launchctl` lines it prints.

Good: no output from either line.
Not that: `Bootstrap failed: 5`. It is already loaded. Go to Step 6.

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
| `dry-run` | the job's own dry run, as the role account, exits 0 |
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

Reply `yes` in that thread.

Good: *Confirmed by @you. This version of the bridge acts on nothing yet, so … was not
changed.*
Not that: *I can't take that as an answer …*. The reply names why: a guest account, an edited
message, a reply with a file, or a member not on `ALLOWED_SLACK_USERS`.

---

## Who can answer, and what is refused

The bridge checks every answer with Slack's own records. It refuses:

- a guest, a bot or an app account, a deactivated account, or one from another workspace;
- a bot post, an edited message, a message with a file, or any special message type;
- a "yes" to a question it did not ask, in another thread, or older than the question;
- a bare "yes" when two questions are open in one thread (it asks for `yes <ticket id>`);
- a bare "yes" when any other bot posted in the thread after the question, since a
  look-alike question could have misled you (it asks for `yes <ticket id>`);
- any answer to a question that was edited after it was posted (it closes the question);
- a "yes" sent more than 24 hours after the question. A "yes" sent in time still counts if
  the Mac was asleep and handles it later.

A message the bridge cannot handle three passes running is given up on, said once, and
passed over, so one bad message cannot stall it.

A request (`plan <ticket id>`) can come from anyone, the chat bot included. Asking is harmless;
only the answer acts. It asks at most 20 questions an hour, and one per ticket at a time.

---

## Turning it off

```sh
python3 scripts/pipeline_bridge_setup.py card CK-B3
```

Run the two lines it prints.

Good: `launchctl print system/<label>` says it could not find the service.
Not that: deleting the plist and stopping there. launchd keeps a loaded job until it is booted
out.

---

## What a deployment accepts when it turns this on

- **Any full member acts with your reach.** That is the access rule, by design.
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
- Everything the bridge will do in the tracker. This version does nothing there (KIT-222,
  KIT-223, KIT-224).
