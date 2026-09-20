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
a packet-filter rule, one settings file, one Slack app and one front-door path. **No session
writes the dispatcher's config.** This file is generic on purpose: no hostnames, account
names or ids from a real deployment. Keep the filled-in copy in your private runbook.

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
$EDITOR chat-lane.conf                                      # six values, none secret
python3 scripts/pipeline_chat_lane_setup.py compose         # prints every piece
```

| Command | What it does |
|---|---|
| `compose` | Prints all seven pieces and the order to apply them. Reads no live file, needs no access to the role account, changes nothing. Exit 0, or 2 on a conf error. |
| `verify` | Reads the dispatcher's config, its env file and the role account's user settings **as the role account**, and asks pf **as root** which rules it holds. One outcome per check. Prints names and tool lists, never a value. Changes nothing. Refuses to run under a model. |
| `card CK-C1` | Prints a checkpoint card: `CK-C1` create the chat app, `CK-C2` the front door, `CK-C3` the live check, `CK-C4` the port, from a second device. |

`verify` exits like the Stage E installer's: **0** every check applied, **10** something is
not applied yet, **4** a check could not measure, **1** something is broken, **5** no
administrator password, **3** refused under a model.

---

## The steps, in order

The order matters twice. The Slack fence goes in **before** the chat token reaches the
dispatcher, so no session holds a Slack server unfenced. And the port block is loaded
**before** `CYRUS_HOST_EXTERNAL=true`, so the dispatcher never listens on the network
unguarded.

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

Merge this one key into the top level of the dispatcher's config. `compose` prints it
filled in; the shape is:

```json
"slackAllowedTools": ["Read", "WebFetch", "WebSearch", "SendMessage", "ToolSearch", "mcp__slack", "mcp__linear", "Bash(git -C /ABSOLUTE/PATH/TO/REPOSITORY pull)", "Bash(git -C /ABSOLUTE/PATH/TO/REPOSITORY pull --ff-only)"]
```

Setting it replaces the built-in chat list (`ToolPermissionResolver.js:68-71`). That list
also holds `Monitor`, `Task`, `ScheduleWakeup`, `Skill` and the task tools
(`allowed-tools-defaults.js:91-119`). `Monitor` runs a shell command.

**The pull rules are per repository, with the path written out.** Repeat both rules for
every repository the dispatcher serves, using that repository's own `repositoryPath`:

```json
"Bash(git -C /srv/first-repo pull)", "Bash(git -C /srv/first-repo pull --ff-only)",
"Bash(git -C /srv/second-repo pull)", "Bash(git -C /srv/second-repo pull --ff-only)"
```

Don't: write `Bash(git -C * pull)`. A Bash rule matches the whole command text, and only
the words before the first `*` limit it — here, `git -C `. So that rule also matches
`git -C <dir> -c core.fsmonitor='<any command>' status pull`, and git's `-c` runs the
program it names. On this lane the grant is the only fence, so the rule would be a shell.
A literal path leaves nothing that can stand before `pull`.

Don't, either: **a pull rule may carry no git option other than `--ff-only`.** `-c`,
`--config-env`, `--exec-path`, `--upload-pack` and `--receive-pack` each name a program git
then runs, so a rule holding one runs that program even with the path written out and no
wildcard anywhere. `-C` is safe: it only changes directory. `verify` refuses any of the
others it finds in the live list.

**The cost, plainly: a new repository needs new rules, or its pull stops working.** Nothing
announces it — the tool call is simply refused. `verify` reads the live repository paths and
names any repository with no pull rule.

Don't: replace the whole file. The dispatcher rewrites it itself to store refreshed tracker
tokens (`EdgeWorker.js:5154-5211`), and a stale copy loses them.

**Two servers stay, whatever the list says.** The dispatcher appends every tool server it
built after the list (`ToolPermissionResolver.js:72-77`). The chat lane's deny list is
hard-coded empty (`RunnerConfigBuilder.js:78`).

| Server | What it carries |
|---|---|
| `mcp__cyrus-tools` | The dispatcher's own tool server, running in the dispatcher's process, outside any sandbox. It can send a message into **any** running session by id, with no check on the caller (`cyrus-tools/index.js:181`; `EdgeWorker.js:4138-4170`). It can read **any** file the role account can read and upload it to the tracker, public if asked (`cyrus-tools/index.js:72-107`). |
| `mcp__cyrus-docs` | A documentation search run by a third party (`McpConfigService.js:82-85`). That service sees what the session searches for. |

One more path adds tools. Any `mcp__` name in the first **active** repository entry's
`allowedTools` joins the chat grant (`RunnerConfigBuilder.js:63-66`;
`ChatRepositoryProvider.js:18-20`). First active, not first in the file: the dispatcher
skips entries with `isActive` false (`EdgeWorker.js:274-290`), so retiring one promotes the
next. Keep every entry free of them; `verify` checks the live order.

### Step 4 — Deny secret-file reads in the role account's user settings (piece 5)

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

### Step 5 — Block the dispatcher's port from the network (piece 4)

`CYRUS_HOST_EXTERNAL=true`, which Step 7 sets, makes the dispatcher listen on every network
interface (see *What `CYRUS_HOST_EXTERNAL=true` changes*). This step adds a packet-filter
rule first. It refuses the dispatcher's port on every interface except loopback, for IPv4
and IPv6. The front door still works, because it connects locally.

`compose` prints three things for this step: the rules file, a root LaunchDaemon, and the
commands that install, check, load and confirm them. Run those commands in a terminal, in
order, and stop at the first error.

- **The port** is `DISPATCHER_PORT` in your conf. It must be the port the dispatcher reads
  from `CYRUS_SERVER_PORT`, which is 3456 when unset (`WorkerService.js:190`;
  `config/constants.js:7`). `verify` compares the two without reading the value.
- **Where the rule lives.** Not in `/etc/pf.conf`, which a macOS update can reset. macOS's
  own `/etc/pf.conf` already evaluates every anchor under `com.apple`: line 26 of the file
  macOS 26.6.2 ships reads `anchor "com.apple/*"`, and `pf.conf(5)` says an anchor ending in
  `/*` evaluates every anchor attached there. So the rule loads into its own anchor,
  `com.apple/250.pipeline-dispatcher-port`. A LaunchDaemon loads it at every boot and
  enables pf with `pfctl -E`. With `-a`, `pfctl -f` loads only that anchor (`pfctl(8)`).
- **Why not the application firewall.** It does not close a port that a signed app already
  holds (measured on a deployment; KIT-117).
- **IPv6.** The dispatcher hands `0.0.0.0` to Fastify's listen
  (`SharedApplicationServer.js:75-78`). Fastify's server reference documents that as all
  IPv4 addresses only, so IPv6 should not answer today. The rule refuses IPv6 anyway, in
  case the listen address changes.

Good: `sudo pfctl -a com.apple/250.pipeline-dispatcher-port -s rules` prints two `block`
lines, one `inet` and one `inet6`, for your port. `sudo pfctl -s info | grep Status` prints
`Status: Enabled`. The dispatcher still answers `curl -s -m 5 http://127.0.0.1:<port>/status`.

Not that: no rules, or `Status: Disabled`. Stop. Do not go on to Step 7 until both are
right.

### Step 6 — Create the chat app (card CK-C1, piece 6)

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

If Slack says the request URL did not verify, carry on. Step 11 retries it.

Good: an app with exactly those four bot scopes and no user scopes.

### Step 7 — Four names in the dispatcher's env file, then restart (piece 3)

| Name | Value |
|---|---|
| `SLACK_BOT_TOKEN` | the **chat** app's bot token, typed by you |
| `SLACK_SIGNING_SECRET` | the chat app's signing secret, typed by you |
| `CYRUS_HOST_EXTERNAL` | `true` |
| `WEBHOOK_IP_VALIDATION` | `false` |

Add all four together, before the restart.

**`WEBHOOK_IP_VALIDATION=false` is not optional. Without it, tickets stop starting sessions.**
`CYRUS_HOST_EXTERNAL=true` turns on webhook source-address checks unless this name is exactly
`false` (`EdgeWorker.js:241-252`). A dispatcher that verifies tracker signatures
(`LINEAR_DIRECT_WEBHOOKS=true`) then takes tracker webhooks only from the tracker's nine
published addresses (`EdgeWorker.js:475-489`; `WebhookIpValidator.js:9-19`). Every other
address gets a 403, loopback included (`LinearEventTransport.js:89-96`). The dispatcher reads
the address from `X-Forwarded-For` (`SharedApplicationServer.js:37-40`). A front door that
does not trust its local tunnel client puts `127.0.0.1` there. So every tracker webhook is
refused, no ticket starts a session, and nothing else looks wrong.

What `false` keeps is today's behaviour, exactly. The tracker webhook stays protected by its
signature alone, as it is now. The Slack webhook gets no address list either way
(`EdgeWorker.js:743-750`).

**`SLACK_BOT_TOKEN` must be the chat app's token, never the notifier's.** A session's
environment is a copy of the dispatcher's (`session-env.js:45-65`), so every coding session
can read this token. The notifier's token lives in its own file under its own name, so a
leaked chat token cannot send a notifier ping.

Don't: add the notifier's token name to this file. Every session would inherit it, and
nothing looks wrong.

**Do not set `CYRUS_API_KEY`, `CYRUS_TEAM_ID` or `CYRUS_APP_URL` in this file.** Any
`CYRUS_API_KEY` at all makes the dispatcher act as paired with the vendor's hosted service.
Sessions then get a `log_failure_mode` tool (`EdgeWorker.js:4016-4027, 4142-4147`), and every
session's prompt tells it to use it (`RunnerConfigBuilder.js:87, 207`). That tool sends the
session id, a recap, a quote from the conversation, the failure text, the ticket id and the
workspace path to `https://app.atcyrus.com/api/failure-modes`
(`failure-modes-http-client.js:4-35`; `ConfigApiClient.js:5-12`). It sends them from the
dispatcher's own unsandboxed process. The only opt-out is leaving the key unset. With
`CYRUS_TEAM_ID` as well, whole session transcripts go there too (`EdgeWorker.js:152-173`).
`CYRUS_APP_URL` only changes where they go.

Then **restart the dispatcher** when no session is in flight. A restart stops every running
session (`docs/STAGE-E-OPERATOR.md`). Some of this reloads live, but not all of it (see
*When a change takes effect*).

### Step 8 — Confirm tickets still start sessions

Do this first after the restart, before anything on the Slack side.

Delegate one throwaway tracker ticket to the dispatcher.

Good: a session starts on it within five minutes.

Not that: no session. Roll back now. Remove `CYRUS_HOST_EXTERNAL` from the dispatcher's env
file and restart the dispatcher. Then read the dispatcher's log for
`Rejected Linear webhook from unauthorized IP` (`LinearEventTransport.js:93`).

### Step 9 — Prove the port is closed, from a second device (card CK-C4)

```sh
python3 scripts/pipeline_chat_lane_setup.py card CK-C4
```

The card has three steps: the dispatcher answers on `127.0.0.1` here; a **control** from a
second device against a port nothing listens on; then the dispatcher's port from that same
device. All three use `curl -sS`, which prints curl's own error, and report `exit=$?`.

**Only a refusal counts.** The rule is `block return`, which answers at once with a reset,
so `exit=7` is what a working rule produces. A timeout (`exit=28`) is not proof of anything:
it is what an unreachable device looks like — client isolation, a guest network, a VPN, or
the address of an interface the device cannot reach. That is why the control runs first: it
shows what a refusal looks like from there before the real check.

The second device is the proof. A request from this machine to its own network address never
crosses the network. And before Step 7's restart, the dispatcher listens on this machine
only, so every test passes for the wrong reason.

Not that: a JSON status on the second device. Remove `CYRUS_HOST_EXTERNAL` from the env file,
restart the dispatcher, and fix Step 5 before anything else.

After the next reboot, run the card again: the rule is loaded at boot.

### Step 10 — Open the front door for one path (card CK-C2, piece 7)

The reverse proxy's path allowlist gains `/slack-webhook`, and nothing else.

```sh
python3 scripts/pipeline_chat_lane_setup.py card CK-C2
```

Good: the door answers `401` on `/slack-webhook` with no signature — the dispatcher asking
for one — and anything **but** `401` on the dispatcher's config-update route, which must
never be reachable. A status or version path is not the test: a front door may forward one
on purpose for monitoring.

### Step 11 — Point Slack at it

In the chat app's Event Subscriptions page, retry the request URL. Make a private channel.
Invite the bot.

Good: Slack shows the URL as verified.

### Step 12 — Verify

```sh
python3 scripts/pipeline_chat_lane_setup.py verify
```

| Check | Passes when |
|---|---|
| `grant` | `slackAllowedTools` is the list in Step 3, with a pull rule pair for every repository path the dispatcher serves, no rule that a wildcard or a shell could widen, and no `mcp__` tools added by the first active entry |
| `coding-fence` | every non-review entry's effective list carries both Slack rules, no prompt type replaces it without them, and no review entry is missing them either |
| `chat-mcp-configs` | `slackMcpConfigs` is empty or unset. Otherwise it names each extra server |
| `dispatcher-env` | the Slack names and `CYRUS_HOST_EXTERNAL=true` are set, checked by name. No secret value is read into the command |
| `ip-validation-off` | `WEBHOOK_IP_VALIDATION=false` is set, checked by name and value (the value is not a secret). Missing or `true` while `CYRUS_HOST_EXTERNAL=true` is a **failure**: tracker webhooks are being refused |
| `notifier-token-absent` | the notifier's token name is **not** in the dispatcher's env file |
| `hosted-keys-absent` | neither `CYRUS_API_KEY` nor `CYRUS_TEAM_ID` is in the dispatcher's env file, checked by name |
| `port-block` | the anchor refuses the port for `inet` and `inet6` off loopback, pf says `Status: Enabled`, the LaunchDaemon and rules file are installed, and `CYRUS_SERVER_PORT` matches `DISPATCHER_PORT` |
| `user-settings` | the role account's settings file exists and carries every composed deny rule |

Good: `No drift: every check measures as applied.` and exit 0.

`verify` cannot see the network from outside. `port-block` says the rule is loaded; card
`CK-C4` is the proof that it works.

### Step 13 — The live check (card CK-C3)

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
| Webhook source-address checks turn on, and the dispatcher fetches GitHub's address list at start. **Step 7's `WEBHOOK_IP_VALIDATION=false` keeps them off** | `EdgeWorker.js:241-252, 411-416` | at start |
| GitHub and GitLab webhooks use signature checks, if their secrets are set | `EdgeWorker.js:561-574, 625-631` | at start |
| A tracker sign-in uses a local page instead of the hosted proxy, when `LINEAR_CLIENT_ID` is set. The self-auth command's listener binds every interface | `SharedApplicationServer.js:209-218`; `SelfAuthCommand.js:153-156` | when you sign in |

The second row matters most. With the server on every interface, a device on your network
reaches every route without the front door. That includes the dispatcher's tool server,
whose auth check lets everything through while `CYRUS_API_KEY` is unset
(`McpConfigService.js:166-170`). Step 5's rule closes that; Step 9 proves it.

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
| **Delete** `promptDefaults`, `slackMcpConfigs` or any other config key | At restart only, and worse: the merge keeps the old value (`ConfigManager.js:176-183`), so the reloaded config equals the old one and nothing is re-applied at all — while the log still prints the reload line. Set the key to an empty value instead, or restart. `verify` reads the file, so both rows go green either way. |
| The port block | Now, when the LaunchDaemon is bootstrapped, and again at every boot. |

Look for `Config file changed, reloading...` in the dispatcher's log after an edit
(`ConfigManager.js:60`). If it is not there, the edit has not loaded. A restart covers
every dispatcher row, which is why Step 7 ends in one.

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
- **A process already running on this machine can call the dispatcher's tool server.** Its
  auth check passes everything while no key is set, and a call needs a live session's id
  (`McpConfigService.js:166-181`). The key stays unset on purpose (Step 7).

### Closed once applied, open until then

- **The dispatcher listening on every network interface** once `CYRUS_HOST_EXTERNAL=true`
  loads. **Closed once the Step 5 rule is applied and card CK-C4 passes; open until then.**
  No setting keeps the signature check and drops the every-interface listen.

### Found while composing this

These came out of reading the dispatcher's source for this build.

- **The address checks would refuse every tracker webhook** behind a front door that
  forwards `127.0.0.1`: they turn on with `CYRUS_HOST_EXTERNAL=true` and allow loopback no
  exemption (`LinearEventTransport.js:89-96`). Step 7 keeps them off; Step 8 proves tickets
  still start sessions.
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
  permissions page, not on a live test (KIT-174).
- That the port rule refuses a second device. Card `CK-C4` measures it (KIT-117).
- That the anchor survives a reboot. The race is real: the operating system's own
  packet-filter job, `/System/Library/LaunchDaemons/com.apple.pfctl.plist`, runs
  `pfctl -f /etc/pf.conf` at load with nothing ordering it against this LaunchDaemon (read on
  macOS 26.6.2, 2026-09-17). Card `CK-C4` after a reboot measures it (KIT-174).
- That a macOS update keeps `anchor "com.apple/*"` in `/etc/pf.conf`. Step 5 has you check
  the line; re-check it after an update (KIT-174).
- What `pfctl -a <anchor> -s rules` prints for an anchor that was never loaded. `verify`
  reads an empty list, or an error naming the anchor, as not loaded; any other error as
  unmeasured (KIT-174).
- Whether the dispatcher gets `CYRUS_API_KEY`, `CYRUS_TEAM_ID` or `CYRUS_SERVER_PORT` from
  somewhere other than its env file, such as its service definition. `verify` reads the env
  file only (KIT-174).
- Whether a front door that trusts its tunnel client would pass the real address, so
  validation could be turned on later (KIT-174).
- How `pfctl -s rules` spells an anchor line. `verify` reads the loaded main ruleset to see
  whether anything still evaluates the anchor; when the file has the line and the loaded
  ruleset does not show it, it says "could not measure" rather than claiming drift
  (KIT-174).
- That the per-repository pull rules match what a chat session actually types. A rule is an
  exact string, so `git -C <path> pull origin main` is refused by design; only the two
  composed forms are allowed (KIT-174).
- Whether the config watcher sees an edit from an editor that replaces the file instead of
  changing it. Only a `change` event reloads (`ConfigManager.js:59`) (KIT-174).
- Whether Slack creates an app from a manifest whose request URL does not answer yet
  (KIT-174).
- Whether the Slack tool server's tools the lane uses need scopes the chat app lacks. None
  were added (KIT-174).
- That `verify` reads the env file exactly as the dispatcher's loader does. It matches
  `NAME=value` lines, with an optional `export` (KIT-174).
- That a live planning session holds the Slack server before its fence goes in. This rests
  on the source, not a live test (KIT-174).
- That seeding an idea from the chat creates exactly one backlog ticket and starts nothing
  (KIT-117).

---

## Turning it off

1. **Uninstall the chat app in Slack.** That revokes its token, and it is the step that
   really ends the lane: a copied token works until then.
2. Remove the four names from the dispatcher's env file, then restart. A reload does not
   unset a name (`Application.js:54`). The dispatcher then listens on this machine only.
3. Remove `/slack-webhook` from the front door.
4. Leave `slackAllowedTools`, the entry fences and the port block in place. Removing the
   grant key restores the built-in list, `Monitor` and `Task` included, at the next restart.
   The port block costs nothing while the dispatcher listens on this machine only.
