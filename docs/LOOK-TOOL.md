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
| **Browser** | Two lines let Chromium start its helpers (measured 31 Oct 2026). |

The command line is fixed, and the environment is the six values the browser needs. The role account's tokens and keys are
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
session only for what it judged on screen. A crashed run is recorded and counts against the plan's runs.

---

## What is proven here, and what is not

- **Proven by `--selftest`:** every refusal; the exact command line and environment; the record and its hashes; the
  per-plan count; the MCP exchange; every profile rule, read from the file; and, where `sandbox-exec` and node exist, a
  real run of node under the profile. In it the fresh folder and the worktree work, and the role home and a loopback
  port do not.
- **Not proven here:** a real Chromium run. The selftest names that check as skipped on a machine with no browser bundle.
  The owner's first live run, after the installer step, is the proof. If Chromium needs a system path this profile does not
  list, that run says which, and the profile is the one place to add it.
