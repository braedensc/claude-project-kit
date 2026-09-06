# Stage E — operator steps (the poller and the bounce loop)

Stage E's **mechanism** (the four scripts below) ships in this kit, tested and CI-green.
Its **activation** — a launchd service, a config surface, and credentials — is a
per-deployment operator step, exactly the split `docs/AUTONOMY.md` already uses for the
push credential and the auto-merge tier. **No session installs, starts, or edits a
service.** Everything under "Operator steps" below is written as commands for a human to
run at their own terminal, on the machine the pipeline dispatches from.

This file is deliberately generic — no real hostnames, machine names, or absolute home
paths (this is a public template repo). A filled-in, machine-specific version of these
steps belongs in whatever private runbook already tracks the rest of a deployment's
setup, the same way this kit's own `docs/AUTONOMY.md` stays generic while a real project's
credentials and domain live elsewhere.

See `docs/adr/2026-09-05-stage-e-under-a-delegation-bound-dispatcher.md` for the design
this activates, and the KIT-89 epic's children (KIT-91–95) for what each script covers.

---

## What runs, and why none of it is a session

Four scripts, all in `scripts/`, none of which loop or daemonize on their own — each is a
single invocation that reads its inputs, does one pass, and exits. Looping is **launchd's**
job, not the script's:

| Script | What one invocation does |
|---|---|
| `pipeline_review_poller.py` | Lists open PRs, launches a review for each newly-opened pipeline ticket branch, then exits. |
| `pipeline_review_basis.py` | Resolves one ticket's review basis and exits. Called by the poller, or by hand. |
| `pipeline_bounce_local.py` | Looks at one PR's CI/review state, starts at most one `/fix-ci` session if warranted, then exits. |
| `pipeline_telemetry_local.py` | Builds and posts one telemetry comment for one run, then exits. |

None of the four is a Claude Code session, and none holds a tracker credential the way a
coding session must not (contract §8) — they are plain Python, run by launchd or by hand,
which is the same trust position the cloud template's credential-holding CI jobs already
occupy.

---

## The daemon config surface

Every value below is a **command-line flag or environment variable on the machine that
runs the poller/bounce loop** — never a value read from a ticket, and never part of
`delivery.json`. Reasonable defaults ship in the scripts themselves; nothing here is
required to change them.

| Surface | Flag | Default | Notes |
|---|---|---|---|
| Managed repository | `--repo` | the cwd's `origin` remote | One poller/bounce pair per managed repo. Run a separate pair (separate state dir) per repo, the same way Cyrus keeps a separate clone per repo. |
| Managed team key(s) | `--team-key` (repeatable) | none (accepts any) | Should match `delivery.json`'s `linear.teamKey` for that repo. |
| State root | `--state-dir` | `~/.claude/pipeline/stage-e` | Outside every worktree, daemon-owned — the seen-set, review bundles, and bounce ledger all live here. Give each managed repo its **own** subdirectory (e.g. append the repo name) if you run more than one. |
| Review model | `--model` on the poller | the reviewer's own default (a cheap model, field guide §12) | Never read from a ticket label — this is a cost decision made once, here. |
| Fix-session model | `--model` on the bounce loop | `claude-sonnet-5` | The coding model for `/fix-ci`, not the review model. |
| Fix-session turn cap | `--max-turns` on the bounce loop | 60 | Prompt-level bound; `budgets.fixIterations` in `delivery.json` is the internal iteration count `/fix-ci` reads on its own. |
| Poll interval | (not a script flag — a launchd `StartInterval`) | — | See the plist example below. |

**Where `maxBounces` and `reviewSeverityThreshold` come from is deliberately NOT on this
list.** They are read fresh from `delivery.json` on the repo's **committed default
branch** every time the bounce loop runs (`docs/PIPELINE-CONTRACT.md` §1), the same value
every other guard in this kit already trusts. Giving the daemon its own copy would be a
second place for that number to live — exactly the drift contract §1 exists to prevent.

---

## Credentials this layer needs

| Credential | Used for | Notes |
|---|---|---|
| `GH_TOKEN` or `GITHUB_TOKEN` | Reading open PRs and check runs; the one PR-comment write (through `scripts/gh_fallback.py`) | Scope it the same way `docs/AUTONOMY.md`'s push credential is scoped: a GitHub App or fine-grained PAT with *Contents* and *Pull requests* only — nothing this layer does needs *Administration* or *Workflows*. |
| `LINEAR_API_KEY` | Reading a ticket's description for the basis resolver; the one `commentCreate` write for telemetry | A personal API key works; scope it to the workspace this pipeline manages. |
| A Claude credential (`ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`) | The reviewer subprocess and the `/fix-ci` subprocess | `docs/AUTONOMY.md` already recommends metering the review lane on its own key so an unattended run cannot drain an interactive subscription window — the same recommendation applies here, doubly so for a poller that can start several reviews unattended. |

All three are plain environment variables in whatever runs the poller/bounce invocations
(a launchd plist's `EnvironmentVariables`, or a wrapper script it calls) — never a flag,
never a file inside a repo checkout.

---

## Operator steps — you run these

**1. Pick a state root and create it**, owned by whichever account runs the daemon:

```bash
mkdir -p ~/.claude/pipeline/stage-e
chmod 700 ~/.claude/pipeline/stage-e
```

**2. Put credentials in an env file OUTSIDE any repo**, mode 600:

```bash
cat > ~/.claude/pipeline/stage-e/env <<'EOF'
GH_TOKEN=...
LINEAR_API_KEY=...
ANTHROPIC_API_KEY=...
EOF
chmod 600 ~/.claude/pipeline/stage-e/env
```

**3. Write a small wrapper** that sources the env file and runs one poller tick followed by
one bounce tick over the PRs that need it. This is the one piece of glue every deployment
writes for itself — it is intentionally NOT shipped as a repo file, so activation stays an
operator artifact, not a mechanism this kit ships and could silently diverge from what a
real deployment actually runs:

```bash
cat > ~/bin/stage-e-tick.sh <<'EOF'
#!/bin/bash
set -euo pipefail
set -a; source ~/.claude/pipeline/stage-e/env; set +a
REPO_DIR=/path/to/your/local/clone   # a plain checkout with `origin` set; not a worktree
cd "$REPO_DIR"
python3 scripts/pipeline_review_poller.py --repo OWNER/REPO --team-key TEAM \
  --state-dir ~/.claude/pipeline/stage-e
# Bounce every open pipeline PR the poller has already seen once.
python3 -c '
import json, subprocess, sys
seen = json.load(open("$HOME/.claude/pipeline/stage-e/seen-prs.json"))["seen"]
for pr in seen:
    subprocess.run(["python3", "scripts/pipeline_bounce_local.py", "--pr", str(pr),
                    "--repo", "OWNER/REPO", "--state-dir",
                    "$HOME/.claude/pipeline/stage-e"])
'
EOF
chmod +x ~/bin/stage-e-tick.sh
```

**4. Create the launchd job.** A `LaunchAgent` (runs while you're logged in) is enough for
most setups; use a `LaunchDaemon` only if the machine runs headless and unattended the way
a dispatcher's own service does:

```xml
<!-- ~/Library/LaunchAgents/com.yourorg.stage-e.plist -->
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.yourorg.stage-e</string>
  <key>ProgramArguments</key>
  <array><string>/Users/you/bin/stage-e-tick.sh</string></array>
  <key>StartInterval</key><integer>120</integer> <!-- seconds between ticks -->
  <key>StandardOutPath</key><string>/Users/you/.claude/pipeline/stage-e/tick.log</string>
  <key>StandardErrorPath</key><string>/Users/you/.claude/pipeline/stage-e/tick.log</string>
</dict></plist>
```

**5. Load it:**

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.yourorg.stage-e.plist
```

(Editing the plist later needs `bootout` then `bootstrap` again — `launchctl kickstart -k`
restarts the process but does **not** re-read the plist, the same trap the field guide
records for the dispatcher's own services.)

**6. Verify, without waiting for a real PR:**

```bash
cd /path/to/your/local/clone
python3 scripts/pipeline_review_poller.py --repo OWNER/REPO --team-key TEAM --dry-run
```

A dry run lists what it would launch and launches nothing — confirm it sees your open
pipeline PRs before trusting the scheduled job to run for real.

---

## The short audit — what Stage E reads, and the one Cyrus seam

| Script | Reads | Writes |
|---|---|---|
| `pipeline_review_poller.py` | GitHub (open PRs) | Nothing directly — launches `pipeline_review_local.py`, which posts one PR comment through `gh_fallback.py`. |
| `pipeline_review_basis.py` | Linear (one ticket's description/timestamps) | Nothing. |
| `pipeline_telemetry_local.py` | A local artifact file | One Linear `commentCreate`. |
| `pipeline_bounce_local.py` | GitHub (checks, PR metadata), the committed `delivery.json`, its own ledger | One PR comment (via `gh_fallback.py`) on exhaustion only; git operations against **its own** worktree. |

None of the four imports anything from Cyrus, reads any path under a dispatcher's own
state directory, or assumes a Cyrus-created worktree exists — confirmed by grep as part
of building each one (no `/opt/clawdispatch`, no Cyrus import, anywhere in `scripts/`).

**The one coupling seam, named once rather than hidden:** a ticket a bounce would act on
might already be in a terminal state, because whatever dispatcher is running this pipeline
force-deletes its own worktree on a terminal transition (KIT-51). Stage E's answer is
structural, not a Cyrus-specific check: every fix session runs in a worktree **E creates
and removes itself**, so that deletion can never pull E's own work out from under it; a
bounce additionally skips outright when the caller can say the ticket is already terminal.
When the dispatcher this pipeline runs under is eventually replaced, this seam is the only
line that needs re-reading.

---

## What's on vs. off right now

**Mechanism:** all four scripts, tested, wired into this kit's own CI (`Kit checks`).
**Activation: OFF.** No step above has been run by this PR — nothing is scheduled,
nothing is credentialed, and no PR anywhere has been reviewed or bounced by a running
instance of this code as a result of merging it. Turning it on is entirely the six steps
above, on your own machine, in your own time.
