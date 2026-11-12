#!/usr/bin/env python3
"""The one write path into queue.conf (KIT-700): a queue setting changed, kept, read back and recorded.

    python3 scripts/pipeline_queue_settings.py --selftest

WHO CALLS IT.  The tech lead changes a queue setting when the owner asks for it in his own DM message
(scripts/pipeline_bridge_tech_lead.py), and the sprint review's proposals apply when he replies `apply ...`
(scripts/pipeline_sprint_changes.py, KIT-577). Both go through change() below. Nothing else writes the queue's
settings between two installs.

WHERE THE CHANGE GOES.  queue.conf is the owner's file, in his checkout. The jobs run as the role account, which can
neither read nor write it. So the queue installer (scripts/pipeline_queue_setup.py) puts an installed copy of
queue.conf beside the queue's settings file, in the role account's folder. Its first line records the base: each
setting's value at that install. change() writes the copy and the settings file, reads both back, and adds one
ledger row. The next install carries each setting the copy changed into the owner's queue.conf (carry()). When he
changed that setting there too, his value stays, and the install says so. The role account never writes his file.

WHICH SETTINGS.  Only ALLOWED: the queue's three daily limits, each a key queue.conf holds. Who may start work, the
teams and repositories, paths, accounts and jobs are not on it. They stay the owner's own edit.

A VALUE THE QUEUE WOULD REFUSE IS NEVER WRITTEN.  The value must have its key's form in queue.conf (the installer's
own rule, pinned below), and the settings with it must pass the queue's own loader (pipeline_queue_start.load).
Otherwise nothing is written, and the words say why, in the loader's own words.

WHO ASKED.  owner_asked() is the gate for the DM, and change() applies it itself to every change made through the DM.
The owner's own message must ask for it in one sentence: a verb such as set, lower or raise (or `=`), then the new
value, with the setting named. A question, a "don't", a "keep" or a "never" asks for nothing, and a number in a time, a
date or a percentage is not a value. A request the tech lead read in a ticket, a pull request or a session's words is
not in his message, so it is refused. The bridge also checks that the sender is the owner, as Slack says.

ONE AT A TIME.  A change holds an exclusive lock beside the settings file while it reads, writes and records. Nothing
raises out of change(): a fault is a failure said in words, with what was written put back.

THE LEDGER.  One JSON line per change, appended, mode 0600, beside the settings file: who asked, when, how, the key,
the old and the new value (contract section 28). The stand-up mentions each change (rows_since).

WHAT IT NEVER DOES (asserted in --selftest).  No network, no shell, no model. It writes the copy, the settings file
and the ledger, each never through a link, and nothing else.
"""
import copy as _copy
import datetime
import fcntl
import json
import os
import re
import stat
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pipeline_queue_start as starter  # noqa: E402  (the queue's own loader judges every value)

SCHEMA = "pipeline-queue-settings/1"
COPY_NAME = "queue.conf"                     # the installed copy, beside the settings file
LEDGER_NAME = "settings-changes.jsonl"
BASE_PREFIX = "# The queue installer's copy of queue.conf (KIT-700). Base:"
VALUE_RE = re.compile(r"^(?:0|[1-9][0-9]{0,3})(?:\.[0-9]{1,2})?\Z")
WHO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}\Z")
VIA = {"dm": "you asked in the DM", "sprint review": "your yes to the sprint review's proposal",
       "terminal": "set at a terminal"}
ROWS_MAX = 10
FILE_MAX = 1 << 20
LOCK_NAME = ".settings.lock"                 # beside the settings file: one change at a time
LOCK_WAIT_SECONDS = 10
OLD_RE = re.compile(r"^(?:0|[1-9][0-9]{0,8})(?:\.[0-9]{0,8}[1-9])?\Z")   # an old value, tidied (tidy())
# Each setting: how it is said, the words his message may name it by, whole numbers only, the places in the queue's
# settings file it is composed into (the installer's compose(), pinned in the selftest), and its default there.
ALLOWED = {
    "PLANNED_LOAD": {"name": "the planned load", "words": ("planned load", "daily load"), "whole": False,
                     "paths": (("order", "planned_load"), ("planner", "planned_load")), "default": "40"},
    "DAY_STOP": {"name": "the day stop", "words": ("day stop", "daily stop"), "whole": False,
                 "paths": (("order", "day_stop"),), "default": "60"},
    "LANES_PER_REPO": {"name": "the lanes per repository", "words": ("lanes",), "whole": True,
                       "paths": (("order", "lanes_per_repo"), ("planner", "lanes_per_repo")), "default": "1"},
}
SENTENCE_RE = re.compile(r"([.!?;](?=\s|\Z)|\n)")
# A number on its own: not inside an id (PROD-30), a time (9:30), a date (11/30) or a percentage (30%).
NUMBER_RE = re.compile(r"(?<![\w.:/-])[0-9]{1,7}(?:\.[0-9]{1,2})?(?![\w%:/-]|\.[0-9])")
# What makes a sentence a request: one of these verbs before the value, or `=` right before it.
REQUEST_RE = re.compile(r"(?<![a-z])(?:set|change|lower|raise|make|put|move|bump|drop|reduce|increase|cut|turn)"
                        r"(?![a-z])")
NEGATION_RE = re.compile(r"n't(?![a-z])|(?<![a-z])(?:not|never|keep|leave|unchanged|dont|cannot)(?![a-z])")


def _iso(dt):
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(text):
    try:
        return datetime.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        return None


def canon(value):
    """A value queue.conf takes, in its one spelling: 30.0 is 30, 7.50 is 7.5."""
    f = round(float(value), 2)
    return ("%d" % f) if f == int(f) else ("%.2f" % f).rstrip("0")


def tidy(text):
    """A number as queue.conf may spell it (040, 7.50) in one spelling (40, 7.5), never rounded; other text as it is."""
    m = re.match(r"^([0-9]+)(?:\.([0-9]+))?\Z", text or "")
    if not m:
        return text
    frac = (m.group(2) or "").rstrip("0")
    return (m.group(1).lstrip("0") or "0") + ("." + frac if frac else "")


def as_number(value):
    f = float(value)
    return int(f) if f == int(f) else f


def value_problem(key, value):
    """None when `value` is a form queue.conf takes for `key`, else why, in words that quote no value."""
    if key not in ALLOWED:
        return "is not a queue setting I can change"
    if not isinstance(value, str) or not VALUE_RE.match(value) or float(value) <= 0:
        return "must be a number above 0, at most 9999.99"
    if ALLOWED[key]["whole"] and "." in value:
        return "must be a whole number"
    return None


# --------------------------------------------------------------------------- #
# queue.conf's text: read as the installer reads it, one line changed
# --------------------------------------------------------------------------- #
def conf_value(text, key):
    """The value a line of queue.conf's text gives `key`, or None when no line does."""
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            k, eq, v = (p.strip() for p in line.partition("="))
            if eq and k == key:
                return v
    return None


def effective(text, key):
    """What the queue is installed with for `key`: its line, else the installer's default."""
    v = conf_value(text, key)
    return ALLOWED[key]["default"] if v is None else v


def set_line(text, key, value):
    """queue.conf's text with `key` set to `value`: its line replaced in place, or one line added at the end. Every
    other byte is kept."""
    lines = text.splitlines(True)
    for i, raw in enumerate(lines):
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line and line.partition("=")[0].strip() == key:
            lines[i] = "%s=%s%s" % (key, value, "\n" if raw.endswith("\n") else "")
            return "".join(lines)
    return text + ("" if not text or text.endswith("\n") else "\n") + "%s=%s\n" % (key, value)


def base_line(text):
    """The copy's first line: each allowed setting's value as installed."""
    return "%s %s" % (BASE_PREFIX, " ".join("%s=%s" % (k, effective(text, k)) for k in sorted(ALLOWED)))


def read_base(copy_text):
    """{key: value} from the copy's first line, or None when it is not the installer's."""
    first = (copy_text or "").split("\n", 1)[0]
    if not first.startswith(BASE_PREFIX + " "):
        return None
    base = {}
    for pair in first[len(BASE_PREFIX) + 1:].split():
        k, eq, v = pair.partition("=")
        if not eq or k in base:
            return None
        base[k] = v
    return base if sorted(base) == sorted(ALLOWED) else None


def installed_copy(owner_text):
    """The copy's whole text, as an install writes it: the base line, then his queue.conf as it is."""
    return base_line(owner_text) + "\n" + owner_text


def carry(owner_text, copy_text):
    """(carried, kept, why). carried: [(key, his value, the copy's)] for each setting the copy changed since the last
    install and he did not: the next install writes it into his queue.conf. kept: the same for a setting both changed:
    his value stays. `why` says the copy could not be used, and then nothing is carried."""
    if copy_text is None:
        return [], [], None
    base = read_base(copy_text)
    if base is None:
        return [], [], "the installed copy of queue.conf has no base line"
    carried, kept = [], []
    for key in sorted(ALLOWED):
        mine, theirs = effective(owner_text, key), effective(copy_text, key)
        if theirs != base[key] and theirs != mine:
            (carried if mine == base[key] else kept).append((key, mine, theirs))
    return carried, kept, None


def carry_into(path, carried):
    """The installer's carry, written: each carried setting (carry()) set in the owner's queue.conf, as the owner.
    Read never through a link, written whole beside itself with its mode, and read back. Returns the new text."""
    text, mode = _read_plain(path)
    for key, _mine, theirs in carried:
        if value_problem(key, theirs):
            raise ValueError("a carried value is not one queue.conf takes")
        text = set_line(text, key, theirs)
    _replace(path, text, mode)
    if _read_plain(path)[0] != text:
        raise ValueError("queue.conf does not read back as written")
    return text


# --------------------------------------------------------------------------- #
# The gate on his words
# --------------------------------------------------------------------------- #
def owner_asked(text, key, value):
    """True when the owner's own message asks for `key` at `value` in one sentence: the setting named (one of its words,
    or its key), and the value as a number of its own with a request verb, or `=`, before it. A sentence that is a
    question or holds a negation asks for nothing. Nothing else of the message is read."""
    if key not in ALLOWED or value_problem(key, value) or not isinstance(text, str):
        return False
    want = float(value)
    names = ALLOWED[key]["words"] + (key.lower().replace("_", " "),)
    parts = SENTENCE_RE.split(text.lower().replace("_", " ").replace("\u2019", "'"))
    for sentence, end in zip(parts[0::2], parts[1::2] + [""]):
        said = " ".join(sentence.split())
        if end == "?" or "?" in said or NEGATION_RE.search(said):
            continue
        if not any(re.search(r"(?<![a-z])%s(?![a-z])" % re.escape(n), said) for n in names):
            continue
        for m in NUMBER_RE.finditer(said):
            before = said[:m.start()]
            if float(m.group()) == want and (REQUEST_RE.search(before) or before.rstrip().endswith("=")):
                return True
    return False


# --------------------------------------------------------------------------- #
# Files: read never through a link, written whole, the ledger appended
# --------------------------------------------------------------------------- #
def paths_for(queue_file):
    """(the installed copy, the ledger): both beside the queue's settings file."""
    folder = os.path.dirname(queue_file)
    return os.path.join(folder, COPY_NAME), os.path.join(folder, LEDGER_NAME)


def _read_plain(path):
    """(text, mode) of a plain file, never through a link."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > FILE_MAX:
            raise ValueError("not a plain file")
        chunks = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8"), stat.S_IMODE(st.st_mode)
    finally:
        os.close(fd)


def _replace(path, text, mode):
    """`text` written beside `path` and moved into place whole, with `mode`."""
    fd, tmp = tempfile.mkstemp(prefix=".queue-settings-", dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _append(path, row):
    """One row at the ledger's end: a plain file nobody else can use, never through a link. A row that cannot be
    written and synced whole is cut off again, so the ledger never keeps a row for a change that was put back."""
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_mode & 0o077:
            raise ValueError("the ledger is not a plain file of this account's alone")
        try:
            os.write(fd, (json.dumps(row, sort_keys=True) + "\n").encode("utf-8"))
            os.fsync(fd)
        except BaseException:
            os.ftruncate(fd, st.st_size)
            raise
    finally:
        os.close(fd)


def _lock(path):
    """An exclusive lock on a file beside the settings file, held for one change; None after LOCK_WAIT_SECONDS."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(fd)
                return None
            time.sleep(0.1)


def valid_row(row):
    return (isinstance(row, dict) and row.get("schema") == SCHEMA and _parse(row.get("at")) is not None
            and row.get("key") in ALLOWED and row.get("via") in VIA and isinstance(row.get("who"), str)
            and WHO_RE.match(row["who"]) and isinstance(row.get("old"), str) and OLD_RE.match(row["old"])
            and isinstance(row.get("new"), str) and VALUE_RE.match(row["new"]))


def rows_since(ledger, since):
    """(rows, why): the ledger's rows written after `since`, oldest first, at most ROWS_MAX of the latest. A row not in
    this shape is left out. No ledger is no rows; one that cannot be read is said."""
    try:
        text, _mode = _read_plain(ledger)
    except FileNotFoundError:
        return [], None
    except (OSError, ValueError, UnicodeDecodeError):
        return [], "the queue settings ledger could not be read"
    rows = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if valid_row(row) and _parse(row["at"]) > since:
            rows.append(row)
    return rows[-ROWS_MAX:], None


def row_words(row, now=None):
    """One change, in plain words: `the planned load from 40 to 30 (you asked in the DM, 14:02)`."""
    at = _parse(row["at"]).astimezone()
    when = at.strftime("%H:%M") if now is None or at.date() == now.astimezone().date() else at.strftime("%a %H:%M")
    return "%s from %s to %s (%s, %s)" % (ALLOWED[row["key"]]["name"], row["old"], row["new"], VIA[row["via"]], when)


# --------------------------------------------------------------------------- #
# The change
# --------------------------------------------------------------------------- #
def change(queue_file, key, value, who, via, now, judge=None, owner_text=None):
    """(state, words): `key` set to `value` in the installed copy of queue.conf and in the queue's settings file, both
    read back, and one ledger row. state is applied, same, refused, unusable or failed; the words are fixed, with the
    old and the new value. `owner_text` is the owner's own message, required when `via` is "dm": the words gate is on
    this one write path itself. `judge` is the queue's own loader (a case stands in for it). Nothing raises out."""
    try:
        return _change(queue_file, key, value, who, via, now, judge or starter.load, owner_text)
    except Exception as exc:  # noqa: BLE001  (a fault before any write: said by its kind, nothing moved)
        return "failed", "I could not change %s (%s), so nothing was changed." % (str(key)[:40], type(exc).__name__)


def _change(queue_file, key, value, who, via, now, judge, owner_text):
    if key not in ALLOWED:
        return "refused", ("%s is not a queue setting I can change. I can change the planned load, the day stop and "
                           "the lanes per repository; the rest of queue.conf is yours to edit." % str(key)[:40])
    name = "%s (%s)" % (ALLOWED[key]["name"], key)
    why = value_problem(key, value)
    if why:
        return "refused", "%s %s, so nothing was changed." % (name[0].upper() + name[1:], why)
    if via not in VIA or not isinstance(who, str) or not WHO_RE.match(who):
        return "refused", "I don't know who asked for that, so nothing was changed."
    new = canon(value)
    if via == "dm" and not owner_asked(owner_text, key, value):
        return "refused", ("I didn't change %s to %s: your own message has to ask for it in one sentence, naming the "
                           "setting and the new value, and not as a question. A ticket, a pull request or a session "
                           "can't ask for it." % (name, new))
    if not queue_file:
        return "unusable", "The queue is not set up here, so there is no queue setting to change."
    lock = _lock(os.path.join(os.path.dirname(queue_file), LOCK_NAME))
    if lock is None:
        return "unusable", ("I can't change %s now: another change to the queue's settings is being made. Ask again in "
                            "a minute." % name)
    try:
        return _locked(queue_file, key, new, who, via, now, judge, name)
    finally:
        os.close(lock)


def _locked(queue_file, key, new, who, via, now, judge, name):
    copy_path, ledger = paths_for(queue_file)
    try:
        copy_text, copy_mode = _read_plain(copy_path)
    except (OSError, ValueError, UnicodeDecodeError):
        return "unusable", ("I can't change %s: the queue's installed copy of queue.conf could not be read, so nothing "
                            "was changed. The queue installer puts it there." % name)
    if read_base(copy_text) is None:
        return "unusable", ("I can't change %s: the queue's installed copy of queue.conf is not the installer's, so "
                            "nothing was changed." % name)
    try:
        live_text, live_mode = _read_plain(queue_file)
        doc = json.loads(live_text)
    except (OSError, ValueError, UnicodeDecodeError):
        return "unusable", "I can't change %s: the queue's settings file could not be read, so nothing was changed." % name
    places = ALLOWED[key]["paths"]
    if not isinstance(doc, dict) or not all(isinstance(doc.get(s), dict) for s, _k in places):
        return "unusable", "I can't change %s: the queue's settings file is not in its shape, so nothing was changed." % name
    old = tidy(effective(copy_text, key))
    if old == new and all(doc[s].get(k) == as_number(new) for s, k in places):
        return "same", "%s is already %s, so nothing was changed." % (name[0].upper() + name[1:], new)
    new_doc = _copy.deepcopy(doc)
    for s, k in places:
        new_doc[s][k] = as_number(new)
    _cfg, problems = judge(new_doc)
    if problems:
        return "refused", ("The queue would refuse %s at %s: %s. So nothing was changed."
                           % (name, new, "; ".join(str(p) for p in problems[:3])[:400]))
    row = {"schema": SCHEMA, "at": _iso(now), "who": who, "via": via, "key": key, "old": old, "new": new}
    done = []
    try:
        _replace(copy_path, set_line(copy_text, key, new), copy_mode)
        done.append((copy_path, copy_text, copy_mode))
        _replace(queue_file, json.dumps(new_doc, indent=2, sort_keys=True) + "\n", live_mode)
        done.append((queue_file, live_text, live_mode))
        back_copy, back_doc = _read_plain(copy_path)[0], json.loads(_read_plain(queue_file)[0])
        if effective(back_copy, key) != new or read_base(back_copy) is None \
                or any(back_doc[s][k] != as_number(new) for s, k in places) or judge(back_doc)[1]:
            raise ValueError("reading it back does not match")
        _append(ledger, row)
    except Exception as exc:  # noqa: BLE001  (any fault once writing began: put back whole, and said)
        stuck = []
        for path, text, mode in reversed(done):
            try:
                _replace(path, text, mode)
            except OSError:
                stuck.append(os.path.basename(path))
        if stuck:
            return "failed", ("I could not change %s (%s), and could not put %s back. Look at it before the next "
                              "queue install." % (name, type(exc).__name__, " and ".join(stuck)))
        return "failed", "I could not change %s (%s), so it is as it was." % (name, type(exc).__name__)
    return "applied", ("Changed %s from %s to %s. The queue's copy of queue.conf and its settings both say %s now, read "
                       "back, so the queue uses it from its next pass, and your next queue install writes it into your "
                       "queue.conf." % (name, old, new, new))


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #
def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--selftest"]:
        from scratch_run import contained
        return contained(_selftest, "queue-settings-selftest-run-")
    print(__doc__.split("\n\n")[0])
    return 2


def _selftest():  # noqa: C901  (one battery, read top to bottom)
    import pipeline_day_planner as planner
    import pipeline_queue_setup as setup
    failures, total = [], [0]

    def check(name, got, want):
        total[0] += 1
        if got != want:
            failures.append("FAIL %s: got %r, want %r" % (name, str(got)[:300], str(want)[:300]))
    now = datetime.datetime(2026, 11, 12, 14, 2, tzinfo=datetime.timezone.utc)

    # The installer's own rules, pinned: its defaults, the forms it takes, and where compose() puts each value.
    check("each setting's default is the installer's", [(k, ALLOWED[k]["default"] == setup.CONF_DEFAULTS[k]) for k in ALLOWED],
          [(k, True) for k in ALLOWED])
    samples = ("30", "7.5", "0.5", "9999.99", "1", "3", "0", "-1", "abc", "", "1.0", "1.005", "01", "10000", "4 ", "3e2")
    conf = dict(setup.CONF_DEFAULTS, **setup.BASE_CONF)
    for key in ALLOWED:
        looser = [v for v in samples if value_problem(key, v) is None
                  and any(key in e for e in setup.validate_conf(dict(setup.BASE_CONF, **{key: v}))[1])]
        check("%s: every value this takes, the installer's validate_conf takes too" % key, looser, [])
    check("the forms taken: positive, at most two decimals, whole for lanes",
          [[v for v in samples if value_problem(k, v) is None] for k in sorted(ALLOWED)],
          [["30", "7.5", "0.5", "9999.99", "1", "3", "1.0"], ["30", "1", "3"], ["30", "7.5", "0.5", "9999.99", "1", "3", "1.0"]])

    def leaves(doc, at=()):
        if isinstance(doc, dict):
            return dict(kv for k, v in doc.items() for kv in leaves(v, at + (k,)).items())
        return {at: json.dumps(doc)}
    for key in ALLOWED:
        a = leaves(setup.compose(dict(conf, **{key: "2"}))[0])
        b = leaves(setup.compose(dict(conf, **{key: "3"}))[0])
        check("%s is composed into exactly its places" % key, sorted(p for p in a if a[p] != b.get(p)),
              sorted(ALLOWED[key]["paths"]))
    check("nothing that names who may start, a team, a path or a job is on the list",
          [k for k in ALLOWED if any(w in k for w in ("USER", "TEAM", "REPO_BY", "ROLE", "FILE", "BIN", "ENV", "LABEL",
                                                       "CONFIG", "BACKLOG", "AGENT", "PLANNER"))], [])
    # the gate on his words
    for text, key, value, want in (
            ("Lower the planned load to 30 please", "PLANNED_LOAD", "30", True),
            ("set PLANNED_LOAD=30", "PLANNED_LOAD", "30", True),
            ("please raise the day stop to 80, thanks", "DAY_STOP", "80", True),
            ("make it two lanes per repo, so lanes 2", "LANES_PER_REPO", "2", True),
            ("set the planned load to $30", "PLANNED_LOAD", "30", True),
            ("can you raise the day stop to 80? thanks", "DAY_STOP", "80", False),
            ("Is the day stop still 80?", "DAY_STOP", "80", False),
            ("Should I lower the planned load to 30?", "PLANNED_LOAD", "30", False),
            ("Don't touch the planned load, 30 is fine", "PLANNED_LOAD", "30", False),
            ("Don\u2019t lower the planned load to 30", "PLANNED_LOAD", "30", False),
            ("keep the planned load at 30", "PLANNED_LOAD", "30", False),
            ("never set the day stop to 80", "DAY_STOP", "80", False),
            ("planned load review at 9:30", "PLANNED_LOAD", "30", False),
            ("set the planned load review at 9:30", "PLANNED_LOAD", "30", False),
            ("set the planned load review for 11/30", "PLANNED_LOAD", "30", False),
            ("lower the planned load by 30%", "PLANNED_LOAD", "30", False),
            ("the planned load was 30 last week", "PLANNED_LOAD", "30", False),
            ("two lanes per repo from now on, so lanes 2", "LANES_PER_REPO", "2", False),
            ("make the planned load 7.5", "PLANNED_LOAD", "7.50", True),
            ("what does PROD-7 say?", "PLANNED_LOAD", "9", False),
            ("Lower the planned load to 30", "PLANNED_LOAD", "300", False),
            ("Lower the planned load to 30", "DAY_STOP", "30", False),
            ("Lower the planned load. Set it to 30", "PLANNED_LOAD", "30", False),
            ("the planned load is fine; 30 lanes would be silly", "PLANNED_LOAD", "30", False),
            ("Lower the planned load to 30", "PLANNED_LOAD", "abc", False),
            ("Lower the planned load to 30", "OWNER_USER_IDS", "30", False),
            ("the unplanned loading bay is 30 feet", "PLANNED_LOAD", "30", False),
            ("set the planned load to PROD-30", "PLANNED_LOAD", "30", False),
            (None, "PLANNED_LOAD", "30", False)):
        check("owner_asked %r %s %s" % (text, key, value), owner_asked(text, key, value), want)
    # queue.conf's text
    owner = "# my queue\nROLE_ACCOUNT=_exqueue\nPLANNED_LOAD = 40\n\nDAY_STOP=60\n"
    check("set_line replaces only that key's line", set_line(owner, "PLANNED_LOAD", "30"),
          "# my queue\nROLE_ACCOUNT=_exqueue\nPLANNED_LOAD=30\n\nDAY_STOP=60\n")
    check("…and adds a line for a key the file leaves to its default", set_line("A=1", "LANES_PER_REPO", "2"),
          "A=1\nLANES_PER_REPO=2\n")
    check("…and never touches a comment that names the key", set_line("# PLANNED_LOAD=1\n", "PLANNED_LOAD", "5"),
          "# PLANNED_LOAD=1\nPLANNED_LOAD=5\n")
    check("the effective value is the line's, else the installer's default",
          (effective(owner, "PLANNED_LOAD"), effective(owner, "LANES_PER_REPO")), ("40", "1"))
    inst = installed_copy(owner)
    check("the copy is the base line, then his file as it is",
          (inst.split("\n", 1)[1] == owner, read_base(inst)), (True, {"DAY_STOP": "60", "LANES_PER_REPO": "1",
                                                                     "PLANNED_LOAD": "40"}))
    check("a copy whose first line is not the installer's has no base",
          [read_base(t) for t in (owner, BASE_PREFIX + " PLANNED_LOAD=1\n", BASE_PREFIX + " " + "A=1 " * 3, None)],
          [None, None, None, None])
    # the carry, at the next install
    tl = set_line(inst, "PLANNED_LOAD", "30")
    check("a setting the copy changed and he did not is carried",
          carry(owner, tl), ([("PLANNED_LOAD", "40", "30")], [], None))
    check("one he changed too stays his, and is said",
          carry(set_line(owner, "PLANNED_LOAD", "50"), tl), ([], [("PLANNED_LOAD", "50", "30")], None))
    check("one he changed alone is his, and nothing is said", carry(set_line(owner, "PLANNED_LOAD", "50"), inst), ([], [], None))
    check("the same value on both sides carries nothing", carry(set_line(owner, "PLANNED_LOAD", "30"), tl), ([], [], None))
    check("no copy carries nothing; a copy with no base line carries nothing and says so",
          (carry(owner, None), carry(owner, set_line(owner, "PLANNED_LOAD", "30"))),
          (([], [], None), ([], [], "the installed copy of queue.conf has no base line")))
    check("a key the copy changed that is not on the list is never carried",
          carry(owner, set_line(inst, "ROLE_ACCOUNT", "root")), ([], [], None))
    with tempfile.TemporaryDirectory() as tmp:
        his = os.path.join(tmp, "queue.conf")
        with open(his, "w", encoding="utf-8") as fh:
            fh.write(owner)
        os.chmod(his, 0o640)
        check("the carry is written into his queue.conf: that line alone, its mode kept, read back",
              (carry_into(his, [("PLANNED_LOAD", "40", "30")]), open(his).read(), oct(os.stat(his).st_mode & 0o777)),
              (set_line(owner, "PLANNED_LOAD", "30"),) * 2 + ("0o640",))
        for label, carried_, want in (("a value of the wrong form", [("PLANNED_LOAD", "30", "3e1")], ValueError),
                                      ("a link", [("PLANNED_LOAD", "30", "20")], OSError)):
            if want is OSError:
                os.rename(his, his + ".real")
                os.symlink(his + ".real", his)
            try:
                carry_into(his, carried_)
                got = "written"
            except want:
                got = "refused"
            check("%s is never carried into his queue.conf" % label,
                  (got, effective(open(his).read(), "PLANNED_LOAD")), ("refused", "30"))

    # the change, against the queue's own loader
    with tempfile.TemporaryDirectory() as tmp:
        home = os.path.join(tmp, "role-home")
        folder = os.path.join(home, ".stage-e", "queue")
        os.makedirs(folder)
        qfile = os.path.join(folder, "queue.json")
        cpath, lpath = paths_for(qfile)
        conf_text = "".join("%s=%s\n" % kv for kv in sorted(setup.BASE_CONF.items()))
        live = setup.with_home(setup.compose(dict(conf, **setup.parse_conf(conf_text)[0]))[0], home)

        def put(copy_text=None, doc=None):
            for path, body in ((cpath, installed_copy(conf_text) if copy_text is None else copy_text),
                               (qfile, json.dumps(live if doc is None else doc, indent=2, sort_keys=True) + "\n")):
                if os.path.lexists(path):
                    os.unlink(path)
                if body is not False:
                    with open(path, "w", encoding="utf-8") as fh:
                        fh.write(body)
                    os.chmod(path, 0o600)
            if os.path.exists(lpath):
                os.unlink(lpath)

        def state():
            rows = rows_since(lpath, now - datetime.timedelta(days=1))[0]
            doc = json.load(open(qfile))
            return (effective(open(cpath).read(), "PLANNED_LOAD"), doc["order"]["planned_load"],
                    doc["planner"]["planned_load"], [(r["key"], r["old"], r["new"], r["who"], r["via"]) for r in rows])
        def dm(key, value, words=None, **kw):
            said = "set the %s to %s" % (ALLOWED[key]["words"][0], value) if words is None else words
            return change(qfile, key, value, "U0OWNER", "dm", now, owner_text=said, **kw)
        put()
        check("the composed settings pass the queue's own loader", starter.load(live)[1], [])
        for label, words in (("no words of his", None), ("a ticket's ask, not his", "what does PROD-7 say?"),
                             ("his question", "should I lower the planned load to 30?")):
            st, why = change(qfile, "PLANNED_LOAD", "30", "U0OWNER", "dm", now, owner_text=words)
            check("the one write path itself refuses a DM change with %s, and writes nothing" % label,
                  (st, why.startswith("I didn't change the planned load (PLANNED_LOAD) to 30: your own message"), state()),
                  ("refused", True, ("40", 40, 40, [])))
        st, words = dm("PLANNED_LOAD", "30")
        check("an owner's change: the copy and both places in the settings, read back, and one ledger row",
              (st, state()), ("applied", ("30", 30, 30, [("PLANNED_LOAD", "40", "30", "U0OWNER", "dm")])))
        check("…and the words say old to new, plainly", words.startswith(
            "Changed the planned load (PLANNED_LOAD) from 40 to 30. The queue's copy of queue.conf and its settings both "
            "say 30 now, read back"), True)
        check("…and the copy's base line is the install's, so the next install carries it",
              carry(conf_text, open(cpath).read()), ([("PLANNED_LOAD", "40", "30")], [], None))
        check("the modes are kept: 0600", [oct(os.stat(p).st_mode & 0o777) for p in (cpath, qfile, lpath)],
              ["0o600", "0o600", "0o600"])
        st, words = dm("PLANNED_LOAD", "30.0")
        check("the same value again changes nothing and adds no row", (st, len(state()[3])), ("same", 1))
        put()
        st, words = dm("PLANNED_LOAD", "70")
        check("a value the queue's own loader refuses is never written, and the loader's words say why",
              (st, state(), "planned_load: is above day_stop" in words), ("refused", ("40", 40, 40, []), True))
        st, words = dm("DAY_STOP", "80")
        st2, words2 = dm("PLANNED_LOAD", "70")
        check("…and the same value fits once the day stop is raised", (st, st2, state()[:3]), ("applied", "applied", ("70", 70, 70)))
        put()
        for label, args in (("a key off the list", ("OWNER_USER_IDS", "30")), ("a value of the wrong form", ("PLANNED_LOAD", "3e2")),
                            ("a fraction of a lane", ("LANES_PER_REPO", "1.5")), ("zero", ("DAY_STOP", "0"))):
            st, _w = change(qfile, args[0], args[1], "U0OWNER", "dm", now, owner_text="set it to %s" % args[1])
            check("%s is refused and nothing is written" % label, (st, state()), ("refused", ("40", 40, 40, [])))
        check("who asked must be said", change(qfile, "PLANNED_LOAD", "30", "", "terminal", now)[0], "refused")
        check("…and how", change(qfile, "PLANNED_LOAD", "30", "U0OWNER", "a ticket", now)[0], "refused")
        check("no queue here is said", change("", "PLANNED_LOAD", "30", "U0OWNER", "terminal", now)[0], "unusable")
        for label, copy_text in (("no installed copy", False), ("a copy with no base line", conf_text)):
            put(copy_text=copy_text)
            check("%s changes nothing" % label, (dm("PLANNED_LOAD", "30")[0],
                                                 json.load(open(qfile))["order"]["planned_load"]), ("unusable", 40))
        put(copy_text=installed_copy("PLANNED_LOAD=040\n"))
        st, _w = dm("PLANNED_LOAD", "30")
        check("an old value in a spelling the installer takes is recorded tidy, and the stand-up keeps the row",
              (st, state()[3]), ("applied", [("PLANNED_LOAD", "40", "30", "U0OWNER", "dm")]))
        # nothing raises out of the one write path: a fault anywhere is a failure, put back
        put()

        def explodes(_doc):
            raise RecursionError("too deep")
        st, words = dm("PLANNED_LOAD", "30", judge=explodes)
        check("a fault inside the write path is a failure said in words, never an exception, and nothing moves",
              (st, "RecursionError" in words, state()), ("failed", True, ("40", 40, 40, [])))
        # a ledger row whose sync fails is taken back with the files: the ledger never reports an undone change
        put()
        real_fsync, calls = os.fsync, []

        def fsync_fails_third(fd):
            calls.append(fd)
            if len(calls) == 3:
                raise OSError("fsync failed")
            return real_fsync(fd)
        os.fsync = fsync_fails_third
        try:
            st, words = dm("PLANNED_LOAD", "30")
        finally:
            os.fsync = real_fsync
        check("a ledger row that cannot be synced is cut off again, and the files are put back",
              (st, state(), os.path.getsize(lpath) if os.path.exists(lpath) else 0), ("failed", ("40", 40, 40, []), 0))
        # one change at a time: the lock beside the settings file
        put()
        import fcntl
        global LOCK_WAIT_SECONDS
        saved_wait, LOCK_WAIT_SECONDS = LOCK_WAIT_SECONDS, 0.3
        holder = os.open(os.path.join(folder, LOCK_NAME), os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(holder, fcntl.LOCK_EX)
        try:
            st, words = dm("PLANNED_LOAD", "30")
        finally:
            fcntl.flock(holder, fcntl.LOCK_UN)
            os.close(holder)
            LOCK_WAIT_SECONDS = saved_wait
        check("while another change holds the lock, nothing is written and it says to try again",
              (st, "another change" in words, state()), ("unusable", True, ("40", 40, 40, [])))
        check("…and once it is free the change goes through", dm("PLANNED_LOAD", "30")[0], "applied")
        put()
        os.rename(cpath, cpath + ".real")
        os.symlink(cpath + ".real", cpath)
        check("a copy that is a link is never written through", (dm("PLANNED_LOAD", "30")[0],
              effective(open(cpath + ".real").read(), "PLANNED_LOAD")), ("unusable", "40"))
        os.unlink(cpath)
        os.unlink(cpath + ".real")
        put()
        os.mkdir(lpath)
        st, words = dm("PLANNED_LOAD", "30")
        os.rmdir(lpath)
        check("a change that cannot be recorded is put back whole, and said",
              (st, words.endswith("so it is as it was."), state()), ("failed", True, ("40", 40, 40, [])))
        put()
        judged = []

        def changes_its_mind(doc):
            judged.append(doc)
            return doc, ([] if len(judged) == 1 else ["order: planned_load: refused on reading it back"])
        st, words = dm("PLANNED_LOAD", "30", judge=changes_its_mind)
        check("what is written is read back and judged again: a read-back the loader refuses is put back whole",
              (st, len(judged), words.endswith("so it is as it was."), state()), ("failed", 2, True, ("40", 40, 40, [])))
        put()
        st, _w = dm("PLANNED_LOAD", "30", judge=lambda d: (d, []))
        check("the judge is the one that decides: a stand-in that takes anything is what lets 30 through here", st, "applied")
        put()
        for v in ("2", "3"):
            check("lanes %s: the order's and the day planner's loaders both take it" % v,
                  (dm("LANES_PER_REPO", v)[0],
                   planner.config(json.load(open(qfile))["planner"])[1]), ("applied", []))
        check("…and four lanes is refused by the queue's own loader", dm("LANES_PER_REPO", "4")[0],
              "refused")
        # the ledger, for the stand-up
        rows, why = rows_since(lpath, now - datetime.timedelta(minutes=1))
        check("rows since a time, oldest first", ([(r["key"], r["new"]) for r in rows], why),
              ([("LANES_PER_REPO", "2"), ("LANES_PER_REPO", "3")], None))
        check("…none since now", rows_since(lpath, now)[0], [])
        with open(lpath, "a") as fh:
            fh.write("{not json\n" + json.dumps(dict(rows[0] if rows else {}, key="OWNER_USER_IDS")) + "\n")
        check("a row not in its shape is left out", len(rows_since(lpath, now - datetime.timedelta(minutes=1))[0]), 2)
        check("no ledger is no rows, and nothing said", rows_since(os.path.join(tmp, "none.jsonl"), now), ([], None))
        check("one row in plain words", row_words(rows[0], now) if rows else None,
              "the lanes per repository from 1 to 2 (you asked in the DM, %s)" % now.astimezone().strftime("%H:%M"))
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    above = src.split("def _selftest(")[0]
    check("no network, no shell, no model above the selftest",
          [b for b in ("urllib", "http.client", "socket", "subprocess", "os.system", "shell=True", "environ") if b in above], [])
    check("it writes through _replace and _append alone", (above.count("_replace("), above.count("_append(")), (5, 2))
    if failures:
        for line in failures:
            print(line)
        print("queue settings selftest: %d of %d checks failed" % (len(failures), total[0]))
        return 1
    print("OK: queue settings selftest (%d checks)" % total[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
