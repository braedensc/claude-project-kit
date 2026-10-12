#!/usr/bin/env python3
"""The health watch: one pass of every check that should reach a person when something
goes wrong and nothing else would say so (KIT-236, KIT-238).

    python3 scripts/pipeline_watch.py run              # one pass: what the scheduled job runs
    python3 scripts/pipeline_watch.py run --dry-run    # measure and say; send and write nothing
    python3 scripts/pipeline_watch.py status           # the last pass, per kind
    python3 scripts/pipeline_watch.py install          # a person: load the hourly LaunchAgent
    python3 scripts/pipeline_watch.py uninstall
    python3 scripts/pipeline_watch.py --selftest

Settings: the same `update.conf` as scripts/pipeline_update.py (its watch keys are listed in
update.conf.example). Guide: docs/UPDATE-OPERATOR.md, "The health watch".

WHY.  On 2026-10-04 a Claude Code update changed what it tells hooks, and every desktop
session's guards misjudged it for six days; nobody was told (KIT-214). The same week the
dispatcher was three releases behind with nothing saying so. This job looks for that class
of fault every hour and tells the owner when the answer CHANGES — once per problem, and once
more when it clears.

WHAT IT CHECKS, one KIND each (a kind is what an alert is about):
  hooks       the kit hooks reported that they could not tell which checkout a session runs
              in (the Stop hook's `session-*__root-unresolved` records, last 24 hours)
  updates     `pipeline_update.py review` rows that are BEHIND, need a RESTART, or could not
              be measured — the dispatcher and Homebrew rows; the kit checkout is listed but
              never pages, because it moves with every merge. Re-measured every 20 hours.
  release     a dispatcher release newer than the version DISPATCHER_VERSION pins
  dispatcher  the dispatcher not answering /version on this machine, two passes running
  front-door  PUBLIC_STATUS_URL not answering 200 while the internet answers, two passes
              running. With no internet the kind is UNKNOWN and pages nothing: a laptop in a
              background wake has no network, and that is not an outage.
  disk        less than DISK_FLOOR_GB free on /
A seventh kind, `watch-job` — this job itself stopped — cannot be reported by the job. The
heartbeat monitor watches the status file this job writes and pages when it goes stale.

WHERE IT SAYS SO.
  * GitHub: when a kind changes between firing and ok, it dispatches ALERT_WORKFLOW in
    ALERT_REPO (a PRIVATE repo — alert text carries machine state). That workflow opens or
    closes one issue per kind as github-actions[bot], with an @mention, so the owner is
    notified. It then waits for that run and checks it succeeded: "dispatch accepted" is not
    "issue filed". A failed send is not recorded as sent, so the next pass sends it again.
  * Slack: it writes HEALTH_STATUS_FILE (readable by the dispatcher's role account). The
    heartbeat monitor reads it and pages through the notifier, as it does for the daemons.
  Either one empty means that channel is OFF, and every pass says so.

EXIT CODES — contract §13.
  0   every kind is ok, and every change was delivered
  1   a change could NOT be delivered (it is sent again next pass)
  2   usage or config — nothing was checked
  3   refused — an agent environment asked to install or uninstall
  4   something could not be measured, and nothing is firing
  10  something is firing (and was delivered, or was already delivered)

WHAT IT NEVER DOES.  Change anything it reports on, read a credential, or update a
component. It reports; `pipeline_update.py` and a person update.
"""
import argparse
import calendar
import json
import os
import plistlib
import re
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import pipeline_update as pu  # noqa: E402

se = pu.se
EX_OK, EX_FAILED, EX_USAGE, EX_REFUSED, EX_UNKNOWN, EX_FIRING = 0, 1, 2, 3, 4, 10
OK, FIRING, UNKNOWN = "ok", "firing", "unknown"

STATUS_SCHEMA = "pipeline-health-status/1"
STATE_SCHEMA = "pipeline-health-watch-state/1"
KINDS = ("hooks", "updates", "release", "dispatcher", "front-door", "disk")
UPDATE_REVIEW_EVERY = 20 * 3600       # the registry and Homebrew need not be asked hourly
DEBOUNCE = 2                          # a liveness kind fires after this many failing passes
HOOK_EVIDENCE_SECONDS = 24 * 3600
NET_PROBE_URL = "https://registry.npmjs.org/"
UNRESOLVED_SUFFIX = "__root-unresolved"
SUMMARY_MAX = 3500
RUN_FIND_TRIES = 12                   # x 5 s: how long to look for the run a dispatch started
RUN_WATCH_TIMEOUT = 300

def diagnose(conf):
    """The read-only next step every alert carries. The Slack tech lead runs as the
    dispatcher's role account with an exact-command grant, so what it can read is the status
    file (written world-readable for that reason); the commands are for the owner's shell."""
    where = conf.get("HEALTH_STATUS_FILE") or "(no status file configured)"
    return ("Diagnose (read-only): the full findings are in %s. In Slack, ask the tech lead to "
            "read it and explain. In the kit checkout: `python3 scripts/pipeline_update.py "
            "review` and `python3 scripts/pipeline_watch.py status`. Anything that needs sudo, "
            "launchctl or an installer is printed for you, never run for you." % where)


def say(msg=""):
    se.say(msg)


def _now(env):
    return getattr(env, "now", time.time)()


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _parse_iso(text):
    try:
        return float(calendar.timegm(time.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")))
    except (TypeError, ValueError):
        return None


def _home(env):
    return env.home


def state_path(env):
    return os.path.join(_home(env), ".pipeline-update", "watch-state.json")


def load_state(env):
    try:
        with open(state_path(env), encoding="utf-8") as fh:
            doc = json.load(fh)
        if isinstance(doc, dict) and doc.get("schema") == STATE_SCHEMA:
            return doc
    except (OSError, ValueError):
        pass
    return {"schema": STATE_SCHEMA, "kinds": {}}


def _atomic_write(path, doc, mode=0o600):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.tmp-%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# The checks — each returns {"kind", "state", "summary"}
# --------------------------------------------------------------------------- #
def finding(kind, state, summary):
    return {"kind": kind, "state": state, "summary": summary}


def check_hooks(env, conf):
    """The Stop hook leaves `session-<id>__root-unresolved` in the anchor checkout's
    `.claude/.stop-pr-nag/` when it cannot tell which worktree a session runs in. A record
    from the last 24 hours fires. It is the one structured trace the hooks leave; the
    SessionStart warning reaches only the person watching that session."""
    roots = pu.split_list(conf.get("HOOK_CHECKOUTS")) or [conf.get("KIT_CHECKOUT") or KIT]
    now, hits = _now(env), []
    for root in roots:
        nag = os.path.join(root, ".claude", ".stop-pr-nag")
        try:
            names = os.listdir(nag)
        except FileNotFoundError:
            continue
        except OSError as exc:
            return finding("hooks", UNKNOWN, "could not read %s: %s" % (nag, exc))
        for name in names:
            if not name.endswith(UNRESOLVED_SUFFIX):
                continue
            try:
                age = now - os.path.getmtime(os.path.join(nag, name))
            except OSError:
                continue
            if age <= HOOK_EVIDENCE_SECONDS:
                hits.append("%s (%s)" % (root, name[:-len(UNRESOLVED_SUFFIX)]))
    if hits:
        return finding("hooks", FIRING, (
            "In the last 24 hours the Stop hook could not tell which worktree %d session(s) "
            "ran in, so their guards judged the wrong checkout: %s. A harness change is the "
            "usual cause (KIT-214). Start a desktop session in a worktree: its first screen "
            "says what the hooks see." % (len(hits), "; ".join(sorted(hits)[:10]))))
    return finding("hooks", OK, "no unresolved session in the last 24 hours")


def check_updates(env, conf, state):
    now = _now(env)
    cached = state.get("review") or {}
    if cached.get("at") and now - cached["at"] < UPDATE_REVIEW_EVERY:
        rows = cached.get("rows") or []
    else:
        rows = pu.review_dispatcher(env, conf) + pu.review_brew(env, conf)
        state["review"] = {"at": now, "rows": rows}
    paging = [r for r in rows if r["state"] in (pu.BEHIND, pu.RESTART)
              or (r["state"] == pu.UNKNOWN and not r["component"].startswith("dispatcher"))]
    if not paging:
        return finding("updates", OK, "nothing behind")
    lines = ["%s: %s %s -> %s; update: %s" % (r["component"], r["state"], r["installed"],
                                               r["target"], r["command"] or r["note"])
             for r in paging]
    return finding("updates", FIRING, "Needs updating:\n" + "\n".join(lines))


def check_release(env, conf):
    if not conf.get("DISPATCHER_SERVICE") or conf.get("DISPATCHER_VERSION") == "latest":
        return finding("release", OK, "no pinned dispatcher version")
    latest = pu.registry_latest(env, conf["DISPATCHER_PACKAGE"])
    if latest is None:
        return finding("release", UNKNOWN, "the package registry did not say the latest version")
    pin = conf["DISPATCHER_VERSION"]
    if pu.vkey(latest) > pu.vkey(pin):
        return finding("release", FIRING, (
            "%s %s is out; DISPATCHER_VERSION pins %s. Compare the two versions before raising "
            "the pin (the 0.2.69 -> 0.2.73 review found boundary changes no version number "
            "shows, KIT-235)." % (conf["DISPATCHER_PACKAGE"], latest, pin)))
    return finding("release", OK, "the pin %s is the latest" % pin)


def _debounced(state, kind, failing):
    rec = state.setdefault("kinds", {}).setdefault(kind, {})
    rec["fails"] = (rec.get("fails") or 0) + 1 if failing else 0
    return rec["fails"] >= DEBOUNCE


def check_dispatcher(env, conf, state):
    if not conf.get("DISPATCHER_SERVICE"):
        return finding("dispatcher", OK, "no dispatcher on this machine")
    version = pu.dispatcher_version(env, conf)
    if version:
        _debounced(state, "dispatcher", False)
        return finding("dispatcher", OK, "answers, version %s" % version)
    if _debounced(state, "dispatcher", True):
        return finding("dispatcher", FIRING, (
            "The dispatcher has not answered http://127.0.0.1:%s/version for %d passes running. "
            "Nothing delegated will start. Check: `launchctl print system/%s` (state, last exit); "
            "start it: `sudo launchctl bootstrap system /Library/LaunchDaemons/%s.plist`."
            % (conf["DISPATCHER_PORT"], DEBOUNCE, conf["DISPATCHER_SERVICE"],
               conf["DISPATCHER_SERVICE"])))
    return finding("dispatcher", UNKNOWN, "did not answer this pass; one more and it fires")


def check_front_door(env, conf, state):
    url = conf.get("PUBLIC_STATUS_URL")
    if not url:
        return finding("front-door", OK, "PUBLIC_STATUS_URL is not set: not checked")
    code = getattr(env, "http_status", http_status)(url)
    if code == 200:
        _debounced(state, "front-door", False)
        return finding("front-door", OK, "answers 200")
    if getattr(env, "http_status", http_status)(NET_PROBE_URL) is None:
        return finding("front-door", UNKNOWN, (
            "%s did not answer, and neither did the internet: no network on this pass "
            "(a background wake has none), so this is not judged" % url))
    if _debounced(state, "front-door", True):
        return finding("front-door", FIRING, (
            "%s answered %s for %d passes running while the internet answers: the tracker's "
            "webhooks are not reaching the dispatcher. 530/1033 means the tunnel is down; 502 "
            "the front door is up and the dispatcher is not." % (url, code or "nothing", DEBOUNCE)))
    return finding("front-door", UNKNOWN, "answered %s this pass; one more and it fires" % code)


def check_disk(env, conf):
    free = getattr(env, "disk_free", None)
    try:
        free_bytes = free() if free else shutil.disk_usage("/").free
    except OSError as exc:
        return finding("disk", UNKNOWN, "could not read free space: %s" % exc)
    floor = int(conf["DISK_FLOOR_GB"])
    gb = free_bytes / float(1 << 30)
    if gb < floor:
        return finding("disk", FIRING, "%.1f GB free on /, below the %d GB floor. Worktrees, "
                                       "transcripts and logs grow until something fails to "
                                       "write." % (gb, floor))
    return finding("disk", OK, "%.0f GB free" % gb)


# Cloudflare answers 403 to Python's default user agent ("Python-urllib/…") and 200 to a
# named one (measured on the reference front door, 2026-10-10). Without this every probe of
# a Cloudflare-fronted door would read as an outage.
USER_AGENT = "pipeline-health-watch/1"


def _request(url):
    import urllib.request
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT})


def http_status(url, timeout=8):
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(_request(url), timeout=timeout) as resp:  # noqa: S310 (fixed URLs)
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Delivery
# --------------------------------------------------------------------------- #
def _clip(text):
    return text if len(text) <= SUMMARY_MAX else text[:SUMMARY_MAX] + " … (cut)"


def send_github(env, conf, kind, state, summary):
    """(delivered, detail). Dispatch, then find that run and wait for it: an accepted
    dispatch whose run failed has filed nothing."""
    repo, wf = conf["ALERT_REPO"], conf["ALERT_WORKFLOW"]
    started = _now(env)
    argv = ["gh", "workflow", "run", wf, "-R", repo, "--ref", "main", "-f", "kind=" + kind,
            "-f", "state=" + state, "-f", "summary=" + _clip(summary + "\n\n" + diagnose(conf))]
    out = env.runner.write("tell GitHub that %s is %s" % (kind, state), argv, timeout=60)
    if out.skipped:
        return True, "dry run"
    if not out.ok:
        return False, "the dispatch was refused: %s" % (out.err or out.out).strip()[:160]
    run_id = None
    for _ in range(RUN_FIND_TRIES):
        got = env.runner.read(["gh", "run", "list", "-R", repo, "--workflow", wf, "--event",
                               "workflow_dispatch", "--limit", "5", "--json",
                               "databaseId,createdAt"], timeout=30)
        try:
            runs = json.loads(got.out) if got.ok else []
        except ValueError:
            runs = []
        fresh = [r for r in runs if (_parse_iso(r.get("createdAt")) or 0) >= started - 30]
        if fresh:
            run_id = str(sorted(fresh, key=lambda r: r.get("createdAt", ""))[-1]["databaseId"])
            break
        env.sleep(5)
    if run_id is None:
        return False, "the dispatch was accepted but no run appeared in %d s" % (RUN_FIND_TRIES * 5)
    watch = env.runner.read(["gh", "run", "watch", run_id, "-R", repo, "--exit-status"],
                            timeout=RUN_WATCH_TIMEOUT)
    if not watch.ok:
        return False, "run %s did not succeed: %s" % (run_id, (watch.err or watch.out).strip()[-160:])
    return True, "run %s succeeded" % run_id


def write_status(env, conf, findings, now, github_note, dry_run):
    path = conf.get("HEALTH_STATUS_FILE")
    if not path:
        return "OFF (HEALTH_STATUS_FILE is empty): the heartbeat monitor has nothing to read"
    firing = sorted(f["kind"] for f in findings if f["state"] == FIRING)
    unknown = sorted(f["kind"] for f in findings if f["state"] == UNKNOWN)
    doc = {"schema": STATUS_SCHEMA, "written_at": _iso(now),
           "result": "problem" if firing else "ok",
           "firing": firing, "unknown": unknown,
           # One identity per firing SET: a new kind firing pages again; a summary that only
           # gets older does not.
           "fingerprint": ",".join(firing) or "ok",
           "findings": [f for f in findings if f["state"] != OK],
           "github": github_note, "diagnose": diagnose(conf)}
    if dry_run:
        return "dry run: would write %s" % path
    try:
        _atomic_write(path, doc, mode=0o644)
        os.chmod(os.path.dirname(path), 0o755)
    except OSError as exc:
        return "COULD NOT WRITE %s: %s" % (path, exc)
    return "written to %s" % path


# --------------------------------------------------------------------------- #
# One pass
# --------------------------------------------------------------------------- #
def run_pass(env, conf, dry_run=False):
    state = load_state(env)
    now = _now(env)
    findings = [check_hooks(env, conf), check_updates(env, conf, state),
                check_release(env, conf), check_dispatcher(env, conf, state),
                check_front_door(env, conf, state), check_disk(env, conf)]
    failed, notes = [], []
    github_on = bool(conf.get("ALERT_REPO"))
    for f in findings:
        rec = state.setdefault("kinds", {}).setdefault(f["kind"], {})
        if f["state"] == UNKNOWN:
            continue                     # not judged: neither a page nor a recovery
        sent = rec.get("sent", OK)       # nothing sent yet reads as "ok": silence on a healthy start
        if f["state"] == sent:
            continue
        if not github_on:
            notes.append("%s is now %s — GitHub alerts are OFF (ALERT_REPO is empty)"
                         % (f["kind"], f["state"]))
            if not dry_run:
                rec["sent"] = f["state"]
            continue
        delivered, detail = send_github(env, conf, f["kind"], f["state"], f["summary"])
        notes.append("%s is now %s — GitHub: %s" % (f["kind"], f["state"], detail))
        if delivered and not dry_run:
            rec["sent"], rec["since"] = f["state"], _iso(now)
        elif not delivered:
            failed.append(f["kind"])
    github_note = ("could not deliver: " + ", ".join(failed)) if failed else (
        "on" if github_on else "OFF")
    status_note = write_status(env, conf, findings, now, github_note, dry_run)
    if not dry_run:
        state["last_run_at"] = _iso(now)
        try:
            _atomic_write(state_path(env), state)
        except OSError as exc:
            notes.append("could not save this pass's state: %s" % exc)
            failed.append("state")
    say("PIPELINE HEALTH WATCH — %s%s" % (_iso(now), " (dry run)" if dry_run else ""))
    for f in findings:
        say("  %-8s %-11s %s" % (f["state"].upper(), f["kind"], f["summary"].splitlines()[0][:110]))
    for n in notes:
        say("  -> %s" % n)
    say("  status file: %s" % status_note)
    if not github_on:
        say("  GitHub alerts: OFF (ALERT_REPO is empty) — a firing kind reaches nobody by GitHub")
    if failed or status_note.startswith("COULD NOT"):
        return EX_FAILED
    states = [f["state"] for f in findings]
    return EX_FIRING if FIRING in states else (EX_UNKNOWN if UNKNOWN in states else EX_OK)


def status(env):
    state = load_state(env)
    if not state.get("last_run_at"):
        say("No pass has run yet (no %s)." % state_path(env))
        return EX_UNKNOWN
    say("Last pass: %s" % state["last_run_at"])
    for kind in KINDS:
        rec = (state.get("kinds") or {}).get(kind) or {}
        say("  %-11s last sent: %-7s since %s%s" % (kind, rec.get("sent", "ok"),
                                                   rec.get("since", "-"),
                                                   ("  (failing passes: %d)" % rec["fails"])
                                                   if rec.get("fails") else ""))
    return EX_OK


# --------------------------------------------------------------------------- #
# The LaunchAgent
# --------------------------------------------------------------------------- #
def agent_plist(conf, python, kit, path_env, home):
    log = os.path.join(home, ".pipeline-update", "watch.log")
    return {
        "Label": conf["WATCH_LABEL"],
        "ProgramArguments": [python, os.path.join(kit, "scripts", "pipeline_watch.py"), "run",
                             "--conf", conf["__source__"]],
        "StartInterval": int(conf["WATCH_INTERVAL_SECONDS"]),
        "RunAtLoad": True,
        "EnvironmentVariables": {"PATH": path_env, "HOME": home},
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "ProcessType": "Background",
    }


def _path_env():
    dirs = []
    for tool in ("brew", "gh", "npm", "git"):
        found = shutil.which(tool)
        if found and os.path.dirname(found) not in dirs:
            dirs.append(os.path.dirname(found))
    return ":".join(dirs + ["/usr/bin", "/bin", "/usr/sbin", "/sbin"])


def install(env, conf, uninstall_only=False):
    pu.refuse_if_agent("pipeline_watch.py " + ("uninstall" if uninstall_only else "install"),
                       env.environ)
    if sys.platform != "darwin" and not getattr(env, "any_platform", False):
        raise pu.UpdateError("the scheduled job is a launchd LaunchAgent: macOS only. Elsewhere, "
                             "run `pipeline_watch.py run` hourly from cron or a systemd timer.")
    label = conf["WATCH_LABEL"]
    plist = os.path.join(_home(env), "Library", "LaunchAgents", label + ".plist")
    domain = "gui/%d" % os.getuid()
    r = env.runner
    r.write("stop %s if it is loaded" % label, ["launchctl", "bootout", "%s/%s" % (domain, label)])
    if uninstall_only:
        if os.path.exists(plist) and not r.dry_run:
            os.remove(plist)
        say("Uninstalled %s (plist removed: %s)." % (label, plist))
        return EX_OK
    say("A dry pass first, so you see what the job will report:")
    run_pass(env, conf, dry_run=True)
    doc = agent_plist(conf, sys.executable, KIT, _path_env(), _home(env))
    say("")
    say("The job: %s every %ds, as you, from %s" % (label, doc["StartInterval"], KIT))
    say("  it runs `pipeline_watch.py run`, logs to %s, and changes nothing it reports on." % doc["StandardOutPath"])
    if env.ask("Type yes to install it: ").strip().lower() != "yes":
        say("Not confirmed. Nothing was installed.")
        return EX_FIRING
    if not r.dry_run:
        os.makedirs(os.path.dirname(plist), exist_ok=True)
        with open(plist, "wb") as fh:
            plistlib.dump(doc, fh)
    boot = r.write("load %s" % label, ["launchctl", "bootstrap", domain, plist])
    if not boot.ok and not boot.skipped:
        raise pu.UpdateError("launchctl bootstrap failed: %s" % (boot.err or boot.out).strip()[:160])
    say("Installed. Check it with: launchctl print %s/%s ; python3 scripts/pipeline_watch.py status"
        % (domain, label))
    return EX_OK


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None, env=None):
    p = argparse.ArgumentParser(prog="pipeline_watch.py", description=__doc__.split("\n")[0])
    p.add_argument("--conf", default=os.path.join(KIT, "update.conf"))
    p.add_argument("--selftest", action="store_true")
    sub = p.add_subparsers(dest="cmd")
    run = sub.add_parser("run")
    run.add_argument("--dry-run", action="store_true")
    sub.add_parser("status")
    sub.add_parser("install")
    sub.add_parser("uninstall")
    args = p.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.cmd:
        p.print_help()
        return EX_USAGE
    env = env or pu.Env()
    try:
        if args.cmd in ("install", "uninstall"):
            pu.refuse_if_agent("pipeline_watch.py " + args.cmd, env.environ)
        if args.cmd == "status":
            return status(env)
        conf = pu.load_conf(args.conf)
        if args.cmd == "run":
            if args.dry_run:
                env.runner.dry_run = True
            return run_pass(env, conf, dry_run=args.dry_run)
        return install(env, conf, uninstall_only=args.cmd == "uninstall")
    except pu.Refusal as exc:
        say(str(exc))
        return EX_REFUSED
    except pu.ConfError as exc:
        say("CONFIG: %s" % exc)
        return EX_USAGE
    except pu.UpdateError as exc:
        say("FAILED: %s" % exc)
        return EX_FAILED


# --------------------------------------------------------------------------- #
# --selftest — offline; every transport stubbed
# --------------------------------------------------------------------------- #
def selftest():
    import tempfile
    fails, ran = [], [0]

    def check(name, got, want):
        ran[0] += 1
        ok = got == want
        print("[%s] %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  (got %r, want %r)" % (got, want)))
        if not ok:
            fails.append(name)

    quiet = []
    se.say = lambda msg="": quiet.append(msg)
    T = 1791700000.0

    def conf(**over):
        values = {"DISPATCHER_SERVICE": "com.test.dispatcher", "ROLE_ACCOUNT": "_testdispatch",
                  "DISPATCHER_ENV_FILE": "/opt/test-dispatch/.env", "DISPATCHER_VERSION": "0.2.73",
                  "BREW_CASKS": "", "BREW_FORMULAE": "", "KIT_CHECKOUT": "/kit",
                  "ALERT_REPO": "example-org/ops-repo", "PUBLIC_STATUS_URL": "https://door.example.com/status"}
        values.update(over)
        c, errors = pu.validate_conf({k: v for k, v in values.items() if v is not None})
        assert not errors, errors
        return c

    def make_env(home, version="0.2.73", latest="0.2.73", door=200, net=200, free_gb=100,
                 gh=(), now=T, answer="yes", environ=None):
        table = [("npm view cyrus-ai version", 0, latest + "\n")] + list(gh)
        env = pu.Env(runner=se.FakeRunner(table),
                     http=lambda url: {"cyrus_cli_version": version} if version and url.endswith("/version") else None,
                     sleep=lambda s: None, ask=lambda q: answer,
                     environ={} if environ is None else environ, home=home)
        env.now = lambda: now
        env.disk_free = lambda: free_gb * (1 << 30)
        env.http_status = lambda url: door if "door" in url else net
        env.any_platform = True
        return env

    def gh_ok(now):
        """GitHub accepting a dispatch and its run succeeding, created just after `now`."""
        return [("gh workflow run", 0, ""),
                ("gh run list", 0, json.dumps([{"databaseId": 77, "createdAt": _iso(now + 5)}])),
                ("gh run watch 77", 0, "")]

    with tempfile.TemporaryDirectory() as home:
        status_file = os.path.join(home, "shared", "pipeline-health", "status.json")
        c = conf(HEALTH_STATUS_FILE=status_file)

        # -- a healthy first pass: nothing sent, the status file says ok ----------------
        env = make_env(home)
        check("a healthy pass exits 0", run_pass(env, c), EX_OK)
        check("…and sends nothing (silence on a healthy start)", env.runner.writes, [])
        doc = json.load(open(status_file))
        check("…and writes the status file the monitor reads",
              (doc["schema"], doc["result"], doc["fingerprint"]), (STATUS_SCHEMA, "ok", "ok"))
        check("…readable by another account (0644)", oct(os.stat(status_file).st_mode & 0o777), "0o644")

        # -- the dispatcher stops: one pass is not enough, two fire, once ---------------
        env = make_env(home, version=None, gh=gh_ok(T))
        check("one silent pass from the dispatcher is UNKNOWN, not a page",
              (run_pass(env, c), env.runner.writes), (EX_UNKNOWN, []))
        env = make_env(home, version=None, gh=gh_ok(T + 3600), now=T + 3600)
        code = run_pass(env, c)
        sent = [w["argv"] for w in env.runner.writes]
        check("the second silent pass fires and tells GitHub once",
              (code, len(sent), "kind=dispatcher" in sent[0], "state=firing" in sent[0]),
              (EX_FIRING, 1, True, True))
        check("…and the status file now names it",
              json.load(open(status_file))["firing"], ["dispatcher"])
        env = make_env(home, version=None, gh=gh_ok(T + 7200), now=T + 7200)
        run_pass(env, c)
        check("a third silent pass sends nothing more (one page per problem)", env.runner.writes, [])
        env = make_env(home, gh=gh_ok(T + 10800), now=T + 10800)
        run_pass(env, c)
        sent = [w["argv"] for w in env.runner.writes]
        check("when it answers again, GitHub is told it cleared",
              (len(sent), "state=ok" in sent[0] if sent else False), (1, True))

        # -- delivery that fails is not recorded as sent ---------------------------------
        bad_gh = [("gh workflow run", 0, ""),
                  ("gh run list", 0, json.dumps([{"databaseId": 78, "createdAt": _iso(T + 20000)}])),
                  ("gh run watch 78", 1, "")]
        env = make_env(home, free_gb=1, gh=bad_gh, now=T + 20000)
        check("a GitHub run that failed is exit 1 (not delivered)", run_pass(env, c), EX_FAILED)
        env = make_env(home, free_gb=1, gh=gh_ok(T + 23600), now=T + 23600)
        run_pass(env, c)
        check("…so the next pass sends it again",
              any("kind=disk" in w["argv"] for w in env.runner.writes), True)

        # -- the front door: no internet is UNKNOWN, never an outage ---------------------
        env = make_env(home, door=None, net=None, free_gb=100, gh=gh_ok(T + 30000), now=T + 30000)
        run_pass(env, c)
        env2 = make_env(home, door=None, net=None, free_gb=100, gh=gh_ok(T + 33600), now=T + 33600)
        code = run_pass(env2, c)
        check("a front door unreachable with no internet never pages (background wake)",
              any("kind=front-door" in w["argv"] for w in env.runner.writes + env2.runner.writes), False)
        env3 = make_env(home, door=530, net=200, gh=gh_ok(T + 37200), now=T + 37200)
        run_pass(env3, c)
        env4 = make_env(home, door=530, net=200, gh=gh_ok(T + 40800), now=T + 40800)
        run_pass(env4, c)
        check("a front door answering 530 twice while the internet works fires",
              any("kind=front-door" in w["argv"] and "state=firing" in w["argv"]
                  for w in env4.runner.writes), True)

    with tempfile.TemporaryDirectory() as home:
        # -- hooks: a fresh root-unresolved record fires, an old one does not -------------
        kit = os.path.join(home, "kit")
        nag = os.path.join(kit, ".claude", ".stop-pr-nag")
        os.makedirs(nag)
        rec = os.path.join(nag, "session-abc" + UNRESOLVED_SUFFIX)
        open(rec, "w").write("1")
        os.utime(rec, (T - 3600, T - 3600))
        c = conf(KIT_CHECKOUT=kit)
        env = make_env(home)
        check("a root-unresolved record from the last day fires `hooks`",
              check_hooks(env, c)["state"], FIRING)
        os.utime(rec, (T - 3 * 86400, T - 3 * 86400))
        check("…and one older than a day does not", check_hooks(env, c)["state"], OK)
        open(os.path.join(nag, "feat_x__no-pr"), "w").write("1")
        check("other Stop-hook records are not mistaken for it", check_hooks(env, c)["state"], OK)

        # -- release and updates --------------------------------------------------------
        check("a release newer than the pin fires `release`",
              check_release(make_env(home, latest="0.2.74"), c)["state"], FIRING)
        check("the pin being latest is ok", check_release(make_env(home), c)["state"], OK)
        check("`latest` (no pin) never fires release",
              check_release(make_env(home, latest="9.9.9"), conf(DISPATCHER_VERSION="latest"))["state"], OK)
        st = {}
        check("an old dispatcher fires `updates`",
              check_updates(make_env(home, version="0.2.69"), c, st)["state"], FIRING)
        cached = check_updates(make_env(home, version="0.2.73", now=T + 3600), c, st)
        check("the review is cached for hours, not re-run every pass", cached["state"], FIRING)
        fresh = check_updates(make_env(home, version="0.2.73", now=T + 21 * 3600), c, st)
        check("…and re-measured after the cache expires", fresh["state"], OK)
        check("a dispatcher that does not answer is the `dispatcher` kind's, not `updates`'",
              check_updates(make_env(home, version=None), c, {})["state"], OK)

        # -- channels OFF are said, never silent ----------------------------------------
        quiet[:] = []
        env = make_env(home, free_gb=1)
        run_pass(env, conf(ALERT_REPO="", KIT_CHECKOUT=kit))
        check("with ALERT_REPO empty the pass says GitHub is OFF",
              any("GitHub alerts: OFF" in q for q in quiet), True)
        check("…and with no status file it says the monitor has nothing to read",
              any("HEALTH_STATUS_FILE is empty" in q for q in quiet), True)

        # -- dry run sends and writes nothing ----------------------------------------------
        env = make_env(home, free_gb=1, gh=gh_ok(T + 50000), now=T + 50000)
        env.runner.dry_run = True
        before = json.dumps(load_state(env), sort_keys=True)
        run_pass(env, conf(HEALTH_STATUS_FILE=os.path.join(home, "dry.json"), KIT_CHECKOUT=kit),
                 dry_run=True)
        check("--dry-run applies no send", env.runner.applied, [])
        check("…writes no status file", os.path.exists(os.path.join(home, "dry.json")), False)
        check("…and leaves the state alone", json.dumps(load_state(env), sort_keys=True), before)

        # -- the installer ---------------------------------------------------------------
        c = conf(KIT_CHECKOUT=kit)
        c["__source__"] = "/kit/update.conf"
        check("install is refused in an agent environment",
              main(["--conf", "/nonexistent", "install"], env=make_env(home, environ={"CLAUDECODE": "1"})),
              EX_REFUSED)
        env = make_env(home, answer="no")
        check("install without a typed yes installs nothing",
              (install(env, c), os.path.exists(os.path.join(home, "Library", "LaunchAgents",
                                                            c["WATCH_LABEL"] + ".plist"))),
              (EX_FIRING, False))
        env = make_env(home, gh=[("launchctl bootout", 0, ""), ("launchctl bootstrap", 0, "")])
        code = install(env, c)
        plist = os.path.join(home, "Library", "LaunchAgents", c["WATCH_LABEL"] + ".plist")
        doc = plistlib.load(open(plist, "rb"))
        check("install writes the LaunchAgent and loads it in the user's own domain",
              (code, doc["StartInterval"], doc["ProgramArguments"][2:4],
               any(w["argv"][:2] == ["launchctl", "bootstrap"] and w["argv"][2].startswith("gui/")
                   for w in env.runner.writes)),
              (EX_OK, 3600, ["run", "--conf"], True))
        check("the job's PATH ends with the system directories",
              doc["EnvironmentVariables"]["PATH"].endswith("/usr/bin:/bin:/usr/sbin:/sbin"), True)

    check("the probe names itself: Cloudflare refuses Python's default user agent",
          _request("https://door.example.com/status").get_header("User-agent"), USER_AGENT)

    print("\n%d/%d checks passed" % (ran[0] - len(fails), ran[0]))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
