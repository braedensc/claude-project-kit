#!/usr/bin/env python3
"""Standard-library-only check: the kit's Python imports nothing it does not ship (KIT-652).

    python3 scripts/check_stdlib_only.py            # check every tracked .py file
    python3 scripts/check_stdlib_only.py --selftest # the battery CI runs

WHY. The kit's jobs and hooks run on a Python with nothing added to it, and a standalone app
will carry such a Python too. A new outside import would pass review and fail on a user's
machine. This reads every tracked .py file with `ast` and lists each import that is neither the
standard library nor one of the kit's own modules (the basename of a tracked .py file: the
scripts import each other as top-level modules). Imports inside functions and `try` blocks
count like any other; relative imports are the kit's own and are skipped.

THE ALLOW LIST below is data: each exception names its file, the module and the reason. An
entry that matches no import fails too, so the list cannot go stale.

THE STANDARD-LIBRARY LIST is one constant, so the answer is the same on Python 3.9 and 3.12
(`sys.stdlib_module_names` exists only from 3.10). The selftest fails when the running Python
knows a module the constant lacks: extend the constant then.

Exit 0 = clean. Exit 1 = an import outside the list, or a stale entry (file, line, module named).
Exit 2 = could not check (no tracked Python files, or git unavailable): never a pass.
"""
import ast
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# (file, module, reason). Every outside import the kit makes today, and why.
ALLOWED = (
    ("scripts/check_alert_pages.py", "yaml", "parses the alert workflow templates as YAML; CI installs PyYAML"),
    ("scripts/check_runner_pin.py", "yaml", "parses workflows as YAML, not by pattern; says COULD NOT CHECK without it"),
    ("scripts/check_workflow_calls.py", "yaml", "parses workflows as YAML; CI installs PyYAML"),
    ("scripts/check_agent_trigger_author.py", "yaml", "parses workflows as YAML; CI installs PyYAML"),
    ("scripts/pipeline_safe_outputs_probe.py", "yaml", "reads a workflow as YAML in the probe; an ImportError is handled"),
    ("scripts/telemetry_scrape.py", "psycopg", "Postgres driver for the optional scrape; psycopg 3 preferred, tried inside try"),
    ("scripts/telemetry_scrape.py", "psycopg2", "Postgres driver fallback to psycopg 3; tried inside try"),
)

# Union of sys.stdlib_module_names for CPython 3.10 to 3.13, plus the modules 3.12 removed
# (asynchat, asyncore, distutils, imp, smtpd).
STDLIB = frozenset("""
    __future__ _abc _aix_support _android_support _apple_support _ast _asyncio _bisect
    _blake2 _bootsubprocess _bz2 _codecs _codecs_cn _codecs_hk _codecs_iso2022
    _codecs_jp _codecs_kr _codecs_tw _collections _collections_abc _colorize
    _compat_pickle _compression _contextvars _crypt _csv _ctypes _curses _curses_panel
    _datetime _dbm _decimal _elementtree _frozen_importlib _frozen_importlib_external
    _functools _gdbm _hashlib _heapq _imp _interpchannels _interpqueues _interpreters
    _io _ios_support _json _locale _lsprof _lzma _markupbase _md5 _msi _multibytecodec
    _multiprocessing _opcode _opcode_metadata _operator _osx_support _overlapped _pickle
    _posixshmem _posixsubprocess _py_abc _pydatetime _pydecimal _pyio _pylong _pyrepl
    _queue _random _scproxy _sha1 _sha2 _sha256 _sha3 _sha512 _signal _sitebuiltins
    _socket _sqlite3 _sre _ssl _stat _statistics _string _strptime _struct _suggestions
    _symtable _sysconfig _thread _threading_local _tkinter _tokenize _tracemalloc
    _typing _uuid _warnings _weakref _weakrefset _winapi _wmi _zoneinfo abc aifc
    antigravity argparse array ast asynchat asyncio asyncore atexit audioop base64 bdb
    binascii binhex bisect builtins bz2 cProfile calendar cgi cgitb chunk cmath cmd code
    codecs codeop collections colorsys compileall concurrent configparser contextlib
    contextvars copy copyreg crypt csv ctypes curses dataclasses datetime dbm decimal
    difflib dis distutils doctest email encodings ensurepip enum errno faulthandler
    fcntl filecmp fileinput fnmatch fractions ftplib functools gc genericpath getopt
    getpass gettext glob graphlib grp gzip hashlib heapq hmac html http idlelib imaplib
    imghdr imp importlib inspect io ipaddress itertools json keyword lib2to3 linecache
    locale logging lzma mailbox mailcap marshal math mimetypes mmap modulefinder msilib
    msvcrt multiprocessing netrc nis nntplib nt ntpath nturl2path numbers opcode
    operator optparse os ossaudiodev pathlib pdb pickle pickletools pipes pkgutil
    platform plistlib poplib posix posixpath pprint profile pstats pty pwd py_compile
    pyclbr pydoc pydoc_data pyexpat queue quopri random re readline reprlib resource
    rlcompleter runpy sched secrets select selectors shelve shlex shutil signal site
    smtpd smtplib sndhdr socket socketserver spwd sqlite3 sre_compile sre_constants
    sre_parse ssl stat statistics string stringprep struct subprocess sunau symtable sys
    sysconfig syslog tabnanny tarfile telnetlib tempfile termios textwrap this threading
    time timeit tkinter token tokenize tomllib trace traceback tracemalloc tty turtle
    turtledemo types typing unicodedata unittest urllib uu uuid venv warnings wave
    weakref webbrowser winreg winsound wsgiref xdrlib xml xmlrpc zipapp zipfile
    zipimport zlib zoneinfo
""".split())


def own_modules(files):
    """The kit's own top-level module names: the basename of each tracked .py file."""
    return {os.path.splitext(os.path.basename(f))[0] for f in files}


def imports(source):
    """[(line, top-level module)] for every absolute import at any depth. Relative imports
    (`from . import x`) are the kit's own and are skipped. Raises SyntaxError on bad source."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, a.name.split(".")[0]) for a in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            found.append((node.lineno, node.module.split(".")[0]))
    return sorted(found)


def judge(sources, allowed=None):
    """(problems, outside) for {relative path: source text}. `outside` is the (file, module)
    pairs that are neither stdlib nor the kit's own; each not on the allow list is a problem,
    and so is each allow-list entry that matched nothing."""
    allowed = ALLOWED if allowed is None else allowed
    own = own_modules(sources)
    problems, matched, outside = [], set(), set()
    for rel in sorted(sources):
        try:
            found = imports(sources[rel])
        except SyntaxError as exc:
            problems.append(f"{rel}:{exc.lineno}: cannot parse ({exc.msg}); this is not a pass")
            continue
        for line, mod in found:
            if mod in STDLIB or mod in own:
                continue
            outside.add((rel, mod))
            if any(rel == f and mod == m for f, m, _ in allowed):
                matched.add((rel, mod))
            else:
                problems.append(f"{rel}:{line}: imports {mod!r}, which is not the standard library "
                                f"or a kit module; remove it or add a reason to ALLOWED")
    for f, m, why in allowed:
        if (f, m) not in matched:
            problems.append(f"ALLOWED entry ({f}, {m}) matches no import: remove it")
        if not why.strip():
            problems.append(f"ALLOWED entry ({f}, {m}) has no reason")
    return problems, sorted(outside)


def tracked_python(root):
    out = subprocess.run(["git", "-C", root, "ls-files", "*.py"], capture_output=True, text=True)
    if out.returncode:
        return None
    return [p for p in out.stdout.splitlines() if p]


def verdict(root):
    """(exit code, lines). No files, or no git, is its own answer, never a pass."""
    try:
        files = tracked_python(root)
    except OSError as exc:
        return 2, [f"COULD NOT CHECK: git did not run ({exc}); this is not a pass"]
    if files is None:
        return 2, ["COULD NOT CHECK: git ls-files failed; this is not a pass"]
    if not files:
        return 2, ["COULD NOT CHECK: no tracked Python files found; this is not a pass"]
    sources = {}
    for rel in files:
        try:
            with open(os.path.join(root, rel), encoding="utf-8") as fh:
                sources[rel] = fh.read()
        except (OSError, UnicodeDecodeError) as exc:
            return 2, [f"COULD NOT CHECK: {rel} unreadable ({exc}); this is not a pass"]
    problems, outside = judge(sources)
    if problems:
        return 1, ["FAIL: " + p for p in problems]
    return 0, [f"OK: {len(files)} Python file(s), {len(outside)} allowed outside import(s), "
                                  "everything else standard library or the kit's own"]


def selftest():
    failures = []

    def expect(cond, msg):
        if not cond:
            failures.append(msg)

    def bad(src, allowed=(), extra=None):
        srcs = {"scripts/a.py": src}
        srcs.update(extra or {})
        return judge(srcs, allowed)[0]

    expect(not bad("import os, sys\nfrom json import loads\nimport os.path\n"), "stdlib flagged")
    expect(not bad("import b\n", extra={"scripts/b.py": ""}), "a kit module flagged")
    expect(not bad("from . import x\nfrom .y import z\n"), "a relative import flagged")
    for src in ("import yaml\n", "import yaml.nodes\n", "from yaml import safe_load\n",
                "def f():\n    import yaml\n", "try:\n    import yaml\nexcept ImportError:\n    pass\n",
                "class C:\n    def m(self):\n        if 1:\n            from psycopg2 import connect\n"):
        p = bad(src)
        expect(len(p) == 1 and "scripts/a.py:" in p[0] and ("yaml" in p[0] or "psycopg2" in p[0]),
               f"{src!r}: want one problem naming file, line and module, got {p}")
    p = bad("x = 1\nimport yaml\n")
    expect(p and "scripts/a.py:2:" in p[0], f"the line is named: {p}")
    ok = (("scripts/a.py", "yaml", "reads workflows"),)
    expect(not bad("import yaml\n", ok), "an allowed import failed")
    expect(len(bad("import yaml\nimport requests\n", ok)) == 1, "an unlisted import beside a listed one")
    expect(len(bad("import os\n", ok)) == 1, "a stale allow-list entry must fail")
    expect(len(bad("import yaml\n", (("scripts/other.py", "yaml", "x"),))) == 2,
           "an entry for another file neither allows nor stays quiet")
    expect(len(bad("import yaml\n", (("scripts/a.py", "yaml", " "),))) == 1, "an entry with no reason")
    expect(bad("def (:\n"), "an unparseable file must fail, not pass")
    # the constant carries what the running Python knows
    names = getattr(sys, "stdlib_module_names", None)
    if names is not None:
        missing = sorted(set(names) - STDLIB)
        expect(not missing, f"STDLIB lacks modules this Python {sys.version_info[:2]} knows: {missing}")
    expect({"os", "ast", "tomllib", "asyncore", "distutils", "sys"} <= STDLIB, "STDLIB lacks a common module")
    # real folders: a git tree is read, an empty one is 'could not check'
    import tempfile
    with tempfile.TemporaryDirectory(prefix="stdlib-only-") as tmp:
        def git(*a):
            return subprocess.run(["git", "-C", tmp, *a], capture_output=True, text=True)
        expect(verdict(tmp)[0] == 2, "a folder that is not a git tree must be 'could not check'")
        git("init", "-q")
        expect(verdict(tmp)[0] == 2, "no tracked Python must be 'could not check'")
        with open(os.path.join(tmp, "a.py"), "w", encoding="utf-8") as fh:
            fh.write("import os\nimport yaml\n")
        git("add", "a.py")
        code, lines = verdict(tmp)
        expect(code == 1 and "a.py:2" in lines[0] and "yaml" in lines[0], f"git tree with outside import: {code} {lines}")
    if failures:
        print("check_stdlib_only selftest: FAIL")
        for f in failures:
            print("  -", f)
        return 1
    print("check_stdlib_only selftest: OK")
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--selftest" in argv:
        sys.path.insert(0, HERE)
        from scratch_run import contained   # its temp folders go when it ends
        return contained(selftest, "stdlib-only-selftest-run-")
    code, lines = verdict(ROOT)
    for line in lines:
        print(line, file=sys.stderr if code == 2 else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
