#!/usr/bin/env python3
"""Alert pages reach a person — or the run says that they did not.

WHY THIS EXISTS. The alert templates (pipeline-failure-alert, cron-health,
frontend-uptime, migration-drift) and the dispatcher's capacity-pause notice
(pipeline-dispatch) used to @mention and assign `context.repo.owner`.
On an organization-owned repository that is an org: an @mention of an org notifies
nobody and an org cannot be assigned, so the issue was filed, looked delivered, and
reached no one. That is contract §13's worst shape — *could not do it* wearing the
face of *did the work*. Each template now resolves recipients the same way (the
ALERT_PAGE_TO repository variable, else the owner when the owner is a person), says
who in the issue, and FAILS its run when nobody can be notified.

The rule lives in a block copied into every alert template (a template must stay a
single self-contained file after bootstrap). Five copies drift unless something
reads all five, and a JavaScript string inside YAML is otherwise never executed
before production. So this check does both.

WHAT IS CHECKED, over every *.yml in templates/workflows/ and .github/workflows/:

  1. No workflow pages the repository owner BLINDLY (`cc @${owner}`,
     `assignees: [owner]`), except a file in KNOWN_BLIND — listed with a reason.
     A stale exception (the shape is gone) fails too, so the list cannot rot.
  2. Every copy of the WHO THIS ALERT PAGES block is identical.
  3. Every github-script step carrying the block passes the variable in:
     `ALERT_PAGE_TO: ${{ vars.ALERT_PAGE_TO }}`. Without it a set variable is ignored.
  4. Each such step's script RUNS under node against a mock GitHub, per SCENARIOS:
     a user owner is paged; an org owner with no variable files the issue, says
     "Nobody was paged" and fails; the variable's logins are mentioned, people
     assigned, teams not; a malformed entry is never embedded and fails the run; an
     owner lookup that errors fails the run; a refused assignment only warns.

Exit 0 = every alert workflow found passes, and the output says how many.
Exit 1 = a check failed, OR the check could not run (no node, no PyYAML, or no alert
         workflow found at all) — the output says which. Never the same words.
"""
import json
import os
import re
import shutil
import subprocess
import sys

try:
    import yaml
except ImportError:  # pragma: no cover - CI installs it alongside the YAML parse step
    print("COULD NOT RUN: PyYAML is required (python3 -m pip install pyyaml)")
    sys.exit(1)

SCAN_DIRS = ("templates/workflows", ".github/workflows")
BEGIN = "// ── WHO THIS ALERT PAGES ──"
END = "// ── end WHO THIS ALERT PAGES ──"
ENV_LINE = "${{ vars.ALERT_PAGE_TO }}"

BLIND = re.compile(r"@\$\{(?:context\.repo\.)?owner\}|assignees:\s*\[\s*(?:context\.repo\.)?owner\s*\]")
# Keyed by basename: bootstrap moves a template from templates/workflows/ to
# .github/workflows/ without changing what it does.
KNOWN_BLIND = {}

OWNER = "octo-owner"
ENV = {
    "REPORT": "http:403|5\ncron:refresh-feed|2",
    "FRONTEND_URL": "https://app.example.com",
    "LAST_CODE": "503",
    "PENDING": "20260101000000_init.sql",
}
PAYLOAD = {"workflow_run": {"name": "Deploy (prod)", "run_number": 12, "id": 1, "head_branch": "main",
                            "head_sha": "0123456789abcdef", "html_url": "https://example.invalid/runs/1"}}

# Runs one github-script body the way actions/github-script does (an async function
# over github, context, core), with every API it may call mocked and recorded.
HARNESS = r"""
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'))
const calls = [], failed = [], warnings = []
const rec = (name, result) => async (args) => {
  calls.push([name, args])
  if (input.throws && input.throws.includes(name)) throw new Error(`HttpError: mocked ${name} failure`)
  return result
}
const github = { rest: {
  repos: { get: rec('repos.get', { data: { owner: { login: input.owner, type: input.ownerType } } }) },
  issues: {
    listForRepo: rec('issues.listForRepo', { data: input.existingTitle ? [{ number: 7, title: input.existingTitle }] : [] }),
    create: rec('issues.create', { data: { number: 42 } }),
    createComment: rec('issues.createComment', { data: {} }),
    addAssignees: rec('issues.addAssignees', { data: {} }),
  },
} }
const core = { info() {}, notice() {}, debug() {}, error() {},
  warning: (m) => warnings.push(String(m)), setFailed: (m) => failed.push(String(m)) }
const context = { repo: { owner: input.owner, repo: 'app' }, payload: input.payload,
  serverUrl: 'https://example.invalid', runId: 1 }
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
new AsyncFunction('github', 'context', 'core', 'process', input.script)(github, context, core, { env: input.env })
  .then(() => null, (e) => String(e && e.stack || e))
  .then((error) => process.stdout.write(JSON.stringify({ calls, failed, warnings, error })))
"""


def run_script(script, owner_type, page_to=None, existing_title=None, throws=()):
    env = dict(ENV)
    if page_to is not None:
        env["ALERT_PAGE_TO"] = page_to
    payload = {"script": script, "owner": OWNER, "ownerType": owner_type, "env": env, "payload": PAYLOAD,
               "existingTitle": existing_title, "throws": list(throws)}
    proc = subprocess.run(["node", "-e", HARNESS], input=json.dumps(payload), capture_output=True,
                          text=True, timeout=30)
    if proc.returncode != 0 or not proc.stdout:
        return {"error": f"node exited {proc.returncode}: {proc.stderr.strip()[-500:]}"}
    return json.loads(proc.stdout)


def posted(result):
    """(api, args) of the one issue write — a create or a comment."""
    writes = [c for c in result["calls"] if c[0] in ("issues.create", "issues.createComment")]
    return writes[0] if len(writes) == 1 else (None, {})


def assigned(result):
    return [c[1] for c in result["calls"] if c[0] == "issues.addAssignees"]


def looked_up_owner(result):
    return any(c[0] == "repos.get" for c in result["calls"])


def scenarios(script):
    """(label, ok) for one alert step. Scenario 1's create supplies the dedupe title."""
    out = []

    r = run_script(script, "User")
    api, args = posted(r)
    title = args.get("title")
    out.append(("user owner, no variable → pages the owner, says so, assigns them",
                not r.get("error") and api == "issues.create" and title
                and f"cc @{OWNER} (the repository owner)" in args.get("body", "")
                and "assignees" not in args
                and assigned(r) == [{"owner": OWNER, "repo": "app", "issue_number": 42, "assignees": [OWNER]}]
                and not r["failed"]))

    r = run_script(script, "Organization")
    api, args = posted(r)
    body = args.get("body", "")
    out.append(("org owner, no variable → files the issue, says nobody was paged, FAILS the run",
                not r.get("error") and api == "issues.create" and "**Nobody was paged:**" in body
                and "ALERT_PAGE_TO" in body and "cc @" not in body and not assigned(r)
                and any("NOBODY" in f for f in r["failed"])))

    r = run_script(script, "Organization", page_to="@alice, acme/on-call bob,alice", existing_title=title)
    api, args = posted(r)
    out.append(("variable set, open issue → comments, mentions every entry once, assigns people not teams",
                not r.get("error") and api == "issues.createComment" and args.get("issue_number") == 7
                and "cc @alice, @acme/on-call, @bob (ALERT_PAGE_TO)" in args.get("body", "")
                and [a["assignees"] for a in assigned(r)] == [["alice", "bob"]]
                and assigned(r)[0]["issue_number"] == 7
                and not looked_up_owner(r) and not r["failed"]))

    r = run_script(script, "User", page_to="alice, bad;name <b>x</b>")
    api, args = posted(r)
    body = args.get("body", "")
    out.append(("variable with malformed entries → valid ones paged, bad ones never embedded, run FAILS",
                not r.get("error") and "cc @alice (ALERT_PAGE_TO)" in body
                and "bad;name" not in body and "<b>" not in body
                and any("ALERT_PAGE_TO names" in f for f in r["failed"])))

    r = run_script(script, "User", page_to="bad;name")
    api, args = posted(r)
    out.append(("variable with only malformed entries → falls back to a person owner, run still FAILS",
                not r.get("error") and f"cc @{OWNER} (the repository owner)" in args.get("body", "")
                and "bad;name" not in args.get("body", "")
                and any("ALERT_PAGE_TO names" in f for f in r["failed"])))

    r = run_script(script, "User", throws=("repos.get",))
    api, args = posted(r)
    out.append(("owner lookup errors → files the issue, says nobody was paged, FAILS the run",
                not r.get("error") and api == "issues.create"
                and "**Nobody was paged:**" in args.get("body", "")
                and any("NOBODY" in f for f in r["failed"])))

    r = run_script(script, "User", throws=("issues.addAssignees",))
    api, args = posted(r)
    out.append(("assignment refused → warns only: the @mention is the page",
                not r.get("error") and f"cc @{OWNER}" in args.get("body", "")
                and r["warnings"] and not r["failed"]))
    return out


def workflow_files():
    for d in SCAN_DIRS:
        if os.path.isdir(d):
            for name in sorted(os.listdir(d)):
                if name.endswith((".yml", ".yaml")):
                    yield os.path.join(d, name)


def alert_steps(doc):
    for job_id, job in ((doc or {}).get("jobs") or {}).items():
        for i, step in enumerate((job or {}).get("steps") or []):
            script = ((step or {}).get("with") or {}).get("script") or ""
            if str(step.get("uses", "")).startswith("actions/github-script@") and BEGIN in script:
                yield f"jobs.{job_id}.steps[{i}]", step, script


def block_of(script):
    lines = script.splitlines()
    starts = [i for i, ln in enumerate(lines) if ln.strip().startswith(BEGIN)]
    ends = [i for i, ln in enumerate(lines) if ln.strip() == END]
    if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
        return None
    return "\n".join(ln.strip() for ln in lines[starts[0]:ends[0] + 1])


def main():
    os.chdir(subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                            check=True).stdout.strip())
    if not shutil.which("node"):
        print("COULD NOT RUN: node is not on PATH, so no alert script was executed")
        return 1

    fails, blocks, steps_checked = [], {}, 0
    for path in workflow_files():
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        base = os.path.basename(path)
        blind = bool(BLIND.search(text))
        if blind and base not in KNOWN_BLIND:
            fails.append(f"{path}: pages the repository owner blindly (an org owner is notified of nothing) "
                         f"— use the WHO THIS ALERT PAGES block from pipeline-failure-alert.yml")
        if not blind and base in KNOWN_BLIND:
            fails.append(f"{path}: listed in KNOWN_BLIND but no longer pages the owner blindly — "
                         f"remove the stale exception")
        if BEGIN not in text:
            continue
        found = False
        for where, step, script in alert_steps(yaml.safe_load(text)):
            found = True
            label = f"{path} {where}"
            block = block_of(script)
            if block is None:
                fails.append(f"{label}: the WHO THIS ALERT PAGES block is not delimited exactly once")
                continue
            blocks.setdefault(block, []).append(label)
            if str((step.get("env") or {}).get("ALERT_PAGE_TO", "")).strip() != ENV_LINE:
                fails.append(f"{label}: env must pass ALERT_PAGE_TO: {ENV_LINE} — without it a set "
                             f"variable is silently ignored")
            steps_checked += 1
            for name, ok in scenarios(script):
                print(f"  {'ok  ' if ok else 'FAIL'} {path}: {name}")
                if not ok:
                    fails.append(f"{label}: {name}")
        if not found:
            fails.append(f"{path}: carries the block outside an actions/github-script step's script")

    listed = {os.path.basename(p) for p in workflow_files()}
    for base in sorted(set(KNOWN_BLIND) - listed):
        fails.append(f"KNOWN_BLIND names {base}, which is in neither {' nor '.join(SCAN_DIRS)} — remove it")
    if len(blocks) > 1:
        fails.append("the WHO THIS ALERT PAGES block differs between copies: "
                     + "; ".join(f"variant {i + 1} in {', '.join(where)}" for i, where in enumerate(blocks.values())))

    if not steps_checked and not fails:
        print("COULD NOT RUN: no alert workflow carries the WHO THIS ALERT PAGES block, so nothing was checked")
        return 1
    if fails:
        print("FAIL: alert pages that could reach nobody unnoticed:")
        for f in fails:
            print(f"  {f}")
        return 1
    print(f"OK: {steps_checked} alert step(s) — one recipient rule, every scenario behaves; "
          f"{len(KNOWN_BLIND)} known exception(s) still listed with a reason")
    return 0


if __name__ == "__main__":
    sys.exit(main())
