#!/usr/bin/env python3
"""Server-side backstop: a PR touching a GRADER path must carry the label.

Grader paths are the machinery that decides whether a change is acceptable —
the hook scripts and settings files (human-only locally, via hook
self-protection), the CI workflows that gate the PR, and, when a project has
opted into the agentic delivery pipeline, `delivery.json` plus everything in its
`autonomy.riskPaths`. Local self-protection constrains only sessions that RUN the
hook; a PR authored anywhere else (another clone, the GitHub web UI, an
Action) never meets it. This job is the layer that does.

The configured set is read from the PR's BASE sha, never from the head: a head
that could shorten its own `riskPaths` would be grading its own homework.

  LABELS      space-separated PR labels
  BASE_SHA    PR base commit
  HEAD_SHA    PR head commit
  REQUIRED_LABEL   default "hooks-change"

Exit 0 = nothing to gate, or the label is present. Exit 1 = label required.
"""
import json
import os
import re
import subprocess
import sys

# Always gated, in every project, pipeline or not. Kept in step with the
# PreToolUse hook's self-protected set and its GRADER_PATH_FLOOR.
FLOOR = (
    ".claude/hooks/**",
    ".claude/settings*.json",
    ".github/workflows/**",
    "delivery.json",
)
DELIVERY_FILE = "delivery.json"


def glob_to_re(pat):
    """git-style glob → regex over a '/'-separated repo-relative path. Mirrors
    `_glob_to_re` in .claude/hooks/pre-tool-use.py — keep the two in step."""
    out, i = [], 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out.append(r"(?:[^/]+/)*"); i += 3
        elif pat.startswith("**", i):
            out.append(r".*"); i += 2
        elif pat[i] == "*":
            out.append(r"[^/]*"); i += 1
        elif pat[i] == "?":
            out.append(r"[^/]"); i += 1
        else:
            out.append(re.escape(pat[i])); i += 1
    return "^" + "".join(out) + "$"


def git(*args):
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def main():
    base = os.environ.get("BASE_SHA", "")
    head = os.environ.get("HEAD_SHA", "")
    label = os.environ.get("REQUIRED_LABEL", "hooks-change")
    if not base or not head:
        print("BASE_SHA/HEAD_SHA not set — nothing to compare.")
        return 0

    diff = git("diff", "--name-only", f"{base}...{head}")
    if diff is None:
        print("::error::could not diff the PR range")
        return 1
    changed = [p.strip() for p in diff.splitlines() if p.strip()]

    globs = list(FLOOR)
    raw = git("show", f"{base}:{DELIVERY_FILE}")
    if raw:
        try:
            for g in (json.loads(raw).get("autonomy") or {}).get("riskPaths") or []:
                if isinstance(g, str) and g.strip() and g.strip() not in globs:
                    globs.append(g.strip())
        except Exception as exc:
            # The pipeline is configured but its config does not parse on the BASE
            # branch. Fail closed: we cannot enumerate what needs a human.
            print(f"::error::{DELIVERY_FILE} on the base branch does not parse ({exc})")
            return 1

    pats = [re.compile(glob_to_re(g)) for g in globs]
    hits = sorted({p for p in changed if any(rx.match(p) for rx in pats)})
    if not hits:
        print("No grader-path files changed — nothing to gate.")
        return 0

    print("This PR modifies grader paths (guard machinery / CI / pipeline config):")
    for h in hits:
        print(f"  {h}")
    print("Gated set: " + ", ".join(globs))
    if label in os.environ.get("LABELS", "").split():
        print(f"OK: '{label}' label present — change acknowledged.")
        return 0
    print(
        f"::error::This PR changes grader paths ({', '.join(hits[:8])}) — the "
        "machinery that decides whether a change is acceptable. Those edits must "
        f"be reviewed, not silent. Add the '{label}' label to acknowledge, then "
        "this check re-runs."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
