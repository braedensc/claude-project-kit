#!/usr/bin/env python3
"""Caller ⇄ callee contract check for LOCAL reusable-workflow calls.

WHY THIS EXISTS. When a job does `uses: ./.github/workflows/x.yml` and the
caller/callee contract does not hold, GitHub cannot build the run graph and the
whole run ends as **`startup_failure`** — which produces *no jobs, no API
annotations and no check run*. The message exists only on the run's HTML page.
Nothing in a PR's checks list indicates anything is wrong; the workflow simply
appears not to have run. A red job is loud. This is silent, and silence is the
one failure mode a checks list can never surface.

It is also entirely decidable from the two files, which is the whole point: this
is not a thing you need a live run to learn. Every rule below is checked by
reading YAML, so it runs in CI on every PR, in a repo where the pipeline is
inert, with no credentials and no dispatch.

WHAT IS CHECKED, per call site:

  1. The callee path resolves, and the callee actually declares `workflow_call`.
  2. PERMISSION CAP — the caller job's declared permissions are a SUPERSET of
     everything the callee declares. GitHub's rule: "The GITHUB_TOKEN permissions
     passed from the caller workflow can be only downgraded (not elevated) by the
     called workflow." A callee asking for `actions: read` from a caller holding
     `actions: none` is not downgraded to none — the run refuses to start.
     This is KIT-19: `pipeline-review.yml`'s workflow-level `contents: read`
     meant `actions: none`, and every review run died before its first job.
  3. Every `required: true` input is supplied, and no unknown input is.
  4. Literal input values match the declared `type` (an expression is skipped —
     its value is not knowable here).
  5. Every `required: true` secret is supplied, or `secrets: inherit` is used;
     and no unknown secret is passed.

THE UNDECLARED CASE IS AN ERROR, NOT A PASS. If a calling job declares no
permissions at all — neither on the job nor on the workflow — its effective set
comes from a repository setting this file cannot see, so a shortfall would be
undetectable *and* invisible at runtime. A job that calls a local reusable
workflow must therefore say what it grants. That is a rule this check imposes;
it is stated here rather than buried, and it costs three lines at each call site.

TWO DIRECTORIES, ONE FILE. The kit ships workflow sources in
`templates/workflows/`; bootstrap `git mv`s them to `.github/workflows/`. A
`uses: ./.github/workflows/x.yml` inside a template therefore points at where
the file *will* live. Resolution tries the literal path first, then the caller's
own directory — so the contract is checked in the kit, before the file that
would fail has ever been activated anywhere.

KNOWN LIMIT, stated rather than implied: this checks each hop independently. A
chain A → B → C is not verified transitively, because B's cap is A's grant and
that composition is not what any single hop declares. The kit has no such chain;
if one appears, this file needs to grow, and this paragraph is the reminder.

Exit 0 = every local call site's contract holds.
Exit 1 = at least one would fail, most likely as a silent `startup_failure`.
"""
import os
import sys

try:
    import yaml
except ImportError:  # pragma: no cover - CI installs it alongside the YAML parse step
    print("FAIL: PyYAML is required (python3 -m pip install pyyaml)")
    sys.exit(1)

# Directories whose *.yml files are scanned as callers. Both, because the kit
# keeps the shipped sources and its own live workflows apart.
SCAN_DIRS = (".github/workflows", "templates/workflows")

# none < read < write. `permissions: {}` means every scope is none; the two
# shorthands set every scope at once.
RANK = {"none": 0, "read": 1, "write": 2}
SHORTHAND = {"read-all": "read", "write-all": "write"}


def load(path):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def norm_permissions(block):
    """A declared `permissions:` value → {scope: rank}, or None if undeclared.

    None and {} are different answers and the difference matters: undeclared
    means "ask the repository settings", empty means "nothing, explicitly".
    """
    if block is None:
        return None
    if isinstance(block, str):
        level = SHORTHAND.get(block.strip())
        if level is None:
            return {}
        return {"*": RANK[level]}
    if isinstance(block, dict):
        out = {}
        for scope, level in block.items():
            out[str(scope)] = RANK.get(str(level).strip(), 0)
        return out
    return {}


def granted(perms, scope):
    """What a normalized permission map grants for one scope (0 if unlisted)."""
    if perms is None:
        return 0
    if "*" in perms:
        return max(perms["*"], perms.get(scope, 0))
    return perms.get(scope, 0)


def callee_requirement(doc):
    """The strongest permission set any job in the called workflow declares.

    A job-level block REPLACES the workflow-level one rather than merging with
    it, so the workflow-level block only counts when some job has no block of
    its own. Returns None when the callee declares nothing anywhere — then it
    inherits whatever the caller passes and can never be the cause of a cap
    failure.
    """
    wf = norm_permissions(doc.get("permissions"))
    jobs = doc.get("jobs") or {}
    contributions = []
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        own = norm_permissions(job.get("permissions"))
        contributions.append(own if own is not None else wf)
    if not jobs:
        contributions.append(wf)
    contributions = [c for c in contributions if c is not None]
    if not contributions:
        return None
    need = {}
    for c in contributions:
        for scope, rank in c.items():
            need[scope] = max(need.get(scope, 0), rank)
    return need


def resolve_callee(caller_path, uses):
    """`./.github/workflows/x.yml` → an on-disk path, or None.

    Literal first; then the caller's own directory, which is how a template
    referencing its post-bootstrap location resolves inside the kit.
    """
    rel = uses[2:] if uses.startswith("./") else uses
    if os.path.isfile(rel):
        return rel
    sibling = os.path.join(os.path.dirname(caller_path), os.path.basename(rel))
    if os.path.isfile(sibling):
        return sibling
    return None


def is_expression(value):
    return isinstance(value, str) and "${{" in value


def type_ok(value, declared):
    """Literal `with:` value vs the callee's declared input type."""
    if is_expression(value) or declared in (None, "string"):
        return True
    if declared == "boolean":
        return isinstance(value, bool)
    if declared == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True


def call_sites():
    """Every (caller_path, job_id, job) whose job is a local reusable call."""
    for d in SCAN_DIRS:
        if not os.path.isdir(d):
            continue
        for entry in sorted(os.listdir(d)):
            if not entry.endswith((".yml", ".yaml")):
                continue
            path = f"{d}/{entry}"
            try:
                doc = load(path)
            except Exception as exc:
                yield path, None, None, f"unparseable: {exc}"
                continue
            if not isinstance(doc, dict):
                continue
            for job_id, job in (doc.get("jobs") or {}).items():
                if not isinstance(job, dict):
                    continue
                uses = job.get("uses")
                if isinstance(uses, str) and uses.startswith("./"):
                    yield path, job_id, (doc, job_id, job), None


def check_site(caller_path, doc, job_id, job):
    """One call site → a list of failure strings (empty = contract holds)."""
    fails = []
    where = f"{caller_path}: job '{job_id}'"
    uses = job["uses"]

    callee_path = resolve_callee(caller_path, uses)
    if callee_path is None:
        return [f"{where} — `uses: {uses}` resolves to no file on disk"]
    try:
        callee = load(callee_path)
    except Exception as exc:
        return [f"{where} — callee {callee_path} is unparseable: {exc}"]

    # `on` is YAML 1.1's boolean true, so a safe_load'ed workflow keys it under
    # True. Both spellings, or this check silently finds no triggers at all.
    triggers = callee.get("on", callee.get(True)) or {}
    wc = triggers.get("workflow_call") if isinstance(triggers, dict) else None
    if wc is None and not (isinstance(triggers, list) and "workflow_call" in triggers):
        fails.append(f"{where} — callee {callee_path} declares no `workflow_call` trigger")
        return fails
    wc = wc or {}

    # ── 1. the permission cap ────────────────────────────────────────────────
    need = callee_requirement(callee)
    if need:
        job_perms = norm_permissions(job.get("permissions"))
        wf_perms = norm_permissions(doc.get("permissions"))
        have = job_perms if job_perms is not None else wf_perms
        if have is None:
            fails.append(
                f"{where} — grants no declared permissions (neither job- nor "
                f"workflow-level), but {os.path.basename(callee_path)} needs "
                f"{fmt(need)}. The effective set would come from a repository "
                f"setting, and a shortfall is a silent `startup_failure`. "
                f"Declare the set on the job."
            )
        else:
            source = "job-level" if job_perms is not None else "workflow-level"
            short = {s: r for s, r in need.items() if granted(have, s) < r}
            if short:
                fails.append(
                    f"{where} — {source} permissions are narrower than "
                    f"{os.path.basename(callee_path)} declares: needs {fmt(short)}, "
                    f"has {fmt({s: granted(have, s) for s in short})}. GitHub "
                    f"downgrades a called workflow's token, never elevates it — "
                    f"this run would end as `startup_failure` with no jobs and no "
                    f"check run."
                )

    # ── 2. inputs ────────────────────────────────────────────────────────────
    declared_inputs = wc.get("inputs") or {}
    supplied = job.get("with") or {}
    for name, spec in declared_inputs.items():
        spec = spec if isinstance(spec, dict) else {}
        if spec.get("required") and name not in supplied:
            fails.append(f"{where} — required input '{name}' is not supplied")
    for name, value in supplied.items():
        if name not in declared_inputs:
            fails.append(f"{where} — passes input '{name}', which the callee does not declare")
            continue
        spec = declared_inputs[name] if isinstance(declared_inputs[name], dict) else {}
        if not type_ok(value, spec.get("type")):
            fails.append(
                f"{where} — input '{name}' is declared `{spec.get('type')}` "
                f"but is passed {value!r}"
            )

    # ── 3. secrets ───────────────────────────────────────────────────────────
    declared_secrets = wc.get("secrets") or {}
    passed = job.get("secrets")
    if passed == "inherit":
        return fails
    passed = passed or {}
    for name, spec in declared_secrets.items():
        spec = spec if isinstance(spec, dict) else {}
        if spec.get("required") and name not in passed:
            fails.append(f"{where} — required secret '{name}' is not passed")
    for name in passed:
        if name not in declared_secrets:
            fails.append(f"{where} — passes secret '{name}', which the callee does not declare")
    return fails


def fmt(scopes):
    inverse = {v: k for k, v in RANK.items()}
    return "{" + ", ".join(f"{s}: {inverse.get(r, r)}" for s, r in sorted(scopes.items())) + "}"


def main():
    fails, sites = [], 0
    for caller_path, job_id, payload, err in call_sites():
        if err:
            fails.append(f"{caller_path} — {err}")
            continue
        doc, job_id, job = payload
        sites += 1
        fails.extend(check_site(caller_path, doc, job_id, job))
    if fails:
        print("FAIL: reusable-workflow call contracts that would not start:")
        for f in fails:
            print(f"  {f}")
        return 1
    print(f"OK: {sites} local reusable-workflow call site(s) — permissions, inputs and secrets all fit")
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# Selftest — synthetic fixtures, because the repo's own call sites are (and
# should stay) green, and a check that only ever sees passing input is untested.
# ─────────────────────────────────────────────────────────────────────────────
def _selftest():
    import contextlib
    import io
    import tempfile

    failures = []

    def expect(label, got, want):
        code, output = got
        ok = code == want
        print(f"  {'ok  ' if ok else 'FAIL'} {label}" + ("" if ok else f"  (exit {code}, want {want})"))
        if not ok:
            failures.append(label)
            print("".join(f"       | {ln}\n" for ln in output.splitlines()))

    CALLEE = """
name: callee
on:
  workflow_call:
    inputs:
      ticket:   {required: true,  type: string}
      dry_run:  {required: false, type: boolean, default: false}
    secrets:
      api_key: {required: true}
permissions:
  contents: read
  actions: read
jobs:
  validate:
    runs-on: ubuntu-latest
    steps: [{run: 'true'}]
"""

    def caller(wf_perms=None, job_perms=None, with_=None, secrets="ok", uses=None):
        w = f"permissions:\n{wf_perms}\n" if wf_perms else ""
        j = f"    permissions:\n{job_perms}\n" if job_perms else ""
        body = with_ if with_ is not None else "      ticket: ENG-1\n"
        sec = {
            "ok": "    secrets:\n      api_key: x\n",
            "inherit": "    secrets: inherit\n",
            "none": "",
            "unknown": "    secrets:\n      api_key: x\n      nope: y\n",
        }[secrets]
        return (
            "name: caller\non: {pull_request: {types: [opened]}}\n" + w
            + "jobs:\n  report:\n" + j
            + f"    uses: {uses or './.github/workflows/callee.yml'}\n"
            + "    with:\n" + body + sec
        )

    def run(caller_yaml, callee_yaml=CALLEE):
        d = tempfile.mkdtemp()
        os.makedirs(f"{d}/templates/workflows")
        if callee_yaml is not None:
            open(f"{d}/templates/workflows/callee.yml", "w").write(callee_yaml)
        open(f"{d}/templates/workflows/caller.yml", "w").write(caller_yaml)
        cwd, buf = os.getcwd(), io.StringIO()
        try:
            os.chdir(d)
            with contextlib.redirect_stdout(buf):
                code = main()
            return code, buf.getvalue()
        finally:
            os.chdir(cwd)

    print("check_workflow_calls selftest")

    # THE KIT-19 BUG ITSELF: `contents: read` at workflow level means
    # `actions: none`, and the callee declares `actions: read`.
    expect("workflow-level contents-only vs callee actions:read → FAIL",
           run(caller(wf_perms="  contents: read")), 1)
    expect("job-level block covering the callee → pass",
           run(caller(wf_perms="  contents: read",
                      job_perms="      contents: read\n      actions: read")), 0)
    expect("workflow-level already covering the callee → pass",
           run(caller(wf_perms="  contents: read\n  actions: read")), 0)
    expect("a wider caller (write ⊃ read) → pass",
           run(caller(wf_perms="  contents: write\n  actions: read")), 0)
    expect("write-all shorthand → pass",
           run(caller(wf_perms="  contents: read", job_perms=None).replace(
               "permissions:\n  contents: read", "permissions: write-all")), 0)
    # A job-level block REPLACES the workflow-level one; a narrow job block does
    # not get to borrow the wide workflow block above it.
    expect("narrow job-level block overrides a sufficient workflow-level one → FAIL",
           run(caller(wf_perms="  contents: read\n  actions: read",
                      job_perms="      contents: read")), 1)
    expect("no permissions declared anywhere → FAIL (repo default is unknowable)",
           run(caller()), 1)
    expect("permissions: {} is explicit none, not undeclared → FAIL",
           run(caller(wf_perms="  {}").replace("permissions:\n  {}", "permissions: {}")), 1)

    GOOD = "  contents: read\n  actions: read"
    expect("missing required input → FAIL",
           run(caller(wf_perms=GOOD, with_="      dry_run: true\n")), 1)
    expect("unknown input → FAIL",
           run(caller(wf_perms=GOOD, with_="      ticket: ENG-1\n      bogus: 1\n")), 1)
    expect("boolean input given a string literal → FAIL",
           run(caller(wf_perms=GOOD, with_="      ticket: ENG-1\n      dry_run: 'yes'\n")), 1)
    expect("boolean input given an expression → pass (not knowable statically)",
           run(caller(wf_perms=GOOD,
                      with_="      ticket: ENG-1\n      dry_run: ${{ inputs.x }}\n")), 0)
    expect("missing required secret → FAIL",
           run(caller(wf_perms=GOOD, secrets="none")), 1)
    expect("secrets: inherit → pass",
           run(caller(wf_perms=GOOD, secrets="inherit")), 0)
    expect("unknown secret → FAIL",
           run(caller(wf_perms=GOOD, secrets="unknown")), 1)
    expect("callee that does not exist → FAIL",
           run(caller(wf_perms=GOOD), callee_yaml=None), 1)
    expect("callee without a workflow_call trigger → FAIL",
           run(caller(wf_perms=GOOD),
               callee_yaml="name: c\non: {push: {branches: [main]}}\npermissions:\n  contents: read\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps: [{run: 'true'}]\n"), 1)
    # A callee declaring nothing inherits the caller's token and can never be
    # the cause of a cap failure — so an undeclared caller is fine there.
    expect("callee declaring no permissions → cap check does not apply",
           run(caller(),
               callee_yaml=CALLEE.replace("permissions:\n  contents: read\n  actions: read\n", "")), 0)

    print(f"\n{'FAILED: ' + str(len(failures)) if failures else 'OK'} — reusable-workflow call contracts")
    return 1 if failures else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    sys.exit(main())
