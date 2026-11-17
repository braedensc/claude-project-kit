# The look tool — one browser check, in a locked sandbox

*Written 2026-11-05 (KIT-323), part 2 of the acceptance check (`docs/adr/2026-11-02-acceptance-check-before-the-last-merge.md`).
Files: `scripts/pipeline_look.py`, `scripts/look_runner.mjs`, `templates/sandbox/look.sb`.*

A coding session cannot start a browser inside its own sandbox, and loosening that sandbox for every session would be
wrong. So the browser is a tool. A session that has built its app asks the look tool to run **one** browser check block
(`docs/PIPELINE-CONTRACT.md` §15) against the build folder. The tool runs it, keeps its own record, and hands back each
step's outcome and the screenshots.

Nothing here is installed by this change. The installer step, the poller that starts an acceptance session and the
bridge's gate are later parts of KIT-302.

---

## What it is

- **`pipeline_look.py`** is a stdio MCP server with one tool, `look_run`, written with the standard library only. An
  acceptance session lists it in its MCP config, a file the session cannot write.
- **`look_runner.mjs`** drives Playwright from the project's own `node_modules`. It serves the build from files, so no
  socket is opened, and the project's fake backend answers every service request.
- **`look.sb`** is the seatbelt profile the runner runs under, and the only way it is ever run.

---

## What the sandbox allows

| | |
| --- | --- |
| **Network** | None. The profile has no network rule at all, and never uses `allowLocalBinding`: that binds every interface and opens each loopback service on the Mac. |
| **Reads** | The system, the session's worktree (the build, `node_modules`, the fake backend), node, the browser bundle, the kit's scripts, the fresh output folder. Nothing else, and none of the role account's home besides node and the browser. |
| **Secrets** | Denied by name wherever they are: `.env` files, `secrets` folders, `.pem` and `.key` files, SSH keys, credential files, the Stage E settings. |
| **Writes** | The fresh output folder, and the null devices. |
| **Browser** | Two lines let Chromium start its helpers (measured 31 Oct 2026). No Security-framework or keychain service is reachable: a headless Chromium on local files ran without them (measured 5 Nov 2026), and with them a session's code could ask the keychain for what the file rules hide. |

The command line is fixed, and the environment is the five values the browser needs. The role account's tokens and keys are
never inherited.

---

## What the session chooses, and what it cannot

The session gives the tool **a build folder and a check block**. It cannot choose the profile, the browser bundle, the
environment, where the record goes, or how many runs a plan gets. Those are in the tool's config file.

| Config key | Meaning |
| --- | --- |
| `worktree` | The session's worktree. The build folder must be inside it. |
| `record_dir` | Where records go. It must be **outside** the worktree, so the session cannot write them. |
| `node`, `browsers` | The node binary and the root-owned Playwright bundle. |
| `role_home` | The role account's home, closed to the sandbox. |
| `plan` | The plan this server counts runs against. |
| `max_runs` | Runs a plan may use (default 6). |
| `timeout_seconds` | Wall time for one run (default 600). |
| `playwright_dir` | The trusted Playwright copy: absolute, outside the worktree, owned by the tool's account, not writable by others, holding `node_modules/playwright` and `node_modules/playwright-core`. Unset: no check can pass. |
| `base_git_dir` | A git directory the tool's own account owns, never the session's worktree, holding the base branch (for example a bare clone the installer keeps current). Absolute, outside the worktree, owned by the tool's account and not writable by others. Unset: no base copy is made. |
| `base_ref` | The branch in that directory the fake backend and the `acceptance` section are copied from (default `origin/main`). |

### One server line for every check: `--config-dir` (KIT-571)

An acceptance entry's MCP config cannot name each session's worktree. So it starts the tool with a folder instead:

```
python3 scripts/pipeline_look.py --config-dir /path/to/look-configs
```

The acceptance poller's live pass writes one config per check into that folder before the check's session starts. The
tool takes **the one config whose `worktree` is the folder it was started in**. The dispatcher starts the session there,
and the session cannot change it. No config naming it, or more than one, is a refusal, said in words.

The tool looks for its config file again at every run. When the live pass stops a check past its time limit, it removes
the file, and every run after that is refused.

**Good:** `--config-dir` on a folder outside every worktree, which only the role account outside the sandbox writes.
**Not:** a config folder inside a worktree. A session could write a config there and point the tool at another plan.

---

## What it refuses, in words

- A check block that fails `schemas/browser-check.schema.json`.
- A build folder outside the worktree, including `..` and a link out of it.
- A worktree whose `delivery.json` has no `acceptance` section: the check is off there.
- A plan past `max_runs`. It says how many were used and tells the session to say what it could not check.
- A run with the profile missing. The runner is never run without it.

A run that crashed, timed out or could not run is **never reported as a pass.**

---

## The record

Each run writes `<record_dir>/<plan>/<run id>.json`: the check ids, the runner's exit, each step's outcome and each
screenshot's SHA-256. The acceptance poller trusts this record for what ran and what failed mechanically, and trusts the
session only for what it judged on screen. Each record says where its outcome came from, in `outcome_trust`:

| `outcome_trust` | Meaning |
|---|---|
| `runner-only` | The project has no fake backend. The outcome is the runner's. |
| `base-copy` | The project has a fake backend, and the tool ran **the base branch's copy** of it, with **the base branch's `acceptance` section**, both read from `base_git_dir` and unpacked into the run's fresh folder where the session cannot change them. A change the session made to either does not reach the check. The session's own git state (a moved branch, a replace ref, a filter in its repository's config) is never read. |
| `session-code-in-runner` | The tool could not use a base copy, and the record's `base_copy` line says why (no `base_git_dir`, or one others can write; the base has no `acceptance` section or no such fake backend file; a link in its folder; or git would not answer). The fake backend is then the session's own code, so its pass is what that code reported, not a mechanical fact. The screenshots still get judged by eye. |

`base_ref` (config, default `origin/main`) names the branch the copy comes from. A fake backend that is to be trusted must be
self-contained: it is unpacked alone, with no `node_modules`, so a bare import fails and the run says it could not run.

**Playwright itself** loads from `playwright_dir`, never from the session's `node_modules` (KIT-550). That is a folder the
installer puts beside the browsers: it holds `node_modules/playwright` and `node_modules/playwright-core`, is owned by the
tool's account, and nobody else can write it. The tool checks every level down to the package before each run. A session
that changes its own `node_modules` changes nothing here. **If there is no such copy** (unset, incomplete, or writable by
others), no browser is started: the record says `playwright_from: none`, gives the reason, and the result is `could-not-run`,
never a pass. The selftest runs a real check with a forged `node_modules` in the worktree and the result is unchanged.
A crashed run is recorded and counts against the plan's runs.

### The screenshots

Each screenshot is **kept beside its record**, in `<record_dir>/<plan>/<run id>/<name>.png` (KIT-567). The acceptance
page sends a few of them to the owner later, so they must outlive the run's scratch folder.

The runner is untrusted, and it may leave a process behind that keeps writing. So the tool reads each screenshot
**once**, and uses those same bytes three times: for the hash in the record, for the kept file and for the image it sends
back. It reads through file descriptors that never follow a link: the run's folder, its `shots` folder, then the file.

A screenshot is kept only when all of these hold:

- It is named straight inside the run's `shots` folder, with a plain name (letters, digits, dots, dashes, underscores).
- It is a plain file with no other name. A hard link could name a file outside the run.
- It is a PNG, and no larger than 3 MB.
- It is one of the first 8 in this look run. A repeated name is kept once.

Anything else is listed in the run's `shots_refused`, by name, with why. It is never kept and never sent back. A
screenshot past the cap is still hashed in the record, with `kept: false` and why. Nothing kept is not a failure of the run.

**How big it gets.** At most 8 screenshots of 3 MB each per look run, so 24 MB. A plan has at most `max_runs` look runs
(default 6, so 144 MB at the very most). Real screenshots are far smaller. The look tool never removes them. Until the
sweep is told where they are (its `look_record_dir`, KIT-572), a finished plan's records stay until a person clears them.

**Good:** `shots_refused: [{"name": "c1--desktop--default--home.png", "why": "not a plain file with one name"}]`.
**Not:** a screenshot read through a path that was checked a moment earlier.

---

## What is proven here, and what is not

- **Proven by `--selftest`:** every refusal; the exact command line and environment; the record and its hashes; the
  kept screenshots and every way one is refused (a link, a hard link, a pipe, a bad name, not a PNG, each cap); the
  per-plan count; the MCP exchange; every profile rule, read from the file; and, where `sandbox-exec` and node exist, a
  real run of node under the profile. In it the fresh folder and the worktree work, and the role home and a loopback
  port do not. Nor can it hard-link a role-home file into the fresh folder (measured 10 Nov 2026).
- **Live, where a browser exists:** with `LOOK_LIVE_APP` set to a folder whose `node_modules` holds `playwright` and
  `playwright-core`, and a Chromium bundle installed, the selftest runs a real check on a tiny page under the real profile and
  expects a `passed` record with one screenshot hash, and that screenshot kept beside the record with the same hash.
  Elsewhere it is skipped, and says which of the three is missing (measured 5 Nov 2026, and again on 10 Nov, passing).
- **Not proven here:** a run against a real app, which is the owner's first live run after the installer step. If Chromium
  needs a system path this profile does not list, that run says which, and the profile is the one place to add it.
