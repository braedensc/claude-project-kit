#!/usr/bin/env python3
"""The pipeline's settings, in one folder on this Mac (KIT-853).

    python3 scripts/pipeline_settings.py where  [--settings-dir DIR]   # where everything is
    python3 scripts/pipeline_settings.py init   [--settings-dir DIR]   # make the folder and machine.conf
    python3 scripts/pipeline_settings.py check  [--settings-dir DIR]   # the folder's rules, read-only
    python3 scripts/pipeline_settings.py write  [--settings-dir DIR] [--apply]
    python3 scripts/pipeline_settings.py map                           # where each shared value goes
    python3 scripts/pipeline_settings.py --selftest

WHY. Eleven settings files repeat the same values: the pipeline's account, the dispatcher, the
names the keys are stored under, the clone the jobs run from. A value changed in one file and
missed in another breaks a part quietly. Settings live on the machine, never in a repository.
So one private folder holds them, each value is typed once, and each part's own settings file is
written from it. (One file per project, a first run that gathers the files already filled, hand
edits, and export and import come in the later pieces of this work.)

THE FOLDER. `~/Library/Application Support/claude-pipeline/settings`, yours, mode 700. Its own
place is a setting too: `--settings-dir`, else the PIPELINE_SETTINGS_DIR variable, else that
default. (It cannot sit in its own file.) It holds `machine.conf`, mode 600, in three kinds of
section:

  [places]   where things are. PARTS_DIR: the folder the part files are in (empty: this kit
             folder). <NAME>_CONF: one part file kept somewhere else, by its full path, such as
             BRIDGE_CONF=/full/path/bridge.conf. CONSENT_RECORD, STATE_DIR and SECRETS_DIR: the
             places of the consent record, the pipeline account's state folder and its secrets
             folder. `where` prints each one, and says what reads it.
  [shared]   a value typed once. MAP below names every part file and key it is written to.
  [stage-e.conf], [queue.conf] and the rest: one part file's own values. A key there wins over a
             shared value for that file, and `check` says so.

  KEY=value, one per line, parsed and never sourced, as the part files are. `#` starts a comment
  only at the start of a line. A part's section takes only the keys its example names.

WHAT `write` DOES. For each part file on this Mac it works out what the folder changes, and shows
each change: the file, the key, the value now and the value after. It writes nothing. With
`--apply`, run by you in your own terminal, it writes them. Every other byte of the file stays, and
so does its mode. The file is swapped in whole from a file made new beside it, never written
through a link, and read back. A part file that is not there is off on this Mac: nothing is written
for it. A problem anywhere, and nothing is written at all.

WHAT `check` FAILS ON (`write` runs it first, so a folder that fails it is never written from)
  secret      a value or a comment shaped like a credential (the notifier's scrub, the one the tech
              lead's installer uses on its own settings, plus a Slack app token and a signing
              secret), a long unbroken run of mixed letters and digits, or a key named like a secret. The folder holds the NAME a key is stored under (a KEY_ENV),
              never the key. Each problem names the line and the key, never the value.
  private     the folder or anything in it belongs to another account, or another account can
              read it.
  link        the folder or anything in it is a link.
  repository  the folder is inside a git working tree.
  lines       a line that is not KEY=value or a section; an unknown section or key; a key twice; a
              place that is not a full path; or a key another tool writes (TOOL_KEYS), which the
              folder would undo.

WHAT IT NEVER DOES (asserted in --selftest). It runs no installer and no other program, loads no
job, and opens no connection. It reads the folder, the kit's parts list and examples, and the part
files. Only `init` and `write --apply` write, and only to the folder and the part files.

Exit: 0 done, or nothing to do (the words say which) · 1 a write failed (the file is named)
      · 2 the folder or a part file needs fixing first, or a usage error · 3 refused (inside a
      Claude session, or as root) · 4 something could not be read · 10 `write` found changes and
      wrote none.
"""
import argparse
import json
import os
import re
import stat
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from pipeline_dispatch_local import AGENT_ENV_MARKERS  # noqa: E402

EX_OK, EX_FAILED, EX_FIX, EX_REFUSED, EX_UNREAD, EX_WAITING = 0, 1, 2, 3, 4, 10

DEFAULT_DIR = os.path.join("~", "Library", "Application Support", "claude-pipeline", "settings")
DIR_ENV = "PIPELINE_SETTINGS_DIR"
MACHINE = "machine.conf"
PARTS_LIST = "pipeline-parts.json"
FILE_MAX = 1 << 20
WALK_MAX = 500

# Each shared value: its name under [shared], what it is, and the part files it is written to,
# as file stems. "stem" means the same key in that file; "stem:KEY" names another key. Only the
# values the part files' own examples say must be the same are here; the selftest checks every
# target against its example. A value only some parts share is left to each part's section.
MAP = (
    ("ROLE_ACCOUNT", "the pipeline's own account, the one the review jobs and the dispatcher run as",
     "stage-e stage-a stage-a:DISPATCHER_ACCOUNT acceptance bridge chat-lane notifier queue sweep "
     "update watcher"),
    ("ROLE_KIT_CLONE", "that account's clone of this kit, the one the review jobs run from",
     "acceptance bridge notifier queue sweep"),
    ("ENV_FILE", "that account's own env file, the one the review installer keeps its keys in",
     "acceptance bridge notifier queue sweep chat-lane:ROLE_ENV_FILE"),
    ("LINEAR_KEY_ENV", "the NAME the pipeline's tracker key is stored under",
     "stage-e stage-a acceptance bridge notifier queue sweep"),
    ("GITHUB_TOKEN_ENV", "the NAME the review jobs' code host token is stored under",
     "stage-e acceptance"),
    ("DISPATCHER_SERVICE", "the dispatcher's launchd label", "stage-e stage-a acceptance chat-lane update"),
    ("DISPATCHER_CONFIG", "the dispatcher's own settings file", "stage-e stage-a acceptance chat-lane"),
    ("DISPATCHER_PORT", "the port the dispatcher listens on", "stage-e stage-a chat-lane update"),
    ("DISPATCHER_ENV_FILE", "the env file the dispatcher hands every session it starts",
     "chat-lane update notifier bridge"),
    ("KIT_REPO_URL", "the https address of this kit's repository", "stage-e stage-a"),
    ("OWNER_USER_ID", "your own user id in the tracker", "stage-a bridge acceptance"),
    ("TICKET_MODEL", "the model every ticket the pipeline files is tagged with", "stage-a stage-e"),
    ("HOLD_FILE", "the watcher's hold file", "stage-e stage-a"),
    ("NOTIFIER_JOB_LABEL", "the notifier job's launchd label", "notifier:JOB_LABEL bridge stage-e"),
    ("NOTIFIER_STATE_DIR", "the notifier's state folder", "notifier:STATE_DIR bridge"),
    ("NOTIFIER_TOKEN_ENV", "the NAME the notifier's chat token is stored under",
     "notifier:CHAT_TOKEN_ENV chat-lane"),
    ("HEALTH_STATUS_FILE", "the health check's status file", "update stage-e bridge:STANDUP_HEALTH_FILE"),
    ("HEALTH_WATCH_INTERVAL_SECONDS", "how often the health check runs, in seconds",
     "update:WATCH_INTERVAL_SECONDS stage-e"),
    ("SPRINT_PLAN_FILE", "the sprint plan's file", "queue bridge:STANDUP_SPRINT_PLAN_FILE"),
)
SHARED_NAMES = tuple(name for name, _what, _to in MAP)

# Keys a part file takes all together or not at all, as its example says.
TOGETHER = {"update.conf": ("DISPATCHER_SERVICE", "ROLE_ACCOUNT", "DISPATCHER_ENV_FILE")}

# Keys another tool writes into a part file. The folder never holds them, so `write` never undoes
# that tool's change.
TOOL_KEYS = {
    ("bridge.conf", "APP_NAME"): "the one command asks for the tech lead's name and writes it",
    ("bridge.conf", "DIRECT_ASKS"): "its own card switches it on, after its checks",
    ("bridge.conf", "PUSH_MODE"): "the instant-replies card switches it on, after its checks",
    ("bridge.conf", "PUSH_JOB_LABEL"): "the instant-replies card writes it",
    ("update.conf", "CREDENTIAL_EXPIRIES"): "the one command and the health check write the end dates",
    ("queue.conf", "PLANNED_LOAD"): "the tech lead changes it when you ask in your DM",
    ("queue.conf", "DAY_STOP"): "the tech lead changes it when you ask in your DM",
    ("queue.conf", "LANES_PER_REPO"): "the tech lead changes it when you ask in your DM",
}

# The places, besides the part files. (key, what it is, its default, what reads it today.)
STATE_DEFAULT = "~/.stage-e"
PLACES = (
    ("CONSENT_RECORD", "The consent record", None,
     "Not read from here yet: the one command still takes --consent-record."),
    ("STATE_DIR", "The pipeline account's state folder", STATE_DEFAULT,
     "Not read from here yet: each part still names its own state files."),
    ("SECRETS_DIR", "The pipeline account's secrets folder", STATE_DEFAULT,
     "Not read from here yet: each part still names its own env file."),
)
PLACE_KEYS = ("PARTS_DIR",) + tuple(key for key, _w, _d, _r in PLACES)

SECTION_RE = re.compile(r"^\[([^\]\s]+)\]\Z")
KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*\Z")
EXAMPLE_KEY_RE = re.compile(r"^#?[ \t]?([A-Z][A-Z0-9_]*)=")
PATH_RE = re.compile(r"^[^\x00-\x1f\x7f]+\Z")
SECRET_NAME_RE = re.compile(
    r"(?:^|_)(?:TOKEN|SECRET|PASSWORD|PASSWD|PASSPHRASE|API_KEY|PRIVATE_KEY|CREDENTIALS?)\Z")
# Two shapes the notifier's scrub does not know, and a run of letters and digits with no separator
# in it: a folder name split by dashes or dots is not one, a random key is.
LOCAL_SHAPES = (
    (re.compile(r"xapp-[A-Za-z0-9-]{10,}"), "a Slack app-level token"),
    (re.compile(r"(?<![0-9A-Fa-f])[0-9a-f]{32}(?![0-9A-Fa-f])"), "a signing secret (32 hex digits)"),
)
RANDOM_RUN_RE = re.compile(r"[A-Za-z0-9+=]{32,}")


def targets(entry):
    """[(part file, key)] a shared value is written to."""
    name, _what, to = entry
    out = []
    for word in to.split():
        stem, _sep, key = word.partition(":")
        out.append((stem + ".conf", key or name))
    return out


def place_key(name):
    """The [places] key that names one part file's own place: stage-e.conf is STAGE_E_CONF."""
    return re.sub(r"[^A-Z0-9]", "_", name[:-len(".conf")].upper()) + "_CONF"


def consent_default():
    """The consent record's default place: the one command's own constant."""
    import pipeline_install
    return pipeline_install.DEFAULT_RECORD


# --------------------------------------------------------------------------- #
# Reading: the kit's facts, the folder, and a part file
# --------------------------------------------------------------------------- #
class Kit(object):
    """Each part's KEY=value settings file, from the parts list, and the keys its example names.
    Raises OSError or ValueError when the list or an example cannot be read."""

    def __init__(self, root=KIT):
        self.root = root
        with open(os.path.join(root, PARTS_LIST), encoding="utf-8") as fh:
            doc = json.load(fh)
        self.examples, self.keys = {}, {}
        for part in doc.get("parts") or []:
            s = part.get("settings") or {}
            name, example = s.get("file") or "", s.get("example") or ""
            if name.endswith(".conf") and example and name not in self.examples:
                self.examples[name] = os.path.join(root, example)
        if not self.examples:
            raise ValueError("the parts list names no settings file")
        for name, path in self.examples.items():
            with open(path, encoding="utf-8") as fh:
                self.keys[name] = set(m.group(1) for m in map(EXAMPLE_KEY_RE.match, fh.read().splitlines()) if m)
        self.files = sorted(self.examples)
        self.conf_keys = dict((place_key(name), name) for name in self.files)


class Folder(object):
    """machine.conf, read: each section as a map, and the line each key is on."""

    def __init__(self):
        self.places, self.shared, self.parts, self.line = {}, {}, {}, {}


def read_text(path):
    """A small file's text, never through a link; CRLF endings kept. Raises OSError or ValueError."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as fh:
        data = fh.read(FILE_MAX + 1)
    if len(data) > FILE_MAX:
        raise ValueError("it is larger than 1 MB")
    return data.decode("utf-8")


def conf_values(text):
    """A part file's values, as the parts' own parsers read them."""
    values = {}
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _eq, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def key_problem(kit, section, key):
    """Why `key` may not sit in `section`, or None."""
    if section == "places":
        if key in PLACE_KEYS or key in kit.conf_keys:
            return None
        return "is not a place: %s, or a part file's own, such as BRIDGE_CONF" % ", ".join(PLACE_KEYS)
    if section == "shared":
        if key in SHARED_NAMES:
            return None
        return "is not a shared value (`map` lists them). A part's own value goes in its own section"
    if (section, key) in TOOL_KEYS:
        return "is not kept here: %s, and the folder would undo it" % TOOL_KEYS[(section, key)]
    if key not in kit.keys[section]:
        return "is not a key %s names" % os.path.basename(kit.examples[section])
    return None


def parse_machine(text, kit):
    """(Folder, problems). Each problem names the line and the key, never a value."""
    folder, problems, section, seen = Folder(), [], None, set()
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = SECTION_RE.match(line)
        if m:
            section = m.group(1)
            if section not in ("places", "shared") and section not in kit.examples:
                problems.append("line %d: [%s] is not a section: [places], [shared], or a part's "
                                "settings file such as [stage-e.conf]" % (n, section))
                section = "?"
            elif section in seen:
                problems.append("line %d: [%s] appears twice. Keep one" % (n, section))
                section = "?"
            else:
                seen.add(section)
            continue
        key, eq, value = line.partition("=")
        key = key.strip()
        if not eq or not KEY_RE.match(key):
            problems.append("line %d is not KEY=value or a [section]" % n)
            continue
        if section is None:
            problems.append("line %d: %s comes before any [section]" % (n, key))
            continue
        if section == "?":
            continue
        where = folder.places if section == "places" else folder.shared if section == "shared" \
            else folder.parts.setdefault(section, {})
        if key in where:
            problems.append("line %d: %s is set twice in [%s]" % (n, key, section))
            continue
        why = key_problem(kit, section, key)
        if why:
            problems.append("line %d: %s %s" % (n, key, why))
            continue
        where[key] = value.strip().strip('"').strip("'")
        folder.line[(section, key)] = n
    return folder, problems + place_problems(folder, kit)


def place_problems(folder, kit):
    """A place must be a full path (STATE_DIR and SECRETS_DIR may start ~/, the pipeline account's
    home), and a part file's own place must end in that file's name. Empty is the default."""
    out = []
    for key, value in folder.places.items():
        if not value:
            continue
        if key in ("STATE_DIR", "SECRETS_DIR"):
            ok, rule = value.startswith(("/", "~/")), "a full path, or ~/ for the pipeline account's home"
        elif key in kit.conf_keys:
            ok = value.startswith("/") and os.path.basename(value) == kit.conf_keys[key]
            rule = "the full path of a file named %s" % kit.conf_keys[key]
        else:
            ok, rule = value.startswith("/"), "a full path"
        if not ok or not PATH_RE.match(value) or ".." in value.split("/"):
            out.append("line %d: %s must be %s" % (folder.line[("places", key)], key, rule))
    return out


# --------------------------------------------------------------------------- #
# The checker: secrets, privacy, links and repositories
# --------------------------------------------------------------------------- #
class ScrubError(Exception):
    """The shared scrub could not run, so nothing it would have judged is called clean."""


def secret_labels(text):
    """What `text` is shaped like, as labels; [] when clean. Raises ScrubError when the shared scrub
    cannot run, so a folder that could not be checked is never called clean."""
    try:
        import pipeline_notify_local as notify
        labels = list(notify.secret_hits(text))
    except Exception as exc:
        raise ScrubError(type(exc).__name__)
    labels += [label for pattern, label in LOCAL_SHAPES if pattern.search(text) and label not in labels]
    for run in RANDOM_RUN_RE.findall(text):
        if re.search(r"[a-z]", run) and re.search(r"[A-Z]", run) and re.search(r"[0-9]", run):
            labels.append("a long random-looking string")
            break
    return labels


def secret_problems(text):
    """Every line of machine.conf shaped like a secret, by line and key. Never the value."""
    out, section = [], None
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        m = SECTION_RE.match(line)
        if m:
            section = m.group(1)
            continue
        if line.startswith("#"):
            labels, what = secret_labels(line), "a comment"
        else:
            key, _eq, value = line.partition("=")
            key = key.strip()
            what = "[%s] %s" % (section, key) if section else key
            labels = secret_labels(value)
            if value.strip() and SECRET_NAME_RE.search(key):
                labels.append("a key named like a secret: this folder holds the NAME a secret is "
                              "stored under (a key ending in _ENV), never the secret")
        if labels:
            out.append("line %d (%s) holds something shaped like %s" % (n, what, "; ".join(labels)))
    return out


def git_tree(path, stop=None):
    """The nearest folder at or above `path` that holds a .git, or None. `stop` ends the walk."""
    p = os.path.realpath(path)
    while p != stop:
        if os.path.lexists(os.path.join(p, ".git")):
            return p
        parent = os.path.dirname(p)
        if parent == p:
            return None
        p = parent
    return None


def tree_problems(top, uid):
    """The folder and everything in it: yours, no other account may read it, and no links."""
    out, count = [], [0]

    def judge(path, label, folder):
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode):
            out.append("%s is a link. Nothing in the settings folder may be one" % label)
            return False
        if folder and not stat.S_ISDIR(st.st_mode):
            out.append("%s is not a folder" % label)
            return False
        if st.st_uid != uid:
            out.append("%s belongs to another account" % label)
        if st.st_mode & 0o077:
            out.append("%s can be read by other accounts (mode %03o). Make it %s"
                       % (label, st.st_mode & 0o777, "700" if stat.S_ISDIR(st.st_mode) else "600"))
        return stat.S_ISDIR(st.st_mode)

    def unread(exc):                 # a folder inside it that could not be listed is said, never passed
        out.append("%s could not be read (%s), so it was not checked"
                   % (os.path.relpath(exc.filename or top, top), type(exc).__name__))

    if judge(top, "the settings folder", True):
        for root, dirs, files in os.walk(top, onerror=unread):
            for name in sorted(dirs) + sorted(files):
                count[0] += 1
                if count[0] > WALK_MAX:
                    out.append("the settings folder holds more than %d entries; not all were checked" % WALK_MAX)
                    return out
                path = os.path.join(root, name)
                judge(path, os.path.relpath(path, top), False)
    return out


def overrides(folder):
    """Notes: a part's own value that stops a shared one reaching that file."""
    out = []
    for entry in MAP:
        if entry[0] in folder.shared:
            for name, key in targets(entry):
                if key in folder.parts.get(name, {}):
                    out.append("%s sets %s itself, so the shared %s does not reach it there"
                               % (name, key, entry[0]))
    return out


def load(settings_dir, kit, uid, stop=None):
    """(state, folder, problems, notes). state is "absent" (no folder or no machine.conf), or
    "read". Raises OSError or ValueError when machine.conf cannot be read."""
    if not os.path.lexists(settings_dir):
        return "absent", None, [], []
    problems = tree_problems(settings_dir, uid)
    repo = git_tree(settings_dir, stop)
    if repo:
        problems.append("the settings folder is inside a git working tree (%s). Settings never go in "
                        "a repository" % repo)
    machine = os.path.join(settings_dir, MACHINE)
    if not os.path.lexists(machine):
        return "absent", None, problems, []
    if os.path.islink(machine):
        return "read", Folder(), problems, []
    text = read_text(machine)
    problems += secret_problems(text)
    folder, parse = parse_machine(text, kit)
    return "read", folder, problems + parse, overrides(folder)


# --------------------------------------------------------------------------- #
# The writer
# --------------------------------------------------------------------------- #
def set_line(text, key, value):
    """KEY=value set in a part file's text with every other byte kept: the tech lead installer's
    one writer of a line, used here unchanged."""
    import pipeline_bridge_setup
    return pipeline_bridge_setup.set_conf_line(text, key, value)


def part_path(folder, kit, name):
    """(where the part file is, which setting said so)."""
    own = folder.places.get(place_key(name))
    if own:
        return own, "[places] " + place_key(name)
    base = folder.places.get("PARTS_DIR")
    return (os.path.join(base, name), "[places] PARTS_DIR") if base else (
        os.path.join(kit.root, name), "this kit folder")


def part_values(folder, name):
    """[(key, value, from)] the folder gives one part file. Its own section wins over [shared]."""
    own, out = folder.parts.get(name, {}), []
    for entry in MAP:
        if entry[0] in folder.shared:
            for file_name, key in targets(entry):
                if file_name == name and key not in own:
                    out.append((key, folder.shared[entry[0]], "[shared] " + entry[0]))
    out += [(key, value, "[%s]" % name) for key, value in own.items()]
    return out


class Plan(object):
    """One part file: its state (off, empty, same, change, held, unread), the changes, and why."""

    def __init__(self, name, path, source):
        self.name, self.path, self.source = name, path, source
        self.state, self.why, self.changes, self.old, self.new = "same", "", [], None, None


def plan_file(folder, kit, name, uid):
    path, source = part_path(folder, kit, name)
    plan, wanted = Plan(name, path, source), part_values(folder, name)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        plan.state = "off"
        return plan
    except OSError as exc:
        plan.state, plan.why = "unread", "could not be looked at (%s)" % type(exc).__name__
        return plan
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode) or st.st_uid != uid:
        plan.state = "held"
        plan.why = ("is a link, and nothing is written through a link" if stat.S_ISLNK(st.st_mode) else
                    "is not a plain file" if not stat.S_ISREG(st.st_mode) else "belongs to another account")
        return plan
    if not wanted:
        plan.state = "empty"
        return plan
    try:
        plan.old = read_text(path)
    except (OSError, ValueError) as exc:
        plan.state, plan.why = "unread", "could not be read (%s)" % type(exc).__name__
        return plan
    have, plan.new = conf_values(plan.old), plan.old
    for key, value, src in wanted:
        if have.get(key) != value:
            plan.new = set_line(plan.new, key, value)
            plan.changes.append((key, have.get(key), value, src))
    together = TOGETHER.get(name, ())
    after = conf_values(plan.new)
    if (set(k for k, _o, _n, _s in plan.changes) & set(together)
            and 0 < len([k for k in together if after.get(k)]) < len(together)):
        plan.state = "held"
        plan.why = ("takes %s all together or none, and the folder would leave some of them empty. "
                    "Set all three under [shared] or [%s]" % (", ".join(together), name))
        return plan
    plan.state = "change" if plan.changes else "same"
    return plan


def apply_plan(plan):
    """None when the file is written and read back; else why not, in words."""
    try:
        st = os.lstat(plan.path)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            return "is no longer a plain file, so nothing was written to it"
        if read_text(plan.path) != plan.old:
            return "changed while this ran, so nothing was written to it. Run write again"
    except (OSError, ValueError) as exc:
        return "could not be read again (%s), so nothing was written to it" % type(exc).__name__
    mode, tmp, swapped = stat.S_IMODE(st.st_mode), "%s.settings.%d" % (plan.path, os.getpid()), False
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
        with os.fdopen(fd, "wb") as fh:
            os.fchmod(fh.fileno(), mode)
            fh.write(plan.new.encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, plan.path)
        swapped = True
    except OSError as exc:
        return "could not be written (%s); it is as it was" % type(exc).__name__
    finally:
        if not swapped:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    try:
        back = read_text(plan.path)
    except (OSError, ValueError) as exc:
        return "was written but could not be read back (%s). Check it by hand" % type(exc).__name__
    values = conf_values(back)
    if back != plan.new or any(values.get(k) != new for k, _o, new, _s in plan.changes):
        return "was written but reads back differently. Check it by hand"
    return None


def shown(value):
    if value is None:
        return "(none)"
    if not value:
        return "(empty)"
    try:
        hidden = bool(secret_labels(value))
    except Exception:            # a value that could not be judged is not shown either
        hidden = True
    return "(shaped like a secret, not shown)" if hidden else value


# --------------------------------------------------------------------------- #
# The commands
# --------------------------------------------------------------------------- #
def refused(what, environ, uid, out):
    found = [m for m in AGENT_ENV_MARKERS if m in environ]
    if found:
        out("REFUSED: `%s` writes the settings the installers apply, and a Claude session must never "
            "do that. Run it in your own terminal. (A session's environment was found: %s.)"
            % (what, ", ".join(found)))
        return True
    if uid == 0:
        out("REFUSED: `%s` writes your own settings. Run it as yourself, not as root." % what)
        return True
    return False


def settings_dir_of(flag, environ):
    """(the folder, where its place came from), or (None, why) for a place that is not a full path."""
    value, source = (flag, "--settings-dir") if flag else (environ.get(DIR_ENV), DIR_ENV) \
        if environ.get(DIR_ENV) else (DEFAULT_DIR, "the default")
    path = os.path.expanduser(value)
    if not os.path.isabs(path):
        return None, "%s must be a full path" % source
    return os.path.normpath(path), source


def say_problems(problems, out):
    for p in problems:
        out("PROBLEM: %s." % p)


def cmd_check(sdir, kit, uid, out, stop=None):
    state, folder, problems, notes = load(sdir, kit, uid, stop)
    if state == "absent" and not problems:
        out("No settings at %s yet, so there was nothing to check. `init` makes the folder." % sdir)
        return EX_OK
    say_problems(problems, out)
    for note in notes:
        out("NOTE: %s." % note)
    if problems:
        out("The settings folder needs fixing: %d problem%s above." % (len(problems), "" if len(problems) == 1 else "s"))
        return EX_FIX
    out("OK: %s is private, holds nothing shaped like a secret, and reads cleanly: %d shared value%s, "
        "%d part section%s, %d place%s." % (
            os.path.join(sdir, MACHINE), len(folder.shared), "" if len(folder.shared) == 1 else "s",
            len(folder.parts), "" if len(folder.parts) == 1 else "s",
            len(folder.places), "" if len(folder.places) == 1 else "s"))
    return EX_OK


def cmd_write(sdir, kit, uid, apply, out, stop=None):
    state, folder, problems, notes = load(sdir, kit, uid, stop)
    if state == "absent" and not problems:
        out("No settings at %s yet, so there is nothing to write from. `init` makes the folder." % sdir)
        return EX_OK
    if problems:
        say_problems(problems, out)
        out("Nothing was written: `check` must pass first.")
        return EX_FIX
    for note in notes:
        out("NOTE: %s." % note)
    plans = [plan_file(folder, kit, name, uid) for name in kit.files]
    for p in plans:
        if p.state in ("held", "unread"):
            out("%s (%s): NOT WRITTEN: it %s." % (p.name, p.path, p.why))
        elif p.state == "change":
            out("%s (%s, from %s): %d change%s" % (p.name, p.path, p.source, len(p.changes),
                                                    "" if len(p.changes) == 1 else "s"))
            for key, old, new, src in p.changes:
                out("  %s: %s -> %s   (%s)" % (key, shown(old), shown(new), src))
    for state, words in (("same", "Already what the folder says"),
                         ("empty", "Nothing in the folder for them"),
                         ("off", "Off on this Mac (no settings file, so nothing is written for them)")):
        names = [p.name for p in plans if p.state == state]
        if names:
            out("%s: %s." % (words, ", ".join(names)))
    if any(p.state == "unread" for p in plans):
        out("Nothing was written: a part file could not be read.")
        return EX_UNREAD
    if any(p.state == "held" for p in plans):
        out("Nothing was written: fix the part file named above first.")
        return EX_FIX
    todo = [p for p in plans if p.state == "change"]
    count = sum(len(p.changes) for p in todo)
    if not todo:
        out("Nothing to write: every part file on this Mac already says what the folder says.")
        return EX_OK
    if not apply:
        out("Nothing was written. `write --apply` writes these %d change%s to %d file%s."
            % (count, "" if count == 1 else "s", len(todo), "" if len(todo) == 1 else "s"))
        return EX_WAITING
    failed = 0
    for p in todo:
        why = apply_plan(p)
        if why:
            failed += 1
            out("FAILED: %s %s." % (p.path, why))
        else:
            out("Wrote %s and read it back: it matches." % p.path)
    if failed:
        out("%d of %d files were not written. Each one named above is as it was, or says to check it."
            % (failed, len(todo)))
        return EX_FAILED
    return EX_OK


def template(kit):
    """The first machine.conf: every place and shared value, commented out, with what it is."""
    lines = ["# This Mac's pipeline settings (scripts/pipeline_settings.py). Names, paths, numbers and",
             "# switches only: never a key, token or password. `check` checks this file; `write` shows",
             "# what reaches each part's own settings file, and `write --apply` writes it.", "",
             "[places]",
             "# The folder the part files are in. Empty: this kit folder.", "#PARTS_DIR=",
             "# A part file kept somewhere else, by its full path, such as BRIDGE_CONF=/full/path/bridge.conf"]
    for key, what, default, _reads in PLACES:
        lines += ["# %s." % what, "#%s=%s" % (key, default if default is not None else consent_default())]
    lines += ["", "[shared]", "# Each value here is typed once, and written to every part file named under it."]
    for entry in MAP:
        lines += ["# %s. Reaches %s." % (entry[1][0].upper() + entry[1][1:], ", ".join(
            "%s %s" % (f, k) for f, k in targets(entry))), "#%s=" % entry[0]]
    lines += ["", "# One section per part file, for its own values, such as [stage-e.conf] or [queue.conf].",
              "# A key there wins over a shared value for that file."]
    return "\n".join(lines) + "\n"


def cmd_init(sdir, kit, uid, out, stop=None):
    machine = os.path.join(sdir, MACHINE)
    if os.path.lexists(machine):
        out("%s is already there. Nothing was changed." % machine)
        return EX_OK
    repo = git_tree(sdir, stop)
    if repo:
        out("REFUSED: %s is inside a git working tree (%s). Settings never go in a repository. "
            "Nothing was made." % (sdir, repo))
        return EX_FIX
    try:
        if not os.path.lexists(sdir):
            os.makedirs(sdir, 0o700)
            os.chmod(sdir, 0o700)
        fd = os.open(machine, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            os.fchmod(fh.fileno(), 0o600)
            fh.write(template(kit))
    except OSError as exc:
        out("FAILED: %s could not be made (%s)." % (machine, type(exc).__name__))
        return EX_FAILED
    out("Made %s. Type each value once there, then run `check` and `write`." % machine)
    return cmd_check(sdir, kit, uid, out, stop)


def cmd_where(sdir, source, kit, uid, out, stop=None):
    state, folder, problems, _notes = load(sdir, kit, uid, stop)
    out("The settings folder: %s (%s; --settings-dir or %s moves it)." % (sdir, source, DIR_ENV))
    if state == "absent":
        out("  machine.conf is not there yet. `init` makes it. The places below are the defaults.")
        folder = Folder()
    elif problems:
        out("  machine.conf has problems (`check` names them). The places below are the defaults.")
        folder = Folder()
    base = folder.places.get("PARTS_DIR")
    out("The part files: %s (%s). Read by `write`." % (base or kit.root, "[places] PARTS_DIR" if base else
                                                       "the default: this kit folder"))
    for name in kit.files:
        if folder.places.get(place_key(name)):
            out("  %s: %s ([places] %s)." % (name, folder.places[place_key(name)], place_key(name)))
    for key, what, default, reads in PLACES:
        value = folder.places.get(key)
        out("%s: %s (%s). %s" % (what, value or (default if default is not None else consent_default()),
                                 "[places] " + key if value else "the default", reads))
    return EX_FIX if problems else EX_OK


def cmd_map(kit, out):
    for entry in MAP:
        out("%s: %s." % (entry[0], entry[1]))
        out("  written to: %s." % ", ".join("%s %s" % (f, k) for f, k in targets(entry)))
    out("A key in a part file's own section ([stage-e.conf] and the rest) wins over these for that file.")
    return EX_OK


def main(argv=None, environ=None, out=print, uid=None, kit_root=KIT, stop=None):
    ap = argparse.ArgumentParser(description="The pipeline's settings, in one folder on this Mac.")
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    for name in ("where", "init", "check", "write"):
        p = sub.add_parser(name)
        p.add_argument("--settings-dir")
        if name == "write":
            p.add_argument("--apply", action="store_true")
    sub.add_parser("map")
    args = ap.parse_args(argv)
    if args.selftest:
        from scratch_run import contained
        return contained(selftest, "settings-selftest-run-")
    if not args.cmd:
        ap.print_help()
        return EX_FIX
    environ = os.environ if environ is None else environ
    uid = os.getuid() if uid is None else uid
    writes = args.cmd == "init" or (args.cmd == "write" and args.apply)
    if writes and refused("%s%s" % (args.cmd, " --apply" if args.cmd == "write" else ""), environ, uid, out):
        return EX_REFUSED
    try:
        kit = Kit(kit_root)
    except (OSError, ValueError) as exc:
        out("COULD NOT RUN: the kit's parts list or a settings example could not be read (%s)."
            % type(exc).__name__)
        return EX_UNREAD
    if args.cmd == "map":
        return cmd_map(kit, out)
    sdir, source = settings_dir_of(args.settings_dir, environ)
    if sdir is None:
        out("COULD NOT RUN: %s." % source)
        return EX_FIX
    try:
        if args.cmd == "where":
            return cmd_where(sdir, source, kit, uid, out, stop)
        if args.cmd == "init":
            return cmd_init(sdir, kit, uid, out, stop)
        if args.cmd == "check":
            return cmd_check(sdir, kit, uid, out, stop)
        return cmd_write(sdir, kit, uid, args.apply, out, stop)
    except (OSError, ValueError) as exc:
        out("COULD NOT RUN: the settings could not be read (%s). Nothing was written." % type(exc).__name__)
        return EX_UNREAD
    except ScrubError as exc:      # the shared scrub could not run: never call the folder clean
        out("COULD NOT RUN: the secret check could not run (%s). Nothing was written." % exc)
        return EX_UNREAD


# ======================================================================================
# SELFTEST: nothing below this line runs outside --selftest, and the source scan stops here.
# ======================================================================================
def selftest():
    import shutil
    import tempfile
    import secret_shapes
    failures, total = [], [0]

    def check(name, got, want):
        total[0] += 1
        if got != want:
            failures.append("FAIL %s: got %r, want %r" % (name, str(got)[:300], str(want)[:300]))

    tmp = os.path.realpath(tempfile.mkdtemp(prefix="settings-"))
    stop = os.path.dirname(tmp)       # the git walk ends here, wherever the temp folder sits
    me, other = os.getuid(), os.getuid() + 1
    kit = Kit(KIT)

    def run(*argv, **kw):
        said = []
        code = main(list(argv), environ=kw.get("environ", {}), out=said.append, uid=kw.get("uid", me),
                    stop=stop)
        return code, said

    def put(path, text, mode=0o600):
        if os.path.lexists(path):
            os.remove(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.chmod(path, mode)

    def get(path):
        with open(path, "rb") as fh:
            return fh.read().decode("utf-8")

    def snapshot(folder):
        out = {}
        for root, _dirs, files in os.walk(folder):
            for f in files:
                p = os.path.join(root, f)
                st = os.lstat(p)
                out[p] = (get(p) if not os.path.islink(p) else os.readlink(p), st.st_mtime_ns,
                          stat.S_IMODE(st.st_mode))
        return out

    parts = os.path.join(tmp, "parts")
    os.makedirs(parts)
    for name in kit.files:
        shutil.copyfile(kit.examples[name], os.path.join(parts, name))
    sdir = os.path.join(tmp, "settings")
    machine = os.path.join(sdir, MACHINE)
    sd = ("--settings-dir", sdir)

    def settings(body):
        put(machine, "[places]\nPARTS_DIR=%s\n%s" % (parts, body))

    # 1. The folder's own place is a setting: the default under the user's Library, the variable, the flag.
    home = os.path.expanduser("~")
    check("the default place is under the user's Library",
          settings_dir_of(None, {}), (os.path.join(home, "Library", "Application Support", "claude-pipeline",
                                                   "settings"), "the default"))
    check("the variable moves it", settings_dir_of(None, {DIR_ENV: "/x/s"}), ("/x/s", DIR_ENV))
    check("the flag wins over the variable", settings_dir_of("/y/s", {DIR_ENV: "/x/s"}), ("/y/s", "--settings-dir"))
    check("a place that is not a full path is refused", settings_dir_of("rel/s", {})[0], None)

    # 2. init: refused in a session and as root, never inside a repository, private, never overwrites.
    for marker in AGENT_ENV_MARKERS:
        code, said = run("init", *sd, environ={marker: ""})
        check("init is refused in a session (%s), before anything is made" % marker,
              (code, os.path.lexists(sdir)), (EX_REFUSED, False))
    check("init is refused as root", run("init", *sd, uid=0)[0], EX_REFUSED)
    repo = os.path.join(tmp, "repo")
    os.makedirs(os.path.join(repo, ".git"))
    code, said = run("init", "--settings-dir", os.path.join(repo, "s"))
    check("init refuses a folder inside a git working tree and makes nothing",
          (code, os.path.lexists(os.path.join(repo, "s"))), (EX_FIX, False))
    code, said = run("init", *sd)
    check("init makes the folder 700 and machine.conf 600, and the new file passes check",
          (code, oct(stat.S_IMODE(os.stat(sdir).st_mode)), oct(stat.S_IMODE(os.stat(machine).st_mode))),
          (EX_OK, "0o700", "0o600"))
    first = get(machine)
    check("its template names every place and every shared value",
          [k for k in PLACE_KEYS + SHARED_NAMES if "#%s=" % k not in first], [])
    check("init never overwrites", (run("init", *sd)[0], get(machine)), (EX_OK, first))

    # 3. A value typed once reaches every part file that needs it; write shows and changes nothing.
    settings("[shared]\nROLE_ACCOUNT=_pipeline\nDISPATCHER_SERVICE=com.test.dispatcher\n"
             "DISPATCHER_ENV_FILE=/opt/test/.env\nNOTIFIER_JOB_LABEL=com.test.notifier\n")
    os.chmod(os.path.join(parts, "queue.conf"), 0o640)
    before = snapshot(parts)
    code, said = run("write", *sd)
    want = set()
    for entry in MAP:
        if entry[0] in ("ROLE_ACCOUNT", "DISPATCHER_SERVICE", "DISPATCHER_ENV_FILE", "NOTIFIER_JOB_LABEL"):
            want |= set(targets(entry))
    shown_pairs = set()
    current = None
    for line in said:
        m = re.match(r"^(\S+\.conf) \(", line)
        if m:
            current = m.group(1)
        m = re.match(r"^  ([A-Z0-9_]+): ", line)
        if m and current:
            shown_pairs.add((current, m.group(1)))
    check("write lists every target of every shared value set, file by file", shown_pairs, want)
    check("…shows the value now and after", "  ROLE_ACCOUNT: _exdispatch -> _pipeline   ([shared] ROLE_ACCOUNT)"
          in said, True)
    check("…exits 10 and writes nothing at all", (code, snapshot(parts) == before), (EX_WAITING, True))
    code, said = run("write", "--apply", *sd)
    check("write --apply writes them all and reads each back", (code, len([s for s in said if s.startswith("Wrote ")])),
          (EX_OK, len(set(f for f, _k in want))))
    for name, key in sorted(want):
        values = conf_values(get(os.path.join(parts, name)))
        value = {"ROLE_ACCOUNT": "_pipeline", "DISPATCHER_ACCOUNT": "_pipeline",
                 "DISPATCHER_SERVICE": "com.test.dispatcher", "DISPATCHER_ENV_FILE": "/opt/test/.env"}.get(
            key, "com.test.notifier")
        check("%s %s holds the shared value" % (name, key), values.get(key), value)
    old_q, new_q = before[os.path.join(parts, "queue.conf")][0], get(os.path.join(parts, "queue.conf"))
    check("every other byte stays", [a for a, b in zip(old_q.splitlines(), new_q.splitlines()) if a != b],
          ["ROLE_ACCOUNT=_exqueue"])
    check("…and the mode stays", oct(stat.S_IMODE(os.stat(os.path.join(parts, "queue.conf")).st_mode)), "0o640")
    check("a second write has nothing to do", run("write", *sd)[0], EX_OK)

    # 4. A part's own value wins, and check says so.
    settings("[shared]\nROLE_ACCOUNT=_pipeline\n[stage-a.conf]\nROLE_ACCOUNT=_planner\n")
    code, said = run("check", *sd)
    check("check notes a part's own value that stops a shared one",
          (code, "NOTE: stage-a.conf sets ROLE_ACCOUNT itself, so the shared ROLE_ACCOUNT does not reach "
                 "it there." in said), (EX_OK, True))
    run("write", "--apply", *sd)
    check("the part's own value is written there, the shared one everywhere else",
          (conf_values(get(os.path.join(parts, "stage-a.conf"))).get("ROLE_ACCOUNT"),
           conf_values(get(os.path.join(parts, "stage-a.conf"))).get("DISPATCHER_ACCOUNT"),
           conf_values(get(os.path.join(parts, "stage-e.conf"))).get("ROLE_ACCOUNT")),
          ("_planner", "_pipeline", "_pipeline"))

    # 5. --apply is refused in a session and as root, before anything is read.
    settings("[shared]\nROLE_ACCOUNT=_other\n")
    before = snapshot(parts)
    for marker in AGENT_ENV_MARKERS:
        code, said = run("write", "--apply", *sd, environ={marker: "1"})
        check("write --apply is refused in a session (%s)" % marker, (code, snapshot(parts) == before),
              (EX_REFUSED, True))
    check("…and as root", run("write", "--apply", *sd, uid=0)[0], EX_REFUSED)
    check("…before it reads anything: no folder at all is still refused",
          run("write", "--apply", "--settings-dir", os.path.join(tmp, "none"), environ={"CLAUDECODE": "1"})[0],
          EX_REFUSED)
    check("a session may still look: write without --apply runs", run("write", *sd, environ={"CLAUDECODE": "1"})[0],
          EX_WAITING)

    # 6. A part file that is a link, or changed while it ran, or reads back wrong, is never written.
    real = os.path.join(tmp, "elsewhere.conf")
    shutil.move(os.path.join(parts, "sweep.conf"), real)
    os.symlink(real, os.path.join(parts, "sweep.conf"))
    before, target = snapshot(parts), get(real)
    code, said = run("write", "--apply", *sd)
    check("a part file that is a link stops the write, and nothing is written anywhere",
          (code, snapshot(parts) == before, get(real) == target,
           any("sweep.conf" in s and "link" in s for s in said)), (EX_FIX, True, True, True))
    os.remove(os.path.join(parts, "sweep.conf"))
    shutil.move(real, os.path.join(parts, "sweep.conf"))
    folder, _p = parse_machine(get(machine), kit)
    plan = plan_file(folder, kit, "stage-e.conf", me)
    with open(os.path.join(parts, "stage-e.conf"), "a", encoding="utf-8") as fh:
        fh.write("# a person's edit\n")
    edited = get(os.path.join(parts, "stage-e.conf"))
    check("a file changed while it ran is not written", ("changed while this ran" in (apply_plan(plan) or ""),
                                                          get(os.path.join(parts, "stage-e.conf"))), (True, edited))
    plan = plan_file(folder, kit, "stage-e.conf", me)
    real_read, calls = globals()["read_text"], [0]

    def wrong_back(path):
        calls[0] += 1
        return real_read(path) + ("X=1\n" if calls[0] == 2 else "")     # 1: the re-read, 2: the read back
    globals()["read_text"] = wrong_back
    try:
        why = apply_plan(plan)
    finally:
        globals()["read_text"] = real_read
    check("a file that reads back differently is said, never called written", "reads back differently" in (why or ""),
          True)
    crlf = os.path.join(parts, "bridge.conf")
    put(crlf, "ROLE_ACCOUNT=_old\r\nJOB_LABEL=com.test.bridge\r\n# no final newline\r\nTEAM_KEYS=X", 0o600)
    settings("[shared]\nROLE_ACCOUNT=_crlf\n")
    run("write", "--apply", *sd)
    check("CRLF endings and a missing final newline are kept",
          get(crlf), "ROLE_ACCOUNT=_crlf\r\nJOB_LABEL=com.test.bridge\r\n# no final newline\r\nTEAM_KEYS=X")

    # 7. Off on this Mac, and one file's own place.
    os.remove(os.path.join(parts, "watcher.conf"))
    code, said = run("write", "--apply", *sd)
    check("a part file that is not there is off, and is not made",
          (os.path.lexists(os.path.join(parts, "watcher.conf")), any(s.startswith("Off on this Mac") and
                                                                     "watcher.conf" in s for s in said)), (False, True))
    ops = os.path.join(tmp, "ops", "notifier.conf")
    os.makedirs(os.path.dirname(ops))
    shutil.move(os.path.join(parts, "notifier.conf"), ops)
    settings("NOTIFIER_CONF=%s\n[shared]\nROLE_ACCOUNT=_moved\n" % ops)
    run("write", "--apply", *sd)
    check("a part file's own place moves where it is written",
          (conf_values(get(ops)).get("ROLE_ACCOUNT"), os.path.lexists(os.path.join(parts, "notifier.conf"))),
          ("_moved", False))

    # 8. Places are settings: each one shown, the defaults the kit's own, a bad place refused.
    settings("CONSENT_RECORD=/srv/consent.json\nSTATE_DIR=/srv/state\nSECRETS_DIR=~/.vault\n")
    code, said = run("where", *sd)
    text = "\n".join(said)
    check("where shows each place from the folder",
          (code, "/srv/consent.json ([places] CONSENT_RECORD)" in text, "/srv/state ([places] STATE_DIR)" in text,
           "~/.vault ([places] SECRETS_DIR)" in text, "%s ([places] PARTS_DIR)" % parts in text), (EX_OK,) + (True,) * 4)
    os.remove(machine)
    text = "\n".join(run("where", *sd)[1])
    check("…and the defaults when the folder names none",
          ("%s (the default)" % consent_default() in text, "%s (the default: this kit folder)" % KIT in text,
           "~/.stage-e (the default)" in text), (True, True, True))
    check("the consent record's default is the one command's own",
          consent_default(), "/Library/Application Support/claude-pipeline/consent-record.json")
    for bad in ("CONSENT_RECORD=relative.json", "STATE_DIR=state", "BRIDGE_CONF=/x/other.conf", "PARTS_DIR=/a/../b"):
        put(machine, "[places]\n%s\n" % bad)
        code, said = run("check", *sd)
        check("a bad place is a problem: %s" % bad.split("=")[0],
              (code, any("%s must be" % bad.split("=")[0] in s for s in said)), (EX_FIX, True))

    # 9. Secrets: every credential shape, a key named like one, a long random string; the value never shown.
    samples = [s[3] for s in secret_shapes.SHAPES] + ["Ab1Cd2" * 6, "0123456789abcdef" * 2]
    for value in samples:
        for body in ("[shared]\nLINEAR_KEY_ENV=%s\n" % value, "[shared]\n# saved it: %s\n" % value):
            settings(body)
            before = snapshot(parts)
            code, said = run("check", *sd)
            check("check fails on %s…" % value[:6], (code, any("shaped like" in s for s in said),
                                                      any(value in s for s in said)), (EX_FIX, True, False))
            code, said = run("write", "--apply", *sd)
            check("…and nothing is written from it (%s…)" % value[:6],
                  (code, snapshot(parts) == before, any(value in s for s in said)), (EX_FIX, True, False))
    settings("[stage-e.conf]\nGITHUB_APP_PASSWORD=hunter\n")
    said = run("check", *sd)[1]
    check("a key named like a secret is a problem of its own", any("named like a secret" in s for s in said), True)
    clean = ["/Users/someone/Library/Application Support/x", "~/.stage-e/env", "com.example.dispatcher",
             "00000000-0000-4000-8000-000000000001", "STAGE_E_LINEAR_API_KEY", "https://github.com/o/r",
             "claude-opus-4-1-20250805", "0123456789abcdef0123456789abcdef01234567", "U0123456789", "3456",
             "/private/tmp/x/-Users-someone-claude-project-kit--claude-worktrees-quiet-river-966e0c/parts",
             "/Users/Someone/Projects2026/MyApp/settings", "mon-sat 09:00 6, sun off", "Hooks change guard"]
    check("ordinary values are not shaped like a secret", [v for v in clean if secret_labels(v)], [])

    # 10. Private, no links, never in a repository.
    settings("[shared]\nROLE_ACCOUNT=_x\n")
    check("a private folder passes", run("check", *sd)[0], EX_OK)
    for path, mode, label in ((sdir, 0o755, "the settings folder"), (machine, 0o644, MACHINE)):
        os.chmod(path, mode)
        said = run("check", *sd)[1]
        check("%s readable by other accounts is a problem" % label,
              any(s.startswith("PROBLEM: %s can be read by other accounts" % label) for s in said), True)
        os.chmod(path, 0o700 if path == sdir else 0o600)
    check("a folder of another account's is a problem",
          any("belongs to another account" in s for s in run("check", *sd, uid=other)[1]), True)
    os.symlink(machine, os.path.join(sdir, "extra.conf"))
    check("a link inside the folder is a problem",
          any("extra.conf is a link" in s for s in run("check", *sd)[1]), True)
    os.remove(os.path.join(sdir, "extra.conf"))
    linked = os.path.join(tmp, "linked")
    os.symlink(sdir, linked)
    check("a folder that is a link is a problem",
          any("the settings folder is a link" in s for s in run("check", "--settings-dir", linked)[1]), True)
    inside = os.path.join(repo, "settings")
    shutil.copytree(sdir, inside)
    check("a folder inside a git working tree is a problem",
          any("inside a git working tree" in s for s in run("check", "--settings-dir", inside)[1]), True)

    # 11. The lines: what is refused, and the value never in the words.
    for body, words in (("[nope]\n", "[nope] is not a section"), ("ROLE_ACCOUNT=_x\n", "comes before any"),
                        ("[shared]\nROLE_ACCOUNT=_a\nROLE_ACCOUNT=_b\n", "set twice"),
                        ("[shared]\nNOT_SHARED=_x\n", "is not a shared value"),
                        ("[queue.conf]\nNO_SUCH_KEY=_x\n", "is not a key queue.conf.example names"),
                        ("[shared]\njust words\n", "is not KEY=value"),
                        ("[shared]\n[shared]\n", "appears twice"),
                        ("[bridge.conf]\nAPP_NAME=Rex\n", "the one command asks for the tech lead's name"),
                        ("[queue.conf]\nPLANNED_LOAD=30\n", "the tech lead changes it"),
                        ("[update.conf]\nCREDENTIAL_EXPIRIES=x\n", "write the end dates")):
        put(machine, body)
        code, said = run("check", *sd)
        check("check refuses: %s" % words, (code, any(words in s for s in said)), (EX_FIX, True))
    put(machine, "[shared]\nROLE_ACCOUNT=_only\n")
    for name in kit.files:
        put(os.path.join(parts, name), get(kit.examples[name]))
    upd = os.path.join(parts, "update.conf")
    put(upd, get(upd).replace("DISPATCHER_SERVICE=com.example.dispatcher", "DISPATCHER_SERVICE=").replace(
        "ROLE_ACCOUNT=_exdispatch", "ROLE_ACCOUNT=").replace("DISPATCHER_ENV_FILE=/opt/example-dispatcher/.env",
                                                             "DISPATCHER_ENV_FILE="))
    put(machine, "[places]\nPARTS_DIR=%s\n[shared]\nROLE_ACCOUNT=_only\n" % parts)
    before = snapshot(parts)
    code, said = run("write", "--apply", *sd)
    check("update.conf's three dispatcher values go together: one alone writes nothing anywhere",
          (code, snapshot(parts) == before, any("update.conf" in s and "all together" in s for s in said)),
          (EX_FIX, True, True))

    # 12. The map: every target is a part file's key its example names, and none is written twice.
    seen = []
    for entry in MAP:
        for name, key in targets(entry):
            check("%s's target %s %s is a key that file's example names" % (entry[0], name, key),
                  name in kit.keys and key in kit.keys[name], True)
            seen.append((name, key))
    check("no part file's key is the target of two shared values", len(seen), len(set(seen)))
    check("the keys that go together are all shared targets",
          [k for k in TOGETHER["update.conf"] if ("update.conf", k) not in seen], [])
    check("no tool's key is a shared target", [k for k in TOOL_KEYS if k in seen], [])
    check("every tool's key is a key its file's example names", [k for k in TOOL_KEYS if k[1] not in kit.keys[k[0]]], [])
    check("map runs anywhere and lists every shared value",
          len([s for s in run("map")[1] if re.match(r"^[A-Z0-9_]+: ", s)]), len(MAP))

    # 13. What it never does.
    with open(os.path.abspath(__file__), encoding="utf-8") as fh:
        src = fh.read()
    above = src.split("\n# SELFTEST: " + "nothing below", 1)[0].split('"""', 2)[2]
    banned = ("subprocess", "os.system", "Popen", "socket", "urlopen", "sudo", "launchctl", "exec(", "eval(")
    check("it starts no process, opens no connection and calls no installer",
          [b for b in banned if b in above], [])
    check("a part file is only ever written through apply_plan, and machine.conf only by init",
          (above.count("os.replace("), above.count("O_CREAT")), (1, 2))

    if failures:
        for line in failures:
            print(line)
        print("settings selftest: %d of %d checks failed" % (len(failures), total[0]))
        return 1
    print("OK: settings selftest (%d checks)" % total[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
