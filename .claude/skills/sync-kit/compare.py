#!/usr/bin/env python3
"""Classify a project's divergence from the kit it was instantiated from.

A GitHub template shares no git history with the repos made from it, so there is
no merge base to diff against. This script manufactures one: the KIT checkout has
its full history even though the project does not, so for any shared path we can
ask "is the project's copy byte-identical to some *historical* kit revision of
this file?" If yes, the project holds an unmodified older kit file and is simply
behind. If no, the project edited it, and the only question left is whether the
kit moved too.

Three buckets, and the asymmetry between them is the whole design:

  1 UPSTREAM-NEWER      — candidates for a PR. REQUIRES PROOF (see below).
  2 PROJECT-SPECIFIC    — the project's own guards, stack fences, allowlist
                          entries, app code. NEVER drift. The default.
  3 DRIFTED             — started shared, since changed on BOTH sides. Reported
                          for human judgement; never auto-resolved.

**Bucket 1 requires proof; 2 and 3 are the defaults.** A path lands in bucket 1
only when (a) it is absent from the project entirely and is not kit-only
scaffolding or an unadopted template, or (b) the project's bytes match a historical
kit blob exactly, proving the project never touched it. Anything else — any local
edit, any missing history, any ambiguity — falls to 2 or 3. That asymmetry is
deliberate: misfiling an intentional divergence as "upstream-newer" proposes
overwriting a working project's own decisions, which is the one failure mode that
does real damage. Misfiling the other way just means a human ports something by
hand, which is the status quo anyway.

Read-only. Never writes to the project or the kit; applying anything is the
caller's job (see SKILL.md), and hook files are report-only everywhere.

Usage:
  compare.py --kit <path-to-kit-checkout> [--project <path>] [--json]
  compare.py --selftest
"""
import argparse
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys

# ── The synced surface ───────────────────────────────────────────────────────
# (prefix, kind, action-tier). Everything outside these prefixes is the project's
# own business and is never even looked at.
SURFACES = (
    (".claude/hooks/",       "hook",     "report-only"),
    (".claude/skills/",      "skill",    "informational"),
    (".github/workflows/",   "workflow", "copyable-flagged"),
    ("templates/workflows/", "template", "copyable"),
    ("scripts/",             "script",   "copyable"),
    ("docs/",                "doc",      "copyable"),
)

# Always the project's own, whatever the diff says. ADRs record *this project's*
# decisions and legitimately use a different numbering convention; examples and
# caches are noise.
ALWAYS_INTENTIONAL = (
    "docs/adr/*", "docs/adr/**", "docs/examples/*", "docs/examples/**",
    "**/__pycache__/**", "*.pyc",
)

# Upstream files that are the TEMPLATE's own scaffolding. They exist in the kit
# by definition and their absence downstream is correct, not a gap.
KIT_ONLY = (
    "scripts/check_placeholders.py",
    "docs/CLAUDE-template.md",
)

# Machinery for the OPTIONAL agentic delivery pipeline. `delivery.json` at the
# project root is the kit's single discriminator for whether a project opted in
# (docs/PIPELINE-CONTRACT.md §2); without one these are an offer, not a gap.
PIPELINE_SCOPED = (
    "docs/PIPELINE-CONTRACT.md", "docs/TICKET-TEMPLATE.md",
    "scripts/check_delivery_config.py", "scripts/check_ticket_dor.py",
    "scripts/check_grader_paths.py",
    "templates/workflows/pipeline-*.yml",
    ".claude/skills/work/*", ".claude/skills/setup-board/*",
)

# Only top-level process docs sync. docs/<subdir>/ is project territory.
DOC_RE = re.compile(r"^docs/[^/]+\.md$")

MAX_REVS = 40  # history depth searched per path


def sh(repo, *args):
    r = subprocess.run(("git", "-C", repo) + args, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def blob_sha(data: bytes) -> str:
    """git's own blob id, computed without needing the project to be a git repo."""
    h = hashlib.sha1()
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


def read(path):
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def in_surface(rel):
    for prefix, kind, tier in SURFACES:
        if rel.startswith(prefix):
            if kind == "doc" and not DOC_RE.match(rel):
                return None
            return kind, tier
    return None


def matches(rel, globs):
    return any(fnmatch.fnmatch(rel, g) for g in globs)


def walk(root):
    """Every file under root that falls in a synced surface, repo-relative."""
    out = set()
    for prefix, _kind, _tier in SURFACES:
        base = os.path.join(root, prefix)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__")]
            for fn in filenames:
                rel = os.path.relpath(os.path.join(dirpath, fn), root)
                if in_surface(rel) and not matches(rel, ALWAYS_INTENTIONAL):
                    out.add(rel)
    return out


def history(kit, rel):
    """Every blob this path has ever had upstream, newest first: [(sha, commit)]."""
    log = sh(kit, "log", "--all", "--format=%H", "--", rel)
    if log is None:
        return None  # not a git repo — the ancestry test is unavailable
    revs = log.split()[:MAX_REVS]
    if not revs:
        return []
    query = "\n".join(f"{r}:{rel}" for r in revs) + "\n"
    r = subprocess.run(
        ("git", "-C", kit, "cat-file", "--batch-check=%(objectname) %(objecttype)"),
        input=query, capture_output=True, text=True,
    )
    pairs = []
    for rev, line in zip(revs, r.stdout.splitlines()):
        parts = line.split()
        if len(parts) == 2 and parts[1] == "blob":
            pairs.append((parts[0], rev))
    return pairs


def distance(kit, sha, project_bytes):
    """Cheap divergence measure: lines present in one side and not the other."""
    raw = subprocess.run(("git", "-C", kit, "cat-file", "blob", sha),
                         capture_output=True)
    a = raw.stdout.decode("utf-8", "replace").splitlines()
    b = project_bytes.decode("utf-8", "replace").splitlines()
    from collections import Counter
    ca, cb = Counter(a), Counter(b)
    return sum(((ca - cb) + (cb - ca)).values())


def kit_state(kit, want_ref):
    """What the kit checkout actually has checked out.

    The ancestry test reads all of history, but the bucket-1 inventory reads the
    kit's WORKING TREE — so a checkout sitting on a stale branch silently
    under-reports what is available upstream. Surface it rather than trusting it.
    """
    if not sh(kit, "rev-parse", "--git-dir"):
        return {"git": False, "warnings": [
            "the kit path is not a git checkout, so NOTHING can be proven "
            "upstream-newer; point --kit at a full clone"]}
    ref = (sh(kit, "rev-parse", "--abbrev-ref", "HEAD") or "?").strip()
    head = (sh(kit, "rev-parse", "--short", "HEAD") or "?").strip()
    dirty = bool((sh(kit, "status", "--porcelain") or "").strip())
    warnings = []
    if ref != want_ref:
        warnings.append(f"kit checkout is on '{ref}', not '{want_ref}' — you are "
                        f"comparing against whatever that branch happens to hold")
    if dirty:
        warnings.append("kit checkout has uncommitted changes — its working tree "
                        "is not a released kit state")
    behind = (sh(kit, "rev-list", "--count", f"HEAD..origin/{want_ref}") or "").strip()
    if behind.isdigit() and int(behind) > 0:
        warnings.append(f"kit checkout is {behind} commit(s) behind "
                        f"origin/{want_ref} — fetch and update before trusting "
                        f"bucket 1")
    return {"git": True, "ref": ref, "head": head, "dirty": dirty,
            "warnings": warnings}


def classify(kit, project, config):
    extra_intentional = tuple(config.get("intentional", ()))
    kit_files, proj_files = walk(kit), walk(project)
    has_templates = os.path.isdir(os.path.join(project, "templates"))
    has_pipeline = os.path.exists(os.path.join(project, "delivery.json"))
    rows = []

    for rel in sorted(kit_files | proj_files):
        kind, tier = in_surface(rel)
        kb = read(os.path.join(kit, rel))
        pb = read(os.path.join(project, rel))

        def row(bucket, status, note="", **kw):
            entry = dict(path=rel, kind=kind, tier=tier, bucket=bucket,
                         status=status, note=note)
            entry.update(kw)  # callers may downgrade the action tier
            rows.append(entry)

        if matches(rel, extra_intentional):
            row(2, "configured", "listed under `intentional` in the sync config")
        elif pb is None and rel in KIT_ONLY:
            row(0, "kit-only", "template scaffolding — correct to be absent")
        elif pb is None and kind == "template" and not has_templates:
            # The project activated (or declined) this template at bootstrap and
            # dropped templates/. It may well be live under another filename.
            row(2, "unadopted", "template not adopted; may exist renamed under "
                                ".github/workflows/ — not matched by name")
        elif pb is None and kind == "skill":
            # Skills do not need per-repo syncing at all — see SKILL.md. Report
            # the gap, but point at the user-level directory, not a copy.
            row(1, "new-upstream", "absent — but skills belong in the user-level "
                                   "~/.claude/skills/, where one copy serves every "
                                   "project; prefer that over vendoring it here",
                tier="informational")
        elif pb is None and matches(rel, PIPELINE_SCOPED) and not has_pipeline:
            row(1, "new-upstream", "absent — pipeline machinery, and this project "
                                   "has no delivery.json, so it is an offer, not a "
                                   "gap; adopt only when opting into the pipeline",
                tier="informational")
        elif pb is None:
            row(1, "new-upstream", "absent from the project")
        elif kb is None:
            row(2, "project-only", "the project's own file")
        elif blob_sha(kb) == blob_sha(pb):
            row(0, "in-sync")
        else:
            hist = history(kit, rel)
            if hist is None:
                row(2, "unclassified", "kit checkout has no git history — the "
                                       "ancestry test is unavailable; assuming "
                                       "intentional")
                continue
            psha, head = blob_sha(pb), blob_sha(kb)
            match = next((c for s, c in hist if s == psha), None)
            if match:
                # hist is newest-first, so the position of the project's blob is
                # exactly how many upstream revisions landed after it.
                behind = [s for s, _ in hist].index(psha)
                row(1, "behind-upstream",
                    f"project bytes == kit {match[:8]}; never edited locally "
                    f"({behind} upstream revision(s) since)")
            else:
                near = min(hist, key=lambda sc: distance(kit, sc[0], pb))
                if near[0] == head:
                    row(2, "local-edits",
                        "kit has not changed this file since the project's base — "
                        "the divergence is the project's own")
                else:
                    row(3, "drifted",
                        f"nearest upstream base {near[1][:8]} is behind kit HEAD — "
                        f"both sides moved")
    return rows


# ── Reporting ────────────────────────────────────────────────────────────────
# How a bucket-1 row may be acted on. Absent from this map = plainly copyable.
TIER_FLAG = {
    "report-only":      "REPORT-ONLY — hand the human a terminal command",
    "informational":    "FYI — do not copy",
    "copyable-flagged": "copyable, but it RUNS in CI — review",
}

BUCKETS = {
    0: "IN SYNC / NOT APPLICABLE",
    1: "1 · UPSTREAM-NEWER — candidates for a PR",
    2: "2 · PROJECT-SPECIFIC — intentional, never drift",
    3: "3 · DRIFTED — both sides changed; human judgement only",
}


def render(rows):
    out = []
    for b in (1, 3, 2, 0):
        group = [r for r in rows if r["bucket"] == b]
        if not group:
            continue
        if b == 0:
            counts = {}
            for r in group:
                counts[r["status"]] = counts.get(r["status"], 0) + 1
            out.append(f"\n{BUCKETS[b]}: " +
                       ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
            continue
        out.append(f"\n{BUCKETS[b]}  ({len(group)})")
        for r in group:
            flag = f"  [{TIER_FLAG[r['tier']]}]" if b == 1 and r["tier"] in TIER_FLAG else ""
            out.append(f"  {r['status']:<16} {r['path']}{flag}")
            if r["note"]:
                out.append(f"  {'':<16}   ↳ {r['note']}")
    return "\n".join(out) + "\n"


# ── Selftest ─────────────────────────────────────────────────────────────────
def _selftest():
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(prefix="sync-kit-selftest-")
    kit, proj = os.path.join(tmp, "kit"), os.path.join(tmp, "proj")

    def wf(root, rel, text):
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(text)

    def commit(msg):
        subprocess.run(("git", "-C", kit, "add", "-A"), check=True,
                       capture_output=True)
        subprocess.run(("git", "-C", kit, "-c", "user.email=dev@example.com",
                        "-c", "user.name=selftest", "commit", "-m", msg),
                       check=True, capture_output=True)

    os.makedirs(kit)
    subprocess.run(("git", "-C", kit, "init", "-q"), check=True)
    # v1 of everything
    wf(kit, "scripts/shared.py", "one\ntwo\n")
    wf(kit, "docs/TESTING.md", "old testing doc\n")
    wf(kit, "scripts/stable.py", "never changes\n")
    wf(kit, "templates/workflows/backup-cron.yml", "on: schedule\n")
    commit("v1")
    # v2 — the kit moves on
    wf(kit, "docs/TESTING.md", "new testing doc\nwith more\n")
    wf(kit, "scripts/shared.py", "one\ntwo\nthree-upstream\n")
    wf(kit, "scripts/brand-new.py", "added upstream\n")
    commit("v2")

    os.makedirs(proj)
    # bucket 1, behind: verbatim v1 of a file the kit has since changed
    wf(proj, "docs/TESTING.md", "old testing doc\n")
    # bucket 3, drifted: edited locally AND changed upstream
    wf(proj, "scripts/shared.py", "one\ntwo\nthree-project\n")
    # bucket 2, local edits to a file the kit never touched
    wf(proj, "scripts/stable.py", "never changes\nplus a project guard\n")
    # bucket 2, project-only
    wf(proj, "scripts/only-here.mjs", "project code\n")
    # bucket 1, new upstream: scripts/brand-new.py simply absent
    # bucket 2, unadopted: no templates/ dir downstream

    got = {r["path"]: (r["bucket"], r["status"])
           for r in classify(kit, proj, {})}
    expect = {
        "docs/TESTING.md":                    (1, "behind-upstream"),
        "scripts/brand-new.py":               (1, "new-upstream"),
        "scripts/shared.py":                  (3, "drifted"),
        "scripts/stable.py":                  (2, "local-edits"),
        "scripts/only-here.mjs":              (2, "project-only"),
        "templates/workflows/backup-cron.yml": (2, "unadopted"),
    }
    fails = [f"  {p}: expected {v}, got {got.get(p)}"
             for p, v in expect.items() if got.get(p) != v]

    # A configured intentional glob must win over everything else.
    cfg = {"intentional": ["docs/TESTING.md"]}
    got2 = {r["path"]: (r["bucket"], r["status"])
            for r in classify(kit, proj, cfg)}
    if got2.get("docs/TESTING.md") != (2, "configured"):
        fails.append(f"  config override: got {got2.get('docs/TESTING.md')}")

    # No history (kit is a plain directory) must fail toward intentional.
    plain = os.path.join(tmp, "plainkit")
    shutil.copytree(kit, plain, ignore=shutil.ignore_patterns(".git"))
    got3 = {r["path"]: (r["bucket"], r["status"]) for r in classify(plain, proj, {})}
    if got3.get("docs/TESTING.md") != (2, "unclassified"):
        fails.append(f"  no-history degrade: got {got3.get('docs/TESTING.md')}")

    shutil.rmtree(tmp, ignore_errors=True)
    if fails:
        print("FAIL: sync-kit classifier\n" + "\n".join(fails))
        return 1
    print(f"OK: sync-kit classifier ({len(expect) + 2} cases)")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--kit", help="path to a kit checkout (a full clone, so the "
                                  "ancestry test has history to work with)")
    ap.add_argument("--project", default=".", help="path to the instantiated project")
    ap.add_argument("--config", help="path to a sync config (default: "
                                     "<project>/.claude/kit-sync.json, if present)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()
    if not args.kit:
        ap.error("--kit is required (or use --selftest)")

    project = os.path.abspath(args.project)
    top = sh(project, "rev-parse", "--show-toplevel")
    if top:
        project = top.strip()
    kit = os.path.abspath(args.kit)
    if not os.path.isdir(kit):
        print(f"FAIL: no kit checkout at {kit}", file=sys.stderr)
        return 2

    cfg_path = args.config or os.path.join(project, ".claude", "kit-sync.json")
    config = {}
    if os.path.exists(cfg_path):
        try:
            config = json.load(open(cfg_path, encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"FAIL: unreadable sync config {cfg_path}: {exc}", file=sys.stderr)
            return 2

    state = kit_state(kit, config.get("ref", "main"))
    rows = classify(kit, project, config)
    if args.json:
        print(json.dumps({"kit": kit, "project": project, "kitState": state,
                          "rows": rows}, indent=2))
    else:
        where = f" @ {state['ref']} {state['head']}" if state.get("git") else ""
        print(f"kit:     {kit}{where}")
        print(f"project: {project}")
        for w in state["warnings"]:
            print(f"WARNING: {w}")
        print(render(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
