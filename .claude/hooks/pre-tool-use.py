#!/usr/bin/env python3
"""
PreToolUse security hook — layer 1 of 3 (see docs/SECURITY.md).
Runs before every Claude Code tool call. Exit 0 = allow. Exit 2 = block
(stdout is shown to Claude + the user as the reason). Unlike git hooks,
the model cannot bypass these — there is no --no-verify equivalent.

Distilled from todoclaw's .claude/hooks/pre-tool-use.py v2 — in production
2026-06-23 → 2026-07-03 across a full build (Stages 0–6). The v2 hardening
(prose-stripping, branch-scoped push guard) shipped post-retro on 2026-07-03.
Every guard here is verified by the block/allow battery in test_hooks.py,
which runs in CI.

BOOTSTRAP ORDER WARNING: settings.json hook wiring hot-loads the moment the
file is written, and a missing hook script BLOCKS EVERY TOOL CALL (python
exits 2 = the block signal — the system fails closed). Create the scripts in
.claude/hooks/ FIRST, write settings.json LAST. (Learned the hard way
building this kit — see docs/LESSONS.md.)

Layout:
  1. UNIVERSAL GUARDS — keep these in every project.
  2. STACK-SPECIFIC GUARDS — Supabase/Postgres examples at the bottom;
     replace them for your datastore, keep the *shape* (protect remote,
     allow local).
"""
import json
import os
import re
import subprocess
import sys


def block(reason: str) -> None:
    print(f"[Security Hook] BLOCKED: {reason}")
    sys.exit(2)


try:
    data = json.load(sys.stdin)
except Exception:
    # Fail-open on malformed input: a broken harness payload must not brick
    # every tool call. The battery asserts this behavior (case "garbage stdin").
    sys.exit(0)

tool = data.get("tool_name", "")
inp = data.get("tool_input", {})


# ════════════════════════════════════════════════════════════════════════════
# 1. UNIVERSAL GUARDS — keep in every project
# ════════════════════════════════════════════════════════════════════════════

# ── Branch guard: no edits or commits while on main ─────────────────────────
# Enforces the feature-branch workflow automatically (see docs/COLLABORATION.md).
# Edit/Write and `git commit` are blocked whenever this repo is on a protected
# branch, so starting new work *forces* a branch first. This is what keeps main
# clean and conflict-free when several people (or agents) share the repo.
PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
PROTECTED_BRANCHES = {"main", "master"}
BRANCH_HELP = (
    "You're on `{branch}` in this repo, where direct edits/commits are "
    "blocked (docs/COLLABORATION.md). Create a feature branch first, then retry:\n"
    "  git checkout -b <type>/<short-kebab-desc>\n"
    "  (type = feat | fix | chore | refactor | docs; e.g. feat/grid-drag)\n"
    "Pull latest main before branching if collaborators are active: "
    "git checkout main && git pull && git checkout -b <type>/<desc>"
)


def _current_branch() -> str:
    try:
        r = subprocess.run(
            ["git", "-C", PROJECT_ROOT, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _in_project(path: str) -> bool:
    if not path:
        return False
    try:
        return (
            os.path.commonpath([os.path.abspath(path), PROJECT_ROOT]) == PROJECT_ROOT
        )
    except Exception:
        return False


if tool in ("Edit", "Write") and _in_project(inp.get("file_path", "")):
    branch = _current_branch()
    if branch in PROTECTED_BRANCHES:
        block(BRANCH_HELP.format(branch=branch))

if tool == "Bash" and re.search(r"\bgit\s+commit\b", inp.get("command", "")):
    branch = _current_branch()
    if branch in PROTECTED_BRANCHES:
        block(BRANCH_HELP.format(branch=branch))


# ── Bash ──────────────────────────────────────────────────────────────────────
def _strip_prose(c: str) -> str:
    """v2 (todoclaw retro 2026-07-03): guards must match OPERATIONS, not PROSE.

    Commit messages and PR titles/bodies passed inline (-m "drop stale rows")
    were false-positiving the destructive-verb patterns below. Strip quoted
    message payloads before scanning; long text via `git commit -F` /
    `--body-file` remains the norm. Message text is inert prose — it is never
    executed — so stripping it loses no protection.
    -[a-z]*m catches combined short flags too (git commit -am / -sm "msg").
    """
    return re.sub(
        r"(-[a-zA-Z]*m|--message|--title|--body|-t|-b)(\s+|=)(\"(?:[^\"\\]|\\.)*\"|'[^']*')",
        r"\1\2''",
        c,
    )


if tool == "Bash":
    cmd = inp.get("command", "")
    scan = _strip_prose(cmd)

    # Block rm -rf / rm -fr / rm --recursive
    if re.search(r"\brm\b[^#\n;&|]*-[a-zA-Z]*r[a-zA-Z]*f", scan) or \
       re.search(r"\brm\b[^#\n;&|]*-[a-zA-Z]*f[a-zA-Z]*r", scan) or \
       re.search(r"\brm\b[^#\n;&|]*--recursive", scan):
        block(
            "rm -rf / rm --recursive detected — use specific paths or ask the user to confirm."
        )

    # Block curl/wget piped directly to a shell
    if re.search(
        r"(curl|wget)\s[^|\n]*\|\s*(bash|sh|zsh|fish|python3?|ruby|perl)", scan
    ):
        block(
            "Piping curl/wget into a shell is a supply-chain risk. "
            "Download first, inspect, then run."
        )

    # Block staging reference dirs or real .env files.
    # `planning/` is this kit's default name for a gitignored reference-material
    # dir (licensed specs, exports, scratch notes). If your project names it
    # differently, update this pattern AND .gitignore AND .husky/pre-commit
    # together — the three layers must agree.
    if re.search(r"\bgit\s+add\b[^#\n;&|]*(planning/|\.env(?!\.example))", scan):
        block(
            "Staging planning/ or .env files is forbidden — "
            "these paths are gitignored to prevent leaks."
        )

    # Push guard v2 (todoclaw retro 2026-07-03): protect main/master from ANY
    # push; elsewhere allow the safe `--force-with-lease` (refuses to clobber
    # unseen remote commits) but block bare `--force`/`-f`. GitHub branch
    # protection is the server-side backstop for anything this heuristic misses.
    _push = re.search(r"\bgit\s+push\b([^#\n;&|]*)", scan)
    if _push:
        _seg = _push.group(1)
        if re.search(r"[\s:](main|master)(?![\w./-])", _seg):
            block("Pushing to main/master is not allowed. Use a feature branch + PR.")
        if re.search(r"(^|\s)--force(?!-with-lease\b)\b", _seg) or re.search(
            r"(^|\s)-f\b", _seg
        ):
            block(
                "Bare --force/-f push is blocked — use `git push --force-with-lease`, "
                "which refuses to overwrite remote commits you haven't seen."
            )

    # Block shell-reading secret files (cat, less, head, etc.).
    # The [^#\n;&|]* gap is scoped per shell command, so a .env named in a
    # LATER command on the same line (e.g. `cat foo; grep x .env`) is not a
    # false positive — while a real `cat .env` still blocks.
    if re.search(
        r"\b(cat|less|head|tail|bat|open|more)\b[^#\n;&|]*(\.env(?!\.example)|\.pem\b|\.key\b)",
        scan,
    ):
        block(
            "Reading secret files (.env, .pem, .key) via shell is not allowed. "
            "Reference by variable name only."
        )


# ── Read ──────────────────────────────────────────────────────────────────────
if tool == "Read":
    path = inp.get("file_path", "")
    basename = os.path.basename(path)

    if re.match(r"^\.env", basename) and not basename.endswith(".example"):
        block(
            f"Reading {basename} is blocked — it may contain real secrets. "
            "Reference env vars by name only."
        )
    if re.search(r"\.(pem|key)$", basename):
        block(f"Reading {basename} is blocked — private key files are off-limits.")


# ── Edit / Write ──────────────────────────────────────────────────────────────
if tool in ("Edit", "Write"):
    path = inp.get("file_path", "")
    basename = os.path.basename(path)

    # Block writing to real .env files
    if re.match(r"^\.env", basename) and not basename.endswith(".example"):
        block(
            f"Writing to {basename} is blocked. "
            "Only .env.example (with placeholder values) is committed."
        )

    # Block embedding secret values in any file content
    content = inp.get("new_string", "") or inp.get("content", "")
    SECRET_PATTERNS = [
        (r"sk-ant-[a-zA-Z0-9\-_]{20,}", "Anthropic API key (sk-ant-…)"),
        (r"(?:supabase|postgres)://[^:@\s]+:[^@\s]{8,}@", "DB connection string with password"),
        (r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----", "Private key block"),
        (r"(?:AKID|AKIA)[A-Z0-9]{16}", "AWS access key"),
        (r"gh[pousr]_[A-Za-z0-9_]{36,}", "GitHub personal access token"),
        (r"eyJ[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,}", "JWT token value"),
    ]
    for pattern, label in SECRET_PATTERNS:
        if re.search(pattern, content):
            block(
                f"Secret value pattern detected in file content ({label}). "
                "Reference secrets by env var name only — never embed values."
            )


# ════════════════════════════════════════════════════════════════════════════
# 2. STACK-SPECIFIC GUARDS — Supabase / Postgres
#    Replace this section for your datastore. Keep the shape: the LOCAL
#    (disposable) instance stays unguarded so routine resets are frictionless;
#    the REMOTE (irreplaceable) instance gets hard blocks on destructive ops.
# ════════════════════════════════════════════════════════════════════════════
if tool == "Bash":
    scan = _strip_prose(inp.get("command", ""))

    # `supabase db reset` wipes the database. Local (Docker) is fine; --linked /
    # --db-url target a REMOTE db and would destroy it.
    if re.search(r"\bsupabase\b[^#\n]*\bdb\s+reset\b", scan) and \
       re.search(r"--linked\b|--db-url\b", scan):
        block(
            "`supabase db reset` against a linked/remote database wipes it. "
            "Only the local (Docker) reset is allowed; change prod via reviewed, "
            "reversible migrations."
        )

    # Deleting a hosted Supabase project is irreversible.
    if re.search(r"\bsupabase\b[^#\n]*\bprojects?\s+delete\b", scan):
        block("`supabase projects delete` is irreversible and is not allowed.")

    # Raw destructive SQL (DROP / TRUNCATE / DELETE) aimed at a NON-localhost
    # Postgres host — e.g. psql against a remote connection string. A postgres
    # URL whose host is not localhost/127.0.0.1 alongside a destructive verb
    # is blocked.
    if re.search(r"\b(drop|truncate|delete)\b", scan, re.IGNORECASE) and re.search(
        r"postgres(?:ql)?://[^\s'\"]*@(?!(?:localhost|127\.0\.0\.1|0\.0\.0\.0))",
        scan,
        re.IGNORECASE,
    ):
        block(
            "Destructive SQL (DROP/TRUNCATE/DELETE) against a remote database is "
            "blocked. Run destructive changes only on the local DB, via migrations."
        )


sys.exit(0)
