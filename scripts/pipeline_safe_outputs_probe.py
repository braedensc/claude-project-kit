#!/usr/bin/env python3
"""Does the session's safe-outputs artifact EXIST? — asked before it is downloaded.

WHY THIS EXISTS. `pipeline-safe-outputs.yml` downloaded the agent's write-requests
with `continue-on-error: true` and a truthful comment: a missing artifact is
normal, because a session can die before writing one. The behaviour was
deliberate; the gap was that **it could not tell absence from failure.** A
permissions problem, a network problem or an expired artifact produced exactly
the downstream state that "the session wrote nothing" produces — no file, a clean
`skipped` verdict, every check green. Inside the one component whose whole job is
being the trustworthy executor, that means tickets stop moving and nothing ever
says so.

`steps.<id>.outcome` on the download cannot separate the two either, because
`actions/download-artifact` fails on absence as well. So the question has to be
asked BEFORE the download, of something that can answer it: the Actions
artifacts API.

  no artifact           -> the benign path. Absence is a FACT here, not an
                           inference drawn from a failed download.
  artifact present      -> the download MUST succeed. A failure is a FAILURE.
  artifact expired      -> the session did write requests and they can no longer
                           be read. That is the loud case, not the quiet one.
  API call failed       -> unknown, and unknown is not absent. Loud.

This is contract §13 (`docs/PIPELINE-CONTRACT.md`): a component that can
legitimately do nothing must distinguish "nothing to do" from "could not do it",
and must say which.

USAGE (inside the workflow, after checkout of the default branch):

    python3 scripts/pipeline_safe_outputs_probe.py --name "$ARTIFACT_NAME"

reading `GITHUB_API_URL`, `GITHUB_REPOSITORY`, `GITHUB_RUN_ID` and `GH_TOKEN`
(or `GITHUB_TOKEN`) from the environment and writing `present=` / `state=` to
`$GITHUB_OUTPUT`.

    --from-file PATH   classify an already-fetched listing instead of calling the
                       API (one JSON payload, or a JSON list of paged payloads).
                       This is what the selftest drives.

EXIT CODES — the whole point of the file:

    0   absent  (present=false)    nothing to download, and that is a real answer
    0   present (present=true)     go download it; a failure from here is a failure
    1   unreadable (present=unknown)  expired, or the listing could not be obtained

Exit 1 is `unreadable`, never `absent`. Nothing in this file may turn a question
it failed to answer into a "no".
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

# GitHub caps `per_page` at 100 for this endpoint. The page cap is a runaway
# guard, not a limit anyone should reach: 100 pages is 10 000 artifacts in one
# run. Reaching it is a failure (we would not know what is on page 101), and
# failure here means loud.
PER_PAGE = 100
MAX_PAGES = 100


class ProbeError(Exception):
    """The listing could not be obtained or made sense of. Never means 'absent'."""


def classify(pages, name):
    """Pages of `/actions/runs/{id}/artifacts` payloads -> a state for `name`.

    Returns {"state": absent|present|unreadable, "present": bool, "detail": str}.
    Raises ProbeError when the payload is not the shape the API documents — a
    reader that cannot understand the answer has not received one.
    """
    if isinstance(pages, dict):
        pages = [pages]
    if not isinstance(pages, list):
        raise ProbeError("artifact listing is %s, not an object or a list of pages"
                         % type(pages).__name__)

    matches = []
    for page in pages:
        if not isinstance(page, dict):
            raise ProbeError("artifact listing page is %s, not an object" % type(page).__name__)
        artifacts = page.get("artifacts")
        if not isinstance(artifacts, list):
            raise ProbeError("artifact listing has no `artifacts` list "
                             "(got %r) — refusing to read that as 'no artifacts'"
                             % type(artifacts).__name__)
        for art in artifacts:
            if not isinstance(art, dict):
                raise ProbeError("artifact entry is %s, not an object" % type(art).__name__)
            if art.get("name") == name:
                matches.append(art)

    if not matches:
        return {
            "state": "absent",
            "present": False,
            "detail": "no artifact named %r in this run — the session wrote no "
                      "write-requests" % name,
        }

    live = [a for a in matches if not a.get("expired")]
    if not live:
        return {
            "state": "unreadable",
            "present": True,
            "detail": "artifact %r exists but every copy has EXPIRED — the session "
                      "did write requests and they can no longer be read" % name,
        }

    return {
        "state": "present",
        "present": True,
        "detail": "artifact %r is present (%d byte(s)) and must download cleanly"
                  % (name, live[0].get("size_in_bytes") or 0),
    }


def fetch(api_url, repo, run_id, token, name, opener=None):
    """Every page of this run's artifact listing, or ProbeError.

    `name=` is passed as a filter because the endpoint supports it, but nothing
    depends on it being honoured: classify() filters by name again. A server that
    ignores the parameter costs a few more rows, not a wrong answer.
    """
    get = opener or _urlopen_json
    pages, seen = [], 0
    for page_no in range(1, MAX_PAGES + 1):
        url = ("%s/repos/%s/actions/runs/%s/artifacts?per_page=%d&page=%d&name=%s"
               % (api_url.rstrip("/"), repo, run_id, PER_PAGE, page_no,
                  urllib.parse.quote(name, safe="")))
        payload = get(url, token)
        pages.append(payload)
        if not isinstance(payload, dict):
            break  # classify() will raise on it; do not paper over the shape here
        rows = payload.get("artifacts")
        seen += len(rows) if isinstance(rows, list) else 0
        total = payload.get("total_count")
        if not isinstance(rows, list) or not rows:
            break
        if isinstance(total, int) and seen >= total:
            break
    else:
        raise ProbeError("artifact listing did not terminate within %d pages" % MAX_PAGES)
    return pages


def _urlopen_json(url, token):
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": "Bearer %s" % token,
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        raise ProbeError("artifacts API returned HTTP %s (%s). The usual cause is a "
                         "caller job granting less than `actions: read`."
                         % (exc.code, exc.reason))
    except (urllib.error.URLError, ValueError, OSError) as exc:
        raise ProbeError("artifacts API call failed: %s" % exc)


def emit(result):
    """Write the answer where the workflow and the human can both see it."""
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        # `unknown` rather than a boolean on the unreadable path, deliberately:
        # neither `true` nor `false` is true, and a later reader that gates on
        # this must not be handed a plausible-looking answer to a question this
        # script failed to settle.
        present = {"absent": "false", "present": "true"}.get(result["state"], "unknown")
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write("present=%s\n" % present)
            fh.write("state=%s\n" % result["state"])
    print("%s: %s" % (result["state"], result["detail"]))


def run(argv):
    name = None
    from_file = None
    i = 0
    while i < len(argv):
        if argv[i] == "--name" and i + 1 < len(argv):
            name = argv[i + 1]; i += 2
        elif argv[i] == "--from-file" and i + 1 < len(argv):
            from_file = argv[i + 1]; i += 2
        else:
            print("usage: pipeline_safe_outputs_probe.py --name NAME [--from-file LISTING.json]")
            return 2
    if not name:
        print("::error::safe_outputs_probe.py needs --name")
        return 2

    try:
        if from_file:
            with open(from_file, encoding="utf-8") as fh:
                pages = json.load(fh)
        else:
            # `GH_TOKEN` first — the spelling the rest of this kit's workflows
            # use, and the one that avoids setting a `GITHUB_`-prefixed name the
            # runner also owns.
            token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
            missing = [k for k, v in (("GITHUB_REPOSITORY", os.environ.get("GITHUB_REPOSITORY")),
                                      ("GITHUB_RUN_ID", os.environ.get("GITHUB_RUN_ID")),
                                      ("GH_TOKEN", token)) if not v]
            if missing:
                raise ProbeError("missing %s in the environment" % ", ".join(missing))
            pages = fetch(
                os.environ.get("GITHUB_API_URL") or "https://api.github.com",
                os.environ["GITHUB_REPOSITORY"],
                os.environ["GITHUB_RUN_ID"],
                token,
                name,
            )
        result = classify(pages, name)
    except (ProbeError, OSError, ValueError) as exc:
        # UNKNOWN IS NOT ABSENT. Say so, and fail.
        emit({"state": "unreadable", "present": None,
              "detail": "could not establish whether %r exists: %s" % (name, exc)})
        print("::error::Could not determine whether the safe-outputs artifact %r "
              "exists (%s). This is a FAILURE to read, not an absence of requests — "
              "refusing to report a clean verdict." % (name, exc))
        return 1

    emit(result)
    if result["state"] == "unreadable":
        print("::error::%s" % result["detail"])
        return 1
    if result["state"] == "absent":
        print("::notice::%s" % result["detail"])
    return 0


# ══════════════════════════════════════════════════════════════════════════════
# Selftest — the classification rows, and the workflow wiring that depends on
# them. Both, because either alone can be green while the pair is broken: a
# perfect classifier wired behind `continue-on-error` still reports success for
# a failure, which is the defect this file exists to close.
# ══════════════════════════════════════════════════════════════════════════════
# The kit ships workflow sources in templates/workflows/; bootstrap `git mv`s
# them into .github/workflows/. Resolve both, so the wiring rows keep their teeth
# after activation. Neither present is a FAILURE, not a pass — a wiring check with
# no file to check has not checked anything.
WORKFLOW_PATHS = (
    "templates/workflows/pipeline-safe-outputs.yml",
    ".github/workflows/pipeline-safe-outputs.yml",
)


def default_workflow():
    for path in WORKFLOW_PATHS:
        if os.path.exists(path):
            return path
    return WORKFLOW_PATHS[0]


def _page(*artifacts):
    return {"total_count": len(artifacts), "artifacts": list(artifacts)}


def _art(name, expired=False, size=12):
    return {"name": name, "expired": expired, "size_in_bytes": size, "id": 1}


def _selftest_classify(fail):
    NAME = "pipeline-safe-outputs-ENG-123"

    def state(pages):
        try:
            return classify(pages, NAME)["state"]
        except ProbeError:
            return "raised"

    cases = [
        ("no artifacts at all is ABSENT", _page(), "absent"),
        ("some other artifact is ABSENT", _page(_art("pipeline-state")), "absent"),
        ("the artifact present is PRESENT", _page(_art(NAME)), "present"),
        ("present among others is PRESENT",
         _page(_art("pipeline-state"), _art(NAME), _art("x")), "present"),
        ("an EXPIRED artifact is unreadable, not absent",
         _page(_art(NAME, expired=True)), "unreadable"),
        ("expired plus live is PRESENT",
         _page(_art(NAME, expired=True), _art(NAME)), "present"),
        ("found on a later page is PRESENT",
         [_page(_art("a")), _page(_art(NAME))], "present"),
        ("a listing with no `artifacts` key RAISES, it is not absent",
         {"total_count": 0}, "raised"),
        ("a listing that is not an object RAISES", "nope", "raised"),
        ("an artifacts value that is not a list RAISES",
         {"artifacts": "none"}, "raised"),
    ]
    for label, pages, want in cases:
        got = state(pages)
        if got != want:
            fail("classify: %s — wanted %s, got %s" % (label, want, got))


def _selftest_exit_codes(fail, tmpdir):
    """The exit code IS the contract. Absent must never cost the job its life,
    and unreadable must never be quiet.

    Each row calls run() for real; its annotations are swallowed so the selftest
    reads as a list of results rather than as a transcript of four failures.
    """
    import contextlib
    import io
    NAME = "pipeline-safe-outputs-ENG-123"
    rows = [
        ("absent exits 0", _page(), 0),
        ("present exits 0", _page(_art(NAME)), 0),
        ("expired exits 1", _page(_art(NAME, expired=True)), 1),
        ("a malformed listing exits 1", {"total_count": 0}, 1),
    ]
    for label, pages, want in rows:
        path = os.path.join(tmpdir, "listing.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(pages, fh)
        with contextlib.redirect_stdout(io.StringIO()):
            got = run(["--name", NAME, "--from-file", path])
        if got != want:
            fail("exit code: %s — wanted %d, got %d" % (label, want, got))

    # A listing file that is not there at all is the same class of unknown.
    with contextlib.redirect_stdout(io.StringIO()):
        gone = run(["--name", NAME, "--from-file", os.path.join(tmpdir, "nope.json")])
    if gone != 1:
        fail("exit code: an unreadable listing file must exit 1, not 0")


def _steps_of(doc):
    return (((doc or {}).get("jobs") or {}).get("validate") or {}).get("steps") or []


def _selftest_wiring(fail, workflow_path):
    """The four structural facts the fix consists of.

    Each is asserted against the file itself because each is a property of the
    STEP GRAPH, not of any Python this repo can call: a probe that runs after the
    download, or a download still wearing `continue-on-error`, restores the exact
    conflation regardless of how well classify() works.
    """
    try:
        import yaml
    except ImportError:
        fail("PyYAML is required for the wiring rows (python3 -m pip install pyyaml)")
        return
    try:
        with open(workflow_path, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
        raw = open(workflow_path, encoding="utf-8").read()
    except OSError as exc:
        fail("wiring: cannot read %s (%s)" % (workflow_path, exc))
        return

    steps = _steps_of(doc)
    by_id = {s.get("id"): s for s in steps if isinstance(s, dict) and s.get("id")}
    order = [s.get("id") for s in steps if isinstance(s, dict)]

    probe = by_id.get("probe")
    if not probe:
        fail("wiring: the validate job has no `id: probe` step — nothing asks "
             "whether the artifact exists before trying to download it")
    else:
        if probe.get("continue-on-error"):
            fail("wiring: the probe step is `continue-on-error` — a probe that "
                 "cannot fail cannot report that it failed")
        if "pipeline_safe_outputs_probe.py" not in json.dumps(probe):
            fail("wiring: the probe step does not run scripts/pipeline_safe_outputs_probe.py")

    dl = by_id.get("dl")
    if not dl:
        fail("wiring: the validate job has no `id: dl` download step")
    else:
        if dl.get("continue-on-error"):
            fail("wiring: the download step is still `continue-on-error: true` — "
                 "a failed download is indistinguishable from a missing artifact")
        cond = str(dl.get("if") or "")
        if "steps.probe.outputs.present" not in cond:
            fail("wiring: the download step is not gated on the probe's answer "
                 "(`if:` is %r)" % cond)
    if "probe" in order and "dl" in order and order.index("probe") > order.index("dl"):
        fail("wiring: the probe runs AFTER the download — the question has to be "
             "asked of something other than the download's own failure")

    run_step = by_id.get("run")
    if not run_step:
        fail("wiring: the validate job has no `id: run` validator step")
    elif "ARTIFACT_PRESENT" not in (run_step.get("env") or {}):
        fail("wiring: the validator is not told whether the artifact existed "
             "(no ARTIFACT_PRESENT in its env) — so a present-but-empty artifact "
             "still reads as 'the session wrote nothing'")

    if "errored" not in raw:
        fail("wiring: the workflow never emits an `errored` verdict — a failure "
             "OF THIS JOB would still be reported as one of 'rejected' (the "
             "agent's batch was refused) or 'skipped' (clean)")
    for token in ("steps.probe.outcome", "steps.dl.outcome"):
        if token not in raw:
            fail("wiring: the verdict is computed without %s, so a hard read "
                 "failure cannot be told apart from a rejected batch" % token)


def _selftest_embedded_validator(fail, workflow_path):
    """The validator is Python inlined in a YAML heredoc, so nothing compiles it
    until it runs — inside the one job that holds the tracker credential. Parse
    it here, and assert the two branches that make a failure to APPLY distinct
    from a batch that was refused."""
    import ast
    import textwrap
    try:
        import yaml
        with open(workflow_path, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
    except (ImportError, OSError) as exc:
        fail("embedded validator: cannot read %s (%s)" % (workflow_path, exc))
        return

    steps = _steps_of(doc)
    run_step = next((s for s in steps if isinstance(s, dict) and s.get("id") == "run"), None)
    if not run_step:
        fail("embedded validator: no `id: run` step to inspect")
        return
    body = str(run_step.get("run") or "")
    if "python3 - <<'PY'" not in body:
        fail("embedded validator: the run step no longer inlines a python heredoc")
        return
    src = textwrap.dedent(body.split("python3 - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0])
    try:
        ast.parse(src)
    except SyntaxError as exc:
        fail("embedded validator: the inlined Python does not parse (line %s: %s)"
             % (exc.lineno, exc.msg))
        return

    if 'out("verdict", "errored")' not in src:
        fail("embedded validator: a failure to APPLY never sets `errored`, so it "
             "falls back to `rejected` — which claims the batch was refused and "
             "that nothing was applied, both false")
    if 'os.environ.get("LINEAR_API_KEY")' not in src:
        fail("embedded validator: LINEAR_API_KEY is never checked for emptiness — "
             "an unset repository secret interpolates to '' and the resulting "
             "anonymous 401 names no cause")
    if "applied" not in src:
        fail("embedded validator: nothing tracks how many requests were applied, "
             "so a mid-plan failure cannot say how far it got")


def selftest(workflow_path):
    import tempfile
    failures = []

    def fail(msg):
        failures.append(msg)

    _selftest_classify(fail)
    with tempfile.TemporaryDirectory() as tmp:
        _selftest_exit_codes(fail, tmp)
    _selftest_wiring(fail, workflow_path)
    _selftest_embedded_validator(fail, workflow_path)

    for msg in failures:
        print("FAIL: %s" % msg)
    if failures:
        print("\n%d safe-outputs probe check(s) failed." % len(failures))
        return 1
    print("safe-outputs probe selftest: all checks passed.")
    return 0


def main(argv):
    if "--selftest" in argv:
        rest = [a for a in argv if a != "--selftest"]
        path = default_workflow()
        if "--workflow" in rest:
            path = rest[rest.index("--workflow") + 1]
        return selftest(path)
    return run(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
