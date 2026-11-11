#!/usr/bin/env python3
"""The watcher's read-only copier: one small job, run as root, that copies a file the workers write into a folder
only root can write (KIT-573).

    python3 -I pipeline_watcher_copy.py run --config etc/copier.json
    python3 -I pipeline_watcher_copy.py --example-config
    python3 -I pipeline_watcher_copy.py --selftest

WHY.  The watcher's `spend` and `runs` monitors read the usage file, and the workers' account writes it. A worker
who can edit that file can blind the monitors that are meant to catch that worker. So root copies the file, and the
watcher reads the copy. A worker can still stop a good file from being written; it can no longer hand the watcher
a bad one, and a copy that is missing, old or refused makes the monitor BLIND, never clear.

WHAT IT DOES.  Reads its rules (a file of root's, in the install folder). For each copy the rules name:
  - opens the source's folder, then the source inside it WITHOUT following a link;
  - checks the thing it opened is a plain file, owned by the one account that is meant to write it, with one
    link, no larger than the cap; anything else is REFUSED, with a reason, and the old copy is REMOVED so a
    reader sees "missing", not stale good figures;
  - writes the bytes to a temp file in the copy folder, root:wheel mode 0644, and renames it over the copy: the
    copy is replaced whole, never edited in place.
It then writes a status file beside the copies: for each copy, copied or refused, why, size, the file's SHA-256.
The watcher checks the status is fresh and that the copy it reads has that hash.

WHAT IT NEVER DOES.  Take a path from anywhere but its rules (there is no argument, no environment variable and no
stdin that names a source). Follow a link in the source's last name. Read a source it was not told. Run a shell,
a network call or another program. Copy into a folder others can write: it checks the copy folder first.
It does not read the content: the watcher judges that.

WHAT IT DOES NOT PROTECT.  A source's folder may itself be a link, since a worker who owns that folder owns the
file in it anyway; the check is that the FILE is the worker account's own. It cannot tell a good figure from a
bad one a worker wrote on purpose: the copy shows what the workers' account says. It does not make the usage
job honest; it only stops a worker from changing what the watcher reads after the fact.

EXIT CODES:  0 every copy made  1 the status could not be written  2 the rules are wrong  4 at least one copy
             was refused (said in the status file)
"""
import argparse
import errno
import hashlib
import json
import os
import pwd
import re
import stat
import sys
import time

RULES_SCHEMA = "pipeline-watcher-copier/1"
STATUS_SCHEMA = "pipeline-watcher-copy-status/1"
DEFAULT_MAX_BYTES = 20 * 1024 * 1024       # the watcher reads no usage file larger than this
HARD_MAX_BYTES = 64 * 1024 * 1024
MAX_COPIES = 8
RULES_MAX_BYTES = 64 * 1024
EX_OK, EX_STATUS, EX_RULES, EX_REFUSED = 0, 1, 2, 4

PATH_LIMIT = 1000      # the system allows 1024; the character set keeps a value safe, not a short limit
PATH_RE = re.compile(r"^/[A-Za-z0-9_./-]{1,%d}\Z" % PATH_LIMIT)
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,60}\Z")
ACCOUNT_RE = re.compile(r"^_?[a-z][a-z0-9_-]{1,30}\Z")
RULE_KEYS = {"schema", "status_file", "copies"}
COPY_KEYS = {"source", "owner", "dest", "max_bytes"}
EXAMPLE = {"schema": RULES_SCHEMA, "status_file": "/usr/local/claw-watcher/copy/copier-status.json",
           "copies": [{"source": "/srv/pipeline/usage/usage.json", "owner": "_workers-account",
                       "dest": "/usr/local/claw-watcher/copy/usage.json", "max_bytes": DEFAULT_MAX_BYTES}]}


def _euid():
    return os.geteuid()


def resolve_uid(name):
    """The numeric id of a local account, or None."""
    try:
        return pwd.getpwnam(name).pw_uid
    except KeyError:
        return None


def _iso(epoch=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if epoch is None else epoch))


def _clean_path(value):
    return (isinstance(value, str) and bool(PATH_RE.match(value)) and not value.endswith("/")
            and not any(part in ("", ".", "..") for part in value.split("/")[1:]))


# --------------------------------------------------------------------------- #
# The rules
# --------------------------------------------------------------------------- #
def _rules_file_problem(path):
    """Why the rules file cannot be trusted, in one plain sentence, or None."""
    try:
        st = os.lstat(path)
    except OSError as exc:
        return "the rules file could not be inspected (%s)" % (exc.strerror or "error")
    if stat.S_ISLNK(st.st_mode):
        return "the rules file is a link"
    if not stat.S_ISREG(st.st_mode):
        return "the rules file is not a plain file"
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return "the rules file is writable by group or other"
    if st.st_uid not in (0, _euid()):
        return "the rules file is owned by another account"
    return None


def validate_rules(raw):
    """(rules, errors): every fault at once."""
    errors = []
    if not isinstance(raw, dict):
        return None, ["the rules must hold one JSON object"]
    extra = sorted(set(raw) - RULE_KEYS)
    if extra:
        errors.append("unknown rule key(s): %s" % ", ".join(str(k) for k in extra))
    if raw.get("schema") != RULES_SCHEMA:
        errors.append("schema must be %s" % RULES_SCHEMA)
    status = raw.get("status_file")
    if not _clean_path(status) or not NAME_RE.match(os.path.basename(str(status))):
        errors.append("status_file must be a clean absolute path")
        status = None
    copy_dir = os.path.dirname(status) if status else None
    copies = raw.get("copies")
    if not isinstance(copies, list) or not 1 <= len(copies) <= MAX_COPIES:
        errors.append("copies must list from 1 to %d copies" % MAX_COPIES)
        copies = []
    out, dests = [], set()
    for n, c in enumerate(copies, 1):
        where = "copy %d" % n
        if not isinstance(c, dict) or set(c) - COPY_KEYS or not {"source", "owner", "dest"} <= set(c):
            errors.append("%s must hold exactly source, owner, dest and optionally max_bytes" % where)
            continue
        ok = True
        if not _clean_path(c["source"]):
            errors.append("%s: source must be a clean absolute path" % where)
            ok = False
        if not isinstance(c["owner"], str) or not ACCOUNT_RE.match(c["owner"]) or c["owner"] == "root":
            errors.append("%s: owner must be a local account name, not root" % where)
            ok = False
        dest = c["dest"]
        if not _clean_path(dest) or not NAME_RE.match(os.path.basename(str(dest))):
            errors.append("%s: dest must be a clean absolute path" % where)
            ok = False
        elif copy_dir and os.path.dirname(dest) != copy_dir:
            errors.append("%s: dest must sit in the same folder as status_file" % where)
            ok = False
        elif dest == status or dest in dests:
            errors.append("%s: dest is used twice, or is the status file" % where)
            ok = False
        if ok and copy_dir and (c["source"] == dest or c["source"].startswith(copy_dir + "/")):
            errors.append("%s: source must not be inside the copy folder" % where)
            ok = False
        cap = c.get("max_bytes", DEFAULT_MAX_BYTES)
        if isinstance(cap, bool) or not isinstance(cap, int) or not 1 <= cap <= HARD_MAX_BYTES:
            errors.append("%s: max_bytes must be a whole number from 1 to %d" % (where, HARD_MAX_BYTES))
            ok = False
        if ok:
            dests.add(dest)
            out.append({"source": c["source"], "owner": c["owner"], "dest": dest, "max_bytes": cap})
    if errors:
        return None, errors
    return {"status_file": status, "copy_dir": copy_dir, "copies": out}, []


def load_rules(path):
    why = _rules_file_problem(path)
    if why:
        return None, [why]
    try:
        with open(path, "rb") as fh:
            raw = fh.read(RULES_MAX_BYTES + 1)
        if len(raw) > RULES_MAX_BYTES:
            return None, ["the rules file is larger than %d bytes" % RULES_MAX_BYTES]
        return validate_rules(json.loads(raw.decode("utf-8")))
    except (OSError, ValueError):
        return None, ["the rules file could not be read as JSON"]


# --------------------------------------------------------------------------- #
# One source
# --------------------------------------------------------------------------- #
def read_source(source, owner, max_bytes):
    """(bytes, None), or (None, a reason in fixed words). The last name is opened WITHOUT following a link,
    inside its folder's own handle, and everything is judged on what was opened, not on the path."""
    uid = resolve_uid(owner)
    if uid is None:
        return None, "the account that is meant to write the source does not exist"
    parent, leaf = os.path.split(source)
    try:
        dfd = os.open(os.path.realpath(parent), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError:
        return None, "the source's folder could not be opened"
    try:
        try:
            fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dfd)
        except FileNotFoundError:
            return None, "the source is missing"
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                return None, "the source is a link"
            return None, "the source could not be opened (%s)" % (exc.strerror or "error")
    finally:
        os.close(dfd)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None, "the source is not a plain file"
        if st.st_uid != uid:
            return None, "the source is not owned by the account that is meant to write it"
        if st.st_nlink != 1:
            return None, "the source has more than one link"
        if st.st_size > max_bytes:
            return None, "the source is larger than its %d-byte cap" % max_bytes
        chunks, total = [], 0
        while True:
            try:
                piece = os.read(fd, 1 << 16)
            except OSError as exc:
                return None, "the source could not be read (%s)" % (exc.strerror or "error")
            if not piece:
                break
            total += len(piece)
            if total > max_bytes:
                return None, "the source is larger than its %d-byte cap" % max_bytes
            chunks.append(piece)
        return b"".join(chunks), None
    finally:
        os.close(fd)


# --------------------------------------------------------------------------- #
# Writing: whole file, then a rename
# --------------------------------------------------------------------------- #
def write_whole(dest, data):
    """root:wheel 0644 (root's own account when it runs as root), written to a temp file in the same folder and
    renamed over `dest`. Never an edit in place: a reader sees the old copy or the new one."""
    tmp = os.path.join(os.path.dirname(dest), ".%s.%d.tmp" % (os.path.basename(dest), os.getpid()))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            os.fchmod(fd, 0o644)
            if _euid() == 0:
                os.fchown(fd, 0, 0)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def folder_problem(path):
    """Why the copy folder is not safe to write into, or None."""
    try:
        st = os.lstat(path)
    except OSError:
        return "the copy folder is missing"
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        return "the copy folder is not a plain folder"
    if st.st_uid != _euid():
        return "the copy folder is not owned by the account that runs the copier"
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return "the copy folder is writable by group or other"
    return None


def remove_old(dest):
    """The old copy goes when its source is refused. True when nothing of it is left."""
    try:
        os.unlink(dest)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def run_rules(rules_path, out=None):
    out = out or sys.stdout
    rules, errors = load_rules(rules_path)
    if errors:
        out.write("copier: the rules are wrong (%s): nothing was copied\n" % "; ".join(errors))
        return EX_RULES
    bad_folder = folder_problem(rules["copy_dir"])
    if bad_folder:
        out.write("copier: %s: nothing was copied, and no status was written\n" % bad_folder)
        return EX_REFUSED
    rows = []
    for c in rules["copies"]:
        data, why = read_source(c["source"], c["owner"], c["max_bytes"])
        row = {"dest": c["dest"], "state": "copied", "reason": None, "bytes": None, "sha256": None, "copied_at": None}
        if why is None:
            try:
                write_whole(c["dest"], data)
                row.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), copied_at=_iso())
            except OSError as exc:
                why = "the copy could not be written (%s)" % (exc.strerror or "error")
        if why is not None:
            row.update(state="refused", reason=why + ("" if remove_old(c["dest"]) else "; the old copy could not be removed"))
        rows.append(row)
    refused = [r for r in rows if r["state"] != "copied"]
    doc = {"schema": STATUS_SCHEMA, "written_at": _iso(), "result": "refused" if refused else "copied", "copies": rows}
    try:
        write_whole(rules["status_file"], (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    except OSError as exc:
        out.write("copier: the status file could not be written (%s)\n" % (exc.strerror or "error"))
        return EX_STATUS
    out.write("copier: %d of %d copied%s\n" % (len(rows) - len(refused), len(rows), "".join(
        "; REFUSED %s: %s" % (os.path.basename(r["dest"]), r["reason"]) for r in refused)))
    return EX_REFUSED if refused else EX_OK


def main(argv=None):
    ap = argparse.ArgumentParser(description="The watcher's read-only copier.")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--example-config", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    runp = sub.add_parser("run")
    runp.add_argument("--config", required=True)
    args = ap.parse_args(argv)
    if args.selftest:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from scratch_run import contained  # noqa: PLC0415 -- the selftest alone needs it; the root job never does
        return contained(selftest, "watcher-copy-selftest-run-")
    if args.example_config:
        print(json.dumps(EXAMPLE, indent=2))
        return EX_OK
    if args.cmd != "run":
        ap.print_usage()
        return EX_RULES
    return run_rules(args.config)


# SELFTEST: nothing below this line is read by the checks that scan the code above it.
def selftest():
    import io
    import shutil
    import tempfile
    failures, total = [], [0]

    def check(name, got, want):
        total[0] += 1
        if got != want:
            failures.append("FAIL %s: got %r, want %r" % (name, str(got)[:300], str(want)[:300]))

    me = os.getuid()
    real = {"resolve_uid": resolve_uid, "euid": _euid}
    with tempfile.TemporaryDirectory() as tmp:
        tmp = os.path.realpath(tmp)
        os.chmod(tmp, 0o700)
        work, copy_dir = os.path.join(tmp, "work"), os.path.join(tmp, "copy")
        os.makedirs(work)
        os.makedirs(copy_dir, mode=0o755)
        os.chmod(copy_dir, 0o755)
        src, decoy = os.path.join(work, "usage.json"), os.path.join(work, "not-told.json")
        dest, status = os.path.join(copy_dir, "usage.json"), os.path.join(copy_dir, "copier-status.json")
        rules_path = os.path.join(tmp, "copier.json")
        good = {"schema": RULES_SCHEMA, "status_file": status,
                "copies": [{"source": src, "owner": "_workers", "dest": dest, "max_bytes": 64}]}

        def put_rules(doc, mode=0o644):
            with open(rules_path, "w", encoding="utf-8") as fh:
                json.dump(doc, fh)
            os.chmod(rules_path, mode)

        def put_src(data=b'{"a": 1}\n', path=src):
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)
            elif os.path.lexists(path):
                os.unlink(path)
            with open(path, "wb") as fh:
                fh.write(data)

        def go():
            buf = io.StringIO()
            rc = run_rules(rules_path, buf)
            doc = None
            try:
                with open(status, encoding="utf-8") as fh:
                    doc = json.load(fh)
            except (OSError, ValueError):
                pass
            return rc, buf.getvalue(), doc

        globals()["resolve_uid"] = lambda name: me if name == "_workers" else None
        put_rules(good)
        put_src()
        put_src(b"{}", decoy)

        # the plain case
        rc, text, doc = go()
        st = os.stat(dest)
        check("a plain file owned by the right account is copied whole, and the status says so",
              (rc, doc["result"], doc["copies"][0]["state"], open(dest, "rb").read(), doc["copies"][0]["bytes"],
               doc["copies"][0]["sha256"] == hashlib.sha256(b'{"a": 1}\n').hexdigest()),
              (EX_OK, "copied", "copied", b'{"a": 1}\n', 9, True))
        check("the copy is 0644 and the status is 0644 (root's own account when run as root)",
              (st.st_mode & 0o7777, os.stat(status).st_mode & 0o7777), (0o644, 0o644))
        check("nothing is left in the copy folder but the copy and its status",
              sorted(os.listdir(copy_dir)), ["copier-status.json", "usage.json"])
        first_inode = st.st_ino
        put_src(b'{"a": 2}\n')
        go()
        check("a new copy is a new file renamed over the old (whole, never edited in place)",
              (os.stat(dest).st_ino != first_inode, open(dest, "rb").read()), (True, b'{"a": 2}\n'))

        # hostile sources: each refused, with a reason, and the old copy is gone
        def refused(label, why, make):
            put_src()
            go()                                             # a good copy exists first
            make()
            rc, text, doc = go()
            check("%s: refused (exit 4), reason in the status, old copy removed" % label,
                  (rc, doc["result"], doc["copies"][0]["state"], why in (doc["copies"][0]["reason"] or ""),
                   os.path.exists(dest), doc["copies"][0]["sha256"]), (EX_REFUSED, "refused", "refused", True, False, None))
            put_src()

        secret = os.path.join(tmp, "elsewhere.json")
        put_src(b"private", secret)

        def make_link():
            os.unlink(src)
            os.symlink(secret, src)
        refused("a source that is a link", "is a link", make_link)

        def make_dir():
            os.unlink(src)
            os.mkdir(src)
        refused("a source that is a folder", "not a plain file", make_dir)

        def make_fifo():
            os.unlink(src)
            os.mkfifo(src)
        refused("a source that is a pipe (and it does not hang)", "not a plain file", make_fifo)
        os.unlink(src)
        put_src()

        def make_hard():
            os.link(src, os.path.join(work, "second-name"))
        refused("a source with a second name", "more than one link", make_hard)
        os.unlink(os.path.join(work, "second-name"))

        globals()["resolve_uid"] = lambda name: me + 1 if name == "_workers" else None
        refused("a file owned by another account", "not owned by the account", lambda: None)
        globals()["resolve_uid"] = lambda name: None
        refused("an account that does not exist", "does not exist", lambda: None)
        globals()["resolve_uid"] = lambda name: me if name == "_workers" else None
        refused("a file that is missing", "is missing", lambda: os.unlink(src))
        refused("a file over the cap", "larger than its 64-byte cap", lambda: put_src(b"x" * 65))
        put_src(b"x" * 64)
        rc, _t, doc = go()
        check("a file exactly at the cap is copied", (rc, doc["copies"][0]["bytes"]), (EX_OK, 64))
        put_src()

        # nothing outside its rules is ever read
        opened = []
        real_open = os.open

        def spy(path, flags, mode=0o777, **k):
            opened.append((str(path), k.get("dir_fd") is not None))
            return real_open(path, flags, mode, **k)
        os.open = spy
        try:
            go()
        finally:
            os.open = real_open
        leafs = sorted(p for p, rel in opened if rel)
        check("only the source it was told is opened inside the source's folder; the not-told file next to it never is",
              (leafs, any(os.path.basename(p) == "not-told.json" for p, _r in opened)), (["usage.json"], False))
        check("the copy folder's writes are the temp files and nothing else",
              sorted(set(os.path.basename(p).split(".")[1] for p, rel in opened if os.path.dirname(p) == copy_dir)),
              ["copier-status", "usage"])
        for argv in (["run", "--config", rules_path, "--source", decoy], ["run", "--config", rules_path, decoy]):
            try:
                with open(os.devnull, "w") as dn:
                    saved = sys.stderr
                    sys.stderr = dn
                    try:
                        main(argv)
                        got = "ran"
                    finally:
                        sys.stderr = saved
            except SystemExit as exc:
                got = "exit %s" % exc.code
            check("the command takes no source argument: %s" % argv[-2], got, "exit 2")

        # the copy folder: group-writable, or not the copier's own
        put_src()
        go()
        os.chmod(copy_dir, 0o775)
        status_before = open(status, "rb").read()
        put_src(b'{"a": 3}\n')
        rc, text, _d = go()
        check("a copy folder others can write is refused: nothing copied, no status written",
              (rc, open(dest, "rb").read(), open(status, "rb").read() == status_before, "writable by group" in text),
              (EX_REFUSED, b'{"a": 1}\n', True, True))
        os.chmod(copy_dir, 0o755)
        globals()["_euid"] = lambda: me + 1
        check("a copy folder owned by another account is refused too",
              "not owned" in (folder_problem(copy_dir) or ""), True)
        globals()["_euid"] = real["euid"]
        shutil.rmtree(copy_dir)
        rc, text, _d = go()
        check("a copy folder that is missing is refused", (rc, "missing" in text), (EX_REFUSED, True))
        os.makedirs(copy_dir, mode=0o755)
        os.chmod(copy_dir, 0o755)
        os.symlink(tmp, copy_dir + "-link")
        put_rules(dict(good, status_file=copy_dir + "-link/copier-status.json",
                       copies=[dict(good["copies"][0], dest=copy_dir + "-link/usage.json")]))
        rc, text, _d = go()
        check("a copy folder that is a link is refused", (rc, "not a plain folder" in text), (EX_REFUSED, True))
        os.unlink(copy_dir + "-link")
        put_rules(good)

        # a write that fails midway leaves no half copy
        put_src()
        go()
        real_replace = os.replace

        def boom(a, b):
            raise OSError(errno.EIO, "disk")
        os.replace = boom
        try:
            put_src(b'{"a": 9}\n')
            rc, text, doc = go()
        finally:
            os.replace = real_replace
        check("a failed rename leaves no temp file and no half copy",
              (rc, [n for n in os.listdir(copy_dir) if n.endswith(".tmp")], os.path.exists(dest)), (EX_STATUS, [], False))
        put_src()
        rc, _t, doc = go()
        check("the next pass copies again", (rc, doc["result"]), (EX_OK, "copied"))

        # the rules: every fault at once
        def bad(label, doc, needle):
            _r, errs = validate_rules(doc)
            check("rules: %s is refused" % label, any(needle in e for e in errs), True)

        def one(**over):
            return dict(good, copies=[dict(good["copies"][0], **over)])
        bad("a relative source", one(source="work/usage.json"), "source")
        bad("a source that climbs", one(source="/a/../etc/x"), "source")
        bad("a source with a double slash", one(source="/a//x"), "source")
        bad("a source in the copy folder", one(source=copy_dir + "/other.json"), "inside the copy folder")
        bad("a dest in another folder", one(dest=work + "/c.json"), "same folder")
        bad("root as the owner", one(owner="root"), "owner")
        bad("a cap of zero", one(max_bytes=0), "max_bytes")
        bad("a boolean cap", one(max_bytes=True), "max_bytes")
        bad("a cap past the hard limit", one(max_bytes=HARD_MAX_BYTES + 1), "max_bytes")
        bad("an unknown copy key", one(extra=1), "exactly")
        check("rules: a deep path, as a deep temp folder gives (over 300 characters), is accepted",
              validate_rules(one(source="/" + "/".join(["d" * 40] * 8) + "/usage.json"))[1], [])
        bad("a path past the system's limit", one(source="/" + "d" * (PATH_LIMIT + 1)), "source")
        bad("an unknown rule key", dict(good, other=1), "unknown rule key")
        bad("a wrong schema", dict(good, schema="x"), "schema")
        bad("no copies", dict(good, copies=[]), "copies")
        bad("the status file as a dest", one(dest=status), "twice")
        bad("two copies to one dest", dict(good, copies=[good["copies"][0], good["copies"][0]]), "twice")
        check("rules: the example is itself valid", validate_rules(EXAMPLE)[1], [])
        put_rules(good, 0o666)
        check("rules writable by others are refused, and nothing is copied",
              (run_rules(rules_path, io.StringIO()), "writable by group" in "; ".join(load_rules(rules_path)[1])), (EX_RULES, True))
        put_rules(good)
        os.unlink(rules_path)
        os.symlink(secret, rules_path)
        check("a rules file that is a link is refused", load_rules(rules_path)[1], ["the rules file is a link"])
        os.unlink(rules_path)

        # no shell, no network, no other program
        with open(os.path.abspath(__file__), encoding="utf-8") as fh:
            above = fh.read().split("\n# SELFTEST: " + "nothing below", 1)[0].split('"""', 2)[2]
        check("no shell, no subprocess, no network, no environment read",
              [b for b in ("shell=True", "os.system", "subprocess", "socket", "urllib", "os.environ", "getenv", "input(")
               if b in above], [])
    globals()["resolve_uid"] = real["resolve_uid"]
    globals()["_euid"] = real["euid"]
    if failures:
        for line in failures:
            print(line)
        print("watcher copier selftest: %d of %d checks failed" % (len(failures), total[0]))
        return 1
    print("OK: watcher copier selftest (%d checks)" % total[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
