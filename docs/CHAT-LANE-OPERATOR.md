# Chat lane — operator steps

The chat lane lets you mention the dispatcher's Slack bot in a private channel and get an
answer. A session reads the tracker, the code and the web, then replies in the thread. It is
the dispatcher's own built-in Slack transport, with its tool list cut down. **That session
runs with no sandbox.**

**How it differs from the notifier.** The notifier tells you when something needs you. It is
its own job, with its own Slack app, and it needs no inbound address
(`docs/adr/2026-09-06-human-action-notifier-and-reply-relay.md`). The chat lane is the other
way round: *you* start a session, by typing. It uses a **second** Slack app, and Slack's
requests come in through your front door.

**Mechanism** ships in this kit as `scripts/pipeline_chat_lane_setup.py`, tested in CI.
**Activation** is yours, by hand: two edits to the dispatcher's config, one to its env file,
one settings file, one Slack app and one front-door path. **No session writes the
dispatcher's config.** This file is generic on purpose: no hostnames, account names or ids
from a real deployment. Keep the filled-in copy in your private runbook.

Every claim about the dispatcher cites its source at version 0.2.69 as `File.js:line`.

---

## Before anything: one member in the Slack workspace

The chat lane has **no user list and no channel list**. The dispatcher checks who is asking
only on tracker webhooks (`EdgeWorker.js:3083-3087, 3620-3624`). So anyone in the Slack
workspace who can mention the bot in a channel it is in starts a session.

The gate is **workspace membership**. Keep the workspace to one member: you. Turn this lane
off before anyone else joins. The private channel keeps the notifier's pings private. It
does not gate this lane.

---

## One command composes every piece

```sh
cp chat-lane.conf.example chat-lane.conf && chmod 600 chat-lane.conf
$EDITOR chat-lane.conf                                      # five values, none secret
python3 scripts/pipeline_chat_lane_setup.py compose         # prints every piece
```

| Command | What it does |
|---|---|
| `compose` | Prints all six pieces and the order to apply them. Reads no live file, needs no access to the role account, changes nothing. Exit 0, or 2 on a conf error. |
| `verify` | Reads the dispatcher's config, its env file and the role account's user settings, **as the role account**, and gives one outcome per check. Prints names and tool lists, never a value. Changes nothing. Refuses to run under a model. |
| `card CK-C1` | Prints a checkpoint card: `CK-C1` create the chat app, `CK-C2` the front door, `CK-C3` the live check. |

`verify` exits like the Stage E installer's: **0** every check applied, **10** something is
not applied yet, **4** a check could not measure, **1** something is broken, **5** no
administrator password, **3** refused under a model.

---

## The steps, in order

The order matters. The Slack fence goes in **before** the chat token reaches the dispatcher,
so no session ever holds a working Slack server unfenced.

### Step 1 — Check the workspace

Open the workspace's member list.

Good: one member, you.
Not that: anyone else. Stop here.

### Step 2 — Fence the Slack server off every other entry (piece 2)

Once the chat token is in the dispatcher's environment, **every** session gets a working
Slack server (`McpConfigService.js:87-99`). A ticket-lane session is allowed every tool it
is not denied (`ClaudeRunner.js:219-228`). So each entry needs two deny rules:

```json
"mcp__slack", "mcp__slack__*"
```

Add them to every entry in `"repositories"` **except a review entry** the Stage E
installer wrote. Its fence already names both.

**That includes a planning entry.** Its `linearMcpAttached` key is read by nothing in the
dispatcher, and every ticket session gets every server (`RunnerConfigBuilder.js:177`).

Add them to the entry's **effective** list (`ToolPermissionResolver.js:217-226`):

| The entry has… | Do this |
|---|---|
| its own `"disallowedTools"`, even `[]` | Append both rules to it. |
| no list of its own | It inherits `"defaultDisallowedTools"`. Give it its own list: a copy of the default **plus** both rules. |

Don't: give an inheriting entry a list of only the two rules. It silently drops every
default it used to inherit, and nothing looks wrong.

**Prompt types replace the list.** A `"disallowedTools"` under an entry's
`"labelPrompts"."<type>"`, or under `"promptDefaults"."<type>"`, replaces the entry's list
for sessions of that type (`ToolPermissionResolver.js:199-216`). Add both rules there too.

Two more ways the fence can open. A session routed to several entries keeps only the rules
**every** one of them denies (`ToolPermissionResolver.js:181-189`). And a `DISALLOWED_TOOLS`
environment variable replaces `defaultDisallowedTools` at start (`WorkerService.js:164-165`).

`verify` prints the exact list for each entry, ready to paste.

### Step 3 — Trim the chat grant (piece 1)

Merge this one key into the top level of the dispatcher's config:

```json
"slackAllowedTools": ["Read", "Bash(git -C * pull)", "WebFetch", "WebSearch", "SendMessage", "ToolSearch", "mcp__slack", "mcp__linear"]
```

Setting it replaces the built-in chat list (`ToolPermissionResolver.js:68-71`). That list
also holds `Monitor`, `Task`, `ScheduleWakeup`, `Skill` and the task tools
(`allowed-tools-defaults.js:91-119`). `Monitor` runs a shell command.

Don't: replace the whole file. The dispatcher rewrites it itself to store refreshed tracker
tokens (`EdgeWorker.js:5154-5211`), and a stale copy loses them.

**Two servers stay, whatever the list says.** The dispatcher appends every tool server it
built after the list (`ToolPermissionResolver.js:72-77`). The chat lane's deny list is
hard-coded empty (`RunnerConfigBuilder.js:78`).

| Server | What it carries |
|---|---|
| `mcp__cyrus-tools` | The dispatcher's own tool server, running in the dispatcher's process, outside any sandbox. It can send a message into **any** running session by id, with no check on the caller (`cyrus-tools/index.js:181`; `EdgeWorker.js:4138-4170`). It can read **any** file the role account can read and upload it to the tracker, public if asked (`cyrus-tools/index.js:72-107`). |
| `mcp__cyrus-docs` | A documentation search run by a third party (`McpConfigService.js:82-85`). That service sees what the session searches for. |

One more path adds tools. Any `mcp__` name in the **first** repository entry's
`allowedTools` joins the chat grant (`RunnerConfigBuilder.js:63-66`;
`ChatRepositoryProvider.js:18-20`). Keep that entry free of them.

### Step 4 — Deny secret-file reads in the role account's user settings (piece 4)

A chat session's folder is a fresh directory, not a project (`ChatSessionHandler.js:427-432`).
So the only settings file it loads is the role account's own `~/.claude/settings.json`
(`ClaudeRunner.js:499`). `compose` prints a `permissions.deny` block for it.

Merge the rules into that file's existing `deny` list. Keep every rule already there. No
hooks.

Don't: overwrite the file. The account may already have one, and its rules would be gone.

**Why the rules start with `//**/`.** They are this repository's own secret-file denies.
Written relative, as the repository has them, a rule matches under the session's current
directory only. For a chat session that is an empty folder. Anchored at the root, each
matches the file name anywhere. The last two rules name this deployment's dispatcher config
and env file.

**What they do not stop.** They block the `Read` tool. They do **not** stop the upload tool
in `mcp__cyrus-tools`, which opens the file inside the dispatcher's own process
(`cyrus-tools/index.js:107`). And they apply to **every** session the role account runs,
coding and review too.

### Step 5 — Create the chat app (card CK-C1, piece 5)

```sh
python3 scripts/pipeline_chat_lane_setup.py card CK-C1
```

In Slack's app settings, create a new app from the manifest `compose` printed. It asks for
exactly four bot scopes, each needed by one dispatcher call:

| Scope | Why |
|---|---|
| `app_mentions:read` | The `app_mention` event, which starts a session (`SlackEventTransport.js:211`) |
| `chat:write` | `chat.postMessage`: the reply (`SlackMessageService.js:17-31`) |
| `groups:history` | `conversations.replies` for thread context (`SlackMessageService.js:71-93`), and the `message.groups` event for follow-ups in a private-channel thread (`SlackEventTransport.js:221-235`) |
| `reactions:write` | The receipt reactions (`SlackReactionService.js:18-30`) |

No public-channel or direct-message scopes. No token rotation: the dispatcher reads one fixed
token and never refreshes it (`SlackEventTransport.js:53-55`). No Socket Mode: the transport
is inbound HTTP only (`SlackEventTransport.js:84-89`). The Slack tool server the dispatcher
starts (`McpConfigService.js:91-98`) may want more scopes for some tools. None is added.

If Slack says the request URL did not verify, carry on. Step 8 retries it.

Good: an app with exactly those four bot scopes and no user scopes.

### Step 6 — Three names in the dispatcher's env file, then restart (piece 3)

| Name | Value |
|---|---|
| `SLACK_BOT_TOKEN` | the **chat** app's bot token, typed by you |
| `SLACK_SIGNING_SECRET` | the chat app's signing secret, typed by you |
| `CYRUS_HOST_EXTERNAL` | `true` |

**`SLACK_BOT_TOKEN` must be the chat app's token, never the notifier's.** A session's
environment is a copy of the dispatcher's (`session-env.js:45-65`), so every coding session
can read this token. The notifier's token lives in its own file under its own name, so a
leaked chat token cannot send a notifier ping.

Don't: add the notifier's token name to this file. Every session would inherit it, and
nothing looks wrong.

Then **restart the dispatcher** when no session is in flight. A restart stops every running
session (`docs/STAGE-E-OPERATOR.md`). Some of this reloads live, but not all of it (see
*When a change takes effect*).

### Step 7 — Open the front door for one path (card CK-C2, piece 6)

The reverse proxy's path allowlist gains `/slack-webhook`, and nothing else.

```sh
python3 scripts/pipeline_chat_lane_setup.py card CK-C2
```

The card has three checks. The door answers `401` on `/slack-webhook` with no signature. It
still refuses `/status`. And, from **another device** on your network, the dispatcher's own
port does not answer.

Not that: the port answering another device. `CYRUS_HOST_EXTERNAL=true` makes the dispatcher
listen on every network interface (next section), so every route is then open past the door.
Stop and decide before going on.

### Step 8 — Point Slack at it

In the chat app's Event Subscriptions page, retry the request URL. Make a private channel.
Invite the bot.

Good: Slack shows the URL as verified.

### Step 9 — Verify

```sh
python3 scripts/pipeline_chat_lane_setup.py verify
```

| Check | Passes when |
|---|---|
| `grant` | `slackAllowedTools` is the list in Step 3, and the first entry adds no `mcp__` tools |
| `coding-fence` | every non-review entry's effective list carries both Slack rules, and no prompt type replaces it without them |
| `chat-mcp-configs` | `slackMcpConfigs` is empty or unset. Otherwise it names each extra server |
| `dispatcher-env` | all three names are set, checked by name. No value is read into the command |
| `notifier-token-absent` | the notifier's token name is **not** in the dispatcher's env file |
| `user-settings` | the role account's settings file exists and carries every composed deny rule |

Good: `No drift: every check measures as applied.` and exit 0.

### Step 10 — The live check (card CK-C3)

```sh
python3 scripts/pipeline_chat_lane_setup.py card CK-C3
```

In the channel, ask the bot to list its tool names. Then ask it what is waiting for you in
the tracker.

Good: no `Monitor`, `Task` or `ScheduleWakeup`; an answer from the tracker.
Expected, and accepted: tools named `mcp__cyrus-tools__…` appear. The trim cannot remove
that server.

---

## What `CYRUS_HOST_EXTERNAL=true` changes

It is set to turn on Slack's signature check. It does five things.

| Effect | Where | When |
|---|---|---|
| Slack requests are checked against `SLACK_SIGNING_SECRET` | `EdgeWorker.js:730-752`; `SlackEventTransport.js:65-80` | per request, so live |
| **The dispatcher's web server listens on every network interface, not only this machine** | `WorkerService.js:149, 191`; `EdgeWorker.js:254-257` | at start |
| Webhook source-address checks turn on, and the dispatcher fetches GitHub's address list at start. Set `WEBHOOK_IP_VALIDATION=false` to keep them off | `EdgeWorker.js:241-252, 411-416` | at start |
| GitHub and GitLab webhooks use signature checks, if their secrets are set | `EdgeWorker.js:561-574, 625-631` | at start |
| A tracker sign-in uses a local page instead of the hosted proxy, when `LINEAR_CLIENT_ID` is set. The self-auth command's listener binds every interface | `SharedApplicationServer.js:209-218`; `SelfAuthCommand.js:153-156` | when you sign in |

The second row matters most. With the server on every interface, a device on your network
reaches every route without the front door. That includes the config-update routes and the
dispatcher's tool server, whose auth check lets everything through when `CYRUS_API_KEY` is
unset (`McpConfigService.js:166-170`).

The tracker webhook gets an address check only with `LINEAR_DIRECT_WEBHOOKS=true`
(`EdgeWorker.js:475-489`). The Slack webhook never gets one (`EdgeWorker.js:743-750`).

Not changed: the Cloudflare tunnel (`SharedApplicationServer.js:81-84`), the loopback
address sessions use for the tool server (`EdgeWorker.js:4224-4230`), and where any token is
read.

---

## When a change takes effect

| Change | Takes effect |
|---|---|
| Set `slackAllowedTools` | Live, for chat sessions started or resumed after the file changes (`ConfigManager.js:51-62, 181`; `EdgeWorker.js:362-381`; `ChatSessionHandler.js:139, 279`). A session still running keeps its old list (`ChatSessionHandler.js:70-77`). |
| Remove `slackAllowedTools` | At restart only: a reload keeps the old value (`ConfigManager.js:181`). |
| Add or change a name in the env file | Live (`Application.js:52-78`), except the listening address and address checks, which are read at start. |
| Remove a name from the env file | At restart only: a reload sets names and never unsets one (`Application.js:54`). |

Look for `Config file changed, reloading...` in the dispatcher's log after an edit
(`ConfigManager.js:60`). If it is not there, the edit has not loaded. A restart covers
every row, which is why Step 6 ends in one.

---

## What a deployment accepts when it turns this on

These were named and accepted by the owner of the deployment this lane was built for. A new
deployment accepts the same list.

- **No sandbox.** The chat session has full network access, no egress allowlist and no
  limit on where it writes.
- **It can rewrite the dispatcher's config**, the file that fences every other session.
- **No identity check beyond Slack workspace membership.**
- **It can steer any running session**, through the tool server's feedback tool.
- **It can read any file the role account can read and upload it**, public if asked. A web
  page it fetches could talk it into sending a credential to a public link, with no shell.
- **Every coding session can read the chat bot's token.**

### Found while composing this — not in that list

These came out of reading the dispatcher's source for this build. Each needs its own
decision.

- **The dispatcher listens on every network interface** once `CYRUS_HOST_EXTERNAL=true`
  loads (see above). There is no setting that keeps the signature check and drops this.
- **The address checks and the GitHub fetch** turn on with it, at start.
- **The repository's deny rules are relative**, so copied verbatim they guard only the
  chat session's empty folder. `compose` anchors them. Step 4 explains.
- **The first repository entry's `mcp__` tools join the chat grant.** `verify` checks it.
- **A planning entry is not free of tool servers.** Its `linearMcpAttached` key is read by
  nothing in 0.2.69 and every ticket session gets every server. `compose` fences it like a
  coding entry.

---

## What is not proven

- That a live chat session holds the trimmed list. Card `CK-C3` measures it once (KIT-117).
- Whether the chat grant can name single tools of a server instead of the whole server
  (KIT-117).
- Whether a sandboxed coding session can actually reach Slack with the chat token, through
  its shell or the injected server (KIT-157).
- Whether the tool server's upload reads a file outside a session on a live dispatcher, and
  whether any user-level rule can take that server away from the chat lane (KIT-162).
- That the anchored deny rules block a `Read` in a chat session. This rests on Claude Code's
  permissions page, not on a live test (no ticket yet).
- Whether the dispatcher's port answers your network after the restart, and whether a host
  firewall closes it. Card `CK-C2` measures the first (no ticket yet).
- Whether a front door's forwarded address passes the tracker or GitHub address check when
  those are on. The code that enforces them was not read (no ticket yet).
- Whether the config watcher sees an edit from an editor that replaces the file instead of
  changing it. Only a `change` event reloads (`ConfigManager.js:59`) (no ticket yet).
- Whether Slack creates an app from a manifest whose request URL does not answer yet
  (no ticket yet).
- Whether the Slack tool server's tools the lane uses need scopes the chat app lacks. None
  were added (no ticket yet).
- That `verify` reads the env file exactly as the dispatcher's loader does. It matches
  `NAME=value` lines, with an optional `export` (no ticket yet).
- That a live planning session holds the Slack server before its fence goes in. This rests
  on the source, not a live test (no ticket yet).
- That seeding an idea from the chat creates exactly one backlog ticket and starts nothing
  (KIT-117).

---

## Turning it off

1. **Uninstall the chat app in Slack.** That revokes its token, and it is the step that
   really ends the lane: a copied token works until then.
2. Remove the three names from the dispatcher's env file, then restart. A reload does not
   unset a name (`Application.js:54`).
3. Remove `/slack-webhook` from the front door.
4. Leave `slackAllowedTools` and the entry fences in place. Removing the grant key restores
   the built-in list, `Monitor` and `Task` included, at the next restart.
