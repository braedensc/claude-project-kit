#!/usr/bin/env python3
"""Union check — are the open PRs green TOGETHER, not just one at a time?

    check_pr_union.py prepare --out plan.json [--max-prs N]     # list + fetch (needs GH_TOKEN)
    check_pr_union.py check --plan plan.json --out result.json [--also CMD]...
    check_pr_union.py gate --verdict V [--left-out N]           # may the job pass? (exit 0 / 1)
    check_pr_union.py --selftest

THE CLASS THIS COVERS

  GitHub tests each PR as main + that PR, never as main + the other open PRs. Two
  PRs can each be green alone and red together, with no textual conflict for git
  to warn about: a check and a fixture it rejects, or two rules that each assume
  the other does not exist. docs/LESSONS.md (semantic conflicts) and
  docs/COLLABORATION.md (seam-check) prescribe the fix by hand — merge them
  together somewhere disposable and run the checks there. This is that, mechanized.

WHAT IT DOES

  prepare  lists open PRs; drops drafts, forks (their code does not run here) and
           PRs GitHub already reports CONFLICTING (the conflict monitor owns those);
           past --max-prs (a runaway guard) it still tests the OLDEST --max-prs and names
           the rest as left out; fetches each head by sha.
  check    builds a detached throwaway worktree off the fetched main, merges the
           PRs in ascending number (one that conflicts with the union so far is
           skipped and named), runs the battery — every `test:*`/`lint:*` script
           in the union's own package.json, plus each --also command — and when
           it is red, bisects over the merge prefixes to name the PR whose
           addition turned it red and the PRs already in the union at that point.

WHAT IT NEVER DOES (asserted in --selftest)

  Merge a PR, push, comment, label, or move any ref in the checkout it ran from.
  Every merge happens inside the throwaway worktree, which is removed afterwards.
  It REPORTS; the workflow's separate, code-free job posts the report.

VERDICTS AND EXIT CODES (contract §13)

  green              0   the union ran the battery and it passed
  nothing-to-combine 0   fewer than two PRs could join a union — said, not silent
  red                1   the union fails the battery; `attribution` says where
  could-not          2   a listing, fetch or tree could not be built. Never "green".

  Whatever the verdict, a run that LEFT PRs OUT (over the cap) is not a complete check: the
  workflow's last step runs `gate`, which fails the job for it (KIT-524). `gate` is where the
  decision lives, so the selftest can pin it.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

SCHEMA = "pr-union/1"
# A runaway guard, not a working limit (KIT-524): 27 PRs were open on a busy night in November
# and the old cap of 8 skipped the whole run. Past it, the oldest PRs are still tested together
# and the rest are named as left out; the job then fails, because a part-checked union is not a green one.
MAX_PRS_DEFAULT = 60
LIST_LIMIT = 500
TAIL_CHARS = 1500
MAX_REPORTED_FAILURES = 5
GIT_ID = ["-c", "user.name=pr-union-check", "-c", "user.email=pr-union-check@example.invalid",
          "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]
# The battery runs PR code: no token, and no way to write the runner's step files
# (a test that appends `verdict=green` to GITHUB_OUTPUT would forge the result).
SCRUBBED_ENV = ("GH_TOKEN", "GITHUB_TOKEN", "GITHUB_OUTPUT", "GITHUB_ENV", "GITHUB_PATH",
                "GITHUB_STATE", "GITHUB_STEP_SUMMARY")


class CouldNot(Exception):
    """Exit 2. The union could not be established."""


def git(*args, cwd, check=True, timeout=300):
    r = subprocess.run(["git", *GIT_ID, *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise CouldNot(f"git {' '.join(args[:3])} failed: {r.stderr.strip()[-300:]}")
    return r


def _emit(name, value):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as fh:
            fh.write(f"{name}={value}\n")


PASSING_VERDICTS = ("green", "nothing-to-combine", "not-run")


def gate(verdict, left_out=0):
    """(may the job pass, why). Green, nothing to combine and not-run pass, and so does an earlier
    verdict repeated from the cache, but never when PRs were left out; red and anything unknown
    (could-not, empty) fail. The one place the workflow's last step asks (KIT-524)."""
    verdict = (verdict or "").strip()
    if left_out:
        if verdict == "red":
            return False, "the open PRs are green alone and red together; %d more were left out" % left_out
        return False, ("%d open PR(s) were over the cap and NOT checked: this is not a complete check "
                       "(verdict for the rest: %s)" % (left_out, verdict or "none"))
    if verdict == "red":
        return False, "the open PRs are green alone and red together - see the summary"
    if verdict in PASSING_VERDICTS or (verdict.startswith("cached-") and verdict[7:] in PASSING_VERDICTS):
        return True, "verdict: " + verdict
    return False, "could not check the union (verdict: %r) - this is NOT green" % verdict


def emit_prepare(plan):
    """What `prepare` hands the workflow: whether to run, the key, and how many PRs were left out."""
    _emit("run", "false" if plan["verdict"] else "true")
    _emit("key", plan["key"])
    _emit("left_out", str(len(plan.get("left_out", []))))


def _summary(text):
    print(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as fh:
            fh.write(text + "\n")


def gh_listing(cwd):
    r = subprocess.run(["gh", "pr", "list", "--state", "open", "--limit", str(LIST_LIMIT), "--json",
                        "number,isDraft,headRefOid,isCrossRepository,mergeable"],
                       cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise CouldNot(f"gh pr list failed: {r.stderr.strip()[-300:]}")
    prs = json.loads(r.stdout)
    if len(prs) >= LIST_LIMIT:
        raise CouldNot(f"{len(prs)} open PRs hit the listing limit — refusing to judge a truncated list")
    return prs


# ── prepare ──────────────────────────────────────────────────────────────────

def prepare(repo, listing, max_prs=MAX_PRS_DEFAULT, remote="origin", base="main",
            pr_ref="refs/pull/{n}/head"):
    prs = sorted(listing(), key=lambda p: p["number"])
    eligible, skipped = [], []
    for p in prs:
        reason = ("draft" if p["isDraft"] else
                  "from a fork — its code does not run in this job" if p["isCrossRepository"] else
                  "CONFLICTING with main — the conflict monitor owns it" if p["mergeable"] == "CONFLICTING" else
                  None)
        if reason:
            skipped.append({"number": p["number"], "reason": reason})
        else:
            eligible.append(p)
    plan = {"schema": SCHEMA, "base_ref": f"{remote}/{base}", "base_sha": None, "prs": [],
            "skipped": skipped, "verdict": None, "key": ""}
    plan["left_out"] = []
    if len(eligible) > max_prs:
        # Test the oldest max_prs PRs together; name the rest. Never skip the whole run.
        for p in eligible[max_prs:]:
            plan["left_out"].append(p["number"])
            plan["skipped"].append({"number": p["number"],
                                    "reason": f"over the cap of {max_prs} — NOT in this union, not checked"})
        eligible = eligible[:max_prs]
    refspecs = [base] + [pr_ref.format(n=p["number"]) for p in eligible]
    git("fetch", "--no-tags", remote, *refspecs, cwd=repo)
    plan["base_sha"] = git("rev-parse", f"{remote}/{base}", cwd=repo).stdout.strip()
    for p in eligible:
        if git("cat-file", "-e", f"{p['headRefOid']}^{{commit}}", cwd=repo, check=False).returncode:
            plan["skipped"].append({"number": p["number"], "reason": "head moved while listing — next run"})
        else:
            plan["prs"].append({"number": p["number"], "sha": p["headRefOid"]})
    if len(plan["prs"]) < 2:
        plan["verdict"] = "nothing-to-combine"
    material = plan["base_sha"] + "," + ",".join(f"{p['number']}:{p['sha']}" for p in plan["prs"])
    plan["key"] = hashlib.sha256(material.encode()).hexdigest()[:16]
    return plan


# ── check ────────────────────────────────────────────────────────────────────

def battery(tree, also, exclude):
    try:
        with open(os.path.join(tree, "package.json")) as fh:
            scripts = json.load(fh).get("scripts", {})
    except (OSError, ValueError) as e:
        raise CouldNot(f"the union's package.json is unreadable: {e}")
    cmds = [(name, cmd) for name, cmd in scripts.items()
            if name.split(":", 1)[0] in ("test", "lint") and name not in exclude]
    return cmds + [(c, c) for c in also]


def run_commands(tree, cmds, timeout):
    env = {k: v for k, v in os.environ.items() if k not in SCRUBBED_ENV and not k.startswith("ACTIONS_")}
    failed = []
    for name, cmd in cmds:
        try:
            r = subprocess.run(["sh", "-c", cmd], cwd=tree, env=env, capture_output=True,
                               text=True, timeout=timeout)
            if r.returncode != 0:
                failed.append({"command": name, "tail": ((r.stdout or "") + (r.stderr or ""))[-TAIL_CHARS:]})
        except subprocess.TimeoutExpired:
            failed.append({"command": name, "tail": f"timed out after {timeout}s"})
    return failed


def first_red(prefixes, is_red):
    """Index of the first merge prefix that is red, by bisection. prefixes[-1] is known red.
    Assumes a break, once present, stays present as more PRs are merged in."""
    if is_red(prefixes[0]):
        return 0
    lo, hi = 0, len(prefixes) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if is_red(prefixes[mid]):
            hi = mid
        else:
            lo = mid
    return hi


# Scripts the battery leaves out by default: both need the secretlint `npm install` puts
# in node_modules, and the union tree has nothing installed, so they could only say
# "could not run" and turn every union red, blamed on main.
NEEDS_INSTALL = ("lint:secrets", "test:secret-scan")


def check(repo, plan, also=(), exclude=NEEDS_INSTALL, timeout=600):
    result = {"schema": SCHEMA, "key": plan.get("key", ""), "base_sha": plan.get("base_sha"),
              "merged": [], "skipped": list(plan.get("skipped", [])), "failures": [],
              "attribution": None, "verdict": plan.get("verdict"), "left_out": list(plan.get("left_out", []))}
    if result["verdict"]:
        return result
    tmp = tempfile.mkdtemp(prefix="pr-union-")
    tree = os.path.join(tmp, "tree")
    try:
        git("worktree", "add", "--detach", "-q", tree, plan["base_sha"], cwd=repo)
        prefixes = [{"number": None, "sha": plan["base_sha"]}]
        for p in plan["prs"]:
            r = git("merge", "--no-ff", "--no-edit", "-q", "-m", f"pr-union: #{p['number']}", p["sha"],
                    cwd=tree, check=False)
            if r.returncode != 0:
                git("merge", "--abort", cwd=tree, check=False)
                names = [f"#{q['number']}" for q in prefixes[1:]] or ["main"]
                result["skipped"].append({"number": p["number"],
                                          "reason": f"conflicts with the union of {', '.join(names)}"})
                continue
            sha = git("rev-parse", "HEAD", cwd=tree).stdout.strip()
            prefixes.append({"number": p["number"], "sha": sha})
            result["merged"].append(p)
        if len(prefixes) < 3:
            result["verdict"] = "nothing-to-combine"
            return result
        cmds = battery(tree, also, exclude)
        if not cmds:
            raise CouldNot("the battery is empty — no test:/lint: scripts and no --also commands")
        failed = run_commands(tree, cmds, timeout)
        if not failed:
            result["verdict"] = "green"
            return result
        result["verdict"] = "red"
        result["failures"] = failed[:MAX_REPORTED_FAILURES]
        red_cmds = [c for c in cmds if c[0] in {f["command"] for f in failed}]

        def is_red(prefix):
            git("checkout", "-q", "--detach", "-f", prefix["sha"], cwd=tree)
            git("clean", "-ffdq", cwd=tree)
            return bool(run_commands(tree, red_cmds, timeout))

        i = first_red(prefixes, is_red)
        result["attribution"] = {
            "kind": "main" if i == 0 else "alone" if i == 1 else "combination",
            "culprit": prefixes[i]["number"],
            "partners": [q["number"] for q in prefixes[1:i]],
        }
        return result
    finally:
        git("worktree", "remove", "--force", tree, cwd=repo, check=False)
        git("worktree", "prune", cwd=repo, check=False)
        shutil.rmtree(tmp, ignore_errors=True)


def render(result):
    v = result["verdict"]
    lines = [f"### PR union check — **{v}**"]
    if result.get("base_sha"):
        lines.append(f"- Asked: does main@{result['base_sha'][:8]} plus every eligible open PR pass the battery together?")
    if result["merged"]:
        lines.append("- In the union: " + ", ".join(f"#{p['number']}@{p['sha'][:8]}" for p in result["merged"]))
    for s in result["skipped"]:
        lines.append(f"- Skipped #{s['number']}: {s['reason']}")
    a = result.get("attribution")
    if a:
        if a["kind"] == "main":
            lines.append("- **main is red on its own** for these checks — not a combination of PRs.")
        elif a["kind"] == "alone":
            lines.append(f"- **#{a['culprit']} is red against current main by itself** — its green CI ran on an older main.")
        else:
            lines.append(f"- **Red once #{a['culprit']} joins** a union already holding "
                         + ", ".join(f"#{n}" for n in a["partners"])
                         + ". Each was green alone; together they are not.")
    for f in result["failures"]:
        lines.append(f"- Failing: `{f['command']}`")
    if result.get("left_out"):
        lines.append("- **Not a complete check.** " + ", ".join(f"#{n}" for n in result["left_out"])
                     + " are over the cap and were not in the union above; the job fails for that, "
                     "whatever the verdict says.")
    if v == "nothing-to-combine":
        lines.append("- Fewer than two PRs could join a union, so there is no combination to test — each PR's own CI covers it.")
    return "\n".join(lines)


# ── selftest ─────────────────────────────────────────────────────────────────

def selftest():
    failures = []

    def expect(cond, msg):
        if not cond:
            failures.append(msg)

    tmp = tempfile.mkdtemp(prefix="pr-union-selftest-")
    try:
        work, origin, clone = (os.path.join(tmp, d) for d in ("work", "origin.git", "clone"))
        os.makedirs(os.path.join(work))

        def g(*a, cwd=work):
            return git(*a, cwd=cwd).stdout.strip()

        def write(path, text):
            full = os.path.join(work, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as fh:
                fh.write(text)

        def commit_on(branch, files, start="main"):
            g("checkout", "-q", "-B", branch, start)
            for path, text in files.items():
                write(path, text)
            g("add", "-A")
            g("commit", "-q", "-m", branch)
            sha = g("rev-parse", "HEAD")
            g("checkout", "-q", "main")
            return sha

        g("init", "-q", "-b", "main")
        write("package.json", json.dumps({"scripts": {"test:check": "python3 check.py",
                                                       "lint:secrets": "exit 1",
                                                       "test:secret-scan": "exit 1",
                                                       "build": "exit 1"}}))
        write("check.py", "import sys\nsys.exit(0)\n")
        write("README.md", "line one\n")
        g("add", "-A")
        g("commit", "-q", "-m", "base")
        strict = ("import os, sys\nbad = [f for f in os.listdir('fixtures') if 'FORBIDDEN' in "
                  "open(os.path.join('fixtures', f)).read()] if os.path.isdir('fixtures') else []\n"
                  "print('rejected:', bad)\nsys.exit(1 if bad else 0)\n")
        shas = {
            1: commit_on("pr-1", {"check.py": strict}),                       # a check…
            2: commit_on("pr-2", {"fixtures/f.txt": "FORBIDDEN\n"}),          # …and a fixture it rejects
            3: commit_on("pr-3", {"README.md": "line one, edited\n"}),
            4: commit_on("pr-4", {"README.md": "line one, edited differently\n"}),
            5: commit_on("pr-5", {"other.txt": "draft\n"}),
            6: commit_on("pr-6", {"other.txt": "fork\n"}),
            7: commit_on("pr-7", {"other.txt": "conflicting\n"}),
        }
        subprocess.run(["git", "clone", "-q", "--mirror", work, origin], check=True, capture_output=True)
        subprocess.run(["git", "clone", "-q", origin, clone], check=True, capture_output=True)

        def listing(numbers=range(1, 8)):
            return lambda: [{"number": n, "headRefOid": shas[n], "isDraft": n == 5,
                             "isCrossRepository": n == 6,
                             "mergeable": "CONFLICTING" if n == 7 else "MERGEABLE"} for n in numbers]

        kw = dict(pr_ref="refs/heads/pr-{n}")
        refs_before = g("for-each-ref", cwd=clone), g("rev-parse", "HEAD", cwd=clone)

        # 1. the semantic conflict: #1 and #2 each green alone, red together; #4 conflicts
        #    textually with the union and is skipped by name; draft/fork/CONFLICTING skipped.
        plan = prepare(clone, listing(), **kw)
        expect(plan["verdict"] is None and [p["number"] for p in plan["prs"]] == [1, 2, 3, 4],
               f"prepare eligibility: {plan}")
        reasons = {s["number"]: s["reason"] for s in plan["skipped"]}
        expect(set(reasons) == {5, 6, 7} and "fork" in reasons[6] and "CONFLICTING" in reasons[7],
               f"prepare must name every skip: {reasons}")
        res = check(clone, plan)
        expect(res["verdict"] == "red", f"union of a check and its fixture must be red: {res}")
        expect(res["attribution"] == {"kind": "combination", "culprit": 2, "partners": [1]},
               f"bisection must name #2 joining a union holding #1: {res['attribution']}")
        expect(any(s["number"] == 4 and "conflicts with the union" in s["reason"] for s in res["skipped"]),
               f"a textual conflict inside the union must be skipped by name: {res['skipped']}")
        expect([f["command"] for f in res["failures"]] == ["test:check"],
               f"only test:/lint: scripts run, lint:secrets and test:secret-scan excluded, "
               f"non-test scripts ignored: {res['failures']}")
        expect("**Red once #2 joins** a union already holding #1" in render(res), render(res))
        # A PR that clashes with the union does not stop the others being tested together.
        expect({p["number"] for p in res["merged"]} == {1, 2, 3},
               f"a clashing PR must not stop the rest: {[p['number'] for p in res['merged']]}")

        # …even when the clashing PR comes first in the order: the ones after it are still merged.
        by_num = {p["number"]: p for p in plan["prs"]}
        early = check(clone, {**plan, "prs": [by_num[n] for n in (1, 3, 4, 2)]})   # #4 clashes with #1+#3
        expect([p["number"] for p in early["merged"]] == [1, 3, 2]
               and any(x["number"] == 4 and "conflicts with the union" in x["reason"] for x in early["skipped"]),
               f"a clash in the middle must not stop the PRs after it: merged "
               f"{[p['number'] for p in early['merged']]}, skipped {early['skipped']}")

        # 2. each alone is green — the premise the class depends on.
        for n in (1, 2):
            alone = check(clone, {**plan, "prs": [p for p in plan["prs"] if p["number"] in (n, 3)]})
            expect(alone["verdict"] == "green", f"#{n} with an unrelated PR must be green: {alone}")

        # 3. nothing moved in the checkout the check ran from; no worktree left behind.
        expect((g("for-each-ref", cwd=clone), g("rev-parse", "HEAD", cwd=clone)) == refs_before,
               "the union check must not move any ref in its checkout")
        expect(g("worktree", "list", "--porcelain", cwd=clone).count("worktree ") == 1,
               "the throwaway worktree must be removed")

        # 4. over the cap (KIT-524): the oldest PRs are still tested together, the rest are named as left
        #    out, and the render says the check is not complete. The whole run is never skipped.
        capped = prepare(clone, listing(), max_prs=2, **kw)
        capped_result = check(clone, capped)
        expect(capped["verdict"] is None and len(capped["prs"]) == 2 and capped["left_out"] == [3, 4],
               f"over the cap: the first two are tested, the others named as left out: {capped}")
        expect({3, 4} <= {s["number"] for s in capped["skipped"]}
               and all("NOT in this union" in s["reason"] for s in capped["skipped"] if s["number"] in (3, 4)),
               f"the PRs over the cap are named, with a reason: {capped['skipped']}")
        expect(capped_result["left_out"] == [3, 4] and "Not a complete check" in render(capped_result),
               f"a part-checked union must say it is not complete: {render(capped_result)}")
        expect(MAX_PRS_DEFAULT >= 40, "the default cap must sit well above a busy night (27 open PRs)")

        # 4b. the job-fails decision lives in gate(), and prepare hands the workflow left_out (KIT-524).
        for verdict, left, want in (("green", 0, True), ("green", 2, False), ("red", 0, False), ("red", 3, False),
                                    ("nothing-to-combine", 0, True), ("nothing-to-combine", 1, False),
                                    ("not-run", 0, True), ("cached-green", 0, True), ("cached-green", 4, False),
                                    ("cached-red", 0, False), ("could-not", 0, False), ("", 0, False),
                                    ("skipped-cap", 0, False)):
            expect(gate(verdict, left)[0] is want, f"gate({verdict!r}, {left}) should be {want}: {gate(verdict, left)}")
        outfile = os.path.join(tmp, "github-output")
        os.environ["GITHUB_OUTPUT"] = outfile
        try:
            emit_prepare(capped)
            emitted = dict(l.split("=", 1) for l in open(outfile).read().split())
            expect(emitted == {"run": "true", "key": capped["key"], "left_out": "2"},
                   f"prepare must hand the workflow how many PRs were left out: {emitted}")
        finally:
            del os.environ["GITHUB_OUTPUT"]
        workflow = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                ".github", "workflows", "pr-union-check.yml")
        if os.path.exists(workflow):
            wf = open(workflow).read()
            expect('check_pr_union.py gate --verdict "$VERDICT" --left-out "${LEFT_OUT:-0}"' in wf
                   and "steps.prepare.outputs.left_out" in wf,
                   "the workflow's last step must ask gate(), with the left_out prepare emitted")

        # 5. one eligible PR is nothing to combine — said, not silent.
        single = prepare(clone, listing([3, 5]), **kw)
        expect(single["verdict"] == "nothing-to-combine" and "no combination" in render(check(clone, single)),
               f"a single PR must say there is nothing to combine: {single}")

        # 6. attribution kinds: main red on its own; a PR red against current main by itself.
        red_main = commit_on("red-main", {"check.py": strict, "fixtures/f.txt": "FORBIDDEN\n"})
        stale = commit_on("stale-main", {"fixtures/f.txt": "FORBIDDEN\n"})
        git("fetch", "-q", work, "red-main", "stale-main", cwd=clone)
        r = check(clone, {**plan, "base_sha": red_main, "prs": [p for p in plan["prs"] if p["number"] in (1, 3)]})
        expect(r["verdict"] == "red" and r["attribution"]["kind"] == "main", f"main red on its own: {r}")
        r = check(clone, {**plan, "base_sha": stale, "prs": [p for p in plan["prs"] if p["number"] in (1, 3)]})
        expect(r["verdict"] == "red" and r["attribution"] == {"kind": "alone", "culprit": 1, "partners": []},
               f"a PR red against current main by itself: {r}")

        # 7. could-not is its own verdict: a failed listing, and a base that does not exist.
        def broken():
            raise CouldNot("gh pr list failed: HTTP 502")
        try:
            prepare(clone, broken, **kw)
            expect(False, "a failed listing must raise CouldNot, not return an empty plan")
        except CouldNot:
            pass
        try:
            check(clone, {**plan, "base_sha": "0" * 40})
            expect(False, "a missing base must raise CouldNot")
        except CouldNot:
            pass

        # 8. the battery never sees a GitHub token.
        planted = {"GH_TOKEN": "not-a-real-token", "GITHUB_OUTPUT": os.path.join(tmp, "out"),
                   "ACTIONS_RUNTIME_TOKEN": "not-a-real-token"}
        os.environ.update(planted)
        try:
            leak = run_commands(tmp, [("env", 'test -z "$GH_TOKEN$GITHUB_TOKEN$GITHUB_OUTPUT$ACTIONS_RUNTIME_TOKEN"')], 30)
            expect(leak == [], "tokens and the runner's step files must be scrubbed from the battery's environment")
        finally:
            for k in planted:
                del os.environ[k]

        # 9. the source builds no push, no PR merge, no comment and no label write.
        src = open(os.path.abspath(__file__)).read().split("# ── selftest", 1)[0]
        for token in ('"pu' + 'sh"', "pr " + "merge", "gh " + "api", "--add" + "-label", "pr " + "comment"):
            expect(token not in src, f"the check must not build {token!r}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("check_pr_union selftest: FAIL")
        for f in failures:
            print("  -", f)
        return 1
    print("check_pr_union selftest: OK (10 cases)")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("prepare")
    p.add_argument("--out", required=True)
    p.add_argument("--max-prs", type=int, default=MAX_PRS_DEFAULT)
    p.add_argument("--remote", default="origin")
    p.add_argument("--base", default="main")
    g = sub.add_parser("gate")
    g.add_argument("--verdict", default="")
    g.add_argument("--left-out", type=int, default=0)
    c = sub.add_parser("check")
    c.add_argument("--plan", required=True)
    c.add_argument("--out", required=True)
    c.add_argument("--also", action="append", default=[])
    c.add_argument("--exclude", action="append", default=list(NEEDS_INSTALL))
    c.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args(argv)
    if args.selftest:
        from scratch_run import contained   # KIT-450: its temp folders go when it ends
        return contained(selftest, "pr-union-selftest-run-")
    if args.cmd == "gate":
        ok, why = gate(args.verdict, args.left_out)
        print(("" if ok else "::error::") + why)
        return 0 if ok else 1
    repo = os.getcwd()
    try:
        if args.cmd == "prepare":
            plan = prepare(repo, lambda: gh_listing(repo), args.max_prs, args.remote, args.base)
            with open(args.out, "w") as fh:
                json.dump(plan, fh)
            emit_prepare(plan)
            if plan.get("left_out"):
                _summary("### PR union check — over the cap\n- " + ", ".join(f"#{n}" for n in plan["left_out"])
                         + " are NOT in this run's union; the job fails for that.")
            if plan["verdict"]:
                _summary(render({**plan, "merged": plan["prs"], "failures": [], "attribution": None}))
            else:
                print(f"plan: main@{plan['base_sha'][:8]} + {[p['number'] for p in plan['prs']]} (key {plan['key']})")
            return 0
        if args.cmd == "check":
            with open(args.plan) as fh:
                plan = json.load(fh)
            result = check(repo, plan, args.also, tuple(args.exclude), args.timeout)
    except CouldNot as e:
        result = {"schema": SCHEMA, "verdict": "could-not", "error": str(e)}
        if args.cmd == "check":
            with open(args.out, "w") as fh:
                json.dump(result, fh)
            _emit("verdict", "could-not")
            _emit("result", json.dumps(result))
        _summary(f"### PR union check — **could not check**\n- {e}\n- This is NOT a green result.")
        return 2
    if args.cmd != "check":
        ap.print_help()
        return 2
    with open(args.out, "w") as fh:
        json.dump(result, fh)
    _emit("verdict", result["verdict"])
    _emit("result", json.dumps(result))
    _summary(render(result))
    return 1 if result["verdict"] == "red" else 0


if __name__ == "__main__":
    sys.exit(main())
