#!/usr/bin/env python3
"""Placeholder integrity check.

Kit mode (default):   every {{TOKEN}} used in tracked files must be documented in
                      PLACEHOLDERS.md, and vice versa — nothing ships half-filled.
--bootstrapped mode:  asserts ZERO {{TOKEN}}s remain anywhere (run after a project
                      finishes BOOTSTRAP-PROMPT.md Phase 4; PLACEHOLDERS.md is
                      deleted by then).

Token syntax: {{UPPER_SNAKE}} — prose like "{{…}}" deliberately doesn't match.
"""
import os
import re
import subprocess
import sys

TOKEN = re.compile(r"\{\{[A-Z0-9_]+\}\}")


def tracked_files():
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True)
    return out.stdout.split()


def main():
    bootstrapped = "--bootstrapped" in sys.argv
    used = {}  # token -> sorted set of files
    for f in tracked_files():
        if f == "PLACEHOLDERS.md" or f == os.path.relpath(__file__):
            continue
        try:
            text = open(f, encoding="utf-8", errors="ignore").read()
        except (OSError, IsADirectoryError):
            continue
        for tok in set(TOKEN.findall(text)):
            used.setdefault(tok, []).append(f)

    if bootstrapped:
        if used:
            print("FAIL: placeholder tokens remain after bootstrap:")
            for tok, files in sorted(used.items()):
                print(f"  {tok}  ({', '.join(sorted(files))})")
            return 1
        print("OK: no placeholder tokens remain")
        return 0

    if not os.path.exists("PLACEHOLDERS.md"):
        print("FAIL: PLACEHOLDERS.md missing (kit mode requires it; after bootstrap use --bootstrapped)")
        return 1
    documented = set(TOKEN.findall(open("PLACEHOLDERS.md", encoding="utf-8").read()))

    undocumented = {t: fs for t, fs in used.items() if t not in documented}
    unused = documented - set(used)
    if undocumented:
        print("FAIL: tokens used but not documented in PLACEHOLDERS.md:")
        for tok, files in sorted(undocumented.items()):
            print(f"  {tok}  ({', '.join(sorted(files))})")
    if unused:
        print("FAIL: tokens documented in PLACEHOLDERS.md but used nowhere:")
        for tok in sorted(unused):
            print(f"  {tok}")
    if undocumented or unused:
        return 1
    print(f"OK: {len(used)} placeholder tokens, all documented")
    return 0


if __name__ == "__main__":
    sys.exit(main())
