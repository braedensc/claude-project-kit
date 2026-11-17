# What a correct host is

*Written 2026-11-11, from the kit's code on `main` and one working host's build scripts.*

Read this before you run any installer in part 2 of `docs/START-HERE.md`. Then build the host by
your dispatcher's own guide, and check it against the list at the end. **The kit does not build
the host.** This page is a specification and a checklist, not an installer. Every name in it is
made up: `_exdispatch`, `com.example.dispatcher`, `/opt/exdispatch`.

### Words used here

| Word | Meaning |
| --- | --- |
| Host | The Mac that runs the pipeline all day, with everything below on it. |
| Dispatcher | The program that turns a delegated ticket into a coding session. The kit ships none. |
| Role account | A hidden local user the dispatcher and every kit job run as. Nobody logs in as it. |
| State folder | The dispatcher's own folder: its settings, env file, worktrees and logs. |
| Front door | A small web proxy on loopback. It passes a short list of paths to the dispatcher. |
| Tunnel | A client that gives the front door a public name, so the tracker can reach it. |
| Tracker | Where tickets live. Its webhooks start sessions. |
| Job | A launchd system job: a file in `/Library/LaunchDaemons` that keeps a program running. |

---

## Why the host comes first

Every installer in part 2 adds to a host that already runs. None creates the role account, the
dispatcher, the front door or the tunnel. Each one stops at its first step if the host is not
there. One working host was built by hand, with a set of build scripts. That build is the evidence
below.

---

## What the installers check before they run

All of them refuse to run inside a Claude session. A person runs them in a terminal
(`scripts/pipeline_stage_e_setup.py:682`).

The review installer's first step checks five things and prints every failure in one pass:

| It checks | Where |
| --- | --- |
| The role account resolves: `dscl . -read /Users/<role> NFSHomeDirectory` | `scripts/pipeline_stage_e_setup.py:2516` |
| `/usr/bin/python3 -V` runs as the role account | `scripts/pipeline_stage_e_setup.py:2524` |
| Your own `gh auth status` passes | `scripts/pipeline_stage_e_setup.py:2529` |
| `launchctl print system/<service>` answers and says `state = running` | `scripts/pipeline_stage_e_setup.py:2535` |
| The dispatcher's settings file reads as JSON, as the role account | `scripts/pipeline_stage_e_setup.py:2544` |

Before that, its settings are checked by shape:

| Setting | Rule | Where |
| --- | --- | --- |
| `ROLE_ACCOUNT` | A local account name: letter or `_`, then letters, digits, `_ . -` | `scripts/pipeline_stage_e_setup.py:843` |
| `DISPATCHER_SERVICE` | A reverse-DNS label with at least one dot | `scripts/pipeline_stage_e_setup.py:845` |
| `DISPATCHER_CONFIG` | An absolute path | `scripts/pipeline_stage_e_setup.py:916` |
| `DISPATCHER_PORT` | A port number; it reads `http://127.0.0.1:<port>/version` | `scripts/pipeline_stage_e_setup.py:922` |

Two later steps check the host as well:

- **Credentials outside the dispatcher's tree.** The role's `~/.stage-e` may not sit beside the
  settings file or under the worktrees' parent (`scripts/pipeline_stage_e_setup.py:3746`).
- **The lane marker.** It must be root's, mode 444, in folders only root can write
  (`scripts/pipeline_stage_e_setup.py:7150`).

The other installers check less:

| Installer | Checks | Where |
| --- | --- | --- |
| Idea gate | The account exists and its home reads | `scripts/pipeline_stage_a_setup.py:2200` |
| Notifier | Home is a plain path; the role's kit copy matches yours; Python runs as the role | `scripts/pipeline_notifier_setup.py:1732` |
| Queue | Home reads; clone and env file read as the role | `scripts/pipeline_queue_setup.py:482` |
| Sweep | Home reads | `scripts/pipeline_sweep_setup.py:241` |
| Watcher | Makes its own account; refuses one with a login shell | `scripts/pipeline_watcher_setup.py:475` |
| Conflict waker | Not root; a screen login; Python 3.8+ and the coding CLI on your PATH | `scripts/pipeline_conflict_waker_setup.py:414` |

**No installer reads** the account's groups, its home mode, its hidden flag, or the state folder's
mode. None tests the boundary between the role and you. Check those yourself, below.

---

## The pipeline's own account

| Property | Value | Why |
| --- | --- | --- |
| Name | `_` then lower-case letters, such as `_exdispatch` | A generic name can collide with one the system owns. |
| User id | Free, in 450 to 499 | Outside that range the role flag fails silently. |
| Primary group | Its own, made first | The default group can read your home. The group is the boundary. |
| Other groups | None. Not staff, not admin | An admin member could rewrite shared program folders. |
| Home | `~_exdispatch`, under the user folder, mode 700 | Holds its runtimes, clones, logs and kit jobs' files. |
| Shell | `/usr/bin/false` | Nobody logs in. Commands run with `sudo -u <role> -H bash -lc`. |
| Password | None set; the group's is `*` | It never logs in and never uses sudo. |
| Hidden | Off the login window | A person never picks it there. |

Run every command for it from `/`. The role cannot read your home, so a child process started
there fails.

**The boundary goes both ways.** The role cannot list your home. You cannot read its state
folder. Test both. A failed switch of user and a denied read both exit 1, so check the switch
worked.

---

## Folders and files

| Path shape | Owner, mode | Holds |
| --- | --- | --- |
| `~_exdispatch` | role, 700 | Runtimes, clones of each repository, logs |
| `~_exdispatch/.stage-e/` | role, 700; files 600 | The kit jobs' env file, kit clone and job settings |
| `/opt/exdispatch/` | role, 700 | The state folder, outside every home |
| `/opt/exdispatch/.env` | role, 600 | The dispatcher's secrets |
| `/opt/exdispatch/config.json` | role, 600 | The dispatcher's settings |
| `/opt/exdispatch/<front door file>` | role, 600 | The front door's settings |
| `/opt/exdispatch/gitconfig` | role, 644 | The git identity, readable inside a session |
| `/opt/exdispatch/worktrees/`, `state/`, `logs/`, `npm-cache/` | role | Sessions, state and its backup, logs, the package cache |
| `/Library/LaunchDaemons/<label>.plist` | root:wheel, 644 | One file per job |
| `/etc/newsyslog.d/<name>.conf` | root:wheel, 644 | Log rotation |
| `/etc/claude-pipeline/dispatched-lane/_exdispatch` | root:wheel, 444, empty | The lane marker; parents root, 755 |

Keep the state folder and the role's home as two separate trees. The review installer refuses
credentials inside the dispatcher's tree. A session's sandbox denies the role's home, so
anything a session must read lives in the state folder.

---

## The programs, and how each signs in

| Program | Where | Signs in by |
| --- | --- | --- |
| Developer tools, `/usr/bin/python3` | System | Nothing. Accept the licence first: until then Python is a stub. |
| Node 22 | A per-user version manager in the role's home | Nothing |
| The dispatcher | A global package, as the role | The tracker's app sign-in, once, as the role |
| The coding CLI, for the role | A global package, as the role | No login. A subscription token in the env file |
| The coding CLI, for you | Your own install | Your browser sign-in |
| The front door | A shared program folder, run as the role | Nothing |
| The tunnel client | A shared program folder, run as root | A token file, root, 600 |
| `gh`, `jq` | A shared program folder | Role: a token in the env file. You: your own login |

Every kit job runs `/usr/bin/python3`, so the role must be able to run it. Never put a token on a
command line. The tunnel's own setup command does that; write its job file and token file
yourself instead.

---

## The jobs that keep it running

All are system jobs, loaded with `launchctl bootstrap system`. Never user agents: the role never
logs in.

| Job | Runs as | What matters |
| --- | --- | --- |
| Dispatcher | role | Starts at load, kept alive, a 120-second exit timeout, `HOME` and `PATH` set |
| Front door | role | Starts at load, kept alive |
| Backup | role | Once a night: copies the state file aside and stamps the time |
| Tunnel | root | Starts at load, restarts on failure |
| Firewall rule | root | Starts at load, once |

Give the dispatcher its state folder with the program's own flag. In the dispatcher measured, the
environment variable did nothing. Name the dispatcher's job in reverse-DNS, such as
`com.example.dispatcher`. Kit jobs swap its last part for their own
(`scripts/pipeline_stage_e_setup.py:1088`).

**To change a job file,** boot it out, wait, then bootstrap it. A forced restart re-runs the copy
in memory and never reads the file. One host ran a stale file for four days that way.

---

## The dispatcher's settings

Name keys only; never paste a value here. The settings file is `config.json` in the state folder.

| Key | What it carries |
| --- | --- |
| `repositories` | One entry per lane, below |
| The tracker workspace keys | The tracker's tokens. Keep them byte for byte |
| `defaultRunner` | Set it explicitly |
| `sandbox` | `enabled`, `logRequests`, `networkPolicy.allow`. A non-empty allow list means deny by default |
| `userAccessControl` | `allowedUsers` by id, never by email |

A coding entry carries `id`, `name`, `repositoryPath`, `baseBranch`, `workspaceBaseDir`,
`teamKeys` (exact case) and `isActive`. Every entry has the same `workspaceBaseDir`, under the
state folder. Leave `routingLabels` and `model` off a coding entry.

**Later installers merge into this file and never overwrite it.** Each adds or updates its own
entries by `id` and keeps every other key (`scripts/pipeline_stage_e_setup.py:5351`). Each
restarts the dispatcher, so run them when no session is working. The dispatcher also rewrites the
file itself. In the one measured, it never validated the file and dropped unknown keys silently.

The env file holds the tracker app's keys, the coding CLI's token and the code host token. It
also sets `GIT_CONFIG_GLOBAL`, `GIT_CONFIG_SYSTEM` (to `/dev/null`) and `npm_config_cache`. Never
put a pay-per-use model key or a tunnel token in it.

---

## The front door, the tunnel and the firewall rule

The chain is: tracker, public name, tunnel, front door on `127.0.0.1:8787`, dispatcher on
`127.0.0.1:3456`. The ports are examples.

- **The front door** binds `127.0.0.1` only. Its automatic certificates and admin port are off.
  One named matcher lists the exact paths: the tracker webhook, the sign-in callback, `/status`.
  Everything else answers 404. The kit's chat installer finds that one line by name
  (`chat-lane.conf.example:140`).
- **The tunnel route** points at the front door's port, never the dispatcher's. Read it back from
  the tunnel client. One mistyped address once made a routable public one.
- **The firewall rule** refuses the dispatcher's port on every interface but loopback, for both
  address families. It loads at boot from anchor `com.apple/250.pipeline-dispatcher-port`
  (`scripts/pipeline_chat_lane_setup.py:497`).

---

## Staying up

| Topic | What a host needs | What nothing sets today |
| --- | --- | --- |
| Sleep | `pmset -c sleep 0`: no idle sleep on mains | A closed lid still sleeps the Mac |
| Power cut | A restart after power loss | Nothing sets it |
| Encrypted disk | The password at each cold start | An unattended restart. Only a restart you choose can skip it |
| Log rotation | Seven copies, 10 MB each, for each job's log | Tunnel logs, kit job logs, session transcripts |
| Backup | A nightly copy of the state file | An off-disk copy; secrets, settings and worktrees |

Whether system jobs start before the disk password after a power cut is not measured.

---

## Check yours

Each command only reads. Put your own names in place of the made-up ones. Run them in a terminal, from `/`.

```sh
id -P _exdispatch
```

Good: a user id from 450 to 499, a group id of its own, and the last field `/usr/bin/false`.
Not that: group id `20`, or a shell such as `/bin/zsh`.

```sh
id -Gn _exdispatch
```

Good: one group, its own.
Not that: `staff` or `admin` in the list.

```sh
sudo stat -f '%Su %Sp %N' ~_exdispatch /opt/exdispatch /opt/exdispatch/.env
```

Good: `_exdispatch drwx------` twice, then `_exdispatch -rw-------`.
Not that: any `r` or `w` in the last six letters.

```sh
cd / && sudo -u _exdispatch ls "/Users/$(id -un)"; echo "exit $?"
```

Good: `Permission denied` and `exit 1`.
Not that: a listing of your files.

```sh
cd / && sudo -u _exdispatch /usr/bin/python3 -V
```

Good: `Python 3.9` or newer.
Not that: a prompt to install developer tools.

```sh
sudo launchctl print system/com.example.dispatcher | grep 'state ='
```

Good: `state = running`.
Not that: `Could not find service`.

```sh
sudo lsof -nP -iTCP -sTCP:LISTEN | grep -E ':(3456|8787) '
```

Good: both ports on `127.0.0.1` only.
Not that: `*:3456` or a LAN address.

```sh
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8787/nothing-here
```

Good: `404`.
Not that: `200` or `502`. The catch-all is missing.

```sh
sudo pfctl -a com.apple/250.pipeline-dispatcher-port -sr
```

Good: two `block return in quick on ! lo0` lines, one per address family.
Not that: no output. The rule is not loaded.

```sh
ls -l /etc/claude-pipeline/dispatched-lane
```

Good: one empty file named for the role, owned by root, `-r--r--r--`.
Not that: `No such file or directory`.

```sh
pmset -g | grep -E '^ *sleep'
```

Good: `sleep 0` while on mains.
Not that: any number above 0.

---

## What only a person can do

These are rough minutes, not measured.

- Accept the developer-tools licence and log in at the screen. About 2 minutes.
- Choose the hostname, the repositories, their team keys and who may delegate. About 10 minutes.
- Register the domain and create the tunnel and its route. About 30 minutes.
- Mint the code host token. An organisation owner may need to approve it. About 10 minutes.
- Mint the coding CLI's subscription token in a browser. About 5 minutes.
- Create the tracker's app and its two URLs, with no trailing slash. About 15 minutes.
- Approve the dispatcher's tracker sign-in, as the role. About 5 minutes.
- Type six secret values at hidden prompts, and your Mac password. About 5 minutes.
- Decide the power and disk posture. About 5 minutes.

---

## What is not proven

- **This page was written from code and build scripts, not by running them.** No `dscl`, `launchctl`, `pmset` or `pfctl` call was made. No ticket yet.
- **The role account's hidden flag, password and real name** on the working host are not measured. No ticket yet.
- **The live settings, env and front-door files** cannot be read from the owner's account. The host list and matcher name there are not measured. No ticket yet.
- **The macOS application firewall** was last read off, months ago. Today it is not measured. No ticket yet.
- **A tunnel job file written by hand,** with the token in a file, is not measured. The working host has that shape, but the tunnel's own setup wrote it. No ticket yet.
- **System jobs before the disk password** after a power cut: not settled. No ticket yet.
- **A whole new Mac built to this page** has never been tried. No ticket yet.
- **A check that the coding CLI's token still works** runs nowhere. It expires in a year. KIT-143.
