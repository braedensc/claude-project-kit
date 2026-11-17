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
single self-contained file after bootstrap). Six copies drift unless something
reads all six, and a JavaScript string inside YAML is otherwise never executed
before production. So this check does both. The sixth is pipeline-alert, which a
caller DISPATCHES (`scripts/pipeline_watch.py run`): one issue per kind of
problem, opened on `firing` and closed on `ok`.

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
  5. A DISPATCHED alert (listed in DISPATCHED) also keeps its caller's contract —
     workflow_dispatch only, the kind/state/summary inputs, inputs reaching the
     script through env and never through an expression inside it, one run per kind
     at a time — and RUNS per DISPATCH SCENARIOS: each kind files under its own fixed
     title, which the summary cannot fork; firing creates or comments, and pages
     either way; ok comments "cleared" and closes, or says nothing was open and
     succeeds; a refused close, an unknown kind or state fails the run; an `@` in the
     summary pages nobody, and the summary can neither break its fence nor run long.
     A listed file that is gone fails too, so the list cannot rot.

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
    # A dispatched alert's inputs. `firing` is the state the generic scenarios above
    # describe; the templates that are not dispatched never read these.
    "KIND": "hooks",
    "STATE": "firing",
    "SUMMARY": "sample summary from the caller",
}

# Dispatched alerts, keyed by basename like KNOWN_BLIND, with who dispatches them.
# Their inputs are a contract with that caller: change one here and there together.
DISPATCHED = {
    "pipeline-alert.yml": "scripts/pipeline_watch.py run",
}
DISPATCH_KINDS = ["hooks", "updates", "release", "dispatcher", "front-door", "disk", "temp",
                  "notifier", "monitor", "credentials", "watch-job"]
DISPATCH_STATES = ["firing", "ok"]
SUMMARY_CAP = 4000
ISSUE_WRITES = ("issues.create", "issues.createComment", "issues.update", "issues.addAssignees")
PAYLOAD = {"workflow_run": {"name": "Deploy (prod)", "run_number": 12, "id": 1, "head_branch": "main",
                            "head_sha": "0123456789abcdef", "html_url": "https://example.invalid/runs/1",
                            "conclusion": "failure", "event": "push", "workflow_id": 9,
                            "created_at": "2026-11-17T10:00:00Z"},
           "repository": {"default_branch": "main"}}
# The failure alert, which reacts to a finished run: its own scenarios (KIT-685).
FAILURE_ALERTS = ("pipeline-failure-alert.yml",)
# The drift monitor: its detection step and its issue step (KIT-685).
DRIFT_ALERTS = ("migration-drift.yml",)

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
  actions: { listWorkflowRuns: rec('actions.listWorkflowRuns', { data: { workflow_runs: input.runs || [] } }) },
  issues: {
    listForRepo: rec('issues.listForRepo', { data: input.existingTitle ? [{ number: 7, title: input.existingTitle }] : [] }),
    create: rec('issues.create', { data: { number: 42 } }),
    createComment: rec('issues.createComment', { data: {} }),
    addAssignees: rec('issues.addAssignees', { data: {} }),
    update: rec('issues.update', { data: { number: 7, state: 'closed' } }),
  },
} }
const infos = []
const core = { debug() {}, error() {},
  info: (m) => infos.push(String(m)), notice: (m) => infos.push(String(m)),
  warning: (m) => warnings.push(String(m)), setFailed: (m) => failed.push(String(m)) }
const context = { repo: { owner: input.owner, repo: 'app' }, payload: input.payload,
  serverUrl: 'https://example.invalid', runId: 1 }
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
new AsyncFunction('github', 'context', 'core', 'process', input.script)(github, context, core, { env: input.env })
  .then(() => null, (e) => String(e && e.stack || e))
  .then((error) => process.stdout.write(JSON.stringify({ calls, failed, warnings, infos, error })))
"""


def run_script(script, owner_type, page_to=None, existing_title=None, throws=(), env_extra=None,
               payload=None, runs=None):
    env = dict(ENV, **(env_extra or {}))
    if page_to is not None:
        env["ALERT_PAGE_TO"] = page_to
    payload = {"script": script, "owner": OWNER, "ownerType": owner_type, "env": env, "payload": payload or PAYLOAD,
               "existingTitle": existing_title, "throws": list(throws), "runs": runs or []}
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


def failure_payload(conclusion="failure", branch="main", event="push", default="main"):
    run = dict(PAYLOAD["workflow_run"], conclusion=conclusion, head_branch=branch, event=event)
    return {"workflow_run": run, "repository": {"default_branch": default}}


def failure_alert_scenarios(script, doc):
    """(label, ok) for the failure alert: which finished runs alert, and which do not."""
    def go(**kw):
        runs = kw.pop("runs", None)
        throws = kw.pop("throws", ())
        return run_script(script, "User", payload=failure_payload(**kw), runs=runs, throws=throws)

    def alerts(r):
        return not r.get("error") and posted(r)[0] == "issues.create" and not r["failed"]

    job = next(iter(((doc or {}).get("jobs") or {}).values()), {})
    cond = str(job.get("if", ""))
    newer = [{"id": 2, "run_number": 13, "created_at": "2026-11-17T10:05:00Z"}]
    older = [{"id": 3, "run_number": 11, "created_at": "2026-11-17T09:00:00Z"}]
    perms = (doc or {}).get("permissions") or {}
    out = [("the lookup for a newer run has the permission it needs: actions: read",
            "github.rest.actions." not in script or perms.get("actions") == "read"),
           ("the job runs for a failed, a timed-out and a cancelled run",
            all(w in cond for w in ("failure", "timed_out", "cancelled")))]
    r = go()
    out.append(("a failed run on main alerts, naming the conclusion",
                alerts(r) and "`failure`" in posted(r)[1].get("body", "")))
    r = go(conclusion="timed_out")
    out.append(("a timed_out run on main alerts, naming the conclusion",
                alerts(r) and "`timed_out`" in posted(r)[1].get("body", "")))
    r = go(conclusion="cancelled")
    out.append(("a cancelled run nothing replaced alerts", alerts(r) and "`cancelled`" in posted(r)[1].get("body", "")))
    r = go(conclusion="cancelled", runs=older)
    out.append(("a cancelled run with only OLDER runs beside it still alerts", alerts(r)))
    r = go(conclusion="cancelled", runs=newer)
    out.append(("a cancelled run a newer run replaced does not page, and says why",
                not r.get("error") and not writes(r) and not r["failed"]
                and any("replaced by run #13" in m for m in r["infos"])))
    r = go(conclusion="cancelled", runs=newer, throws=("actions.listWorkflowRuns",))
    out.append(("a failed lookup for a newer run alerts anyway, with a warning",
                alerts(r) and bool(r["warnings"])))
    r = go(conclusion="timed_out", runs=newer)
    out.append(("a newer run never silences a timed_out run", alerts(r)))
    r = go(branch="nightly", event="schedule")
    out.append(("a failed scheduled run on a branch that is not main alerts", alerts(r)))
    r = go(branch="develop", default="develop")
    out.append(("the default branch is read from the repository, not assumed to be main", alerts(r)))
    r = go(branch="feature-x")
    out.append(("a failed push run on a feature branch does not alert",
                not r.get("error") and not writes(r) and not r["failed"]))
    return out


def drift_scenarios(script, doc):
    """(label, ok) for the drift monitor: the detection step run for real against a stub CLI,
    and the issue step for an unchecked run."""
    import tempfile
    out = []
    steps = [s for j in ((doc or {}).get("jobs") or {}).values() for s in (j.get("steps") or [])]
    detect = next((s for s in steps if s.get("id") == "drift"), None)
    if detect is None:
        return [("the detection step (id: drift) exists", False)]
    cases = [
        ("a dry run that fails to run reads \"could not check\", not \"no drift\"", 1, "connection refused", "unchecked=true", "drift=false"),
        ("a dry run that prints nothing it recognises reads \"could not check\"", 0, "something new", "unchecked=true", "drift=false"),
        ("a dry run that finds pending migrations reads drift", 0,
         "Would push these migrations:\n 20260101000000_init.sql", "drift=true", "unchecked"),
        ("a dry run that says up to date reads clean", 0, "Remote database is up to date.", "drift=false", "unchecked"),
    ]
    for label, code, text, want, not_want in cases:
        with tempfile.TemporaryDirectory(prefix="alert-pages-") as tmp:
            stub = os.path.join(tmp, "supabase")
            with open(stub, "w", encoding="utf-8") as fh:
                fh.write('#!/bin/sh\nprintf \'%b\\n\' "$STUB_OUT"\nexit "$STUB_CODE"\n')
            os.chmod(stub, 0o755)
            gh_out = os.path.join(tmp, "out")
            open(gh_out, "w").close()
            env = dict(os.environ, PATH=tmp + os.pathsep + os.environ.get("PATH", ""), SUPABASE_DB_URL="x",
                       GITHUB_OUTPUT=gh_out, STUB_OUT=text, STUB_CODE=str(code))
            proc = subprocess.run(["bash", "-eo", "pipefail", "-c", detect.get("run", "")], env=env, capture_output=True, text=True)
            with open(gh_out, encoding="utf-8") as fh:
                got = fh.read()
        out.append((label, proc.returncode == 0 and want in got and not_want not in got))
    for unchecked in (True, False):
        r = run_script(script, "User", env_extra={"UNCHECKED": "true" if unchecked else "false"})
        api, args = posted(r)
        body, title = args.get("body", ""), args.get("title", "")
        if unchecked:
            ok = (not r.get("error") and api == "issues.create" and "could not be checked" in title
                  and "**could not check**" in body and "behind" not in title
                  and any("COULD NOT CHECK" in f for f in r["failed"]))
            out.append(("an unchecked run files its own issue, pages, and FAILS the run", ok))
        else:
            ok = (not r.get("error") and "behind" in title and "could not check" not in body and not r["failed"])
            out.append(("a drift run still files the behind issue and passes", ok))
    return out


def writes(result):
    return [c for c in result.get("calls", []) if c[0] in ISSUE_WRITES]


def dispatch_contract(doc, step):
    """What the caller relies on, read from the workflow itself. A list of breaches."""
    on = (doc or {}).get("on", (doc or {}).get(True)) or {}
    inputs = ((on.get("workflow_dispatch") if isinstance(on, dict) else None) or {}).get("inputs") or {}
    kind, state, summary = (inputs.get(k) or {} for k in ("kind", "state", "summary"))
    script = ((step.get("with") or {}).get("script")) or ""
    env = step.get("env") or {}
    conc = (doc or {}).get("concurrency") or {}
    rules = [
        (isinstance(on, dict) and list(on) == ["workflow_dispatch"], "the only trigger must be workflow_dispatch"),
        (kind.get("type") == "choice" and kind.get("options") == DISPATCH_KINDS,
         f"input `kind` must be a choice of exactly {DISPATCH_KINDS}"),
        (state.get("type") == "choice" and state.get("options") == DISPATCH_STATES,
         f"input `state` must be a choice of exactly {DISPATCH_STATES}"),
        (summary.get("type") == "string" and summary.get("required") is False,
         "input `summary` must be an optional string"),
        (all(str(env.get(k.upper(), "")).strip() == f"${{{{ inputs.{k} }}}}" for k in ("kind", "state", "summary")),
         "env must pass KIND, STATE and SUMMARY from the inputs"),
        ("${{" not in script, "an expression inside `script:` is pasted into the JavaScript — pass it through env"),
        ((doc or {}).get("permissions") == {"contents": "read", "issues": "write"},
         "permissions must be exactly contents: read, issues: write"),
        (conc.get("group") == "pipeline-alert-${{ inputs.kind }}" and conc.get("cancel-in-progress") is False,
         "concurrency must queue one run per kind (two at once would file two issues)"),
    ]
    return [why for ok, why in rules if not ok]


def dispatch_scenarios(script):
    """(label, ok) for a dispatched alert: one issue per kind, opened and closed by state."""
    def go(kind="disk", state="firing", summary="disk 97% full on /", **kw):
        return run_script(script, "User", env_extra={"KIND": kind, "STATE": state, "SUMMARY": summary}, **kw)

    out = []
    titles = {}
    for kind in DISPATCH_KINDS:
        a, b = (posted(go(kind, summary=s))[1].get("title") for s in ("one wording", "@another `wording`"))
        titles[kind] = a if a == b else None
    out.append(("every kind files under its own FIXED title, which the summary cannot fork",
                all(titles.values()) and len(set(titles.values())) == len(DISPATCH_KINDS)))
    title = titles.get("disk")
    page = [{"owner": OWNER, "repo": "app", "issue_number": 42, "assignees": [OWNER]}]

    r = go()
    api, args = posted(r)
    body = args.get("body", "")
    out.append(("firing, nothing open → opens the issue, summary fenced, pages and assigns",
                not r.get("error") and api == "issues.create" and args.get("title") == title
                and "```text\ndisk 97% full on /\n```" in body and f"cc @{OWNER} (the repository owner)" in body
                and assigned(r) == page and not r["failed"]))

    r = go(existing_title=title)
    api, args = posted(r)
    out.append(("firing, issue open → comments on it and pages again (mention + assign)",
                not r.get("error") and api == "issues.createComment" and args.get("issue_number") == 7
                and "disk 97% full on /" in args.get("body", "") and f"cc @{OWNER}" in args.get("body", "")
                and [a["issue_number"] for a in assigned(r)] == [7]
                and not any(c[0] == "issues.update" for c in r["calls"]) and not r["failed"]))

    r = go(state="ok", summary="back under 80%", existing_title=title)
    w = writes(r)
    out.append(("ok, issue open → comments \"cleared\", then closes it; pages nobody",
                not r.get("error") and [c[0] for c in w] == ["issues.createComment", "issues.update"]
                and w[0][1].get("issue_number") == 7 and "Cleared" in w[0][1].get("body", "")
                and "back under 80%" in w[0][1].get("body", "") and "cc @" not in w[0][1].get("body", "")
                and w[1][1] == {"owner": OWNER, "repo": "app", "issue_number": 7, "state": "closed"}
                and not r["failed"]))

    r = go(state="ok", summary="")
    out.append(("ok, nothing open → writes nothing, succeeds, and says which nothing",
                not r.get("error") and not writes(r) and not r["failed"]
                and any("nothing open, nothing to close" in m for m in r["infos"])))

    r = go(state="ok", existing_title=title, throws=("issues.update",))
    out.append(("ok, close refused → the run FAILS",
                not r.get("error") and any("could not close" in f for f in r["failed"])))

    r = go(kind="bogus")
    out.append(("unknown kind → FAILS, files nothing",
                not r.get("error") and not writes(r) and any("unknown kind" in f for f in r["failed"])))

    r = go(state="maybe")
    out.append(("unknown state → FAILS, files nothing",
                not r.get("error") and not writes(r) and any("unknown state" in f for f in r["failed"])))

    r = go(summary="ping @someone and @acme/on-call")
    api, args = posted(r)
    body = args.get("body", "")
    out.append(("an @mention in the summary pages nobody: defused, never assigned",
                not r.get("error") and "@\u200bsomeone" in body and "@\u200bacme/on-call" in body
                and "@someone" not in body and "@acme/on-call" not in body
                and f"cc @{OWNER} (the repository owner)" in body and assigned(r) == page))

    r = go(summary="```\n**escaped**\n```\n" + "x" * (SUMMARY_CAP + 500))
    api, args = posted(r)
    body = args.get("body", "")
    out.append(("a hostile summary cannot break its fence and is capped, saying so",
                not r.get("error") and body.count("```") == 2 and "**escaped**" in body
                and "x" * SUMMARY_CAP not in body and "truncated" in body))
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

    fails, blocks, steps_checked, dispatched, seen = [], {}, 0, set(), set()
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
        doc = yaml.safe_load(text)
        for where, step, script in alert_steps(doc):
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
            for names, fn in ((FAILURE_ALERTS, failure_alert_scenarios), (DRIFT_ALERTS, drift_scenarios)):
                if base in names:
                    seen.add(base)
                    for name, ok in fn(script, doc):
                        print(f"  {'ok  ' if ok else 'FAIL'} {path}: {name}")
                        if not ok:
                            fails.append(f"{label}: {name}")
            if base in DISPATCHED:
                dispatched.add(base)
                for why in dispatch_contract(doc, step):
                    print(f"  FAIL {path}: {why}")
                    fails.append(f"{label}: breaks the contract with {DISPATCHED[base]}: {why}")
                for name, ok in dispatch_scenarios(script):
                    print(f"  {'ok  ' if ok else 'FAIL'} {path}: {name}")
                    if not ok:
                        fails.append(f"{label}: {name}")
        if not found:
            fails.append(f"{path}: carries the block outside an actions/github-script step's script")

    listed = {os.path.basename(p) for p in workflow_files()}
    for base in sorted(set(KNOWN_BLIND) - listed):
        fails.append(f"KNOWN_BLIND names {base}, which is in neither {' nor '.join(SCAN_DIRS)} — remove it")
    for base in sorted(set(DISPATCHED) - dispatched):
        fails.append(f"DISPATCHED names {base}, but no alert step in {' or '.join(SCAN_DIRS)} has that name, "
                     f"so its open/close scenarios never ran — remove it, or restore the file")
    for base in sorted((set(FAILURE_ALERTS) | set(DRIFT_ALERTS)) - seen):
        fails.append(f"{base} is named for its own scenarios but no alert step in {' or '.join(SCAN_DIRS)} "
                     f"has that name, so they never ran — remove it, or restore the file")
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
          f"{len(dispatched)} dispatched alert(s) keep their caller's contract and open/close as specified; "
          f"{len(KNOWN_BLIND)} known exception(s) still listed with a reason")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from scratch_run import contained       # the detection scenarios' temp folders go when it ends
    sys.exit(contained(main, "alert-pages-run-"))
