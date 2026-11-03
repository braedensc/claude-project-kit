#!/usr/bin/env python3
"""Layer 3's secret scan: every file git tracks, read as git stores it, and its proof.

    python3 scripts/check_secret_scan.py --scan       # npm run lint:secrets; CI's first step
    python3 scripts/check_secret_scan.py --scan --tree <commit>   # a commit's files instead
    python3 scripts/check_secret_scan.py --scan --staged          # what a commit will hold:
                                                                  # the pre-commit hook
    python3 scripts/check_secret_scan.py --selftest   # the checks CI runs after `npm ci`

--scan reads every file in git's index (or, with --tree, in a commit; with --staged, each
file a commit would add or change) as git stores it (`git cat-file`, no filters), so the
working tree, a .gitattributes conversion and untracked files never decide what it sees.
That is also why the pre-commit hook uses it: a value staged and then edited out of the
file is in the commit, and a name with a space, a quote or a backslash is just a name. Each file is copied to a folder of its own in a throwaway directory, under its own
name so a rule that reads the name (.npmrc, package.json, .p12) still applies, and
secretlint judges the copies there:
  - a name secretlint would skip, or read as an ignore file or its config (node_modules,
    .git*, .secretlint*), gets a `_` in front, and a character outside [A-Za-z0-9._-]
    becomes `_`, so a backslash or a name that is not UTF-8 is still found. With the
    numbered folder in front, no name reads as an option or a glob, and no file in the
    scanned tree is an ignore file, whatever the PR holds;
  - beside each copy sit text copies, each named to read as text: its bytes with NULs
    dropped, and, where it holds a NUL or starts with a byte-order mark, its UTF-16 and
    UTF-32 readings in both byte orders. secretlint runs no text rule on a file it calls
    binary, by its content or by a name like .png, and a key between non-ASCII characters
    in UTF-16 is only found once decoded: dropping NULs glues their bytes onto it;
  - `secretlint-disable` and `secretlint-enable` are made inert in both: a directive in
    the tree would be the PR's own say over its scan.
secretlint's JSON report names every file it read, so a copy it did not read is reported
as could-not-scan, never as clean.

In CI the scan is the first step after Node is set up, before any of the PR's code runs,
and it runs the default branch's copy of this file, its `.secretlintrc.json`, and the
secretlint packages at the versions its lockfile pins (--scanner-packages prints them;
their own dependencies resolve fresh from the registry), never the PR's, nor those of the
base the PR names, which its author could have pushed. On a PR it scans the head's own
tree too: a base can delete one of the head's files from the merge it checks out.
This file and the rules are grader paths: a change needs the `hooks-change` label and
takes effect once merged (KIT-400, KIT-401). The lockfile is not, so --scanner-packages
refuses a pin older than SCANNER_FLOOR. Because the default branch's copy runs, a change
to these flags lands in two steps: the file first, then the workflows that call it.

It does not find a key that is split, encoded, compressed or encrypted, one in an
encoding other than UTF-8, UTF-16 or UTF-32, one beside non-ASCII text in UTF-16 or UTF-32
that starts at an odd byte inside a binary file, or one secretlint's rules do not know. A
`.secretlintignore` is not read: every tracked file is scanned. Keep key-shaped values
out of the tree; a test builds them at run time, as this file's selftest does.

--selftest checks that the kit's CI and the template's run the same scan step, first,
and that lint:secrets runs it; then runs the scan with the real secretlint over a
throwaway repo holding a key-shaped value, made at run time, in every shape above, and
proves that a scan secretlint did not finish is never reported as clean.

Exit 0 = clean, or every check held. Exit 1 = a secret found, or a check failed. Exit 2 =
it could not scan (no secretlint, no node, no config, a file secretlint did not read):
docs/PIPELINE-CONTRACT.md §13, could-not-scan is never reported as a pass.
"""
import argparse
import json
import os
import re
import secrets
import shutil
import string
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAN_STEP = "Secret scan (every tracked file, as git stores it)"
# (workflow, job): the job whose first step after Node runs the scan.
WORKFLOWS = ((".github/workflows/ci.yml", "kit-checks"),
             ("templates/workflows/ci.yml", "secrets-and-paths"))
LINT_SECRETS = "python3 scripts/check_secret_scan.py --scan"
BLOB_MODES = (b"100644", b"100755", b"120000")  # files and links; a submodule has no blob here
CHUNK = 300
TEXT_CODECS = ("utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be")
BOMS = (b"\xff\xfe", b"\xfe\xff", b"\x00\x00\xfe\xff")  # FF FE 00 00 starts like FF FE
# The oldest scanner and rules the scan accepts. The lockfile that pins them needs no label,
# so a pin below these, which would run fewer rules, is refused instead. Raising them is free.
SCANNER_FLOOR = {"secretlint": (13, 0, 2),
                 "@secretlint/secretlint-rule-preset-recommend": (13, 0, 2)}


def copy_name(base):
    """The name a tracked file's copy is scanned under; `base` is its last path part."""
    name = re.sub(rb"[^A-Za-z0-9._-]", b"_", base).decode("ascii")
    low = name.lower()
    if low == "node_modules" or low.startswith((".git", ".secretlint")):
        name = "_" + name
    return name if len(name) <= 120 else "_" + name[-119:]


def text_views(data):
    """[(how, bytes)]: each reading a text rule may need. Always the bytes with NULs dropped,
    which keeps ASCII whole whatever surrounds it, a byte-order mark included; and, where the
    file holds a NUL or starts with a mark, each UTF-16 and UTF-32 reading in both byte
    orders, so a key between non-ASCII characters is not glued to their bytes."""
    views = [("text", data.replace(b"\x00", b""))]
    if b"\x00" in data or data.startswith(BOMS):
        views += [(codec, data.decode(codec, "replace").encode("utf-8", "replace"))
                  for codec in TEXT_CODECS]
    return views


def inert(data):
    """The file with no `secretlint-disable` or `-enable` directive left; the same length,
    so every line and column a finding names is the file's own."""
    return (data.replace(b"secretlint-disable", b"secretlint_disable")
            .replace(b"secretlint-enable", b"secretlint_enable"))


def shown(path):
    text = path.decode("utf-8", "backslashreplace")
    return text if text.isprintable() else ascii(text)[1:-1]


def secretlint_js(tool):
    """The secretlint CLI script in `tool`/node_modules, by its package's `bin`, or None."""
    pkg = os.path.join(tool, "node_modules", "secretlint")
    try:
        with open(os.path.join(pkg, "package.json"), encoding="utf-8") as fh:
            entry = json.load(fh).get("bin")
    except (OSError, ValueError, AttributeError):
        return None
    rel = entry.get("secretlint") if isinstance(entry, dict) else entry
    path = os.path.join(pkg, rel) if isinstance(rel, str) else None
    return path if path and os.path.isfile(path) else None


def find_tool():
    """The folder whose node_modules holds secretlint: this checkout's, else the main
    checkout's (a worktree has none of its own), as the pre-commit hook finds it."""
    r = subprocess.run(["git", "-C", ROOT, "rev-parse", "--path-format=absolute",
                        "--git-common-dir"], capture_output=True, text=True)
    main = [os.path.dirname(r.stdout.strip())] if r.returncode == 0 else []
    return next((d for d in [ROOT] + main if secretlint_js(d)), None)


def scanner_packages(lock_path):
    """`name@version` for each secretlint package a lockfile's root depends on, at the
    version it pins: what CI installs to run the scan, chosen by the default branch. A pin
    older than SCANNER_FLOOR, or not a plain version, is refused."""
    with open(lock_path, encoding="utf-8") as fh:
        packages = json.load(fh).get("packages") or {}
    root = packages.get("") or {}
    names = sorted({n for kind in ("dependencies", "devDependencies", "optionalDependencies")
                    for n in root.get(kind) or {}
                    if n == "secretlint" or n.startswith("@secretlint/") or "secretlint-rule" in n})
    missing = [name for name in SCANNER_FLOOR if name not in names]
    if missing:
        raise ValueError("%s pins no %s" % (lock_path, " and no ".join(missing)))
    found = []
    for name in names:
        version = (packages.get("node_modules/" + name) or {}).get("version")
        if not isinstance(version, str) or not re.fullmatch(r"[0-9A-Za-z.+-]+", version):
            raise ValueError("%s pins no plain version of %s" % (lock_path, name))
        plain = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
        if name in SCANNER_FLOOR and (not plain or tuple(map(int, plain.groups()))
                                      < SCANNER_FLOOR[name]):
            raise ValueError("%s pins %s@%s, older than %s, the oldest the scan accepts"
                             % (lock_path, name, version,
                                ".".join(map(str, SCANNER_FLOOR[name]))))
        found.append("%s@%s" % (name, version))
    return found


def tracked_blobs(repo, rev=None):
    """[(object id, path)] for every file in the index of the repo `repo` is in, or in the
    tree of commit `rev`; None if git cannot list it."""
    top = subprocess.run(["git", "-C", repo, "rev-parse", "--show-toplevel"], capture_output=True)
    if top.returncode != 0:
        return None
    listing = (["ls-tree", "-r", "-z", "--full-tree", rev] if rev  # <mode> <type> <id>\t<path>
               else ["ls-files", "-s", "-z"])                      # <mode> <id> <stage>\t<path>
    r = subprocess.run(["git", "-C", top.stdout.rstrip(b"\n")] + listing, capture_output=True)
    if r.returncode != 0:
        return None
    blobs = []
    for record in r.stdout.split(b"\0"):
        meta, tab, path = record.partition(b"\t")
        fields = meta.split(b" ")
        if tab and len(fields) == 3 and fields[0] in BLOB_MODES:
            blobs.append(((fields[2] if rev else fields[1]).decode("ascii"), path))
    return blobs


def staged_blobs(repo):
    """[(object id, path)] for every staged path whose new content is a file or a link,
    as the commit will hold it (the index's blob), or None if git cannot list them. A
    deletion holds no content; without a HEAD, git compares with the empty tree."""
    top = subprocess.run(["git", "-C", repo, "rev-parse", "--show-toplevel"], capture_output=True)
    if top.returncode != 0:
        return None
    r = subprocess.run(["git", "-C", top.stdout.rstrip(b"\n"), "diff", "--cached", "--raw", "-z",
                        "--no-renames", "--no-abbrev", "--diff-filter=d"], capture_output=True)
    if r.returncode != 0:
        return None
    fields = r.stdout.split(b"\0")   # ":<old mode> <new mode> <old id> <new id> <status>", path
    blobs = []
    for meta, path in zip(fields[0::2], fields[1::2]):
        parts = meta.lstrip(b":").split(b" ")
        if len(parts) != 5 or not path:
            return None
        if parts[1] in BLOB_MODES:
            blobs.append((parts[3].decode("ascii"), path))
    return blobs


def read_blobs(repo, ids):
    """{object id: its bytes as git stores them}, or None if one cannot be read."""
    ids = list(dict.fromkeys(ids))
    r = subprocess.run(["git", "-C", repo, "cat-file", "--batch"], capture_output=True,
                       input=b"".join(i.encode("ascii") + b"\n" for i in ids))
    if r.returncode != 0:
        return None
    out, pos, data = r.stdout, 0, {}
    for oid in ids:
        end = out.find(b"\n", pos)
        head = out[pos:end].split(b" ") if end >= 0 else []
        if len(head) != 3 or head[1] != b"blob":
            return None
        start = end + 1
        data[oid] = out[start:start + int(head[2])]
        pos = start + int(head[2]) + 1
    return data


def scan_chunk(chunk, data, node, js, config):
    """(findings, could-not-scan reasons) for one chunk of tracked files."""
    tmp = tempfile.mkdtemp(prefix="secret-scan-")
    try:
        where, args = {}, []
        for n, (oid, path) in enumerate(chunk):
            os.mkdir(os.path.join(tmp, str(n)))
            name = copy_name(path.rsplit(b"/", 1)[-1])
            copies = [(name, inert(data[oid]), "")]
            copies += [("%s.%s.scan.txt" % (name, how), inert(body), " (read as %s)" % how)
                       for how, body in text_views(data[oid])]
            for rel, body, how in copies:
                full = os.path.join(tmp, str(n), rel)
                with open(full, "wb") as fh:
                    fh.write(body)
                where[os.path.realpath(full)] = (shown(path), how)
                args.append("%d/%s" % (n, rel))
        # The report goes to a file: secretlint exits straight after printing it, which cuts
        # a long report short in a pipe. With --output it exits 0 whatever it found.
        out = os.path.join(tmp, "report.json")
        env = {k: v for k, v in os.environ.items() if k not in ("NODE_OPTIONS", "NODE_PATH")}
        r = subprocess.run([node, js, "--secretlintrc", config, "--format", "json",
                            "--output", out] + args,
                           cwd=tmp, capture_output=True, env=env, timeout=600)
        said = r.stderr.decode("utf-8", "replace").strip().splitlines()[-12:]
        try:
            with open(out, encoding="utf-8") as fh:
                report = {os.path.realpath(e["filePath"]): e.get("messages") or []
                          for e in json.load(fh)}
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            return [], ["secretlint exited %d without its report" % r.returncode] + said
        findings, reasons = [], []
        for real, messages in report.items():
            path, how = where.get(real, (real, ""))
            for m in messages:
                if isinstance(m, dict) and m.get("severity") == "error":
                    start = (m.get("loc") or {}).get("start") or {}
                    findings.append((path, start.get("line", 0), start.get("column", 0) + 1,
                                     m.get("ruleId", "?"), m.get("message", ""), how))
        unread = sorted({where[p][0] for p in where if p not in report})
        if unread:
            reasons.append("secretlint did not read %d tracked file(s): %s"
                           % (len(unread), ", ".join(unread[:10])))
        if r.returncode != 0:
            reasons += ["secretlint exited %d after writing its report" % r.returncode] + said
        return findings, reasons
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def scan_tracked(repo, tool, config, rev=None, staged=False):
    """(exit code, lines to print, findings) for every file git tracks in `repo` (given
    `rev`, every file of that commit; given `staged`, every file a commit would add or
    change); a finding is (path, line, column, rule, message, how it was read)."""
    js, node = secretlint_js(tool) if tool else None, shutil.which("node")
    problem = ((not js and "no secretlint in %s" % (os.path.join(tool, "node_modules") if tool
                                                     else "this checkout or the main checkout"))
               or (not node and "no node on PATH")
               or (not os.path.isfile(config) and "no secretlint config at %s" % config))
    if problem:
        return 2, ["COULD NOT SCAN: %s. Run `npm install`." % problem], []
    what = ("files of commit %s" % rev[:12] if rev else "staged files" if staged
            else "tracked files")
    blobs = staged_blobs(repo) if staged else tracked_blobs(repo, rev)
    if blobs is None:
        return 2, ["COULD NOT SCAN: git could not list the %s." % (
            "files of commit %s" % rev if rev else what)], []
    findings, reasons = [], []
    for at in range(0, len(blobs), CHUNK):
        chunk = blobs[at:at + CHUNK]
        data = read_blobs(repo, [oid for oid, _ in chunk])
        if data is None:
            reasons.append("git could not read a tracked file as it stores it")
            break
        found, why = scan_chunk(chunk, data, node, js, os.path.abspath(config))
        findings += found
        reasons += why
    best = {}
    for f in sorted(findings):  # a copy read as it is sorts before its text copy
        best.setdefault(f[:4], f)
    findings = sorted(best.values())
    lines = ["%s:%d:%d %s: %s%s" % f for f in findings]
    lines += ["COULD NOT SCAN: " + why for why in reasons]
    if findings:
        return 1, (["secret scan: %d finding(s) in %s:" % (len(findings), what)]
                   + lines), findings
    if reasons:
        return 2, lines, findings
    return 0, ["secret scan: no secrets in %d %s, each read as git stores it"
               % (len(blobs), what)], findings


# ---------------------------------------------------------------------------------------
# selftest

# The scan step, line for line as job_steps() reads it (comments and blank lines dropped). A
# textual check that only looked for the scan line passed `|| true`, an `echo` in front of
# it, an `exit 0` before it and a `shell:` that runs nothing; pinned whole, any edit to the
# step fails this until it is made here too, in the same reviewed PR.
SCAN_STEP_LINES = (
    "      - name: " + SCAN_STEP,
    "        env:",
    "          DEFAULT_BRANCH: ${{ github.event.repository.default_branch }}",
    "          PR_HEAD: ${{ github.event.pull_request.head.sha }}",
    "        run: |",
    "          set -euo pipefail",
    '          scan="$RUNNER_TEMP/secret-scan"',
    '          mkdir -p "$scan/tool"',
    '          git fetch --no-tags --depth=1 origin "refs/heads/$DEFAULT_BRANCH"',
    "          trusted=$(git rev-parse FETCH_HEAD)",
    '          git show "$trusted:package-lock.json" > "$scan/package-lock.json"',
    '          git show "$trusted:.secretlintrc.json" > "$scan/.secretlintrc.json"',
    '          if ! git show "$trusted:scripts/check_secret_scan.py" > "$scan/check_secret_scan.py"'
    ' 2>/dev/null; then',
    '            echo "::notice::the default branch has no scripts/check_secret_scan.py yet, so the'
    ' PR\'s copy runs"',
    '            cp scripts/check_secret_scan.py "$scan/check_secret_scan.py"',
    "          fi",
    '          python3 -I "$scan/check_secret_scan.py" --scanner-packages "$scan/package-lock.json"'
    ' > "$scan/packages.txt"',
    '          (cd "$scan/tool" && xargs npm install --prefix "$scan/tool" --ignore-scripts --no-save'
    ' --no-package-lock --no-audit --no-fund < "$scan/packages.txt")',
    '          python3 -I "$scan/check_secret_scan.py" --scan --tool "$scan/tool" --config'
    ' "$scan/.secretlintrc.json"',
    '          if [ -n "$PR_HEAD" ]; then',
    '            git fetch --no-tags --depth=1 origin "$PR_HEAD"',
    '            python3 -I "$scan/check_secret_scan.py" --scan --tree "$PR_HEAD" --tool "$scan/tool"'
    ' --config "$scan/.secretlintrc.json"',
    "          fi",
)


def job_steps(text, job):
    """The steps of `job` in a workflow, each as its lines with comments and blank lines
    dropped: enough to say where a step sits and what it says, without PyYAML."""
    lines = text.splitlines()
    if "  %s:" % job not in lines:
        return None
    steps, in_steps = [], False
    for line in lines[lines.index("  %s:" % job) + 1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith("    "):
            break
        if line.startswith("    ") and not line.startswith("     "):
            in_steps = line == "    steps:"
        elif in_steps and line.startswith("      - "):
            steps.append([line])
        elif in_steps and steps and line.startswith("        "):
            steps[-1].append(line)
    return steps


# Keys that can skip the job, excuse its failure, or change what its steps run in, at the
# job's level or the workflow's. Quoted or bare: YAML reads `"if":` as `if:`.
JOB_KEYS = r"(if|continue-on-error|defaults|container|services|needs|env)"
WORKFLOW_KEYS = r"(defaults|env)"


def job_level_skips(text, job):
    """Job- and workflow-level keys that would skip, excuse or redirect every step."""
    lines = text.splitlines()
    found = [line.strip() for line in lines
             if re.match(r"[\"']?%s[\"']?\s*:" % WORKFLOW_KEYS, line)]
    if "  %s:" % job not in lines:
        return found
    for line in lines[lines.index("  %s:" % job) + 1:]:
        if line.strip() and not line.startswith("    "):
            break
        if re.match(r"    [\"']?%s[\"']?\s*:" % JOB_KEYS, line):
            found.append(line.strip())
    return found


def scan_steps():
    """{workflow: (problems, the scan step's lines)} for each workflow in WORKFLOWS."""
    out = {}
    for rel, job in WORKFLOWS:
        try:
            with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
                text = fh.read()
            steps = job_steps(text, job)
        except OSError:
            text, steps = "", None
        if not steps or len(steps) < 3:
            out[rel] = (["no job %s with three steps" % job], None)
            continue
        problems = []
        if tuple(steps[0]) != ("      - uses: actions/checkout@v4",):
            problems.append("step 1 is not the plain checkout")
        if tuple(steps[1]) != ("      - uses: actions/setup-node@v4", "        with:",
                               "          node-version: 22"):
            problems.append("step 2 is not Node 22 without the npm cache")
        scan = steps[2]
        if scan[0] != "      - name: " + SCAN_STEP:
            problems.append("step 3 is not %r" % SCAN_STEP)
        if any(re.match(r"\s+(if|continue-on-error)\s*:", line) for line in scan):
            problems.append("the scan step can be skipped or let fail")
        if not any('check_secret_scan.py" --scan' in line for line in scan):
            problems.append("the scan step does not run the scan")
        if not any("github.event.repository.default_branch" in line for line in scan):
            problems.append("the scan step does not take its copies from the default branch")
        if tuple(scan) != SCAN_STEP_LINES:
            problems.append("the scan step is not the one SCAN_STEP_LINES pins")
        if job_level_skips(text, job):
            problems.append("the job can be skipped or let fail: %s" % job_level_skips(text, job))
        out[rel] = (problems, scan)
    return out


def template_guard():
    """(the `grep -E` pattern the template's hooks-change guard flags paths with, its text)."""
    try:
        with open(os.path.join(ROOT, "templates/workflows/ci.yml"), encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None, ""
    m = re.search(r"SENSITIVE=.*grep (?:--null-data )?-E '([^']+)'", text)
    return (m.group(1) if m else None), text


def key_value():
    # Built at run time: a key-shaped literal in this file would fail the very scan it tests.
    body = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(40))
    return ("TOKEN=" + "lin" + "_api_" + body + "\n").encode("ascii")


def throwaway_repo(files):
    """A repo whose index holds `files` ({path: (bytes, mode)}) and that has no working tree
    at all, so a scan reading anything but what git stores finds nothing."""
    root = tempfile.mkdtemp(prefix="secret-scan-repo-")
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    subprocess.run(["git", "init", "-q", root], check=True, capture_output=True, env=env)
    info, oids = b"", {}
    for path, (body, mode) in files.items():
        if body not in oids:
            oids[body] = subprocess.run(["git", "-C", root, "hash-object", "-w", "--stdin"],
                                        input=body, check=True, capture_output=True,
                                        env=env).stdout.strip()
        info += b"%s %s\t%s\0" % (mode, oids[body], path)
    subprocess.run(["git", "-C", root, "update-index", "-z", "--add", "--index-info"],
                   input=info, check=True, capture_output=True, env=env)
    return root


def fake_tool(script):
    """A node_modules holding a stand-in secretlint that runs `script` (JavaScript)."""
    tool = tempfile.mkdtemp(prefix="secret-scan-tool-")
    pkg = os.path.join(tool, "node_modules", "secretlint")
    os.makedirs(pkg)
    with open(os.path.join(pkg, "package.json"), "w") as fh:
        json.dump({"name": "secretlint", "bin": {"secretlint": "./cli.js"}}, fh)
    with open(os.path.join(pkg, "cli.js"), "w") as fh:
        fh.write(script)
    return tool


def selftest():
    results, could_not = [], []

    def check(name, ok, detail=""):
        results.append(ok)
        print("[%s] %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else " — %s" % (detail,)))

    steps = scan_steps()
    check("the kit's CI and the template's run the scan as the first step after Node, "
          "unskippable", all(not p for p, _ in steps.values()),
          {rel: p for rel, (p, _) in steps.items() if p})
    scans = [scan for _, scan in steps.values()]
    check("…and the two scan steps say the same thing", scans[0] is not None and scans[0] == scans[1])
    try:
        with open(os.path.join(ROOT, "package.json"), encoding="utf-8") as fh:
            lint = (json.load(fh).get("scripts") or {}).get("lint:secrets")
    except (OSError, ValueError):
        lint = None
    check("npm run lint:secrets runs the same scan", lint == LINT_SECRETS, lint)

    # The template's own guard is a grep in YAML (the kit's is check_grader_paths.py): it
    # must flag the scan's rules, its script and the workflows, and nothing that only looks
    # like them (KIT-401).
    pattern, text = template_guard()
    # The template's gate lists names raw (-z), or a quoted name matches no pattern.
    flagged = {p: bool(pattern and re.search(pattern, p)) for p in (
        ".secretlintrc.json", ".secretlintignore", "docs/deep/.secretlintignore",
        ".claude/hooks/pre-tool-use.py", ".github/workflows/ci.yml",
        "scripts/check_secret_scan.py", "docs/secretlintignore.md",
        "docs/.github/workflows/ci.yml", "scripts/check_secret_scan.py.md", "src/app.ts")}
    check("the template's hooks-change guard flags the scan's rules, script and workflows, "
          "and only them", flagged == {
              ".secretlintrc.json": True, ".secretlintignore": True,
              "docs/deep/.secretlintignore": True, ".claude/hooks/pre-tool-use.py": True,
              ".github/workflows/ci.yml": True, "scripts/check_secret_scan.py": True,
              "docs/secretlintignore.md": False, "docs/.github/workflows/ci.yml": False,
              "scripts/check_secret_scan.py.md": False, "src/app.ts": False}, flagged)
    check("…sees a renamed file's old path, and a grep error fails the guard",
          "git diff -z --no-renames --name-only" in text and "grep --null-data -E" in text
          and "|| [ $? -eq 1 ]; } | tr '\\0' '\\n')" in text)
    # Both label gates judge the PR against the default branch, never a base its author
    # named: such a base could carry its own gate, and a retarget re-runs nothing.
    gates = {}
    for rel in (".github/workflows/ci.yml", "templates/workflows/ci.yml"):
        try:
            with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
                guard = fh.read().split("hooks-change-guard:", 1)[-1]
        except OSError:
            guard = ""
        gates[rel] = ("DEFAULT_BRANCH: ${{ github.event.repository.default_branch }}" in guard
                      and "github.event.pull_request.base.sha" not in guard)
    check("both label gates judge against the default branch, whatever base the PR names",
          all(gates.values()), gates)

    lock = tempfile.mkdtemp(prefix="secret-scan-lock-")
    with open(os.path.join(lock, "a.json"), "w") as fh:
        json.dump({"packages": {
            "": {"devDependencies": {"husky": "^9", "secretlint": "^13",
                                     "@secretlint/secretlint-rule-preset-recommend": "^13"}},
            "node_modules/husky": {"version": "9.1.7"},
            "node_modules/secretlint": {"version": "13.1.0"},
            "node_modules/@secretlint/secretlint-rule-preset-recommend": {"version": "13.0.2"},
            "node_modules/@secretlint/core": {"version": "13.0.2"}}}, fh)
    with open(os.path.join(lock, "b.json"), "w") as fh:
        json.dump({"packages": {"": {"devDependencies": {"husky": "^9"}}}}, fh)
    with open(os.path.join(lock, "c.json"), "w") as fh:
        json.dump({"packages": {
            "": {"devDependencies": {"secretlint": "^9",
                                     "@secretlint/secretlint-rule-preset-recommend": "^9"}},
            "node_modules/secretlint": {"version": "9.3.4"},
            "node_modules/@secretlint/secretlint-rule-preset-recommend": {"version": "9.3.4"}}},
            fh)
    try:
        got = scanner_packages(os.path.join(lock, "a.json"))
    except ValueError as e:
        got = str(e)
    check("CI installs the secretlint packages the default branch's lockfile pins, only those",
          got == ["@secretlint/secretlint-rule-preset-recommend@13.0.2", "secretlint@13.1.0"], got)
    try:
        scanner_packages(os.path.join(lock, "b.json"))
        check("…and refuses a lockfile that pins no secretlint", False, "no error")
    except ValueError:
        check("…and refuses a lockfile that pins no secretlint", True)
    try:
        scanner_packages(os.path.join(lock, "c.json"))
        check("…and refuses a pin older than SCANNER_FLOOR (the lockfile needs no label)",
              False, "no error")
    except ValueError:
        check("…and refuses a pin older than SCANNER_FLOOR (the lockfile needs no label)", True)
    shutil.rmtree(lock, ignore_errors=True)

    node = shutil.which("node")
    if not node:
        could_not.append("no node on PATH")
    else:
        # The stand-in writes only to a report.json the scan named. Unchecked, a run without
        # --output would take argv[0], the node binary, as the place to write (it did, once).
        report = ("const a = process.argv, i = a.indexOf('--output'), out = a[i + 1] || '';"
                  "if (i < 0 || require('path').basename(out) !== 'report.json') process.exit(3);"
                  "const files = a.slice(i + 2);"
                  "const write = r => require('fs').writeFileSync(out, JSON.stringify(r));")
        clean = "write(files.map(f => ({filePath: require('path').resolve(f), messages: []})));"
        for name, script, why in (
                ("writes no report", "", "a report cut short or never written"),
                ("reads nothing", report + "write([]);", "a file secretlint did not read"),
                ("crashes", "process.stderr.write('Error: boom'); process.exit(2);",
                 "secretlint failing"),
                ("writes a clean report, then exits 2", report + clean + "process.exit(2);",
                 "a run that did not end well")):
            tool = fake_tool(script)
            repo = throwaway_repo({b"plain.md": (b"plain\n", b"100644")})
            rc, said, _ = scan_tracked(repo, tool, os.path.join(ROOT, ".secretlintrc.json"))
            check("a secretlint that %s is could-not-scan (exit 2), not clean: %s" % (name, why),
                  rc == 2, "exit %d: %s" % (rc, said))
            shutil.rmtree(tool, ignore_errors=True)
            shutil.rmtree(repo, ignore_errors=True)

    tool = find_tool()
    if not tool:
        could_not.append("no secretlint here or in the main checkout; run `npm install`")
    if not tool or not node:
        print("COULD NOT RUN the scans: " + "; ".join(could_not))
        return 1 if not all(results) else 2

    config = os.path.join(ROOT, ".secretlintrc.json")
    key = key_value()
    plain = lambda body: (body, b"100644")
    shapes = {
        b"ignored/held.md": plain(key),
        b".secretlintignore": plain(b"*\n" + key),
        b"docs/held.md": plain(key),
        b"node_modules/held.js": plain(key),
        b"lib/node_modules": plain(key),
        b"deep/.SecretlintRC.json": plain(key),
        b"src/disabled.js": plain(b"// secretlint-disable\n" + key + b"// secretlint-enable\n"),
        b"src/line.js": plain(key.rstrip(b"\n") + b" // secretlint-disable-line\n"),
        b"held-bom16.txt": plain(b"\xff\xfe" + key.decode().encode("utf-16-le")),
        b"held16.txt": plain(key.decode().encode("utf-16-le")),
        # Dropping NULs glues a neighbour's bytes onto a key: で (U+3067) leaves "g0" in
        # UTF-16LE, so 45 of them carry the key past the rule's 128 characters; ≡ (U+2261)
        # leaves an "a" just before it in UTF-16BE. Only a real decoding finds either.
        b"bom-ascii.env": plain(b"\xff\xfe" + key),
        b"bom32-ascii.env": plain(b"\xff\xfe\x00\x00" + key),
        b"mixed.ps1": plain(b"\xff\xfe" + "APP=demo\r\n".encode("utf-16-le") + key),
        b"le16-glued.txt": plain((key.decode()[:-1] + "\u3067" * 45 + "\n").encode("utf-16-le")),
        b"be16-glued.txt": plain(("\u2261" + key.decode()[6:]).encode("utf-16-be")),
        b"le32-glued.txt": plain((key.decode()[:-1] + "\u3067" * 45 + "\n").encode("utf-32-le")),
        b"odd32.bin": plain(b"\x01" + key.decode().encode("utf-32-le")),
        b"held.bin": plain(b"\x00\x01" + key),
        b"held.png": plain(key),
        b"a\\b.md": plain(key),
        b"\xff.md": plain(key),
        b"new\nline.md": plain(key),
        b"--help": plain(key),
        b"--output=report.txt": plain(key),
        b"run.sh": (key, b"100755"),
        b"link": (key.strip(), b"120000"),
    }
    why = {
        b"ignored/held.md": "in a file a .gitignore rule covers",
        b".secretlintignore": "in a file that is one of secretlint's ignore files, saying `*`",
        b"docs/held.md": "in a file under two of secretlint's ignore files saying `*`",
        b"node_modules/held.js": "in a folder secretlint skips",
        b"lib/node_modules": "in a file named like that folder",
        b"deep/.SecretlintRC.json": "in a file named like secretlint's config, in another case",
        b"src/disabled.js": "between secretlint-disable and -enable",
        b"src/line.js": "on a secretlint-disable-line line",
        b"held-bom16.txt": "in UTF-16 with a byte-order mark",
        b"held16.txt": "in UTF-16 without one",
        b"bom-ascii.env": "in plain text behind a UTF-16 byte-order mark",
        b"bom32-ascii.env": "in plain text behind a UTF-32 byte-order mark",
        b"mixed.ps1": "in plain text after UTF-16 with a mark, as a shell appends to one",
        b"le16-glued.txt": "right before non-ASCII text, in UTF-16LE without a mark",
        b"be16-glued.txt": "right after a non-ASCII character, in UTF-16BE without a mark",
        b"le32-glued.txt": "right before non-ASCII text, in UTF-32LE without a mark",
        b"odd32.bin": "in UTF-32 one byte into a binary file, where no decoding lines up",
        b"held.bin": "behind a NUL byte",
        b"held.png": "in a file named like an image",
        b"a\\b.md": "in a file named with a backslash",
        b"\xff.md": "in a file named in bytes that are not UTF-8",
        b"new\nline.md": "in a file named with a line break",
        b"--help": "in a file named like an option",
        b"--output=report.txt": "in a file named like an option that writes a file",
        b"run.sh": "in an executable file",
        b"link": "as a link's target",
    }
    repo = throwaway_repo({**shapes,
                           b".gitignore": plain(b"ignored/\n*\n"),
                           b"docs/.secretlintignore": plain(b"*\n"),
                           b"conf/.npmrc": plain(b"//registry.npmjs.org/:_authToken=t0ken\n")})
    rc, said, findings = scan_tracked(repo, tool, config)
    paths = {f[0] for f in findings}
    check("a repo holding a key exits 1, having read every file",
          rc == 1 and not any(line.startswith("COULD NOT SCAN") for line in said),
          "exit %d: %s" % (rc, said[-5:]))
    for path, reason in why.items():
        check("a key is found %s" % reason, shown(path) in paths,
              "found in %s" % sorted(paths))
    check("a rule that reads the name still applies: .npmrc's authToken",
          any(f[0] == "conf/.npmrc" and f[3].endswith("secretlint-rule-npm") for f in findings),
          [f for f in findings if f[0] == "conf/.npmrc"])
    check("nothing else is reported, and no option-like name wrote a file",
          paths == {shown(p) for p in why} | {"conf/.npmrc"}
          and os.listdir(repo) == [".git"], (sorted(paths), os.listdir(repo)))
    check("a finding names its line and rule, and never the key",
          any(f[0] == "docs/held.md" and f[1:3] == (1, 7) and "linear" in f[3] for f in findings)
          and not any(key.strip()[6:].decode() in line for line in said), said[:3])
    shutil.rmtree(repo, ignore_errors=True)

    # Past one chunk, and a report far past what a pipe holds: secretlint exits straight
    # after printing, so a long report read from its stdout comes back cut short.
    bulk = {b"bulk/%03d.md" % n: plain(b"plain words\n" * 1500) for n in range(CHUNK + 50)}
    bulk[b"bulk/%03d.md" % (CHUNK + 49)] = plain(b"plain words\n" * 1500 + key)
    repo = throwaway_repo(bulk)
    rc, said, findings = scan_tracked(repo, tool, config)
    check("%d files of 18 KB are all read, past one chunk, and a key in the last is found"
          % len(bulk), rc == 1 and [f[0] for f in findings] == ["bulk/%03d.md" % (CHUNK + 49)],
          "exit %d: %s" % (rc, said[-3:]))
    shutil.rmtree(repo, ignore_errors=True)

    # On a PR, CI scans the head's own tree as well: a base the PR names can delete one of
    # the head's files from the merge CI checks out, and retargeting to main brings it back
    # (KIT-410). Here the index has lost a file its commit still holds.
    repo = throwaway_repo({b"docs/leak.md": plain(key), b"plain.md": plain(b"plain\n")})
    genv = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_AUTHOR_NAME": "selftest", "GIT_AUTHOR_EMAIL": "selftest@host.invalid",
            "GIT_COMMITTER_NAME": "selftest", "GIT_COMMITTER_EMAIL": "selftest@host.invalid"}
    tree = subprocess.run(["git", "-C", repo, "write-tree"], check=True, capture_output=True,
                          env=genv).stdout.strip()
    head = subprocess.run(["git", "-C", repo, "commit-tree", tree, "-m", "head"], check=True,
                          capture_output=True, env=genv).stdout.strip().decode("ascii")
    subprocess.run(["git", "-C", repo, "update-index", "--force-remove", "docs/leak.md"],
                   check=True, capture_output=True, env=genv)
    rc_index, _, _ = scan_tracked(repo, tool, config)
    rc_tree, said, found = scan_tracked(repo, tool, config, rev=head)
    check("--tree scans a commit's own files: a key its index no longer holds is found there",
          rc_index == 0 and rc_tree == 1 and [f[0] for f in found] == ["docs/leak.md"],
          "index exit %d, tree exit %d: %s" % (rc_index, rc_tree, said[-3:]))
    rc, said, _ = scan_tracked(repo, tool, config, rev="0" * 40)
    check("…and a commit git cannot read is could-not-scan (exit 2), not clean", rc == 2, said)
    shutil.rmtree(repo, ignore_errors=True)

    # --staged, the pre-commit hook's scan: what the commit will hold, whatever the working
    # tree says (KIT-398). A first commit has no HEAD; git compares with the empty tree.
    repo = throwaway_repo({b"held.md": plain(key), b"name with space's.md": plain(key),
                           b"plain.md": plain(b"plain\n")})
    with open(os.path.join(repo, "held.md"), "wb") as fh:
        fh.write(b"edited out, not staged\n")
    rc, said, found = scan_tracked(repo, tool, config, staged=True)
    check("--staged reads the staged blob, not the working tree, on a first commit",
          rc == 1 and sorted(f[0] for f in found) == ["held.md", "name with space's.md"]
          and said[0] == "secret scan: 2 finding(s) in staged files:", said[:3])
    subprocess.run(["git", "-C", repo, "commit", "-q", "-m", "seed"], check=True,
                   capture_output=True, env=genv)
    subprocess.run(["git", "-C", repo, "update-index", "--force-remove", "held.md"], check=True,
                   capture_output=True, env=genv)
    rc, said, _ = scan_tracked(repo, tool, config, staged=True)
    check("…and a commit that only deletes has nothing to scan, and says so",
          rc == 0 and said == ["secret scan: no secrets in 0 staged files, each read as git "
                               "stores it"], said)
    shutil.rmtree(repo, ignore_errors=True)

    repo = throwaway_repo({b"plain.md": plain(b"plain\n"), b".gitignore": plain(b"*\n"),
                           b"img.png": plain(b"\x89PNG\r\n\x1a\n\x00\x00"),
                           b"conf/.npmrc": plain(b"registry=https://registry.npmjs.org/\n"),
                           b"docs/rules.md": plain(b"A secretlint-disable comment silences "
                                                   b"nothing here.\n")})
    rc, said, _ = scan_tracked(repo, tool, config)
    check("a clean repo exits 0, and says how many files it read",
          rc == 0 and said == ["secret scan: no secrets in 5 tracked files, each read as git "
                               "stores it"], "exit %d: %s" % (rc, said))
    rc, said, _ = scan_tracked(repo, tool, os.path.join(repo, "missing.json"))
    check("a missing config is could-not-scan (exit 2), not clean", rc == 2,
          "exit %d: %s" % (rc, said))
    shutil.rmtree(repo, ignore_errors=True)
    elsewhere = tempfile.mkdtemp(prefix="secret-scan-none-")
    rc, said, _ = scan_tracked(elsewhere, tool, config)
    check("a folder in no repo is could-not-scan (exit 2), not clean", rc == 2,
          "exit %d: %s" % (rc, said))
    shutil.rmtree(elsewhere, ignore_errors=True)

    passed = sum(results)
    print("%d/%d secret-scan checks passed" % (passed, len(results)))
    return 0 if passed == len(results) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    what = parser.add_mutually_exclusive_group(required=True)
    what.add_argument("--scan", action="store_true", help="scan every file git tracks")
    what.add_argument("--selftest", action="store_true")
    what.add_argument("--scanner-packages", metavar="LOCKFILE",
                      help="print the secretlint packages LOCKFILE pins, one name@version a line")
    parser.add_argument("--repo", default=".", help="the repository to scan (default: .)")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--tree", metavar="COMMIT",
                       help="scan the files of COMMIT instead of the index (it must be fetched)")
    scope.add_argument("--staged", action="store_true",
                       help="scan only what the next commit adds or changes (the pre-commit hook)")
    parser.add_argument("--tool", help="the folder whose node_modules holds secretlint "
                                       "(default: this checkout's, else the main checkout's)")
    parser.add_argument("--config", help="secretlint's rules (default: the repo's "
                                         ".secretlintrc.json)")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if args.scanner_packages:
        try:
            print("\n".join(scanner_packages(args.scanner_packages)))
        except (OSError, ValueError, AttributeError) as e:
            print("COULD NOT READ the scanner's packages: %s" % e, file=sys.stderr)
            return 2
        return 0
    code, lines, _ = scan_tracked(args.repo, args.tool or find_tool(),
                                  args.config or os.path.join(args.repo, ".secretlintrc.json"),
                                  args.tree, args.staged)
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
