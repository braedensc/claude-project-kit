#!/usr/bin/env python3
"""The session writer's key (KIT-933): where it comes from, how the runner keeps it working, and what
the runner says about it. Guide: docs/RUNNER.md, "The writer's key".

    python3 scripts/pipeline_writer_key.py --selftest

THREE SOURCES.  `session_tools.key_source` in the runner's settings, from WRITER_KEY_SOURCE in runner.conf:
  file           A person writes the key file with `pipeline_runner_setup.py writer-key`, and its end date
                 goes into the health watch's CREDENTIAL_EXPIRIES. No key_source at all means this one.
  other-program  Another program on this account keeps the key in its own JSON settings file, and renews
                 it there itself. The runner reads it from that file each time it needs it, by the file's
                 path and the name of the JSON key that holds it, at whatever depth. It only reads: it never
                 renews, so the two never clash. A renewal token works once, and the second program to use
                 it would be refused.
  renew          The runner renews the key itself, before it ends, through Linear's OAuth token endpoint
                 (POST https://api.linear.app/oauth/token, grant_type=refresh_token, a form body). It holds
                 the renewal token and the app's client details in files of this account's, mode 0600.
                 Linear's answer holds a new key, a new renewal token and the key's life in seconds. The new
                 renewal token is written first, then the key: Linear rotates the renewal token on each
                 renewal, so the chain is never left with a used one. The first renewal may take the
                 renewal token from the other program's settings file (adopt_file, adopt_field), once that
                 program is stopped for good, so no person copies a key across.

WHAT THE RUNNER WRITES.  keep(), on each of the runner's passes: a renewal when one is due, and every 30
minutes the key is asked who it is (Linear's `viewer`). Then <store>/writer-key.json
(pipeline-writer-key/1): the source, the state and why, when it last answered as the session writer, the
display name it answered with, and for a renewed key when it was renewed and when it ends. Never the key.
The runner's `check`, the live tool and the health watch's `writer-key` kind read it, and judge() is the one
place each takes its words from.

Nothing here prints, logs or raises the text of a key, a renewal token or a client secret. An error names a
file and a JSON key name, never what a file holds.
"""
import calendar
import errno
import json
import os
import re
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pipeline_tech_lead_tools as tl  # noqa: E402  (the key's one shape and its file's one reader)
import pipeline_runner_store as store  # noqa: E402  (the one way a file is written whole)
from pipeline_bridge_transport import _RefuseRedirect  # noqa: E402  (a redirect would carry the secret on)

SOURCES = ("file", "other-program", "renew")
SOURCE_KEYS = {"file": ("kind",), "other-program": ("kind", "file", "field"),
               "renew": ("kind", "renewal_file", "client_file", "adopt_file", "adopt_field")}
STATUS_SCHEMA = "pipeline-writer-key/1"
STATUS_FILE = "writer-key.json"
STATUS_KEYS = ("schema", "at", "source", "writer_user_id", "state", "reason", "detail", "check",
               "answered_at", "writer_name", "renewal", "renewed_at", "ends_at")
TRACKER_API = tl.TRACKER_API
TOKEN_URL = "https://api.linear.app/oauth/token"
USER_AGENT = "pipeline-runner-writer-key/1"
Q_VIEWER = "query WriterKeyViewer { viewer { id displayName } }"

SETTINGS_MAX = 1 << 20        # another program's settings file: read whole, never larger than this
SECRET_MAX = 4096             # a renewal token's or the client details' file
ANSWER_MAX = 1 << 16          # Linear's answer to one renewal or one `viewer`
DETAIL_MAX = 300
FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}\Z")
RENEWAL_RE = re.compile(r"^[A-Za-z0-9._~+/=-]{10,1000}\Z")
CLIENT_RE = re.compile(r"^([A-Za-z0-9._~+/=-]{8,500}):([A-Za-z0-9._~+/=-]{8,500})\Z")
NAME_RE = re.compile(r"^[^\x00-\x1f\x7f-\x9f]{1,80}\Z")
TIME_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")

# The clock, in seconds. Linear's keys live 24 hours, and a renewal token 30 days from its issue.
CHECK_EVERY = 30 * 60         # how often the key is asked who it is, while it answers
RETRY_EVERY = 5 * 60          # after a failure: inside Linear's 30-minute window for a lost answer
RENEW_BEFORE = 12 * 3600      # a renewed key is renewed this long before its end
WARN_BEFORE = 6 * 3600        # ...and said to be failing once its end is this close
ANSWER_STALE = 3 * 3600       # no answer as the session writer for this long is said
QUIET_AFTER = 3 * 3600        # the runner has not written its status for this long: it may be stopped
EXPIRES_MIN, EXPIRES_MAX = 300, 400 * 86400

CHECKS = ("writer", "other-user", "refused", "unreadable", "unreachable")
RENEWALS = ("renewed", "renewal-refused", "client-refused", "no-renewal-token", "no-client", "renewal-failed")
STATES = ("ok", "failed", "unknown")
# Why, in fixed words: the health watch says these and nothing a file wrote (a reason is a code).
REASONS = {
    "unreadable": "the key could not be read from where it is kept",
    "other-user": "the key answers as another user than the session writer",
    "refused": "the tracker refused the key: it has ended, or was revoked",
    "unreachable": "the tracker could not be reached to ask the key who it is",
    "not-checked": "the key has not been asked who it is yet",
    "renewal-refused": ("Linear refused the renewal token: it ended, was revoked, or another program used it "
                        "first. A person gives the runner a new one (`writer-renewal`)"),
    "client-refused": "Linear refused the app's client details. A person puts in the current ones (`writer-client`)",
    "no-renewal-token": "there is no renewal token to renew it with (`writer-renewal`, or adopt_file)",
    "no-client": "the app's client details could not be read (`writer-client`)",
    "renewal-failed": "the renewal did not complete: Linear could not be reached, or its answer held no usable key",
}
# Linear's OAuth error codes (RFC 6749 section 5.2), as the renewal's result.
OAUTH_ERRORS = {"invalid_grant": "renewal-refused", "invalid_client": "client-refused",
                "unauthorized_client": "client-refused"}

_OPENER = urllib.request.build_opener(_RefuseRedirect)


class WriterKeyError(tl.ToolError):
    """The writer's key could not be read, in fixed words: a file and a key name, never what it holds. A
    ToolError, so every reader that already catches the key file's errors catches these."""


class Absent(WriterKeyError):
    """The file is not there."""


class Refused(Exception):
    """The tracker refused the key."""


class Unreachable(Exception):
    """The tracker could not be asked."""


class TokenRefused(Exception):
    """Linear's token endpoint said no. `result` is one of RENEWALS."""

    def __init__(self, result):
        Exception.__init__(self, result)
        self.result = result


def iso(seconds):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))


def epoch(stamp):
    if not (isinstance(stamp, str) and TIME_RE.match(stamp)):
        return None
    try:
        return float(calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")))
    except (ValueError, OverflowError):
        return None


def _resolve(path):
    """`~/` is this account's home from the account database, never $HOME (the runner's rule)."""
    import pwd
    if isinstance(path, str) and (path == "~" or path.startswith("~/")):
        return os.path.join(pwd.getpwuid(os.getuid()).pw_dir, path[2:])
    return path


def _inside(a, b):
    a, b = os.path.realpath(a), os.path.realpath(b)
    return a == b or a.startswith(b.rstrip(os.sep) + os.sep)


# ════════════════════════════════════════════════════════════════════ the setting

def source_setting(raw, key_file, folders=()):
    """(source, problems): `session_tools.key_source`, judged. Absent is {"kind": "file"}. `key_file` is
    the session writer's key file; `folders` are where a session can write, and no file here is inside one."""
    if raw is None:
        return {"kind": "file"}, []
    if not isinstance(raw, dict) or raw.get("kind") not in SOURCES:
        return None, ["session_tools.key_source: must be an object whose kind is file, other-program or renew"]
    kind = raw["kind"]
    bad = ["session_tools.key_source.%s: not a setting of the %s source" % (repr(k)[:40], kind)
           for k in sorted(raw, key=repr) if k not in SOURCE_KEYS[kind]]
    out = {"kind": kind}
    paths = [("key_file", key_file)] if isinstance(key_file, str) else []
    for k in SOURCE_KEYS[kind][1:]:
        v = raw.get(k)
        if k.endswith("_field") or k == "field":
            if v is None and k == "adopt_field":
                out[k] = None
            elif not (isinstance(v, str) and FIELD_RE.match(v)):
                bad.append("session_tools.key_source.%s: must be the name of a JSON key" % k)
            else:
                out[k] = v
            continue
        v = _resolve(v)
        if v is None and k == "adopt_file":
            out[k] = None
            continue
        if not (isinstance(v, str) and os.path.isabs(v) and "\0" not in v):
            bad.append("session_tools.key_source.%s: must be an absolute path (~/ is this account's home)" % k)
            continue
        if any(isinstance(f, str) and os.path.isabs(f) and _inside(v, f) for f in folders):
            bad.append("session_tools.key_source.%s: is inside a folder where a session can write" % k)
            continue
        out[k] = v
        paths.append((k, v))
    if kind == "renew" and (out.get("adopt_file") is None) != (out.get("adopt_field") is None) \
            and not any("adopt_" in b for b in bad):
        bad.append("session_tools.key_source: adopt_file and adopt_field go together, or neither")
    seen = {}
    for k, v in paths:
        real = os.path.realpath(v)
        if real in seen:
            bad.append("session_tools.key_source.%s: is the same file as %s: each holds one thing" % (k, seen[real]))
        seen.setdefault(real, k)
    return (None, bad) if bad else (out, [])


def source_of(st):
    return (st or {}).get("key_source") or {"kind": "file"}


def files(st):
    """Every file that holds a part of the writer's key, as written and as resolved: the session's walls."""
    if not st:
        return []
    src = source_of(st)
    out = [st["key_file"]] + [src[k] for k in ("file", "renewal_file", "client_file", "adopt_file") if src.get(k)]
    for p in list(out):
        real = os.path.realpath(p)
        if real not in out:
            out.append(real)
    return out


def where(st):
    """Where the key comes from, in words."""
    src = source_of(st)
    if src["kind"] == "other-program":
        return "read from another program's settings file %s (its key %s) each time it is needed; that program " \
               "renews it, and the runner never does" % (src["file"], src["field"])
    if src["kind"] == "renew":
        return "renewed by the runner itself, in %s, with the renewal token in %s" % (st["key_file"], src["renewal_file"])
    return "written by a person into %s (`writer-key`)" % st["key_file"]


def key_at(st):
    """(path, JSON key or None): where a reader finds the key now. The session tools' run file carries it."""
    src = source_of(st)
    if src["kind"] == "other-program":
        return src["file"], src["field"]
    return st["key_file"], None


# ════════════════════════════════════════════════════════════════════ reading

def _open_own(path, strict, max_bytes, noun):
    """The bytes of `path`, opened once, never through a link, and judged on that descriptor: a regular file
    of this account's. `strict`: no access at all for group or others (the runner's own files). Not strict
    (another program's): group and others may read it, as that program chose, but never write it."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            raise Absent("%s %s is not there" % (noun, path))
        if exc.errno == errno.ELOOP:
            raise WriterKeyError("%s %s is a link, which is never read" % (noun, path))
        raise WriterKeyError("%s %s could not be read (%s)" % (noun, path, type(exc).__name__))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise WriterKeyError("%s %s is not a regular file" % (noun, path))
        if st.st_uid != os.getuid():
            raise WriterKeyError("%s %s belongs to another account" % (noun, path))
        if strict and st.st_mode & 0o077:
            raise WriterKeyError("%s %s can be read by others: it must be mode 0600" % (noun, path))
        if st.st_mode & 0o022:
            raise WriterKeyError("%s %s can be changed by others, so nothing in it is trusted" % (noun, path))
        if st.st_size > max_bytes:
            raise WriterKeyError("%s %s is larger than %d bytes" % (noun, path, max_bytes))
        chunks, total = [], 0
        while True:
            got = os.read(fd, 65536)
            if not got:
                break
            total += len(got)
            if total > max_bytes:
                raise WriterKeyError("%s %s is larger than %d bytes" % (noun, path, max_bytes))
            chunks.append(got)
        return b"".join(chunks)
    except OSError as exc:
        raise WriterKeyError("%s %s could not be read (%s)" % (noun, path, type(exc).__name__))
    finally:
        os.close(fd)


def read_secret(path, pattern, noun):
    """One value from a file of the runner's own (mode 0600), judged by `pattern`. Absent when not there."""
    try:
        value = _open_own(path, True, SECRET_MAX, noun).decode("utf-8").strip()
    except UnicodeDecodeError:
        raise WriterKeyError("%s %s is not text" % (noun, path))
    if not pattern.match(value):
        raise WriterKeyError("%s %s does not hold what it should" % (noun, path))
    return value


def read_field(path, field, pattern=tl.KEY_RE, noun="the other program's settings file", tries=2, pause=0.2):
    """The one value under the JSON key `field`, at any depth of another program's settings file. Every
    place that holds that key must hold the same value: two different values are never guessed between. A
    file that is not JSON is read once more after `pause`, since its program may be rewriting it."""
    for n in range(tries):
        raw = _open_own(path, False, SETTINGS_MAX, noun)
        try:
            doc = json.loads(raw.decode("utf-8"))
            break
        except (ValueError, UnicodeDecodeError, RecursionError):
            if n + 1 >= tries:
                raise WriterKeyError("%s %s is not JSON" % (noun, path))
            time.sleep(pause)
    found, todo = [], [doc]
    while todo:
        node = todo.pop()
        if isinstance(node, dict):
            for k, v in node.items():
                if k == field:
                    found.append(v)
                todo.append(v)
        elif isinstance(node, list):
            todo.extend(node)
    if not found:
        raise WriterKeyError("%s %s holds no key named %s" % (noun, path, field))
    if not all(isinstance(v, str) for v in found):
        raise WriterKeyError("%s %s holds something other than text under %s" % (noun, path, field))
    values = set(v.strip() for v in found)
    if len(values) > 1:
        raise WriterKeyError("%s %s holds %d different values under %s, so which one is the session writer's "
                             "is not guessed" % (noun, path, len(values), field))
    value = values.pop()
    if not pattern.match(value):
        raise WriterKeyError("%s %s holds something under %s that is not one key" % (noun, path, field))
    return value


def read_key(st):
    """The session writer's key now, from its source, or WriterKeyError (a ToolError) in fixed words."""
    path, field = key_at(st)
    return read_key_at(path, field)


def read_key_at(path, field):
    """The key at `path`: the whole of a key file, or the value under `field` in a settings file."""
    if field is None:
        return tl.read_key(path)
    return read_field(path, field)


def read_client(path):
    """(client id, client secret) from the client details' file: one line, `id:secret`."""
    m = CLIENT_RE.match(read_secret(path, CLIENT_RE, "the app's client details file"))
    return m.group(1), m.group(2)


def write_secret(path, value):
    """One value into `path`, mode 0600, whole or not at all, never through a link."""
    if os.path.islink(path):
        raise OSError(errno.ELOOP, "a link is never written through")
    folder = os.path.dirname(path)
    if not os.path.isdir(folder):
        os.makedirs(folder, mode=0o700)
    tmp = "%s.new.%d" % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(value + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ════════════════════════════════════════════════════════════════════ Linear

def _json_answer(raw):
    try:
        doc = json.loads(raw.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None
    return doc if isinstance(doc, dict) else None


def _error_body(exc):
    try:
        return _json_answer(exc.read(ANSWER_MAX + 1)[:ANSWER_MAX] or b"{}")
    except Exception:  # noqa: BLE001  (Python 3.9's HTTPError may hold no body: the status is the answer)
        return None


def _auth_refused(doc):
    """True when a tracker answer's errors say the key was not accepted."""
    for e in (doc or {}).get("errors") or []:
        ext = e.get("extensions") if isinstance(e, dict) else None
        if isinstance(ext, dict) and (ext.get("code") in ("AUTHENTICATION_ERROR", "FORBIDDEN")
                                      or ext.get("statusCode") in (401, 403)):
            return True
    return False


def ask_tracker(key, timeout=20):
    """Linear's answer to `viewer`, as its `data`. Refused when the key is not accepted; Unreachable when the
    tracker could not be asked. The key goes in the Authorization header alone, as every reader sends it."""
    req = urllib.request.Request(TRACKER_API, data=json.dumps({"query": Q_VIEWER}).encode("utf-8"),
                                 headers={"Authorization": key, "Content-Type": "application/json",
                                          "User-Agent": USER_AGENT}, method="POST")
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            raw = resp.read(ANSWER_MAX + 1)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403) or _auth_refused(_error_body(exc)):
            raise Refused()
        raise Unreachable("HTTP %d" % exc.code)
    except Exception as exc:  # noqa: BLE001  (no network, a timeout, a reset: the tracker was not asked)
        raise Unreachable(type(exc).__name__)
    doc = _json_answer(raw[:ANSWER_MAX]) if len(raw) <= ANSWER_MAX else None
    if doc is None:
        raise Unreachable("the answer was not a JSON object")
    if doc.get("errors"):
        if _auth_refused(doc):
            raise Refused()
        raise Unreachable("the tracker answered with errors")
    return doc.get("data") if isinstance(doc.get("data"), dict) else {}


def post_form(url, form, timeout=30):
    """One renewal: the form to Linear's token endpoint, its answer as a dict. TokenRefused with the result
    for an OAuth error; OSError or ValueError when it could not be made. A redirect is refused, so the form
    never goes anywhere else."""
    req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode("utf-8"),
                                 headers={"Content-Type": "application/x-www-form-urlencoded",
                                          "Accept": "application/json", "User-Agent": USER_AGENT},
                                 method="POST")
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            raw = resp.read(ANSWER_MAX + 1)
    except urllib.error.HTTPError as exc:
        code = (_error_body(exc) or {}).get("error")
        raise TokenRefused(OAUTH_ERRORS.get(code if isinstance(code, str) else "", "renewal-failed"))
    doc = _json_answer(raw) if len(raw) <= ANSWER_MAX else None
    if doc is None:
        raise ValueError("the answer was not a JSON object")
    return doc


def renew(st, post=None):
    """One renewal of a renewed key. (result, its life in seconds or None): `renewed`, or why not (RENEWALS).
    On success the new renewal token is written first, then the key, so a stop between the two leaves a
    renewal token that works; a stop before either leaves the old one, which Linear answers again for 30
    minutes after a lost answer."""
    src = source_of(st)
    try:
        cid, secret = read_client(src["client_file"])
    except WriterKeyError:
        return "no-client", None
    try:
        token = read_secret(src["renewal_file"], RENEWAL_RE, "the renewal token file")
    except Absent:
        if not src.get("adopt_file"):
            return "no-renewal-token", None
        try:            # once: the other program, stopped for good, hands its chain over
            token = read_field(src["adopt_file"], src["adopt_field"], RENEWAL_RE)
        except WriterKeyError:
            return "no-renewal-token", None
    except WriterKeyError:
        return "no-renewal-token", None
    form = {"grant_type": "refresh_token", "refresh_token": token, "client_id": cid, "client_secret": secret}
    try:
        doc = (post or post_form)(TOKEN_URL, form)
    except TokenRefused as exc:
        return (exc.result if exc.result in RENEWALS else "renewal-failed"), None
    except Exception:  # noqa: BLE001  (no network, a timeout, an answer that is not JSON: tried again later)
        return "renewal-failed", None
    access, fresh, life = doc.get("access_token"), doc.get("refresh_token"), doc.get("expires_in")
    if fresh is not None and not (isinstance(fresh, str) and RENEWAL_RE.match(fresh)):
        return "renewal-failed", None
    whole = (isinstance(access, str) and tl.KEY_RE.match(access) and isinstance(life, int)
             and not isinstance(life, bool) and EXPIRES_MIN <= life <= EXPIRES_MAX)
    try:
        write_secret(src["renewal_file"], fresh or token)        # the chain first, whatever else the answer holds
        if not whole:
            return "renewal-failed", None                        # the key is written only from a whole answer
        write_secret(st["key_file"], access)
    except OSError:
        return "renewal-failed", None
    return "renewed", life


def check(st, owners, gql=None):
    """(result, display name or None, the reader's words or None): the key asked who it is. `writer` only
    when it answers as `writer_user_id`, which is never an owner. The words are the reader's own, for a key
    that could not be read: a file and a key name, never what it holds."""
    try:
        key = read_key(st)
    except tl.ToolError as exc:
        return "unreadable", None, str(exc)
    try:
        data = (gql or ask_tracker)(key)
    except Refused:
        return "refused", None, None
    except Unreachable:
        return "unreachable", None, None
    viewer = (data or {}).get("viewer") if isinstance(data, dict) else None
    who = viewer.get("id") if isinstance(viewer, dict) else None
    if not isinstance(who, str) or who != st["writer_user_id"] or who in (owners or ()):
        return "other-user", None, None
    name = viewer.get("displayName")
    return "writer", (name if isinstance(name, str) and NAME_RE.match(name) else None), None


# ════════════════════════════════════════════════════════════════════ the runner's pass

def status_path(store_dir):
    return os.path.join(store_dir, STATUS_FILE)


def _blank(st):
    return {"schema": STATUS_SCHEMA, "at": None, "source": source_of(st)["kind"],
            "writer_user_id": st["writer_user_id"], "state": "unknown", "reason": "not-checked", "detail": None,
            "check": None, "answered_at": None, "writer_name": None, "renewal": None, "renewed_at": None,
            "ends_at": None}


def _since(stamp, now):
    t = epoch(stamp)
    return None if t is None else now - t


def _state(doc):
    """(state, reason). A renewal a person must mend (a refused renewal token or client, or none to renew with) is
    failed at once. One that did not complete (Linear out of reach, an answer that was not whole) is unknown: it is
    tried again every five minutes, and judge() says so once the key's end is six hours off."""
    renewal = (doc.get("renewal") or {}).get("result") if doc["source"] == "renew" else None
    if renewal not in (None, "renewed", "renewal-failed"):
        return "failed", renewal
    result = (doc.get("check") or {}).get("result")
    if result in ("other-user", "refused", "unreadable"):
        return "failed", result
    if renewal == "renewal-failed":
        return "unknown", renewal
    if result == "unreachable":
        return "unknown", "unreachable"
    if result is None:
        return "unknown", "not-checked"
    return "ok", None


def _clean(text):
    text = re.sub(r"[^\x20-\x7e]", " ", str(text or ""))
    return text[:DETAIL_MAX] or None


_TRIED = {}         # (status file, "renew" or "check", source, writer) -> when this process last tried


def _may_try(key, now):
    """Never more than one renewal, and one question to the tracker, per RETRY_EVERY in this process for one
    source and writer, whatever the status file says: a file that cannot be written must not turn every pass
    into a renewal."""
    last = _TRIED.get(key)
    if last is not None and abs(now - last) < RETRY_EVERY:
        return False
    _TRIED[key] = now
    return True


def keep(st, store_dir, owners, now, gql=None, post=None):
    """One look at the session writer's key, on a runner's pass: a renewal when one is due (a renewed key
    alone), then the key asked who it is when that is due, and the status written when anything moved.
    Returns the status document. Only the runner calls this: it holds its store's lock, so no two renew."""
    path = status_path(store_dir)
    old, _why = read_status(path)
    if old is not None and (old["source"] != source_of(st)["kind"] or old["writer_user_id"] != st["writer_user_id"]):
        old = None                      # another source or another writer: its old answers say nothing now
    doc = dict(old) if old is not None else _blank(st)
    moved, asked, checked = old is None, False, False
    detail = result = None
    memo = (path, doc["source"], st["writer_user_id"])
    if doc["source"] == "renew":
        last = doc.get("renewal") or {}
        since = _since(last.get("at"), now)
        ends = epoch(doc.get("ends_at"))
        failing = last.get("result") not in (None, "renewed")
        due = ends is None or ends - now <= RENEW_BEFORE or failing
        if due and not (failing and since is not None and since < RETRY_EVERY) and _may_try(memo + ("renew",), now):
            outcome, life = renew(st, post)
            doc["renewal"] = {"at": iso(now), "result": outcome}
            if outcome == "renewed":
                doc["renewed_at"], doc["ends_at"] = iso(now), iso(now + life)
                asked = True            # a new key is asked who it is at once
            moved = True
    last = doc.get("check") or {}
    since = _since(last.get("at"), now)
    if (asked or since is None or since >= (CHECK_EVERY if last.get("result") == "writer" else RETRY_EVERY)) \
            and (asked or _may_try(memo + ("check",), now)):
        result, name, detail = check(st, owners, gql)
        doc["check"] = {"at": iso(now), "result": result}
        if result == "writer":
            doc["answered_at"], doc["writer_name"] = iso(now), name
        moved = checked = True
    if moved:
        doc["state"], doc["reason"] = _state(doc)
        if checked:
            doc["detail"] = _clean(detail) if result == "unreadable" else None
        if doc["reason"] != "unreadable":
            doc["detail"] = None        # the reader's words belong to a key that could not be read, alone
        doc["at"] = iso(now)
        store.write_json(path, doc)
    return doc


def status_problems(doc):
    """What is wrong with a status document, as a reader judges it before believing a word of it."""
    if not isinstance(doc, dict):
        return ["it is not one JSON object"]
    bad = ["%s: not a field" % repr(k)[:40] for k in sorted(doc, key=repr) if k not in STATUS_KEYS]
    bad += ["%s: missing" % k for k in STATUS_KEYS if k not in doc]
    if bad:
        return bad
    if doc["schema"] != STATUS_SCHEMA:
        bad.append("schema: not %s" % STATUS_SCHEMA)
    if doc["source"] not in SOURCES:
        bad.append("source: not one of %s" % ", ".join(SOURCES))
    if doc["state"] not in STATES:
        bad.append("state: not one of %s" % ", ".join(STATES))
    if doc["reason"] is not None and doc["reason"] not in REASONS:
        bad.append("reason: not one this reader knows")
    if not (isinstance(doc["writer_user_id"], str) and tl.USER_ID_RE.match(doc["writer_user_id"])):
        bad.append("writer_user_id: not a tracker user id")
    for k in ("at", "answered_at", "renewed_at", "ends_at"):
        if doc[k] is not None and epoch(doc[k]) is None:
            bad.append("%s: not a time" % k)
    if epoch(doc["at"]) is None:
        bad.append("at: no time")
    for k, results in (("check", CHECKS), ("renewal", RENEWALS)):
        v = doc[k]
        if v is not None and not (isinstance(v, dict) and set(v) == {"at", "result"} and epoch(v["at"]) is not None
                                  and v["result"] in results):
            bad.append("%s: not a time and a result" % k)
    if doc["writer_name"] is not None and not (isinstance(doc["writer_name"], str) and NAME_RE.match(doc["writer_name"])):
        bad.append("writer_name: not a display name")
    if doc["detail"] is not None and not (isinstance(doc["detail"], str) and len(doc["detail"]) <= DETAIL_MAX):
        bad.append("detail: not short text")
    return bad


def read_status(path):
    """(status, "") or (None, why). A document that fails status_problems() is never believed."""
    try:
        doc = store.read_json(path)
    except store.StoreError as exc:
        return None, "the runner's status of the writer's key %s" % exc
    bad = status_problems(doc)
    if bad:
        return None, "the runner's status of the writer's key is not in its shape (%s)" % bad[0]
    return doc, ""


def _ago(seconds):
    seconds = max(0, int(seconds))
    if seconds < 3600:
        return "%d minute%s" % (seconds // 60, "" if seconds // 60 == 1 else "s")
    hours = seconds // 3600
    return "%d hour%s" % (hours, "" if hours == 1 else "s")


def judge(doc, now):
    """(problems, words): what is wrong, each in fixed words, and a line that says where the key stands. The
    runner's `check`, the live tool and the health watch each judge a status this one way."""
    problems = []
    quiet = _since(doc["at"], now)
    if quiet is None or quiet > QUIET_AFTER:
        problems.append("the runner has not said how the writer's key stands since %s: it may be stopped"
                        % (doc["at"] or "it began"))
    if doc["state"] == "failed":
        problems.append(REASONS.get(doc["reason"], "it failed"))
    ends = epoch(doc["ends_at"])
    if doc["source"] == "renew" and ends is not None and ends - now <= WARN_BEFORE:
        problems.append("the key ends at %s (%s), and has not been renewed" % (
            doc["ends_at"], "in " + _ago(ends - now) if ends > now else _ago(now - ends) + " ago"))
    answered = _since(doc["answered_at"], now)
    if answered is None or answered > ANSWER_STALE:
        problems.append("the key has not answered as the session writer %s" % (
            "since " + doc["answered_at"] if doc["answered_at"] else "yet"))
    said = {"file": "written by a person", "other-program": "read from another program's settings, which "
            "renews it", "renew": "renewed by the runner"}[doc["source"]]
    words = "the session writer's key is %s" % said
    if answered is not None:
        words += "; it answered as the session writer %s ago" % _ago(answered)
    if doc["source"] == "renew" and ends is not None:
        words += "; it ends at %s (%s)" % (doc["ends_at"], "in " + _ago(ends - now) if ends > now else
                                          "ended " + _ago(now - ends) + " ago")
        if doc["renewed_at"]:
            words += ", renewed at %s" % doc["renewed_at"]
        if doc["reason"] == "renewal-failed":
            words += "; its last renewal did not complete, and is tried again every %d minutes" % (RETRY_EVERY // 60)
    elif doc["source"] == "other-program":
        words += "; its end is that program's to keep, so the runner asks it who it is every %d minutes" % (
            CHECK_EVERY // 60)
    return problems, words


# ════════════════════════════════════════════════════════════════════ selftest

def selftest():
    import tempfile
    fails = []

    def expect(name, got, want):
        ok = got == want
        print("%s %s" % ("PASS" if ok else "FAIL", name) + ("" if ok else "\n     got  %r\n     want %r" % (got, want)))
        if not ok:
            fails.append(name)

    with tempfile.TemporaryDirectory(prefix="writer-key-selftest-") as tmp:
        tmp = os.path.realpath(tmp)
        cases(expect, tmp)
    print("\n%d failed" % len(fails) if fails else "\nall passed")
    return 1 if fails else 0


PREFIX = "lin" + "_oauth_"           # built at runtime: no line here holds a key's shape
KEY_A = PREFIX + "a1" * 32
KEY_B = PREFIX + "b2" * 32
RENEWAL_0 = "renew0" + "r" * 30
CLIENT = "client0id0123456789:secret0abcdefghij"


class StandInLinear(object):
    """Linear's token endpoint and `viewer`, offline. A renewal token works once (rotation), and a key
    answers as its user until it is replaced or ends."""

    def __init__(self, user="writer-1", name="dispatcher-agent", life=86399):
        self.renewals, self.keys, self.posts, self.asked = {RENEWAL_0: 0}, {KEY_A: user}, [], []
        self.user, self.name, self.life, self.n, self.down, self.refuse = user, name, life, 0, False, None

    def post(self, url, form):
        self.posts.append((url, dict(form)))
        if self.down:
            raise OSError("down")
        if self.refuse:
            raise TokenRefused(self.refuse)
        if form.get("grant_type") != "refresh_token" or form.get("client_id") != CLIENT.split(":")[0] \
                or form.get("client_secret") != CLIENT.split(":")[1]:
            raise TokenRefused("client-refused")
        if form.get("refresh_token") not in self.renewals:
            raise TokenRefused("renewal-refused")
        del self.renewals[form["refresh_token"]]
        self.n += 1
        key, fresh = PREFIX + "%064x" % self.n, "renew%d" % self.n + "r" * 30
        self.renewals[fresh] = self.n
        self.keys = {key: self.user}            # the old key goes with the renewal here, the worst case
        return {"access_token": key, "token_type": "Bearer", "expires_in": self.life, "scope": "read write",
                "refresh_token": fresh}

    def gql(self, key):
        self.asked.append(key)
        if self.down:
            raise Unreachable("down")
        if key not in self.keys:
            raise Refused()
        return {"viewer": {"id": self.keys[key], "displayName": self.name}}


def _put(path, text, mode=0o600):
    folder = os.path.dirname(path)
    if not os.path.isdir(folder):
        os.makedirs(folder, mode=0o700)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(path, mode)


def cases(expect, tmp):  # noqa: C901  (one battery, read top to bottom)
    T0 = 1_800_000_000.0
    work, home = os.path.join(tmp, "work"), os.path.join(tmp, "home")
    keyf = os.path.join(tmp, "runner", "writer-key")
    other = os.path.join(tmp, "other", "config.json")
    renewal, client = os.path.join(tmp, "runner", "writer-renewal-key"), os.path.join(tmp, "runner", "writer-client")
    sdir = os.path.join(tmp, "store")
    os.makedirs(sdir, mode=0o700)

    # ---- the setting ---------------------------------------------------------------- #
    expect("no key_source is the file source", source_setting(None, keyf), ({"kind": "file"}, []))
    expect("the other-program source: its file and its key",
           source_setting({"kind": "other-program", "file": other, "field": "linearToken"}, keyf, (work,)),
           ({"kind": "other-program", "file": other, "field": "linearToken"}, []))
    full = {"kind": "renew", "renewal_file": renewal, "client_file": client, "adopt_file": other,
            "adopt_field": "linearRefreshToken"}
    expect("the renew source, adopting from the other program", source_setting(full, keyf), (full, []))
    expect("the renew source alone",
           source_setting({"kind": "renew", "renewal_file": renewal, "client_file": client}, keyf),
           ({"kind": "renew", "renewal_file": renewal, "client_file": client, "adopt_file": None,
             "adopt_field": None}, []))
    for name, raw, want in (
            ("an unknown kind", {"kind": "vault"}, "kind is file, other-program or renew"),
            ("not an object", "file", "kind is file, other-program or renew"),
            ("a setting of another source", {"kind": "file", "field": "x"}, "not a setting of the file source"),
            ("a relative file", {"kind": "other-program", "file": "config.json", "field": "k"}, "absolute path"),
            ("no field", {"kind": "other-program", "file": other}, "the name of a JSON key"),
            ("a field that is a path", {"kind": "other-program", "file": other, "field": "a.b c"}, "JSON key"),
            ("a file where a session writes", {"kind": "other-program", "file": os.path.join(work, "c.json"),
                                               "field": "k"}, "where a session can write"),
            ("the key file itself", {"kind": "other-program", "file": keyf, "field": "k"}, "same file as key_file"),
            ("the client file as the renewal file", {"kind": "renew", "renewal_file": client, "client_file": client},
             "same file as renewal_file"),
            ("adopt_file alone", {"kind": "renew", "renewal_file": renewal, "client_file": client,
                                  "adopt_file": other}, "go together")):
        got = source_setting(raw, keyf, (work, home))
        expect("refused: %s" % name, (got[0], any(want in p for p in got[1])), (None, True))
    expect("`~/` is this account's home",
           source_setting({"kind": "other-program", "file": "~/c.json", "field": "k"}, keyf)[0]["file"],
           _resolve("~/c.json"))

    st_file = {"key_file": keyf, "writer_user_id": "writer-1", "key_source": {"kind": "file"}}
    st_other = {"key_file": keyf, "writer_user_id": "writer-1",
                "key_source": {"kind": "other-program", "file": other, "field": "linearToken"}}
    st_renew = {"key_file": keyf, "writer_user_id": "writer-1", "key_source": full}
    expect("the walls: the key file alone for a file source", files(st_file), [keyf])
    expect("the walls: the other program's settings file too", files(st_other), [keyf, other])
    expect("the walls: every file of a renewed key, and the file it adopts from",
           files(st_renew), [keyf, renewal, client, other])
    expect("no session tools, no walls", files(None), [])
    expect("a reader finds the key in the other program's file, by its key name", key_at(st_other),
           (other, "linearToken"))
    expect("…and a renewed key in the runner's own key file", key_at(st_renew), (keyf, None))

    # ---- reading another program's settings file ------------------------------------- #
    shapes = (("at the top", {"repositories": [], "linearToken": KEY_A, "linearRefreshToken": RENEWAL_0}),
              ("in each repository", {"repositories": [{"id": "r1", "linearToken": KEY_A},
                                                       {"id": "r2", "linearToken": KEY_A}]}),
              ("under a workspace", {"linearWorkspaces": {"ws": {"linearToken": KEY_A}}}))
    for name, doc in shapes:
        _put(other, json.dumps(doc), 0o644)
        expect("the key is read %s, the file being the other program's own mode 0644" % name,
               read_key(st_other), KEY_A)

    def refused(doc_or_text, mode=0o600, field="linearToken", link=False):
        if os.path.lexists(other):
            os.unlink(other)
        if link:
            _put(other + ".real", json.dumps({"linearToken": KEY_A}))
            os.symlink(other + ".real", other)
        else:
            _put(other, doc_or_text if isinstance(doc_or_text, str) else json.dumps(doc_or_text), mode)
        try:
            read_field(other, field, pause=0)
        except WriterKeyError as exc:
            return str(exc)
        return "read"
    for name, args, want in (
            ("no such key", ({"other": KEY_A},), "holds no key named linearToken"),
            ("two different keys", ({"a": {"linearToken": KEY_A}, "b": {"linearToken": KEY_B}},),
             "2 different values under linearToken"),
            ("a number under the key", ({"linearToken": 5},), "other than text"),
            ("text that is not a key", ({"linearToken": "not a key at all"},), "not one key"),
            ("not JSON", ("{\"linearToken\": ",), "is not JSON"),
            ("a file others can change", ({"linearToken": KEY_A}, 0o666), "can be changed by others"),
            ("a link", (None,), "is a link")):
        got = refused(*args) if name != "a link" else refused(None, link=True)
        expect("refused: %s, said in words without the key" % name, (want in got, KEY_A in got, KEY_B in got),
               (True, False, False))
    if os.path.lexists(other):
        os.unlink(other)
    expect("an absent file is said as absent", isinstance(_raises(lambda: read_key(st_other)), Absent), True)
    _put(other, json.dumps({"linearToken": KEY_A}) + " " * (SETTINGS_MAX + 1))
    expect("refused: a settings file past its size", "larger than" in str(_raises(lambda: read_key(st_other))), True)
    _put(other, '{"linearToken": "%s", "pad": "' % KEY_A)
    expect("a file caught mid-write is read again, once, and then said", "is not JSON" in str(
        _raises(lambda: read_field(other, "linearToken", pause=0))), True)

    # ---- renewing --------------------------------------------------------------------- #
    lin = StandInLinear()
    _put(other, json.dumps({"repositories": [{"linearToken": KEY_A, "linearRefreshToken": RENEWAL_0}]}), 0o644)
    expect("no client details: said, and Linear is not asked", (renew(st_renew, lin.post), lin.posts),
           (("no-client", None), []))
    _put(client, CLIENT + "\n")
    os.chmod(client, 0o644)
    expect("client details others can read are refused", renew(st_renew, lin.post), ("no-client", None))
    os.chmod(client, 0o600)
    st_alone = {"key_file": keyf, "writer_user_id": "writer-1",
                "key_source": {"kind": "renew", "renewal_file": renewal, "client_file": client, "adopt_file": None,
                               "adopt_field": None}}
    expect("no renewal token and nothing to adopt: said", renew(st_alone, lin.post), ("no-renewal-token", None))
    order = []
    real_write = write_secret

    def watched(path, value):
        order.append(os.path.basename(path))
        real_write(path, value)
    globals()["write_secret"] = watched
    try:
        got = renew(st_renew, lin.post)
    finally:
        globals()["write_secret"] = real_write
    url, form = lin.posts[-1]
    expect("the first renewal adopts the other program's renewal token, and Linear answers",
           (got, url, form["grant_type"], form["refresh_token"]), (("renewed", 86399), TOKEN_URL, "refresh_token",
                                                                     RENEWAL_0))
    expect("…the client details go in the form", (form["client_id"], form["client_secret"]),
           tuple(CLIENT.split(":")))
    expect("…the new renewal token is written first, then the key", order, ["writer-renewal-key", "writer-key"])
    expect("…both are the runner's own now, mode 0600",
           [(stat.S_IMODE(os.stat(p).st_mode), open(p).read().strip()) for p in (renewal, keyf)],
           [(0o600, "renew1" + "r" * 30), (0o600, PREFIX + "%064x" % 1)])
    os.unlink(other)
    expect("the other program removed, the runner renews from its own files alone",
           (renew(st_renew, lin.post), lin.posts[-1][1]["refresh_token"]), (("renewed", 86399), "renew1" + "r" * 30))
    expect("…and the key file holds the newest key", open(keyf).read().strip(), PREFIX + "%064x" % 2)
    lin.renewals = {}
    before = (open(renewal).read(), open(keyf).read())
    expect("a renewal token Linear refuses (another program used it): said, nothing written",
           (renew(st_renew, lin.post), (open(renewal).read(), open(keyf).read()) == before),
           (("renewal-refused", None), True))
    lin.down = True
    expect("Linear out of reach: said, nothing written",
           (renew(st_renew, lin.post), (open(renewal).read(), open(keyf).read()) == before),
           (("renewal-failed", None), True))
    lin.down, lin.refuse = False, "client-refused"
    expect("client details Linear refuses: said", renew(st_renew, lin.post), ("client-refused", None))
    lin.refuse = None
    for name, answer in (("no key in the answer", {"refresh_token": "renew9" + "r" * 30, "expires_in": 86399}),
                         ("a renewal token of another shape", {"access_token": KEY_B, "refresh_token": "x y",
                                                               "expires_in": 86399}),
                         ("no life in the answer", {"access_token": KEY_B, "refresh_token": "renew8" + "r" * 30})):
        got = renew(st_renew, lambda url, form, a=answer: a)
        expect("refused: %s" % name, got, ("renewal-failed", None))
    expect("…a renewal token that came back is kept even when the rest is wrong: the chain is never lost",
           open(renewal).read().strip(), "renew8" + "r" * 30)
    expect("…but the key is written only from a whole answer: the last good key stays", open(keyf).read().strip(),
           PREFIX + "%064x" % 2)

    # ---- the runner's pass ------------------------------------------------------------ #
    lin = StandInLinear()
    _put(other, json.dumps({"linearRefreshToken": RENEWAL_0}), 0o644)
    for p in (renewal, keyf):
        os.unlink(p)
    doc = keep(st_renew, sdir, ["owner-1"], T0, lin.gql, lin.post)
    expect("the first pass renews (no end known) and asks the new key who it is",
           (doc["state"], doc["reason"], doc["check"]["result"], doc["ends_at"], doc["writer_name"], len(lin.posts)),
           ("ok", None, "writer", iso(T0 + 86399), "dispatcher-agent", 1))
    expect("…its status is in its shape", status_problems(read_status(status_path(sdir))[0]), [])
    raw = open(status_path(sdir)).read()
    expect("…and holds no key, renewal token or client secret",
           [s in raw for s in (KEY_A, PREFIX, RENEWAL_0, "renew1", CLIENT.split(":")[1])], [False] * 5)
    doc2 = keep(st_renew, sdir, ["owner-1"], T0 + 60, lin.gql, lin.post)
    expect("a pass a minute later asks nothing and writes nothing", (len(lin.posts), len(lin.asked), doc2 == doc),
           (1, 1, True))
    keep(st_renew, sdir, ["owner-1"], T0 + CHECK_EVERY, lin.gql, lin.post)
    expect("after 30 minutes the key is asked again, and not renewed", (len(lin.posts), len(lin.asked)), (1, 2))
    doc = keep(st_renew, sdir, ["owner-1"], T0 + 86399 - RENEW_BEFORE, lin.gql, lin.post)
    expect("12 hours before its end it is renewed", (len(lin.posts), doc["renewed_at"], doc["state"]),
           (2, iso(T0 + 86399 - RENEW_BEFORE), "ok"))
    t = T0 + 86399 - RENEW_BEFORE
    for day in range(1, 4):                       # three days on, with no person: renewed twice a day
        for step in range(48):
            t += 1800
            keep(st_renew, sdir, ["owner-1"], t, lin.gql, lin.post)
        doc = read_status(status_path(sdir))[0]
        expect("day %d: still renewed by itself, and answering" % day,
               (doc["state"], judge(doc, t)[0], epoch(doc["ends_at"]) - t > WARN_BEFORE), ("ok", [], True))
    lin.renewals = {}                              # another program renewed it first
    t = epoch(doc["ends_at"]) - RENEW_BEFORE
    doc = keep(st_renew, sdir, ["owner-1"], t, lin.gql, lin.post)
    problems, _w = judge(doc, t)
    expect("a renewal Linear refuses is FAILED at once, in words, while the key still answers",
           (doc["state"], doc["reason"], doc["check"]["result"], [REASONS["renewal-refused"]] == problems),
           ("failed", "renewal-refused", "writer", True))
    n = len(lin.posts)
    keep(st_renew, sdir, ["owner-1"], t + 60, lin.gql, lin.post)
    keep(st_renew, sdir, ["owner-1"], t + RETRY_EVERY, lin.gql, lin.post)
    expect("…it is tried again after five minutes, not every pass", len(lin.posts), n + 1)
    t2 = epoch(doc["ends_at"]) - WARN_BEFORE + 1
    doc = keep(st_renew, sdir, ["owner-1"], t2, lin.gql, lin.post)
    expect("six hours before its end, the end is said too",
           any("has not been renewed" in p for p in judge(doc, t2)[0]), True)
    lin.renewals = {"renew-again" + "r" * 20: 0}
    write_secret(renewal, "renew-again" + "r" * 20)
    doc = keep(st_renew, sdir, ["owner-1"], t2 + RETRY_EVERY, lin.gql, lin.post)
    expect("a new renewal token put in: renewed, and OK again", (doc["state"], judge(doc, t2 + RETRY_EVERY)[0]),
           ("ok", []))

    sdir5 = os.path.join(tmp, "store5")
    os.makedirs(sdir5, mode=0o700)
    lin = StandInLinear()
    write_secret(renewal, RENEWAL_0)
    doc = keep(st_renew, sdir5, ["owner-1"], T0, lin.gql, lin.post)
    t = epoch(doc["ends_at"]) - RENEW_BEFORE

    def down(url, form):
        raise OSError("Linear out of reach")
    doc = keep(st_renew, sdir5, ["owner-1"], t, lin.gql, down)
    problems, words = judge(doc, t)
    expect("a renewal that did not complete (Linear out of reach) is unknown, not failed, while the key answers and "
           "its end is 12 hours off; the words say it is tried again",
           (doc["state"], doc["reason"], problems, "its last renewal did not complete" in words),
           ("unknown", "renewal-failed", [], True))
    t2 = epoch(doc["ends_at"]) - WARN_BEFORE + 1
    doc = keep(st_renew, sdir5, ["owner-1"], t2, lin.gql, down)
    expect("…and once its end is six hours off, that is said", any("has not been renewed" in p for p in judge(doc, t2)[0]),
           True)

    lin = StandInLinear()
    _put(other, json.dumps({"linearToken": KEY_A}), 0o644)
    sdir2 = os.path.join(tmp, "store2")
    os.makedirs(sdir2, mode=0o700)
    doc = keep(st_other, sdir2, ["owner-1"], T0, lin.gql, lin.post)
    expect("the other-program source: read and asked, never renewed",
           (doc["source"], doc["state"], doc["renewal"], lin.posts, lin.asked), ("other-program", "ok", None, [], [KEY_A]))
    _put(other, json.dumps({"linearToken": KEY_B}), 0o644)
    lin.keys = {KEY_B: "writer-1"}
    doc = keep(st_other, sdir2, ["owner-1"], T0 + CHECK_EVERY, lin.gql, lin.post)
    expect("…the other program renews: the next look reads the new key from its file",
           (lin.asked[-1], doc["state"]), (KEY_B, "ok"))
    lin.keys = {}
    doc = keep(st_other, sdir2, ["owner-1"], T0 + 2 * CHECK_EVERY, lin.gql, lin.post)
    expect("…its key ended and was not renewed: FAILED, refused", (doc["state"], doc["reason"]), ("failed", "refused"))
    os.unlink(other)
    doc = keep(st_other, sdir2, ["owner-1"], T0 + 2 * CHECK_EVERY + RETRY_EVERY, lin.gql, lin.post)
    expect("…its file gone: FAILED, unreadable, the runner's words kept for `check`",
           (doc["state"], doc["reason"], "is not there" in (doc["detail"] or "")), ("failed", "unreadable", True))
    lin.keys = {KEY_A: "someone-else"}
    _put(other, json.dumps({"linearToken": KEY_A}), 0o644)
    doc = keep(st_other, sdir2, ["owner-1"], T0 + 3 * CHECK_EVERY, lin.gql, lin.post)
    expect("…a key of another user: FAILED", (doc["state"], doc["reason"]), ("failed", "other-user"))
    lin.keys = {KEY_A: "owner-1"}
    st_owner = dict(st_other, writer_user_id="owner-1")
    doc = keep(st_owner, sdir2, ["owner-1"], T0 + 4 * CHECK_EVERY, lin.gql, lin.post)
    expect("…a key of an owner is never the writer's", doc["reason"], "other-user")
    lin.keys, lin.down = {KEY_A: "writer-1"}, True
    doc = keep(st_other, sdir2, ["owner-1"], T0 + 5 * CHECK_EVERY, lin.gql, lin.post)
    expect("…the tracker out of reach: unknown, not failed", (doc["state"], doc["reason"]), ("unknown", "unreachable"))
    t = T0 + 5 * CHECK_EVERY + ANSWER_STALE + 1
    expect("…and once it has not answered for three hours, that is said",
           any("has not answered" in p for p in judge(doc, t)[0]), True)
    expect("a status the runner stopped writing is said", any("may be stopped" in p for p in judge(doc, t + QUIET_AFTER)[0]),
           True)
    sdir3 = os.path.join(tmp, "store3")
    os.makedirs(sdir3, mode=0o700)
    write_secret(keyf, KEY_A)
    lin = StandInLinear()
    doc = keep(st_file, sdir3, ["owner-1"], T0, lin.gql, lin.post)
    expect("the file source: asked, never renewed, no end of its own",
           (doc["source"], doc["state"], doc["ends_at"], lin.posts), ("file", "ok", None, []))
    expect("…its words", judge(doc, T0 + 60), ([], "the session writer's key is written by a person; it answered "
                                                   "as the session writer 1 minute ago"))
    doc = keep(st_other, sdir3, ["owner-1"], T0 + 61, lin.gql, lin.post)
    expect("a change of source starts the status again", (doc["source"], doc["check"]["at"]), ("other-program", iso(T0 + 61)))
    sdir4 = os.path.join(tmp, "store4")
    os.makedirs(status_path(sdir4))        # a folder where the status goes, so it can never be written
    lin = StandInLinear()
    write_secret(renewal, RENEWAL_0)
    raised = [_raises(lambda t=t: keep(st_renew, sdir4, ["owner-1"], t, lin.gql, lin.post)) for t in (T0, T0 + 5, T0 + 10)]
    expect("a status that cannot be written is raised each pass, and the key is renewed once, not once a pass",
           ([isinstance(e, OSError) for e in raised], len(lin.posts), len(lin.asked)), ([True] * 3, 1, 2))

    # ---- the status a reader believes ------------------------------------------------- #
    good = read_status(status_path(sdir3))[0]
    for name, change in (("an unknown field", {"key": KEY_A}), ("a state of its own", {"state": "fine"}),
                         ("a reason this reader does not know", {"reason": "because"}),
                         ("a check with no time", {"check": {"at": "soon", "result": "writer"}}),
                         ("another schema", {"schema": "pipeline-writer-key/2"})):
        expect("not believed: %s" % name, status_problems(dict(good, **change)) != [], True)
    _put(os.path.join(tmp, "bad.json"), "{")
    expect("a status that is not JSON is said, never read as fine", read_status(os.path.join(tmp, "bad.json"))[0], None)
    expect("the reasons the health watch says are its own fixed words, one for each the runner writes",
           sorted(set(CHECKS[1:] + RENEWALS[1:] + ("not-checked",))), sorted(REASONS))

    # ---- the real calls, offline: a redirect is refused, so the form never goes anywhere else ------- #
    expect("the opener refuses a redirect", any(isinstance(h, _RefuseRedirect) for h in _OPENER.handlers), True)
    expect("the token endpoint is Linear's", (urllib.parse.urlparse(TOKEN_URL).scheme,
                                               urllib.parse.urlparse(TOKEN_URL).netloc,
                                               urllib.parse.urlparse(TOKEN_URL).path),
           ("https", "api.linear.app", "/oauth/token"))


def _raises(fn):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        return exc
    return None


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--selftest"]:
        from scratch_run import contained   # KIT-450: its temp folders go when it ends
        return contained(selftest, "writer-key-selftest-run-")
    print(__doc__.strip())
    return 2


if __name__ == "__main__":
    sys.exit(main())
