#!/usr/bin/env python3
"""The one command asks for each missing value, one question at a time, and writes the answers
only after its one yes (KIT-735).

    scripts/pipeline_install.py calls this module. Nothing here is run by hand.
    python3 scripts/pipeline_ask.py --selftest

WHAT IT ASKS, AND WHEN
  Right after the first screen and before the consent page, the one command puts one plain
  question for each value only a person has and that is still missing. It asks in both modes. It
  does not ask after it has started itself again on newly pulled code: the person answered before
  the restart. Each value is asked once in a run.
    * The end date of a stored key. A key that ends in a year (`one-year`) or on a date chosen
      when it was made (`set-when-made`) and has no date on record is asked. So is any key whose
      date is within CREDENTIAL_WARN_DAYS of today, or past. A key whose end is `unknown`, or
      that never ends, is not asked for being missing. A name in CREDENTIAL_EXPIRIES that the
      parts list does not know is never asked about. The dates live in the health check's
      settings file (update.conf); `pipeline_watch.py dates` lists them.
    * The tech lead's name, when bridge.conf names none (neither APP_NAME nor PROJECT_NAME).

HOW IT ASKS
  One question at a time, in plain words: the question, one line saying where to find the value,
  and what is on record now. Enter keeps what is on record, or skips when nothing is; for the name
  with nothing on record it takes the default, `Tech lead`. An answer is typed on screen; no key
  is ever asked here. An answer longer than 80 characters is refused and never repeated back.
    * A date is YYYY-MM-DD. For a `one-year` key, `made YYYY-MM-DD` also works: the end is a year
      after that day, the same month and day (28 February for a 29 February). The end date is
      shown back. A day that does not exist is refused, and so is a date more than three years
      away, which is more likely a typo. A date already past is taken and marked "already past".
    * A name is checked with the tech lead installer's own rule.
    * Three bad answers skip the question with a note. A skipped question changes nothing and is
      asked again next run.

WHAT IT WRITES, AND HOW
  Nothing, until the person has typed yes on the consent page, which lists every change as its
  file, key, old value and new value. Then, right after the yes and before the first installer:
    * Dates go through `pipeline_watch.py dates --apply`'s own function, so its checks, its
      atomic write, the file's mode, every other entry and its read-back are the ones that
      command has.
    * The name is set as APP_NAME with the tech lead installer's `set_conf_line` and a swap of the
      whole file (a temp file beside it, made new and never through a link, the old mode kept,
      renamed over). Every other line stays as it was. The file is read back and compared.
  Before writing, each file is read again: if it is no longer as it was when the person was asked
  (the value on record changed), nothing is written to it. A write that fails, or reads back
  differently, is named by its file and stops the run before any installer. It removes nothing but
  its own temp file, and the old file stays in place.

WHAT IT NEVER DOES
  Read, print or log a key's value. Only dates and names pass through here. Ask without a person
  at a terminal (the command refuses that before it gets here). Write a file the person did not
  see listed. Touch a line other than the one it sets.

Exit: 0 every selftest case passed · 1 a case failed · 2 run without --selftest, which is the
      only thing to run here.
"""
import collections
import contextlib
import datetime
import io as io_module
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

ASK_TRIES = 3             # one question gets this many answers, then it is skipped
ANSWER_MAX = 80           # a longer answer is refused, and never repeated back
YEARS_AHEAD_MAX = 3       # a date further off than this is more likely a typo
NAME_KEY = "APP_NAME"     # the tech lead installer's own key for the name

# One question the person is asked. `kind` is "date" or "name"; `file` is the settings file the
# answer would go in; `key` is the clock name of a date, or APP_NAME; `current` is what is on record
# (an ISO date, or None); `ends` is the stored key's end kind (None for the name); `days` is how
# many days are left on a date on record (None when there is none).
Question = collections.namedtuple("Question", "kind file key question where current ends days")
# One answer that changes a file: `old` and `new` are an ISO date or a name; `past` marks a new
# date that is already behind today.
Change = collections.namedtuple("Change", "kind file key old new past")

DATE_RE = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})$")
MADE_RE = re.compile(r"^made\s+([0-9]{4})-([0-9]{2})-([0-9]{2})$", re.IGNORECASE)

NO_SUCH_DAY = "There is no such day. Type a real date, like 2027-03-15."
NOT_A_DATE = "That is not a date I can use. Type it like 2027-03-15: year, month, day."
TOO_FAR = ("That is more than %d years away, which is more likely a typo. Check the year."
           % YEARS_AHEAD_MAX)
TOO_LONG = ("That is longer than %d characters, so I did not read it. %%s" % ANSWER_MAX)
NO_MADE = ("This key's end date is set when it is made, so type the date its settings page "
           "shows, like 2027-03-15.")


# --------------------------------------------------------------------------- #
# The modules this one borrows from, loaded when they are needed. A refusal at the start of
# the one command must not wait for the tech lead installer and its neighbours to load.
# --------------------------------------------------------------------------- #
def _watch():
    import pipeline_watch
    return pipeline_watch


def _update():
    import pipeline_update
    return pipeline_update


def _bridge():
    import pipeline_bridge_setup
    return pipeline_bridge_setup


# --------------------------------------------------------------------------- #
# Dates and words
# --------------------------------------------------------------------------- #
def utc_today():
    return datetime.datetime.now(datetime.timezone.utc).date()


def real_date(year, month, day):
    """The date, or None when there is no such day."""
    try:
        return datetime.date(year, month, day)
    except ValueError:
        return None


def add_years(day, years):
    """The same month and day `years` later. A 29 February lands on 28 February in a year with no
    29th. None when that is past year 9999."""
    year = day.year + years
    if year > 9999:
        return None
    try:
        return day.replace(year=year)
    except ValueError:
        return day.replace(year=year, day=28)


def label(path):
    """A settings file as a person names it: its name when it sits in the kit folder, else its path."""
    here = os.path.dirname(os.path.abspath(path))
    return os.path.basename(path) if here == KIT else path


def days_words(days):
    if days is None:
        return ""
    if days < 0:
        return "%d day%s ago" % (-days, "" if days == -1 else "s")
    if days == 0:
        return "today"
    return "in %d day%s" % (days, "" if days == 1 else "s")


# --------------------------------------------------------------------------- #
# What is missing: a pure read
# --------------------------------------------------------------------------- #
class Questions(list):
    """The questions, as a plain list of Question. `notes` holds the plain lines for what could
    not be asked about (a settings file that cannot be used, or that is absent where something
    would have been asked of it). It is a read: nothing is changed and no terminal is needed."""

    def __init__(self, items=(), notes=()):
        list.__init__(self, items)
        self.notes = list(notes)


def missing_values(update_conf, bridge_conf, kit=KIT, today=None):
    """The questions for every value that is still missing: the name first, then each key's date.

    `update_conf` is the health check's settings file and `bridge_conf` the tech lead's; either may
    be absent. A file absent on a Mac that does not run its part asks nothing and says nothing."""
    today = today or utc_today()
    notes, found = [], []
    for what, read in (("what to call your tech lead", lambda: _name_questions(bridge_conf, notes)),
                       ("any key's end date", lambda: _date_questions(update_conf, kit, today, notes))):
        try:
            found += read()
        except Exception as exc:       # a surprise is said, and the other question is still asked
            notes.append("I could not work out %s (%s), so I did not ask." % (what, type(exc).__name__))
    return Questions(found, notes)


def _name_questions(bridge_conf, notes):
    if not os.path.isfile(bridge_conf):
        return []            # no bridge.conf: this Mac does not run the tech lead; nothing to ask
    bs = _bridge()
    name = label(bridge_conf)
    try:
        with open(bridge_conf, encoding="utf-8") as fh:
            values, errors = bs.parse_conf(fh.read())
    except (OSError, ValueError) as exc:
        notes.append("%s could not be read (%s), so I did not ask what to call your tech lead."
                     % (name, type(exc).__name__))
        return []
    if errors:
        notes.append("%s has %d problem%s, so I did not ask what to call your tech lead. "
                     "`python3 scripts/pipeline_bridge_setup.py verify` says what."
                     % (name, len(errors), "" if len(errors) == 1 else "s"))
        return []
    if bs.names_its_lead(values):
        return []
    return [Question("name", bridge_conf, NAME_KEY, "Your tech lead's name.",
                     "%s in %s. Change that line any time to rename it." % (NAME_KEY, name),
                     None, None, None)]


def _date_questions(update_conf, kit, today, notes):
    pw, pu = _watch(), _update()
    name = label(update_conf)
    try:
        keys = pw.listed_keys(kit)
    except pw.PartsUnreadable:
        notes.append("The parts list could not be read, so I did not ask for any key's end date.")
        return []
    needing = [k["clock"] for k in keys if k["ends"] in pw.ENDS_NEEDING_A_DATE]
    if not os.path.isfile(update_conf):
        # Absent, and the keys that end are stored on this Mac: the dates would have been asked
        # of it. Say so once. With no such key there is nothing to say.
        if needing:
            here = os.path.dirname(os.path.abspath(update_conf))
            notes.append("No %s in %s, so I did not ask for the end dates of %d stored key%s (%s). "
                         "Create it from update.conf.example to record them, then run this again."
                         % (os.path.basename(update_conf), "the kit folder" if here == KIT else here,
                            len(needing), "" if len(needing) == 1 else "s", ", ".join(needing)))
        return []
    try:
        conf = pu.load_conf(update_conf)
        clocks, problems = pu.credential_clocks(conf.get("CREDENTIAL_EXPIRIES"))
    except (pu.ConfError, ValueError):
        problems, clocks, conf = ["unreadable"], [], {}
    if problems:
        notes.append("%s has a problem, so I did not ask for any key's end date. "
                     "`python3 scripts/pipeline_watch.py --conf update.conf dates` says what."
                     % name)
        return []
    warn = int(conf.get("CREDENTIAL_WARN_DAYS") or 30)
    rows, _code = pw.date_rows(keys, clocks, today, warn)
    by_clock = dict((k["clock"], k) for k in keys)
    out = []
    for clock, what, when, days, date_from in rows:
        key = by_clock.get(clock)
        if key is None:
            continue         # a name the parts list does not know: kept as written, never asked
        near = days is not None and days <= warn
        if when is None and key["ends"] in pw.ENDS_NEEDING_A_DATE:
            ask = "When does this key end? %s" % what
        elif near:
            ask = "This key's end date is close or past. When does it end now? %s" % what
        else:
            continue
        out.append(Question("date", update_conf, clock, ask, date_from,
                            when.isoformat() if when else None, key["ends"], days))
    return out


# --------------------------------------------------------------------------- #
# Asking
# --------------------------------------------------------------------------- #
def read_date(answer, ends, today):
    """(end date, the day the key was made or None, None) for a usable answer; else (None, None, why).

    The answer is YYYY-MM-DD, or for a `one-year` key `made YYYY-MM-DD`. The reason never repeats
    the answer."""
    if len(answer) > ANSWER_MAX:
        return None, None, TOO_LONG % "A date is 10 characters, like 2027-03-15."
    text = " ".join(answer.split())
    made_on = None
    m = DATE_RE.match(text)
    if m:
        end = real_date(*[int(g) for g in m.groups()])
        if end is None:
            return None, None, NO_SUCH_DAY
    else:
        m = MADE_RE.match(text)
        if not m:
            return None, None, NOT_A_DATE
        if ends != "one-year":
            return None, None, NO_MADE
        made_on = real_date(*[int(g) for g in m.groups()])
        if made_on is None:
            return None, None, NO_SUCH_DAY
        end = add_years(made_on, 1)
        if end is None:
            return None, None, TOO_FAR
    if end > (add_years(today, YEARS_AHEAD_MAX) or datetime.date.max):
        return None, None, TOO_FAR
    return end, made_on, None


def read_name(answer, current):
    """(name, None) for a usable answer; else (None, why). The caller has handled an empty answer.
    The reason never repeats the answer."""
    if len(answer) > ANSWER_MAX:
        return None, TOO_LONG % "A name is 1 to 35 characters."
    bs = _bridge()
    name = answer.strip()
    problem = bs.lead_name_problem(name)
    if problem:
        return None, "Slack would not take that as a name: %s." % problem
    # The installer's own reader refuses a value shaped like a credential; so does this.
    if bs.parse_conf("%s=%s\n" % (NAME_KEY, name))[1]:
        return None, "That looks like a key or a token, so I did not use it. A name is plain words."
    return name, None


def on_record(q):
    if q.current is None:
        return "nothing yet."
    words = days_words(q.days)
    return "%s%s." % (q.current, ", ends %s" % words if q.kind == "date" and words else "")


def prompt_for(q):
    if q.kind == "name":
        bs = _bridge()
        if q.current:
            return "Your tech lead's name, or press Enter to keep %s: " % q.current
        return bs.NAME_QUESTION % bs.DEFAULT_LEAD_NAME
    shape = 'YYYY-MM-DD, or "made YYYY-MM-DD"' if q.ends == "one-year" else "YYYY-MM-DD"
    return "End date as %s, or press Enter to %s: " % (shape, "keep it" if q.current else "skip")


def ask_one(io, q, n, total, today):
    """Ask one question. ('change', Change) · ('kept', None) · ('skipped', why) · ('ended', None)."""
    io.say("")
    io.say("%d of %d. %s" % (n, total, q.question))
    io.say("   %s: %s" % ("Where it is kept" if q.kind == "name" else "Where to find it", q.where))
    io.say("   On record: %s" % on_record(q))
    prompt = prompt_for(q)
    for _try in range(ASK_TRIES):
        answer = io.line(prompt)
        if answer is None:
            io.say("   No answer came, because the input ended. Nothing is written for this one.")
            return "ended", None
        if answer.strip() == "":
            if q.current:
                io.say("   Kept: %s." % q.current)
                return "kept", None
            if q.kind == "date":
                io.say("   Skipped. I will ask again next time.")
                return "skipped", "no answer"
            answer = _bridge().DEFAULT_LEAD_NAME          # a name: Enter takes the default
        if q.kind == "date":
            end, made_on, why = read_date(answer, q.ends, today)
            if why is None:
                new = end.isoformat()
                if new == q.current:
                    io.say("   That is the date already on record, so nothing changes.")
                    return "kept", None
                past = end < today
                if made_on:
                    io.say("   Made on %s, so it ends %s.%s" % (made_on.isoformat(), new,
                                                               " That is already past." if past else ""))
                else:
                    io.say("   Noted: it ends %s.%s" % (new, " That is already past." if past else ""))
                return "change", Change("date", q.file, q.key, q.current, new, past)
        else:
            new, why = read_name(answer, q.current)
            if why is None:
                if new == q.current:
                    io.say("   That is the name already on record, so nothing changes.")
                    return "kept", None
                io.say("   Noted: your tech lead is called %s." % new)
                return "change", Change("name", q.file, q.key, q.current, new, False)
        io.say("   " + why)
    io.say("   Skipped: no usable answer after %d tries. Nothing is written for this one, and I "
           "will ask again next time." % ASK_TRIES)
    return "skipped", "tries"


def ask_all(io, questions):
    """Ask every question once, in order. ([Change], [(Question, why)]). A skipped question
    changes nothing. When the input ends, the rest are skipped without being read."""
    changes, skipped = [], []
    today = io.today()
    ended = False
    for n, q in enumerate(questions, 1):
        if ended:
            skipped.append((q, "the input ended"))
            continue
        what, got = ask_one(io, q, n, len(questions), today)
        if what == "change":
            changes.append(got)
        elif what == "ended":
            ended = True
            skipped.append((q, "the input ended"))
        else:
            skipped.append((q, got or "kept"))
    return changes, skipped


def settings_lines(changes):
    """The consent page's section for what will be written: each change as its file, key, old value
    and new value. Names and dates only. Empty when there is nothing to write."""
    if not changes:
        return []
    lines = ["Settings I will write, after you agree:"]
    for c in changes:
        lines.append("  - %s: %s: %s -> %s%s" % (label(c.file), c.key, c.old or "none", c.new,
                                                 " (already past)" if c.past else ""))
    lines.append("Names and dates only. Every other line of those files stays as it is.")
    return lines


# --------------------------------------------------------------------------- #
# Writing, then reading back
# --------------------------------------------------------------------------- #
class DatesEnv(object):
    """What `dates --apply` reads of its surroundings: the process environment (for its own refusal
    inside a Claude session) and the kit folder, and nothing else."""

    def __init__(self, environ, kit):
        self.environ = environ
        self.kit = kit


def write_dates(io, path, items, kit):
    """None when the dates are written and read back; else why not, naming the file."""
    pw, pu = _watch(), _update()
    name = label(path)
    try:
        on_file = dict(pu.credential_clocks(pu.load_conf(path).get("CREDENTIAL_EXPIRIES"))[0])
    except (pu.ConfError, ValueError):
        return "%s has a problem now, so nothing was written to it" % name
    for c in items:
        was = on_file.get(c.key)
        if (was.isoformat() if was else None) != c.old:
            return ("%s changed while you were answering (the date for %s is not the one shown), "
                    "so nothing was written to it" % (name, c.key))
    text = "".join("%s:%s\n" % (c.key, c.new) for c in items)
    said = io_module.StringIO()
    with contextlib.redirect_stdout(said):
        code = pw.cmd_dates(DatesEnv(io.env(), kit), path, True, io_module.StringIO(text))
    if code == 0:
        io.say("Wrote %d end date%s to %s and read %s back: they match."
               % (len(items), "" if len(items) == 1 else "s", name,
                  "it" if len(items) == 1 else "them"))
        return None
    return "%s: %s" % (name, " ".join(said.getvalue().split())[:300] or "exit %d" % code)


def write_name(io, path, change):
    """None when the name is written and read back; else why not, naming the file. The whole file is
    swapped in: a temp file beside it, made new and never through a link, with the old mode."""
    bs = _bridge()
    name = label(path)
    try:
        with open(path, encoding="utf-8") as fh:
            old = fh.read()
        mode = os.stat(path).st_mode & 0o777
    except (OSError, ValueError) as exc:
        return "%s could not be read (%s), so nothing was written to it" % (name, type(exc).__name__)
    values, errors = bs.parse_conf(old)
    if errors or bs.names_its_lead(values) or (values.get(NAME_KEY) or None) != change.old:
        return "%s changed while you were answering, so nothing was written to it" % name
    new = bs.set_conf_line(old, NAME_KEY, change.new)
    tmp = "%s.name.%d" % (path, os.getpid())
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    except OSError as exc:
        return "%s could not be written (%s); it is as it was" % (name, type(exc).__name__)
    swapped = False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            os.fchmod(fh.fileno(), mode)
            fh.write(new)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        swapped = True
    except OSError as exc:
        return "%s could not be written (%s); it is as it was" % (name, type(exc).__name__)
    finally:
        if not swapped:                         # a failure or Ctrl-C: the temp file this call made,
            try:                                # and nothing else, goes
                os.remove(tmp)
            except OSError:
                pass
    try:
        with open(path, encoding="utf-8") as fh:
            back = fh.read()
    except (OSError, ValueError) as exc:
        return "%s was written but could not be read back (%s); check it by hand" % (
            name, type(exc).__name__)
    back_values, back_errors = bs.parse_conf(back)
    if back != new or back_errors or back_values.get(NAME_KEY) != change.new:
        return "%s was written but reads back differently; check it by hand" % name
    io.say("Wrote %s in %s and read it back: it matches." % (NAME_KEY, name))
    return None


def write_answers(io, changes, kit=KIT):
    """Write every change, then read each file back. None when all of it is written and read back;
    else one plain sentence naming each file that was not. A file that fails is left as it was
    (or, if it reads back wrong, named); the others are still written."""
    failed = []
    date_files = []
    for c in changes:
        if c.kind == "date" and c.file not in date_files:
            date_files.append(c.file)
    jobs = [(c.file, lambda c=c: write_name(io, c.file, c)) for c in changes if c.kind == "name"]
    jobs += [(path, lambda path=path: write_dates(
        io, path, [c for c in changes if c.kind == "date" and c.file == path], kit))
        for path in date_files]
    for path, write in jobs:
        try:
            why = write()
        except Exception as exc:       # a surprise stops the run in words, naming the file
            why = "%s could not be written (%s)" % (label(path), type(exc).__name__)
        if why:
            failed.append(why)
    return "; ".join(failed) or None


# --------------------------------------------------------------------------- #
# Selftest: the machine stood in for. Real settings files in a folder of its own, the one command's
# own scripted terminal (pipeline_install.FakeIo), and a parts list made here.
# --------------------------------------------------------------------------- #
def run_cases(check):
    import json
    import shutil
    import subprocess
    import tempfile
    import pipeline_install as pi
    pw, pu, bs = _watch(), _update(), _bridge()
    from pipeline_dispatch_local import AGENT_ENV_MARKERS

    @contextlib.contextmanager
    def patched(obj, attr, value):
        old = getattr(obj, attr)
        setattr(obj, attr, value)
        try:
            yield
        finally:
            setattr(obj, attr, old)

    TODAY = datetime.date(2026, 10, 3)               # the day pipeline_install.FakeIo's clock says
    TOKEN = "ghp_" + "A1b2C3d4E5" * 3                # shaped like a credential; built here, not typed
    tmp = tempfile.mkdtemp(prefix="pipeline-ask-selftest-")
    kit = os.path.join(tmp, "kit")
    os.makedirs(kit)

    def on(days):
        return (TODAY + datetime.timedelta(days=days)).isoformat()

    def put(path, text, mode=0o640):
        if os.path.lexists(path):
            os.remove(path)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.chmod(path, mode)

    def get(path):
        try:
            with open(path, "rb") as fh:
                return fh.read()
        except OSError:
            return None

    def secret(key, name, ends, clock=None):
        s = {"key": key, "default_name": name, "what": "The %s key." % name.lower(), "ends": ends}
        if clock:
            s.update(clock=clock, date_from="Read it at the %s page." % clock)
        return s
    gh_tok = secret("GITHUB_TOKEN_ENV", "GH_TOKEN", "set-when-made", "gh-tok")
    parts_doc = {"schema": "pipeline-parts/1", "parts": [
        {"id": "p1", "settings": {"file": "a.conf"}, "secrets": [
            gh_tok, secret("TURN_TOKEN_ENV", "TURN_TOKEN", "one-year", "claude-tok"),
            secret("APP_TOKEN_ENV", "APP_TOKEN", "unknown", "unk-tok"),
            secret("LINEAR_KEY_ENV", "TRACKER_KEY", "never")]},
        {"id": "p2", "settings": {"file": "b.conf"}, "secrets": [gh_tok]},
        {"id": "p3", "settings": {"file": "c.conf"},
         "secrets": [secret("X_ENV", "OFF_TOKEN", "one-year", "off-tok")]},
        {"id": "p4", "settings": {"file": None}, "secrets": []}]}
    put(os.path.join(kit, "pipeline-parts.json"), json.dumps(parts_doc))
    for name in ("a.conf", "b.conf"):                # present: those parts are on. c.conf is absent.
        put(os.path.join(kit, name), "")
    kit2 = os.path.join(tmp, "kit-unknown-only")     # a Mac whose only stored key has no known end
    os.makedirs(kit2)
    put(os.path.join(kit2, "pipeline-parts.json"), json.dumps({"schema": "pipeline-parts/1", "parts": [
        {"id": "p1", "settings": {"file": "a.conf"},
         "secrets": [secret("APP_TOKEN_ENV", "APP_TOKEN", "unknown", "unk-tok")]}]}))
    put(os.path.join(kit2, "a.conf"), "")

    update_conf = os.path.join(kit, "update.conf")
    bridge_conf = os.path.join(kit, "bridge.conf")
    UPDATE_HEAD = "# my settings\nWATCH_INTERVAL_SECONDS=3600\n"
    BRIDGE_HEAD = "# the tech lead\nACT=no\nINTERVAL_SECONDS=60\n"

    def put_update(expiries=None, warn=None, mode=0o640):
        text = UPDATE_HEAD
        if expiries is not None:
            text += "CREDENTIAL_EXPIRIES=%s\n" % expiries
        if warn is not None:
            text += "CREDENTIAL_WARN_DAYS=%s\n" % warn
        put(update_conf, text + "# end of file\n", mode)

    def put_bridge(body="", mode=0o600):
        put(bridge_conf, BRIDGE_HEAD + body + "# end of file\n", mode)

    def rm(path):
        if os.path.lexists(path):
            os.remove(path)

    def forget():
        rm(update_conf)
        rm(bridge_conf)

    def ask(which=None):
        return missing_values(update_conf, bridge_conf, which or kit, TODAY)

    def keys_of(found):
        return [(q.kind, q.key) for q in found]

    FAR = "gh-tok:%s,claude-tok:%s" % (on(200), on(300))      # both dates set, nowhere near their end

    def surprise(*args, **kw):
        raise RuntimeError("not an OSError")

    def contained_call(fn):
        try:
            return fn()
        except Exception as exc:               # got out of the call: say so, do not crash
            return "ESCAPED " + type(exc).__name__

    # A. WHAT IS MISSING (point 1): a pure read of the two settings files.
    forget()
    found = ask()
    check("kit735-absent-files-ask-nothing", list(found), [])
    note = (found.notes or [""])[0]
    check("kit735-absent-update-conf-says-so-once-when-keys-end",
          (len(found.notes), "No update.conf in " in note, "gh-tok" in note, "claude-tok" in note),
          (1, True, True, True))
    check("kit735-absent-update-conf-does-not-name-a-key-that-has-no-known-end", "unk-tok" in note, False)
    check("kit735-absent-bridge-conf-says-nothing",
          [n for n in found.notes if "bridge" in n], [])
    check("kit735-absent-and-nothing-would-be-asked-says-nothing", ask(kit2).notes, [])
    put_bridge("APP_NAME=Rex\n")
    check("kit735-absent-update-conf-still-says-so-with-a-bridge-conf", len(ask().notes), 1)
    forget()
    put_update()
    found = ask()
    check("kit735-a-missing-date-is-asked-for-each-key-that-ends",
          [(q.kind, q.key, q.ends, q.current, q.days) for q in found],
          [("date", "gh-tok", "set-when-made", None, None),
           ("date", "claude-tok", "one-year", None, None)])
    check("kit735-a-question-carries-its-file-its-words-and-where-to-find-the-date",
          (found[0].file, found[0].where, "The gh_token key." in found[0].question) if found else None,
          (update_conf, "Read it at the gh-tok page.", True))
    check("kit735-unknown-end-missing-is-not-asked", "unk-tok" in [q.key for q in found], False)
    check("kit735-a-key-that-never-ends-is-not-asked", "tracker" in " ".join(q.key for q in found), False)
    check("kit735-a-part-that-is-off-asks-nothing", "off-tok" in [q.key for q in found], False)
    check("kit735-a-file-that-exists-gives-no-note", found.notes, [])
    put_update(expiries=FAR)
    check("kit735-a-second-run-with-nothing-missing-asks-nothing", list(ask()), [])
    put_update(expiries="gh-tok:%s,claude-tok:%s,unk-tok:%s" % (on(200), on(300), on(30)))
    found = ask()
    check("kit735-date-in-the-warn-window-is-asked-again",
          [(q.key, q.current, q.days, q.ends) for q in found], [("unk-tok", on(30), 30, "unknown")])
    put_update(expiries="gh-tok:%s,claude-tok:%s,unk-tok:%s" % (on(200), on(300), on(31)))
    check("kit735-date-one-day-past-the-warn-window-is-not-asked", list(ask()), [])
    put_update(expiries="gh-tok:%s,claude-tok:%s" % (on(-2), on(300)))
    check("kit735-a-date-already-past-is-asked-again",
          [(q.key, q.days) for q in ask()], [("gh-tok", -2)])
    put_update(expiries="gh-tok:%s,claude-tok:%s,old-key:%s" % (on(200), on(300), on(-5)))
    check("kit735-a-name-the-parts-list-does-not-know-is-never-asked", list(ask()), [])
    put_update(expiries="gh-tok:%s,claude-tok:%s" % (on(45), on(300)), warn="30")
    check("kit735-warn-days-30-leaves-a-date-45-days-out", list(ask()), [])
    put_update(expiries="gh-tok:%s,claude-tok:%s" % (on(45), on(300)), warn="60")
    check("kit735-warn-days-60-asks-for-a-date-45-days-out", keys_of(ask()), [("date", "gh-tok")])
    put_update(expiries="not-a-date")
    found = ask()
    check("kit735-a-conf-that-cannot-be-read-asks-no-date-and-says-so",
          (list(found), len(found.notes), "update.conf" in (found.notes or [""])[0]), ([], 1, True))
    put(update_conf, "DISPATCHER_SERVICE=com.example.dispatcher\nROLE_ACCOUNT=_exdispatch\n"
                     "DISPATCHER_ENV_FILE=/opt/example-dispatcher/.env\n")
    check("kit735-an-unedited-example-conf-asks-no-date-and-says-so", (list(ask()), len(ask().notes)), ([], 1))
    put(os.path.join(kit, "pipeline-parts.json"), "{")
    found = ask()
    check("kit735-an-unreadable-parts-list-says-so-and-asks-nothing",
          (list(found), len(found.notes)), ([], 1))
    put(os.path.join(kit, "pipeline-parts.json"), json.dumps(parts_doc))
    # the name
    forget()
    put_bridge()
    found = ask()
    check("kit735-a-tech-lead-with-no-name-is-asked-for-one",
          [(q.kind, q.file, q.key, q.current, q.ends) for q in found],
          [("name", bridge_conf, "APP_NAME", None, None)])
    for body, label_ in (("APP_NAME=Rex\n", "app-name"), ("PROJECT_NAME=Proj\n", "project-name")):
        put_bridge(body)
        check("kit735-a-bridge-conf-that-names-its-lead-asks-no-name:%s" % label_, list(ask()), [])
    put_bridge("APP_NAME=\n")
    check("kit735-an-empty-app-name-is-asked", keys_of(ask()), [("name", "APP_NAME")])
    put_bridge("BOGUS=1\n")
    found = ask()
    check("kit735-a-bridge-conf-with-a-problem-asks-no-name-and-says-so",
          (list(found), len([n for n in found.notes if "bridge" in n])), ([], 1))
    if os.getuid() != 0:
        put_bridge()
        os.chmod(bridge_conf, 0)
        found = ask()
        check("kit735-a-bridge-conf-that-cannot-be-opened-asks-no-name-and-says-so",
              (list(found), len([n for n in found.notes if "bridge" in n])), ([], 1))
        os.chmod(bridge_conf, 0o600)
    put_bridge()
    put_update()
    found = ask()
    check("kit735-the-name-is-asked-first-then-each-date",
          keys_of(found), [("name", "APP_NAME"), ("date", "gh-tok"), ("date", "claude-tok")])
    # PURE: no byte, mode, time or file changes, and no terminal is needed
    folder = sorted(os.listdir(kit))
    before = [(get(p), os.stat(p).st_mtime_ns, os.stat(p).st_mode) for p in (update_conf, bridge_conf)]
    saved_stdin = sys.stdin
    with open(os.devnull) as nothing:
        sys.stdin = nothing
        try:
            ask()
        finally:
            sys.stdin = saved_stdin
    after = [(get(p), os.stat(p).st_mtime_ns, os.stat(p).st_mode) for p in (update_conf, bridge_conf)]
    check("kit735-reading-what-is-missing-changes-nothing-and-needs-no-terminal",
          (before, sorted(os.listdir(kit))), (after, folder))

    # a surprise while reading is said in a line; the other question is still asked
    put_update()
    put_bridge()
    with patched(bs, "parse_conf", surprise):
        found = contained_call(ask)
    check("kit735-a-surprise-while-reading-the-bridge-conf-is-said-and-the-dates-are-still-asked",
          (keys_of(found), len(found.notes), "RuntimeError" in (found.notes or [""])[0])
          if not isinstance(found, str) else found,
          ([("date", "gh-tok"), ("date", "claude-tok")], 1, True))
    with patched(pw, "listed_keys", surprise):
        found = contained_call(ask)
    check("kit735-a-surprise-while-reading-the-dates-is-said-and-the-name-is-still-asked",
          (keys_of(found), len(found.notes), "RuntimeError" in (found.notes or [""])[0])
          if not isinstance(found, str) else found,
          ([("name", "APP_NAME")], 1, True))

    # B. HOW IT ASKS (point 2): one plain question at a time, on screen, with a bounded number of tries.
    def dq(clock="gh-tok", ends="set-when-made", current=None, days=None):
        return Question("date", update_conf, clock, "When does this key end? The %s key." % clock,
                        "Read it at the %s page." % clock, current, ends, days)
    nq = Question("name", bridge_conf, "APP_NAME", "Your tech lead's name.",
                  "APP_NAME in bridge.conf. Change that line any time to rename it.", None, None, None)

    def asking(questions, typed):
        io = pi.FakeIo(typed=typed)
        changes, skipped = ask_all(io, questions)
        return changes, skipped, io

    def said(io):
        return "\n".join(io.lines)

    changes, skipped, io = asking([nq], [""])
    check("kit735-enter-takes-the-default-name-when-none-is-on-record",
          [(c.kind, c.key, c.old, c.new) for c in changes], [("name", "APP_NAME", None, bs.DEFAULT_LEAD_NAME)])
    check("kit735-the-name-question-is-the-tech-lead-installers-own-words",
          io.line_log[0][0], bs.NAME_QUESTION % bs.DEFAULT_LEAD_NAME)
    changes, _s, _io = asking([nq], ["  Rex  "])
    check("kit735-a-typed-name-is-taken-trimmed", [c.new for c in changes], ["Rex"])
    changes, skipped, io = asking([dq()], [""])
    check("kit735-enter-skips-a-missing-date",
          (changes, [(q.key, why) for q, why in skipped]), ([], [("gh-tok", "no answer")]))
    changes, skipped, io = asking([dq(current=on(10), days=10)], [""])
    check("kit735-enter-keeps-the-date-on-record",
          (changes, [why for _q, why in skipped], "Kept: %s." % on(10) in said(io)), ([], ["kept"], True))
    changes, skipped, io = asking([dq(current=on(10), days=10)], [on(10)])
    check("kit735-typing-the-date-already-on-record-changes-nothing",
          (changes, [why for _q, why in skipped]), ([], ["kept"]))
    old_name_q = nq._replace(current="Old")
    changes, skipped, io = asking([old_name_q], [""])
    check("kit735-enter-takes-the-default-name-only-when-nothing-is-on-record",
          (changes, [why for _q, why in skipped]), ([], ["kept"]))
    changes, _s, io = asking([dq()], ["2027-02-30", "2027-03-15"])
    check("kit735-a-bad-date-is-refused-then-the-second-try-is-taken",
          ([(c.old, c.new, c.past) for c in changes], len(io.line_log), "no such day" in said(io)),
          ([(None, "2027-03-15", False)], 2, True))
    check("kit735-a-refused-answer-is-never-repeated", "2027-02-30" in said(io), False)
    changes, _s, io = asking([dq()], ["x", "y", "2027-03-15"])
    check("kit735-the-third-try-is-still-taken",
          ([c.new for c in changes], len(io.line_log)), (["2027-03-15"], 3))
    changes, skipped, io = asking([dq(), dq("claude-tok", "one-year")], ["x", "y", "z", "2027-03-15"])
    check("kit735-three-bad-tries-skip-the-question-and-the-fourth-answer-goes-to-the-next",
          ([(c.key, c.new) for c in changes], [(q.key, why) for q, why in skipped], len(io.line_log)),
          ([("claude-tok", "2027-03-15")], [("gh-tok", "tries")], 4))
    check("kit735-a-skip-after-three-tries-says-so", "after 3 tries" in said(io), True)
    changes, _s, io = asking([dq()], ["2037-03-15", "2027-03-15"])
    check("kit735-a-date-years-away-is-refused-as-a-likely-typo",
          ([c.new for c in changes], "more than 3 years away" in said(io)), (["2027-03-15"], True))
    limit = on((datetime.date(2029, 10, 3) - TODAY).days)
    after_limit = on((datetime.date(2029, 10, 4) - TODAY).days)
    changes, _s, io = asking([dq()], [limit])
    check("kit735-exactly-three-years-ahead-is-taken", [c.new for c in changes], ["2029-10-03"])
    changes, _s, io = asking([dq()], [after_limit, "2027-01-01"])
    check("kit735-a-day-past-three-years-ahead-is-refused",
          ([c.new for c in changes], "more than 3 years away" in said(io)), (["2027-01-01"], True))
    changes, _s, io = asking([dq("claude-tok", "one-year")], ["made 2026-03-15"])
    check("kit735-made-plus-a-year-is-the-end-date-and-it-is-shown-back",
          ([c.new for c in changes], "so it ends 2027-03-15" in said(io)), (["2027-03-15"], True))
    changes, _s, io = asking([dq("claude-tok", "one-year")], ["MADE  2026-03-15"])
    check("kit735-made-is-read-in-any-case-with-any-spaces", [c.new for c in changes], ["2027-03-15"])
    changes, _s, io = asking([dq("claude-tok", "one-year")], ["made 2028-02-29"])
    check("kit735-made-on-29-february-ends-on-28-february-of-the-next-year",
          [c.new for c in changes], ["2029-02-28"])
    changes, _s, io = asking([dq("claude-tok", "one-year")], ["made 2027-02-29", "2027-04-01"])
    check("kit735-made-on-a-day-that-does-not-exist-is-refused",
          ([c.new for c in changes], "no such day" in said(io)), (["2027-04-01"], True))
    changes, _s, io = asking([dq("gh-tok", "set-when-made")], ["made 2026-03-15", "2027-04-01"])
    check("kit735-made-is-refused-for-a-key-whose-date-is-chosen-when-it-is-made",
          ([c.new for c in changes], "set when it is made" in said(io)), (["2027-04-01"], True))
    check("kit735-the-prompt-offers-made-only-for-a-key-that-ends-in-a-year",
          ("made" in prompt_for(dq("claude-tok", "one-year")), "made" in prompt_for(dq())), (True, False))
    changes, _s, io = asking([dq("claude-tok", "one-year")], ["made 2024-02-29"])
    check("kit735-a-date-already-past-is-taken-and-marked",
          ([(c.new, c.past) for c in changes], "already past" in said(io)), ([("2025-02-28", True)], True))
    changes, _s, io = asking([dq()], ["2025-01-01"])
    check("kit735-a-plain-date-already-past-is-taken-and-marked",
          [(c.new, c.past) for c in changes], [("2025-01-01", True)])
    changes, _s, io = asking([dq()], [on(0)])
    check("kit735-todays-date-is-not-past", [(c.new, c.past) for c in changes], [(on(0), False)])
    for answer, label_ in (("x" * 81, "81-characters"), (TOKEN * 3, "token-length")):
        changes, _s, io = asking([dq()], [answer, "2027-03-15"])
        check("kit735-an-answer-over-80-characters-is-refused-and-never-repeated:%s" % label_,
              ([c.new for c in changes], "longer than 80" in said(io), answer in said(io)),
              (["2027-03-15"], True, False))
    changes, _s, io = asking([dq()], [TOKEN, "2027-03-15"])
    check("kit735-a-token-typed-as-a-date-is-refused-and-never-repeated",
          ([c.new for c in changes], TOKEN in said(io)), (["2027-03-15"], False))
    changes, _s, io = asking([nq], [TOKEN, "Rex"])
    check("kit735-a-token-typed-as-a-name-is-refused-and-never-repeated",
          ([c.new for c in changes], TOKEN in said(io), "looks like a key" in said(io)),
          (["Rex"], False, True))
    changes, _s, io = asking([nq], ["Rex!", "Rex"])
    check("kit735-a-name-with-a-bad-character-is-refused-then-the-second-try-is-taken",
          ([c.new for c in changes], "Slack would not take that as a name" in said(io),
           "Rex!" in said(io)), (["Rex"], True, False))
    changes, skipped, io = asking([nq], ["!", "?", "#"])
    check("kit735-three-bad-names-skip-the-question",
          (changes, [why for _q, why in skipped], len(io.line_log)), ([], ["tries"], 3))
    changes, _s, io = asking([nq], ["a" * 36, "Rex"])
    check("kit735-a-name-over-35-characters-is-refused", [c.new for c in changes], ["Rex"])
    changes, _s, io = asking([nq], ["x" * 81, "Rex"])
    check("kit735-a-name-over-80-characters-is-refused-and-never-repeated",
          ([c.new for c in changes], "longer than 80" in said(io), "x" * 81 in said(io)),
          (["Rex"], True, False))
    changes, skipped, io = asking([dq(), dq("claude-tok", "one-year"), nq], [])
    check("kit735-input-that-ends-skips-the-rest-without-reading-them",
          (changes, [why for _q, why in skipped], len(io.line_log)),
          ([], ["the input ended"] * 3, 1))
    changes, skipped, io = asking([nq, dq(), dq("claude-tok", "one-year")], ["Rex", "2027-05-06", "2027-06-07"])
    check("kit735-each-value-is-asked-once",
          (len(io.line_log), [c.key for c in changes]), (3, ["APP_NAME", "gh-tok", "claude-tok"]))
    page = said(io)
    check("kit735-each-question-shows-its-words-where-to-find-it-and-what-is-on-record",
          ("1 of 3. Your tech lead's name." in page, "2 of 3. When does this key end?" in page,
           page.count("Where to find it:") + page.count("Where it is kept:"),
           page.count("On record: nothing yet.")), (True, True, 3, 3))
    changes, _s, io = asking([dq(current=on(10), days=10)], [on(300)])
    check("kit735-the-days-left-on-a-date-on-record-are-shown", "ends in 10 days" in said(io), True)
    check("kit735-an-end-in-the-past-is-said-in-days-ago",
          (days_words(-3), days_words(-1), days_words(0), days_words(1)),
          ("3 days ago", "1 day ago", "today", "in 1 day"))
    check("kit735-the-list-of-settings-shows-file-key-old-and-new",
          settings_lines([Change("date", update_conf, "gh-tok", on(10), "2027-06-01", False),
                          Change("date", update_conf, "claude-tok", None, "2025-01-01", True),
                          Change("name", bridge_conf, "APP_NAME", None, "Rex", False)]),
          ["Settings I will write, after you agree:",
           "  - %s: gh-tok: %s -> 2027-06-01" % (update_conf, on(10)),
           "  - %s: claude-tok: none -> 2025-01-01 (already past)" % update_conf,
           "  - %s: APP_NAME: none -> Rex" % bridge_conf,
           "Names and dates only. Every other line of those files stays as it is."])
    check("kit735-no-changes-no-list", settings_lines([]), [])
    check("kit735-no-questions-no-output", (ask_all(pi.FakeIo(), []), pi.FakeIo().lines), (([], []), []))
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    check("kit735-nothing-here-uses-a-hidden-prompt", ("get" + "pass") in src, False)

    # C. WRITING (point 3): written, then read back; a failure names the file and leaves the old one.
    forget()
    put_update(expiries="old-key:%s" % on(250))
    put_bridge(mode=0o664)
    update_before, bridge_before = get(update_conf), get(bridge_conf)
    dates = [Change("date", update_conf, "gh-tok", None, "2027-05-06", False),
             Change("date", update_conf, "claude-tok", None, "2027-03-04", False)]
    name = Change("name", bridge_conf, "APP_NAME", None, "Rex", False)
    io = pi.FakeIo()
    took_rights = os.umask(0o077)           # a process that would take rights away from a new file
    try:
        wrote = write_answers(io, [name] + dates, kit)
    finally:
        os.umask(took_rights)
    check("kit735-write-answers-writes-the-name-and-the-dates", wrote, None)
    want_expiries = "old-key:%s,gh-tok:2027-05-06,claude-tok:2027-03-04" % on(250)
    check("kit735-dates-go-through-the-dates-engine-keeping-every-other-entry-and-byte",
          get(update_conf), update_before.replace(("old-key:%s" % on(250)).encode(), want_expiries.encode()))
    check("kit735-dates-keep-the-files-mode", oct(os.stat(update_conf).st_mode & 0o7777), "0o640")
    check("kit735-dates-are-read-back-through-the-parts-list",
          str(dict(pu.credential_clocks(pu.load_conf(update_conf)["CREDENTIAL_EXPIRIES"])[0]).get("gh-tok")),
          "2027-05-06")
    check("kit735-the-name-is-added-and-no-other-line-changes",
          get(bridge_conf), bridge_before.replace(b"# end of file\n", b"# end of file\nAPP_NAME=Rex\n"))
    check("kit735-the-name-keeps-the-files-mode", oct(os.stat(bridge_conf).st_mode & 0o7777), "0o664")
    check("kit735-writing-says-what-it-wrote-and-that-it-read-it-back",
          (sum("read it back" in ln or "read them back" in ln for ln in io.lines), len(io.lines)), (2, 2))
    check("kit735-a-write-leaves-no-temp-file-behind",
          sorted(os.listdir(kit)), sorted(["a.conf", "b.conf", "bridge.conf", "pipeline-parts.json", "update.conf"]))
    check("kit735-after-the-write-a-second-run-asks-nothing", list(ask()), [])
    # an APP_NAME line that is empty is set in place
    put_bridge("APP_NAME=\nDM_USER_IDS=\n")
    expected = bs.set_conf_line(get(bridge_conf).decode(), "APP_NAME", "Rex").encode()
    check("kit735-an-empty-name-line-is-set-in-place",
          (write_answers(pi.FakeIo(), [name], kit), get(bridge_conf)), (None, expected))
    check("kit735-nothing-to-write-is-nothing-written", write_answers(pi.FakeIo(), [], kit), None)
    # a write that fails: the file named, the old file in place, nothing left behind
    put_update(expiries="old-key:%s" % on(250))
    put_bridge()
    update_before, bridge_before = get(update_conf), get(bridge_conf)

    real_replace, real_write = os.replace, pw.write_atomic

    def broken(path, data):
        raise OSError("disk full")

    def boom(src, dst):
        raise OSError("disk full")

    def lying(path, data):
        real_write(path, data.replace(b"2027-05-06", b"2027-05-07"))
    with patched(pw, "write_atomic", broken):
        failed = write_answers(pi.FakeIo(), dates, kit)
    check("kit735-a-failed-date-write-names-the-file-and-leaves-the-old-one",
          (update_conf in (failed or ""), "FAILED" in (failed or ""), get(update_conf)),
          (True, True, update_before))
    with patched(os, "replace", boom):
        failed = write_answers(pi.FakeIo(), dates, kit)
    check("kit735-a-date-write-whose-swap-fails-leaves-the-old-file-and-no-temp-file",
          (update_conf in (failed or ""), get(update_conf),
           [n for n in os.listdir(kit) if n.startswith(".update.conf-")]), (True, update_before, []))
    with patched(pw, "write_atomic", lying):
        failed = write_answers(pi.FakeIo(), dates, kit)
    check("kit735-readback-mismatch-is-a-failure:dates",
          (update_conf in (failed or ""), "reads back differently" in (failed or "")), (True, True))
    with patched(os, "replace", boom):
        failed = write_answers(pi.FakeIo(), [name], kit)
    check("kit735-a-failed-name-write-names-the-file-and-leaves-the-old-one",
          (bridge_conf in (failed or ""), get(bridge_conf)), (True, bridge_before))
    check("kit735-a-failed-name-write-removes-only-its-own-temp-file",
          [n for n in os.listdir(kit) if ".name." in n], [])

    def tampering(src, dst):
        with open(src, "a", encoding="utf-8") as fh:
            fh.write("# tampered\n")
        real_replace(src, dst)
    put_bridge()
    with patched(os, "replace", tampering):
        failed = write_answers(pi.FakeIo(), [name], kit)
    check("kit735-readback-mismatch-is-a-failure:name",
          (bridge_conf in (failed or ""), "reads back differently" in (failed or "")), (True, True))
    put_bridge()
    bridge_before = get(bridge_conf)
    stale = "%s.name.%d" % (bridge_conf, os.getpid())
    put(stale, "someone else's file\n")
    failed = write_answers(pi.FakeIo(), [name], kit)
    check("kit735-a-temp-name-that-already-exists-is-a-failure-and-is-never-removed",
          (bridge_conf in (failed or ""), get(stale), get(bridge_conf)),
          (True, b"someone else's file\n", bridge_before))
    rm(stale)
    victim = os.path.join(tmp, "victim.txt")
    put(victim, "do not touch\n")
    os.symlink(victim, stale)
    failed = write_answers(pi.FakeIo(), [name], kit)
    check("kit735-a-temp-name-that-is-a-link-is-never-followed",
          (bridge_conf in (failed or ""), get(victim), os.path.islink(stale), get(bridge_conf)),
          (True, b"do not touch\n", True, bridge_before))
    rm(stale)
    put_bridge("APP_NAME=Other\n")
    other = get(bridge_conf)
    failed = write_answers(pi.FakeIo(), [name], kit)
    check("kit735-a-name-set-since-the-question-is-never-overwritten",
          (bridge_conf in (failed or ""), "changed while you were answering" in (failed or ""),
           get(bridge_conf)), (True, True, other))
    put_update(expiries="gh-tok:%s,claude-tok:%s" % (on(20), on(300)))
    seen_then = get(update_conf)
    failed = write_answers(pi.FakeIo(), [Change("date", update_conf, "gh-tok", on(21), "2027-06-01", False)], kit)
    check("kit735-a-date-changed-since-the-question-is-never-overwritten",
          (update_conf in (failed or ""), "changed while you were answering" in (failed or ""),
           get(update_conf)), (True, True, seen_then))
    marker = sorted(AGENT_ENV_MARKERS)[0]
    put_update(expiries="old-key:%s" % on(250))
    update_before = get(update_conf)
    failed = write_answers(pi.FakeIo(env={marker: "1"}), dates, kit)
    check("kit735-the-dates-engine-refuses-inside-a-claude-session-and-writes-nothing",
          (update_conf in (failed or ""), "REFUSED" in (failed or ""), get(update_conf)),
          (True, True, update_before))
    put_update(expiries="old-key:%s" % on(250))
    put_bridge()
    bridge_before, update_before = get(bridge_conf), get(update_conf)
    with patched(pw, "write_atomic", broken):
        io = pi.FakeIo()
        failed = write_answers(io, [name] + dates, kit)
    check("kit735-one-file-failing-does-not-stop-the-other-being-written",
          (update_conf in (failed or ""), bridge_conf in (failed or ""), b"APP_NAME=Rex" in get(bridge_conf),
           get(update_conf)), (True, False, True, update_before))

    put_update(expiries="old-key:%s" % on(250))
    put_bridge("APP_NAME=Other\n")                 # the name was set since it was asked
    bridge_before = get(bridge_conf)
    failed = write_answers(pi.FakeIo(), [name] + dates, kit)
    check("kit735-a-name-that-fails-does-not-stop-the-dates-being-written",
          (bridge_conf in (failed or ""), update_conf in (failed or ""), get(bridge_conf) == bridge_before,
           dict(pu.credential_clocks(pu.load_conf(update_conf)["CREDENTIAL_EXPIRIES"])[0]).get("gh-tok")),
          (True, False, True, datetime.date(2027, 5, 6)))

    # a surprise is a failure in words, and Ctrl-C leaves no temp file behind
    put_update(expiries="old-key:%s" % on(250))
    put_bridge()
    update_before, bridge_before = get(update_conf), get(bridge_conf)

    def interrupt(src, dst):
        raise KeyboardInterrupt
    with patched(pw, "cmd_dates", surprise):
        failed = contained_call(lambda: write_answers(pi.FakeIo(), dates, kit))
    check("kit735-a-surprise-while-writing-dates-is-a-failure-naming-the-file",
          (update_conf in (failed or ""), "RuntimeError" in (failed or ""), get(update_conf)),
          (True, True, update_before))
    with patched(bs, "set_conf_line", surprise):
        failed = contained_call(lambda: write_answers(pi.FakeIo(), [name], kit))
    check("kit735-a-surprise-while-writing-the-name-is-a-failure-naming-the-file",
          (bridge_conf in (failed or ""), "RuntimeError" in (failed or ""), get(bridge_conf)),
          (True, True, bridge_before))
    try:
        with patched(os, "replace", interrupt):
            write_answers(pi.FakeIo(), [name], kit)
        got = "returned"
    except KeyboardInterrupt:
        got = "interrupted"
    check("kit735-ctrl-c-while-writing-the-name-leaves-no-temp-file-and-the-old-file",
          (got, [n for n in os.listdir(kit) if ".name." in n], get(bridge_conf)),
          ("interrupted", [], bridge_before))

    # D. THE ONE COMMAND (point 4): ask, then consent, then write, then the steps.
    se_conf = os.path.join(tmp, "stage-e.conf")
    put(se_conf, "ROLE_ACCOUNT=_x\n")
    a_conf = os.path.join(tmp, "stage-a.conf")
    sweep_conf = os.path.join(tmp, "sweep.conf")
    base = ["--conf", a_conf, "--stage-e-conf", se_conf, "--sweep-conf", sweep_conf,
            "--update-conf", update_conf, "--bridge-conf", bridge_conf]
    healthy = {"sync_user_skills.py": 0, "pipeline_stage_e_setup.py verify": 0,
               "pipeline_stage_a_setup.py run": 0}

    def state():
        return (get(update_conf), get(bridge_conf))

    class Watching(pi.FakeIo):
        """The command's scripted terminal over this battery's own files: the real seams, and a
        look at the files at the moments that matter."""

        def __init__(self, *args, **kw):
            pi.FakeIo.__init__(self, *args, **kw)
            self.kit = kit
            self.finds = 0
            self.snaps = {}

        def missing_values(self, update_path, bridge_path):
            self.finds += 1
            return pi.Io.missing_values(self, update_path, bridge_path)

        def write_settings(self, changes):
            self.events.append("settings")
            self.snaps["at-settings"] = state()
            return pi.Io.write_settings(self, changes)

        def line(self, prompt):
            if prompt.startswith("Type yes"):
                self.snaps["at-consent"] = state()
            return pi.FakeIo.line(self, prompt)

        def run(self, argv, capture=False):
            self.snaps.setdefault("at-first-program", state())
            return pi.FakeIo.run(self, argv, capture)

    def go(typed, argv=(), cls=Watching, **kw):
        io = cls(codes=dict(healthy), typed=list(typed), **kw)
        return pi.main(base + list(argv), io=io), io

    def prompts(io):
        return [p for p, _runs, _reads in io.line_log]

    def values_asked(io):
        return [p for p in prompts(io) if not p.startswith("Type ")]

    def section(io, start, stop_):
        lines = io.lines
        first = ([i for i, ln in enumerate(lines) if ln.startswith(start)] or [0])[0]
        last = ([i for i, ln in enumerate(lines) if ln.startswith(stop_)] or [len(lines)])[0]
        return lines[first:last]

    def reset(expiries=None, warn=None, body=""):
        forget()
        put_update(expiries, warn)
        put_bridge(body)

    reset()
    original = state()
    code, io = go(["1", "Rex", "2027-05-06", "made 2026-03-15", "yes"])
    saved_record = json.loads((io.written or [("", "{}")])[0][1])
    check("kit735-the-ask-comes-after-the-first-screen-and-before-the-consent",
          [p[:12] for p in prompts(io)],
          ["Type 1 or 2,", "What do you ", "End date as ", "End date as ", "Type yes to "])
    check("kit735-the-ask-runs-before-any-program",
          [runs for p, runs, _r in io.line_log if not p.startswith("Type yes")], [0, 0, 0, 0])
    check("kit735-the-questions-are-the-name-then-each-date-that-ends",
          (code, ([c for c in prompts(io) if c.startswith("End date")] + ["", ""])[1].count("made")),
          (pi.EX_OK, 1))
    check("kit735-nothing-is-written-before-the-yes", io.snaps.get("at-consent"), original)
    check("kit735-the-yes-writes-it-and-reads-it-back",
          (get(bridge_conf).endswith(b"APP_NAME=Rex\n"),
           pu.load_conf(update_conf)["CREDENTIAL_EXPIRIES"]),
          (True, "gh-tok:2027-05-06,claude-tok:2027-03-15"))
    check("kit735-the-write-is-after-the-record-and-before-the-first-installer",
          (io.events, io.snaps.get("at-settings") != original, io.snaps.get("at-first-program") == state()),
          (["sudo", "write", "settings"], False, True))
    page = said(io)
    check("kit735-the-consent-page-lists-each-change-as-file-key-old-and-new",
          ("Settings I will write, after you agree:" in page, "APP_NAME: none -> Rex" in page,
           "gh-tok: none -> 2027-05-06" in page, "claude-tok: none -> 2027-03-15" in page),
          (True, True, True, True))
    where_at = [page.find(t) for t in ("Whatever only a person can do", "Settings I will write",
                                       "Automatic: after you agree")]
    check("kit735-the-list-sits-after-the-page-and-before-the-mode-line",
          -1 not in where_at and where_at == sorted(where_at), True)
    check("kit735-the-one-yes-covers-the-settings", [p[:8] for p in prompts(io)].count("Type yes"), 1)
    check("kit735-the-writing-is-said",
          "Writing the settings listed above." in io.lines, True)
    # the page and the record are as they were when nothing was answered
    reset(body="APP_NAME=Rex\n")
    code, io = go(["1", "", "", "yes"])
    answered_none = io
    reference = pi.FakeIo(codes=dict(healthy), typed=["1", "yes"])
    reference_code = pi.main(base, io=reference)
    check("kit735-with-nothing-answered-the-page-is-exactly-as-before",
          (section(io, "== 0c.", "== 1."), code), (section(reference, "== 0c.", "== 1."), reference_code))
    check("kit735-with-nothing-answered-the-record-is-exactly-as-before",
          (io.written or [None])[0], (reference.written or [None])[0])
    check("kit735-with-nothing-answered-nothing-is-written",
          (io.events, state()[1].endswith(b"APP_NAME=Rex\n# end of file\n")), (["sudo", "write"], True))
    reset()
    code, io = go(["1", "Rex", "2027-05-06", "2027-03-15", "yes"])
    wrote_record = json.loads((io.written or [("", "{}")])[0][1])
    check("kit735-the-record-never-holds-the-settings-section",
          ("Settings I will write" in json.dumps(wrote_record), wrote_record.get("consent"),
           wrote_record.get("settings")),
          (False, saved_record["consent"], saved_record["settings"]))
    # a declined consent writes nothing at all
    reset()
    original = state()
    code, io = go(["1", "Rex", "2027-05-06", "2027-03-15", "no"])
    check("kit735-a-declined-consent-writes-nothing-and-changes-nothing-else",
          (code, state(), io.events, io.written, io.ran), (pi.EX_BLOCKED, original, [], [], []))
    check("kit735-a-declined-consent-still-prints-the-lines",
          "Where every part stands" in said(io), True)
    # a second run asks nothing
    reset()
    code, io = go(["1", "Rex", "2027-05-06", "2027-03-15", "yes"])
    after_first = state()
    code, io = go(["1"], record=("ok", json.loads((io.written or [("", "{}")])[0][1]), ""))
    check("kit735-the-second-run-asks-nothing-and-writes-nothing",
          (values_asked(io), len(io.line_log), code, state(), io.events, io.written),
          ([], 1, pi.EX_OK, after_first, [], []))
    check("kit735-the-second-run-prints-no-asking-heading", "0b2." in said(io), False)
    # a matching record and something to write: only that list, and the yes is for it alone
    reset(expiries="gh-tok:%s,claude-tok:%s" % (on(10), on(300)), body="APP_NAME=Rex\n")
    code, io = go(["1", "2027-06-01", "yes"], record=("ok", saved_record, ""))
    page = said(io)
    check("kit735-a-matching-record-with-something-to-write-asks-a-yes-for-it-alone",
          ((prompts(io) or [""])[-1].startswith("Type yes to write these settings"), io.events, io.written, code),
          (True, ["settings"], [], pi.EX_OK))
    check("kit735-a-matching-record-shows-the-list-and-not-the-whole-page",
          ("WHAT YOU ARE AGREEING TO" in page, "gh-tok: %s -> 2027-06-01" % on(10) in page,
           "This matches what you agreed to" in page), (False, True, True))
    check("kit735-a-matching-record-with-something-to-write-writes-it-after-the-yes",
          (pu.load_conf(update_conf)["CREDENTIAL_EXPIRIES"]),
          "gh-tok:2027-06-01,claude-tok:%s" % on(300))
    reset(expiries="gh-tok:%s,claude-tok:%s" % (on(10), on(300)), body="APP_NAME=Rex\n")
    original = state()
    code, io = go(["1", "2027-06-01", "no"], record=("ok", saved_record, ""))
    check("kit735-declining-that-yes-writes-nothing", (code, state(), io.events), (pi.EX_BLOCKED, original, []))
    # nothing is written when the consent cannot be completed
    reset()
    original = state()
    io = Watching(codes=dict(healthy), typed=["1", "Rex", "2027-05-06", "2027-03-15", "yes"])
    io.sudo_ok = False
    code = pi.main(base, io=io)
    check("kit735-no-password-writes-no-settings", (code, state(), io.events, io.ran), (5, original, ["sudo"], []))
    io = Watching(codes=dict(healthy), typed=["1", "Rex", "2027-05-06", "2027-03-15", "yes"])
    io.write_failure = "disk full"
    code = pi.main(base, io=io)
    check("kit735-a-record-that-cannot-be-saved-writes-no-settings",
          (code, state(), io.events, io.ran), (1, original, ["sudo", "write"], []))
    # a failed write stops the run before any step, naming the file
    reset()
    original = state()
    with patched(pw, "write_atomic", broken):
        code, io = go(["1", "Rex", "2027-05-06", "2027-03-15", "yes"])
    check("kit735-a-failed-write-stops-the-run-before-any-step",
          (code, io.ran, get(update_conf) == original[0]), (1, [], True))
    check("kit735-a-failed-write-names-the-file-in-a-plain-line",
          any(ln.startswith("Stopped: your settings were not all written:") and update_conf in ln
              for ln in io.lines), True)
    check("kit735-a-failed-write-still-prints-the-lines", "Where every part stands" in said(io), True)
    reset(expiries=FAR)
    original = state()
    with patched(os, "replace", boom):
        code, io = go(["1", "Rex", "yes"])
    check("kit735-a-failed-name-write-stops-the-run-before-any-step",
          (code, io.ran, state(), any(bridge_conf in ln and ln.startswith("Stopped:") for ln in io.lines)),
          (1, [], original, True))
    reset(expiries=FAR)
    original = state()
    with patched(bs, "set_conf_line", surprise):
        code, io = go(["1", "Rex", "yes"])
    check("kit735-a-surprise-while-writing-stops-the-run-in-words-before-any-step",
          (code, io.ran, state(), any(bridge_conf in ln and "RuntimeError" in ln for ln in io.lines)),
          (1, [], original, True))
    # both modes ask
    for typed_mode, mode_label in (("1", "automatic"), ("2", "manual")):
        reset()
        code, io = go([typed_mode, "Rex", "2027-05-06", "2027-03-15", "yes"])
        check("kit735-it-asks-in-both-modes:%s" % mode_label,
              (len(values_asked(io)), json.loads((io.written or [("", "{}")])[0][1]).get("mode")),
              (3, mode_label))
    # not after a restart
    reset()
    original = state()
    code, io = go(["yes"], argv=["--pulled-from", "abcdef1234", "--no-pull"])
    check("kit735-no-questions-after-a-restart",
          (io.finds, values_asked(io), state(), (prompts(io) or [""])[0][:8]), (0, [], original, "Type yes"))
    code, io = go([], argv=["--pulled-from", "abcdef1234", "--no-pull", "--chosen-mode", "manual"],
                  record=("ok", dict(saved_record, mode="manual"), ""))
    check("kit735-no-questions-after-a-restart-even-with-a-matching-record", (io.finds, values_asked(io)), (0, []))
    # the refusals come first and read nothing
    for label_, kw in [("marker:%s" % m, {"env": {m: ""}}) for m in AGENT_ENV_MARKERS] + [
            ("root", {"uid": 0}), ("no-terminal", {"tty": False})]:
        code, io = go(["1"], **kw)
        check("kit735-a-refusal-asks-and-reads-nothing:%s" % label_, (io.finds, io.line_log), (0, []))
    io = Watching(codes=dict(healthy), typed=["1"])
    io.parts_list = OSError("no list")
    pi.main(base, io=io)
    check("kit735-no-parts-list-no-questions", io.finds, 0)
    # files that are absent
    reset(body="APP_NAME=Rex\n")
    os.remove(update_conf)
    code, io = go(["1", "yes"])
    notes = [ln for ln in io.lines if ln.startswith("No update.conf in ")]
    check("kit735-an-absent-update-conf-is-said-once-where-keys-end",
          (len(notes), values_asked(io), code), (1, [], pi.EX_OK))
    reset(expiries=FAR)
    os.remove(bridge_conf)
    code, io = go(["1", "yes"])
    check("kit735-an-absent-bridge-conf-asks-nothing-and-says-nothing",
          (values_asked(io), "0b2." in said(io), code), ([], False, pi.EX_OK))
    forget()
    io = Watching(codes=dict(healthy), typed=["1", "yes"])
    io.kit = kit2
    code = pi.main(base, io=io)
    check("kit735-absent-files-with-nothing-that-would-be-asked-say-nothing",
          ("0b2." in said(io), values_asked(io), code), (False, [], pi.EX_OK))
    forget()
    code, io = go(["1", "yes"])
    check("kit735-both-files-absent-says-one-line-and-never-names-the-bridge-conf",
          (sum(ln.startswith("No update.conf in ") for ln in io.lines),
           any("bridge" in ln for ln in section(io, "== 0b2.", "== 0c."))), (1, False))
    reset(body="BOGUS=1\n")
    code, io = go(["1", "2027-05-06", "2027-03-15", "yes"])
    check("kit735-a-file-with-a-problem-is-said-and-its-question-is-not-asked",
          (len(values_asked(io)), any("bridge.conf" in ln and "problem" in ln for ln in io.lines)),
          (2, True))
    # Ctrl-C while asking
    reset()
    original = state()

    class Interrupting(Watching):
        def line(self, prompt):
            if prompt.startswith("End date"):
                raise KeyboardInterrupt
            return Watching.line(self, prompt)
    try:
        code, io = go(["1", "Rex"], cls=Interrupting)
    except KeyboardInterrupt:
        code, io = "Ctrl-C got out of the command", pi.FakeIo()
    check("kit735-ctrl-c-while-asking-changes-nothing-and-prints-the-lines",
          (code, state(), io.events, io.ran, "Where every part stands" in said(io)),
          (pi.EX_BLOCKED, original, [], [], True))
    # words, flags and the parts list
    helped = subprocess.run([sys.executable, os.path.join(HERE, "pipeline_install.py"), "--help"],
                            capture_output=True, text=True).stdout
    check("kit735-the-two-file-flags-are-hidden-from-the-help",
          ("update-conf" in helped, "bridge-conf" in helped), (False, False))
    install_src = open(os.path.join(HERE, "pipeline_install.py"), encoding="utf-8").read()
    check("kit735-the-flags-default-to-the-kit-folder",
          ('os.path.join(KIT, "update.conf")' in install_src, 'os.path.join(KIT, "bridge.conf")' in install_src),
          (True, True))
    for verb in ("pr " + "merge", "--" + "approve", "--add-" + "label", "auto-" + "merge"):
        check("kit735-no-verb:%s" % verb, verb in src, False)
    check("kit735-no-hard-coded-python-path", ("/usr/bin/" + "python3") in src, False)
    real_parts = pi.Io().parts()
    one = [pt for pt in real_parts if pt["id"] == "one-command"][0]
    check("kit735-the-parts-list-owns-this-script-and-says-what-it-writes",
          ("scripts/pipeline_ask.py" in one["scripts"], any("end dates" in c and "name" in c for c in one["consent"])),
          (True, True))
    reset(expiries=FAR, body="APP_NAME=Rex\n")
    code, io = go(["1", "yes"])
    check("kit735-the-consent-page-says-what-it-writes",
          (all(c in said(io) for c in one["consent"]), code), (True, pi.EX_OK))

    shutil.rmtree(tmp, ignore_errors=True)


def selftest():
    failures, cases = [], [0]

    def check(name, got, want):
        cases[0] += 1
        if got != want:
            failures.append("%s: got %r, want %r" % (name, got, want))
    try:
        run_cases(check)
    except Exception as exc:             # a crash is a failure too, said after those before it
        failures.append("the battery crashed (%s: %s)" % (type(exc).__name__, str(exc)[:100]))
    if failures:
        print("FAIL: pipeline_ask selftest")
        for f in failures:
            print("  - %s" % f)
        return 1
    print("OK: pipeline_ask selftest (%d cases)" % cases[0])
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv == ["--selftest"]:
        from scratch_run import contained   # its temp folders go when it ends
        return contained(selftest, "ask-selftest-run-")
    sys.stderr.write("pipeline_ask.py is called by pipeline_install.py. Its only option is "
                     "--selftest.\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
