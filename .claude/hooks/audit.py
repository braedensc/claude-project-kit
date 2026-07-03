#!/usr/bin/env python3
"""
PostToolUse audit hook.
Appends a one-line record of every Bash/Edit/Write call to .claude/audit.log.
The log is gitignored — local only. Review it to see what Claude did in a
session, especially before a commit.

Distilled from todoclaw's .claude/hooks/audit.py — in production
2026-06-23 → 2026-07-03, unchanged since day one.
"""
import json
import os
import sys
from datetime import datetime, timezone

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

tool = data.get("tool_name", "")
inp = data.get("tool_input", {})

summaries = {
    "Bash": inp.get("command", "")[:200].replace("\n", " "),
    "Edit": f"{inp.get('file_path', '')} — edit",
    "Write": f"{inp.get('file_path', '')} — write",
}

summary = summaries.get(tool)
if not summary:
    sys.exit(0)

hooks_dir = os.path.dirname(os.path.abspath(__file__))
log_path = os.path.join(os.path.dirname(hooks_dir), "audit.log")

ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
try:
    with open(log_path, "a") as f:
        f.write(f"{ts} [{tool}] {summary}\n")
except OSError:
    pass

sys.exit(0)
