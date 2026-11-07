#!/usr/bin/env python3
"""The look tool: one browser check, run in a locked sandbox (KIT-323, docs/LOOK-TOOL.md).

A stdio MCP server, standard library only, with one tool, `look_run`. A session that has built its app asks it to run
one browser check block (docs/PIPELINE-CONTRACT.md §15) against the build folder. The tool runs node, Playwright and
Chromium under the kit's seatbelt profile, `templates/sandbox/look.sb`: no network, none of the role account's secrets,
reads limited to the worktree, node and the browser bundle, writes only to a fresh temp folder. It returns each step's
outcome and the screenshots, and keeps its own record of every run where the session cannot write.

    python3 scripts/pipeline_look.py --config look.json      # the MCP server, as an acceptance session's tool
    python3 scripts/pipeline_look.py --selftest

THE CONFIG is a file the session cannot write (root-owned, outside every worktree): the session's `worktree`, the
`record_dir` (outside it), the `node` binary, the `browsers` bundle, the `role_home`, the `plan` this server counts runs
against, and `max_runs` per plan. The session chooses only the build folder and the check block: not the profile, the
browser, the environment or the record.

WHAT IT REFUSES, and says so: a check block that fails `schemas/browser-check.schema.json`; a build folder outside the
session's worktree (a `..` or a link included); a worktree with no `delivery.json` `acceptance` section; a plan past
`max_runs`; a profile that is not there. It never runs the runner without the profile.
"""
import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import jsonschema_mini as js  # noqa: E402
from scratch_run import scratch_run  # noqa: E402

PROFILE = os.path.join(KIT, "templates", "sandbox", "look.sb")
RUNNER = os.path.join(HERE, "look_runner.mjs")
SCHEMA_FILE = os.path.join(KIT, "schemas", "browser-check.schema.json")
SANDBOX_EXEC = "/usr/bin/sandbox-exec"
RECORD_SCHEMA = "pipeline-look-record/1"
PROTOCOL = "2024-11-05"
DEFAULT_MAX_RUNS, DEFAULT_TIMEOUT = 6, 600
MAX_IMAGES, MAX_IMAGE_BYTES = 4, 1500000
CONFIG_KEYS = ("worktree", "record_dir", "node", "browsers", "role_home", "plan", "max_runs", "timeout_seconds",
               "base_ref", "base_git_dir", "playwright_dir")
BASE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
DEFAULT_BASE_REF = "origin/main"
MAX_FAKE_BYTES = 2000000
PLAN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
TOOL = {
    "name": "look_run",
    "description": ("Run one browser check block against the app you built, in a locked sandbox with no network, and get "
                    "each step's outcome and screenshots back. `build_dir` is the built app inside your worktree; `check` "
                    "is one pipeline-browser-check/1 block. A plan has a fixed number of runs."),
    "inputSchema": {"type": "object", "additionalProperties": False, "required": ["build_dir", "check"],
                    "properties": {"build_dir": {"type": "string", "description": "the build folder, inside your worktree"},
                                   "check": {"type": "object", "description": "one pipeline-browser-check/1 block"}}},
}


class Refused(Exception):
    """A request the tool will not run. Its text says why, in words a session can act on."""


def load_config(path):
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as exc:
        raise Refused("could not read --config %s: %s" % (path, exc))
    if not isinstance(raw, dict) or set(raw) - set(CONFIG_KEYS):
        raise Refused("config keys must be among: %s" % ", ".join(CONFIG_KEYS))
    cfg = dict(raw)
    for key in ("worktree", "record_dir", "node", "browsers", "role_home"):
        value = cfg.get(key)
        if not isinstance(value, str) or not os.path.isabs(value):
            raise Refused("config %r must be an absolute path" % key)
        cfg[key] = os.path.realpath(value)
    # The git directory the base copy is read from: a clone the tool's own account owns, never found from the
    # session's worktree (whose refs, config and attributes the session can write). Unset: no base copy.
    bgd = cfg.get("base_git_dir")
    if bgd is not None:
        if not isinstance(bgd, str) or not os.path.isabs(bgd):
            raise Refused("config 'base_git_dir' must be an absolute path")
        cfg["base_git_dir"] = os.path.realpath(bgd)
        if _inside(cfg["base_git_dir"], cfg["worktree"]) or _inside(cfg["worktree"], cfg["base_git_dir"]):
            raise Refused("config 'base_git_dir' must be outside the worktree: the session must not be able to write it")
    # Playwright is loaded from here, not from the session's node_modules (KIT-550): a folder holding
    # node_modules/playwright and node_modules/playwright-core, installed with the browsers, owned by the tool's
    # account and not writable by others. Unset: the tool cannot say a check passed.
    pwd = cfg.get("playwright_dir")
    if pwd is not None:
        if not isinstance(pwd, str) or not os.path.isabs(pwd):
            raise Refused("config 'playwright_dir' must be an absolute path")
        cfg["playwright_dir"] = os.path.realpath(pwd)
        if _inside(cfg["playwright_dir"], cfg["worktree"]) or _inside(cfg["worktree"], cfg["playwright_dir"]):
            raise Refused("config 'playwright_dir' must be outside the worktree: the session must not be able to write it")
    cfg["base_ref"] = cfg.get("base_ref", DEFAULT_BASE_REF)
    if not isinstance(cfg["base_ref"], str) or not BASE_REF_RE.match(cfg["base_ref"]) or ".." in cfg["base_ref"]:
        raise Refused("config 'base_ref' must be a branch name such as origin/main")
    if not PLAN_RE.match(str(cfg.get("plan") or "")):
        raise Refused("config 'plan' must be a short name of letters, digits, dots, dashes and underscores")
    for key, default, low, high in (("max_runs", DEFAULT_MAX_RUNS, 1, 50), ("timeout_seconds", DEFAULT_TIMEOUT, 30, 3600)):
        cfg[key] = cfg.get(key, default)
        if not isinstance(cfg[key], int) or isinstance(cfg[key], bool) or not low <= cfg[key] <= high:
            raise Refused("config %r must be a whole number from %d to %d" % (key, low, high))
    if _inside(cfg["record_dir"], cfg["worktree"]) or _inside(cfg["worktree"], cfg["record_dir"]):
        raise Refused("config 'record_dir' must be outside the worktree: the session must not be able to write the record")
    if _inside(cfg["worktree"], cfg["role_home"]):
        raise Refused("config 'worktree' must be outside the role account's home: the profile closes that home to the runner")
    return cfg


def _inside(path, root):
    root = root.rstrip(os.sep) + os.sep
    return (path.rstrip(os.sep) + os.sep).startswith(root)


def _records(cfg):
    folder = os.path.join(cfg["record_dir"], cfg["plan"])
    try:
        return sorted(n for n in os.listdir(folder) if n.endswith(".json"))
    except FileNotFoundError:
        return []


def _uses_fake_backend(delivery):
    try:
        with open(delivery, encoding="utf-8") as fh:
            return bool((json.load(fh).get("acceptance") or {}).get("fakeBackend"))
    except (OSError, ValueError, AttributeError):
        return True  # cannot tell: say the less trusting thing


def _git_out(git_dir, *args):
    """(bytes, error): git run against ONE fixed git directory, with no user or system config, no replace
    objects and no attributes file, so nothing the session can write changes what it prints."""
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_CONFIG_SYSTEM": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "HOME": os.devnull,
           "GIT_NO_REPLACE_OBJECTS": "1", "GIT_ATTR_NOSYSTEM": "1"}
    try:
        done = subprocess.run(["git", "--git-dir=" + git_dir, "-c", "core.attributesFile=" + os.devnull, *args],
                              capture_output=True, timeout=60, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    if done.returncode != 0:
        return None, done.stderr.decode("utf-8", "replace").strip()[-200:]
    return done.stdout, None


def base_copy(cfg, out):
    """(config path, fake-backend root, note). The `acceptance` section and the fake backend as the BASE
    branch holds them, written into the run's fresh folder, where the session under test cannot change them
    (KIT-522). (None, None, why) when the base has no such section or no readable copy: the caller then falls
    back to the worktree's and says so in the record."""
    import io
    import tarfile
    ref = cfg["base_ref"]
    gd = cfg.get("base_git_dir")
    if not gd:
        return None, None, "no base_git_dir is configured, so there is no copy of the base branch the session cannot write"
    try:
        st = os.stat(gd)
    except OSError as exc:
        return None, None, "the base git directory cannot be read: %s" % exc
    if st.st_uid != os.getuid() or st.st_mode & 0o022:
        return None, None, "the base git directory is not owned by this account alone, or others can write it"
    raw, err = _git_out(gd, "show", "%s:delivery.json" % ref)
    if raw is None:
        return None, None, "the base branch (%s) has no readable delivery.json: %s" % (ref, err)
    try:
        doc = json.loads(raw.decode("utf-8"))
        acc = doc.get("acceptance")
    except (ValueError, AttributeError):
        return None, None, "the base branch's delivery.json does not parse"
    if not isinstance(acc, dict):
        return None, None, "the base branch has no `acceptance` section"
    path = os.path.join(out, "base-delivery.json")
    with open(path, "wb") as fh:
        fh.write(raw)
    fake = acc.get("fakeBackend")
    if not fake:
        return path, None, None
    rel = os.path.normpath(str(fake))
    if os.path.isabs(rel) or rel.startswith("..") or rel in (".", ""):
        return None, None, "the base branch's fakeBackend path is not a path inside the repository"
    folder = os.path.dirname(rel) or rel   # a file at the top is taken alone
    blob, err = _git_out(gd, "archive", "--format=tar", ref, "--", folder)
    if blob is None:
        return None, None, "the base branch has no copy of the fake backend at %s: %s" % (folder, err)
    if len(blob) > MAX_FAKE_BYTES:
        return None, None, "the base branch's fake backend folder is larger than %d bytes" % MAX_FAKE_BYTES
    dest = os.path.join(out, "fake-base")
    os.makedirs(dest)
    try:
        with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
            members = tar.getmembers()
            for m in members:
                target = os.path.normpath(os.path.join(dest, m.name))
                if not (m.isfile() or m.isdir()) or not _inside(target, dest):
                    return None, None, "the base branch's fake backend holds a link or a path out of its folder"
            tar.extractall(dest, members=members)
    except (tarfile.TarError, OSError) as exc:
        return None, None, "could not unpack the base branch's fake backend: %s" % exc
    if not os.path.isfile(os.path.join(dest, rel)):
        return None, None, "the base branch has no fake backend file %s" % rel
    return path, dest, None


def check_request(cfg, args):
    """(build_dir, block, delivery_path), or Refused."""
    if not isinstance(args, dict) or set(args) != {"build_dir", "check"}:
        raise Refused("look_run takes exactly build_dir and check")
    if not isinstance(args["build_dir"], str) or not args["build_dir"]:
        raise Refused("build_dir must be a path")
    with open(SCHEMA_FILE, encoding="utf-8") as fh:
        schema = json.load(fh)
    errors = js.validate(args["check"], schema)
    if errors:
        raise Refused("the check block fails schemas/browser-check.schema.json: " + "; ".join(
            "%s %s" % (e["path"] or "(block)", e["message"]) for e in errors[:3]))
    build = os.path.realpath(os.path.join(cfg["worktree"], args["build_dir"])
                             if not os.path.isabs(args["build_dir"]) else args["build_dir"])
    if not _inside(build, cfg["worktree"]):
        raise Refused("build_dir is outside your worktree (%s). Only a build folder you made there can be looked at"
                      % cfg["worktree"])
    if not os.path.isdir(build):
        raise Refused("build_dir %s is not a folder: build the app first" % args["build_dir"])
    delivery = os.path.join(cfg["worktree"], "delivery.json")
    try:
        with open(delivery, encoding="utf-8") as fh:
            acceptance = json.load(fh).get("acceptance")
    except (OSError, ValueError, AttributeError):
        acceptance = None
    if not isinstance(acceptance, dict):
        raise Refused("the worktree's delivery.json has no `acceptance` section, so the acceptance check is off here")
    return build, args["check"], delivery


def trusted_playwright(cfg):
    """(folder, None) or (None, why-not): the Playwright copy the runner may load. Every level down to the
    package is owned by this account and writable by nobody else, so a session cannot change what it loads."""
    d = cfg.get("playwright_dir")
    if not d:
        return None, "no playwright_dir is configured, so there is no Playwright copy the session cannot write"
    for rel in ("", "node_modules", "node_modules/playwright", "node_modules/playwright-core"):
        path = os.path.join(d, rel)
        try:
            st = os.stat(path)
        except OSError as exc:
            return None, "the Playwright copy is incomplete (%s): %s" % (rel or ".", exc.strerror)
        if st.st_uid != os.getuid() or st.st_mode & 0o022:
            return None, "the Playwright copy (%s) is not owned by this account alone, or others can write it" % (rel or ".")
    return d, None


def sandbox_argv(cfg, out, build, delivery, block_path, fake_root=None, playwright_dir=None):
    """The one command line the runner is ever run with: under the kit's profile, nothing else."""
    node_dir = os.path.dirname(os.path.dirname(cfg["node"]))
    params = {"READ_ROOT": cfg["worktree"], "NODE_DIR": node_dir, "BROWSERS": cfg["browsers"], "KIT_DIR": os.path.realpath(HERE),
              "OUT_DIR": os.path.realpath(out), "ROLE_HOME": cfg["role_home"],
              "PLAYWRIGHT_DIR": playwright_dir or "/var/empty"}
    argv = [SANDBOX_EXEC, "-f", PROFILE]
    for name in sorted(params):
        argv += ["-D", "%s=%s" % (name, params[name])]
    return argv + [cfg["node"], RUNNER, "--config", delivery, "--checks", block_path, "--app-root", cfg["worktree"],
                   "--root", cfg["worktree"], "--build-dir", build, "--out", os.path.join(out, "shots")] \
        + (["--fake-root", fake_root] if fake_root else []) \
        + (["--playwright-root", playwright_dir] if playwright_dir else [])


def clean_env(cfg, out):
    """Only these: nothing inherited, so no token or key of the role account's reaches the browser."""
    return {"PATH": os.path.dirname(cfg["node"]) + ":/usr/bin:/bin", "HOME": out, "TMPDIR": out,
            "PLAYWRIGHT_BROWSERS_PATH": cfg["browsers"], "LANG": "en_US.UTF-8"}


def default_runner(argv, env, cwd, timeout):
    try:
        done = subprocess.run(argv, env=env, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "", "stopped after %d seconds" % timeout
    except OSError as exc:
        return 127, "", str(exc)
    return done.returncode, done.stdout, done.stderr


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def look_run(cfg, args, runner=None, now=None):
    """(text, images): refuses with Refused; otherwise runs, records, and returns what was seen."""
    if not os.path.isfile(PROFILE):
        raise Refused("the kit's sandbox profile is missing (%s): the runner is never run without it" % PROFILE)
    build, block, delivery = check_request(cfg, args)
    done = _records(cfg)
    if len(done) >= cfg["max_runs"]:
        raise Refused("this plan has used its %d look runs (%d recorded). No more are allowed for it: say what you could "
                      "not check in your result, and the owner can raise the number" % (cfg["max_runs"], len(done)))
    run_id = "%03d-%s" % (len(done) + 1, time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(time.time() if now is None else now)))
    record = {"schema": RECORD_SCHEMA, "plan": cfg["plan"], "run_id": run_id, "checks": [c["id"] for c in block["checks"]],
              "runner_exit": None, "result": None, "runs": []}
    # A fake backend is the session's own code, run inside the runner: with one, the outcome below is what that
    # code and the runner together reported, not a mechanical fact. Said in the record so no reader takes it as one.
    images, lines = [], []
    with scratch_run("look-run-") as out:
        # KIT-522: the `acceptance` section and the fake backend come from the BASE branch when it has them,
        # so the session under test cannot shape a check's result through either. Without a base copy the
        # worktree's is used and the record says why. Playwright itself loads from its own trusted copy (below).
        base_cfg, fake_root, why_not = base_copy(cfg, out)
        if base_cfg:
            delivery = base_cfg
            record["outcome_trust"] = "base-copy" if fake_root else "runner-only"
        else:
            record["outcome_trust"] = "session-code-in-runner" if _uses_fake_backend(delivery) else "runner-only"
            record["base_copy"] = why_not
        # KIT-550: Playwright loads from a copy the session cannot write. Without one the browser is not run and the
        # record says so; a run that cannot get that copy is never a pass.
        pw_dir, pw_why = trusted_playwright(cfg)
        record["playwright_from"] = "trusted-copy" if pw_dir else "none"
        if pw_dir is None:
            record["playwright_note"] = pw_why
        block_path = os.path.join(out, "block.json")
        with open(block_path, "w", encoding="utf-8") as fh:
            json.dump(block, fh)
        os.makedirs(os.path.join(out, "shots"))
        no_copy = lambda *a: (3, "", "")                                          # noqa: E731
        rc, so, se = (no_copy if pw_dir is None else (runner or default_runner))(
            sandbox_argv(cfg, out, build, delivery, block_path, fake_root, pw_dir), clean_env(cfg, out), out,
                                               cfg["timeout_seconds"])
        record["runner_exit"] = rc
        result_path = os.path.join(out, "shots", "result.json")
        result = None
        if os.path.isfile(result_path):
            try:
                with open(result_path, encoding="utf-8") as fh:
                    result = json.load(fh)
            except ValueError:
                result = None
        if rc in (0, 1, 3) and isinstance(result, dict):
            record["result"] = "passed" if rc == 0 else "failed" if rc == 1 else "could-not-run"
            for run in result.get("runs") or []:
                shots = []
                for path in run.get("shots") or []:
                    real = os.path.realpath(path)
                    if _inside(real, out) and os.path.isfile(real):
                        shots.append({"name": os.path.basename(real), "sha256": sha256(real)})
                        if len(images) < MAX_IMAGES and os.path.getsize(real) <= MAX_IMAGE_BYTES:
                            with open(real, "rb") as fh:
                                images.append((os.path.basename(real), base64.b64encode(fh.read()).decode("ascii")))
                steps = [{k: v for k, v in s.items() if k in ("i", "do", "failed", "error", "skipped", "via")}
                         for s in run.get("steps") or []]
                record["runs"].append({"check": run.get("check"), "viewport": run.get("viewport"), "mode": run.get("mode"),
                                       "status": run.get("status"), "steps": steps, "shots": shots,
                                       "unanswered": run.get("unanswered") or [], "errors": (run.get("errors") or [])[:5]})
        else:
            record["result"] = "crashed"
            record["detail"] = ("the runner exited %s and left no result: %s" % (rc, (se or so or "").strip()[-300:])).strip()
    if record["playwright_from"] == "none":
        record["result"] = "could-not-run"
        record["detail"] = "no browser was started: " + record["playwright_note"]
        record["runs"] = []
    folder = os.path.join(cfg["record_dir"], cfg["plan"])
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, ".%s.tmp" % run_id)
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=1, sort_keys=True)
    os.replace(tmp, os.path.join(folder, run_id + ".json"))
    lines.append("look run %s of %d for plan %s: %s" % (len(done) + 1, cfg["max_runs"], cfg["plan"], record["result"].upper()))
    if record["result"] in ("crashed", "could-not-run") and record.get("detail"):
        lines.append(record["detail"])
    for run in record["runs"]:
        bad = [s for s in run["steps"] if s.get("failed") or s.get("error")]
        lines.append("  %s / %s / %s: %s" % (run["check"], run["viewport"], run["mode"], run["status"]))
        for s in bad[:4]:
            lines.append("    step %s (%s): %s" % (s.get("i"), s.get("do"), "; ".join(s.get("failed") or [s.get("error")])))
        if run["unanswered"]:
            lines.append("    the fake backend did not answer: %s" % ", ".join(run["unanswered"][:5]))
    lines.append("%d screenshots kept in the record, %d attached." % (sum(len(r["shots"]) for r in record["runs"]), len(images)))
    return "\n".join(lines), images


# --------------------------------------------------------------------------- #
# The MCP server: newline-delimited JSON-RPC over stdio
# --------------------------------------------------------------------------- #
def handle(msg, cfg, runner=None):
    """The response for one JSON-RPC message, or None for a notification."""
    if not isinstance(msg, dict) or "method" not in msg:
        return None
    mid, method = msg.get("id"), msg["method"]
    if "id" not in msg:
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def err(code, text):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": text}}
    if method == "initialize":
        return ok({"protocolVersion": (msg.get("params") or {}).get("protocolVersion") or PROTOCOL,
                   "capabilities": {"tools": {}}, "serverInfo": {"name": "look", "version": "1"}})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": [TOOL]})
    if method == "tools/call":
        params = msg.get("params") or {}
        if params.get("name") != "look_run":
            return err(-32602, "unknown tool %r" % params.get("name"))
        try:
            text, images = look_run(cfg, params.get("arguments"), runner)
        except Refused as exc:
            return ok({"isError": True, "content": [{"type": "text", "text": "REFUSED: %s" % exc}]})
        except Exception as exc:                                   # noqa: BLE001 — a tool never kills its server
            return ok({"isError": True, "content": [{"type": "text", "text": "the look tool failed: %s" % type(exc).__name__}]})
        content = [{"type": "text", "text": text}] + [
            {"type": "image", "data": data, "mimeType": "image/png"} for _name, data in images]
        return ok({"content": content})
    return err(-32601, "method not found: %s" % method)


def serve(cfg, stdin=None, stdout=None, runner=None):
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            stdout.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}) + "\n")
            stdout.flush()
            continue
        reply = handle(msg, cfg, runner)
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="The look tool: one browser check in a locked sandbox (KIT-323).")
    p.add_argument("--config")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args(argv)
    if args.selftest:
        from scratch_run import contained
        return contained(selftest, "look-selftest-run-")
    if not args.config:
        p.print_usage(sys.stderr)
        return 2
    try:
        cfg = load_config(args.config)
    except Refused as exc:
        sys.stderr.write("look: %s\n" % exc)
        return 2
    return serve(cfg)


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #
def selftest():
    import io
    import shutil
    import tempfile
    fails, ran, skipped = [], [0], []

    def check(name, got, want):
        ran[0] += 1
        ok = got == want
        print("[%s] %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  (got %r, want %r)" % (got, want)))
        if not ok:
            fails.append(name)

    def skip(name, why):
        skipped.append(name)
        print("[SKIP] %s: %s" % (name, why))

    root = os.path.realpath(tempfile.mkdtemp(prefix="look-case-"))
    try:
        wt, rec, home = (os.path.join(root, n) for n in ("wt", "rec", "home"))
        for d in (wt + "/dist", rec, home, root + "/node/bin", root + "/browsers"):
            os.makedirs(d)
        node = root + "/node/bin/node"
        open(node, "w").close()
        with open(wt + "/delivery.json", "w") as fh:
            json.dump({"acceptance": {"serve": {"mode": "files", "dir": "dist"}}}, fh)
        cfgfile = root + "/look.json"

        pwd = os.path.join(root, "pwcopy")
        for rel in ("node_modules/playwright", "node_modules/playwright-core"):
            os.makedirs(os.path.join(pwd, rel))
        for rel in ("", "node_modules", "node_modules/playwright", "node_modules/playwright-core"):
            os.chmod(os.path.join(pwd, rel), 0o755)

        def conf(**over):
            doc = {"worktree": wt, "record_dir": rec, "node": node, "browsers": root + "/browsers", "role_home": home,
                   "plan": "plan-1", "max_runs": 2, "playwright_dir": pwd}
            doc.update(over)
            with open(cfgfile, "w") as fh:
                json.dump(doc, fh)
            return load_config(cfgfile)

        def refusal(fn):
            try:
                fn()
                return None
            except Refused as exc:
                return str(exc)
        block = {"schema": "pipeline-browser-check/1", "checks": [{"id": "c1", "viewports": ["desktop"],
                                                              "modes": ["default"], "open": "/", "steps": [
            {"do": "screenshot", "name": "home"}]}]}
        seen = {}

        def fake(argv, env, cwd, timeout, rc=0, shots=("home",)):
            seen.update(argv=argv, env=env, cwd=cwd, timeout=timeout)
            out = argv[argv.index("--out") + 1]
            os.makedirs(out, exist_ok=True)
            runs = []
            for name in shots:
                png = os.path.join(out, "c1--desktop--default--%s.png" % name)
                with open(png, "wb") as fh:
                    fh.write(b"\x89PNG fake " + name.encode())
                runs.append(png)
            with open(os.path.join(out, "result.json"), "w") as fh:
                json.dump({"runs": [{"check": "c1", "viewport": "desktop", "mode": "default",
                                     "status": "passed" if rc == 0 else "failed", "shots": runs,
                                     "steps": [{"i": 0, "do": "screenshot"}] + (
                                         [{"i": 1, "do": "expect", "failed": ["text lacks 'x'"]}] if rc else [])}]}, fh)
            return rc, "", ""

        cfg = conf()
        good = {"build_dir": "dist", "check": block}
        # the schema, the build folder, the worktree's own acceptance section
        check("a block that fails the schema is refused, naming where",
              "fails schemas/browser-check.schema.json" in (refusal(lambda: look_run(cfg, dict(good, check={"schema": "x"}))) or ""),
              True)
        check("an extra field in the block is refused (the schema is closed)", refusal(lambda: look_run(
            cfg, dict(good, check=dict(block, extra=1)), fake)) is not None, True)
        os.symlink(root, wt + "/escape")
        for bad in ("../rec", "/etc", "escape", "dist/../../rec"):
            check("a build folder outside the worktree is refused: %s" % bad,
                  "outside your worktree" in (refusal(lambda b=bad: look_run(cfg, dict(good, build_dir=b), fake)) or ""), True)
        check("a build folder that is not there says to build first",
              "build the app first" in (refusal(lambda: look_run(cfg, dict(good, build_dir="nope"), fake)) or ""), True)
        check("a request with another field (a profile, a browser, a record) is refused",
              all(refusal(lambda k=k: look_run(cfg, dict(good, **{k: "/x"}), fake)) for k in ("profile", "browsers", "record_dir")),
              True)
        os.rename(wt + "/delivery.json", wt + "/delivery.off")
        check("a worktree with no delivery.json acceptance section is refused: the check is off there",
              "acceptance check is off" in (refusal(lambda: look_run(cfg, good, fake)) or ""), True)
        os.rename(wt + "/delivery.off", wt + "/delivery.json")

        me = sys.modules[__name__]
        real_profile = me.PROFILE
        me.PROFILE = root + "/no-such.sb"
        try:
            said = refusal(lambda: look_run(cfg, good, fake))
        finally:
            me.PROFILE = real_profile
        check("a missing profile is refused: the runner is never run without it",
              ("never run without it" in (said or ""), "argv" in seen and seen["argv"][2] == real_profile or "argv" not in seen),
              (True, True))

        # the command line: only under the kit's profile, with a clean environment
        os.environ["LINEAR_API_KEY"] = "must-not-leak"
        text, images = look_run(cfg, good, fake, now=0)
        argv, env = seen["argv"], seen["env"]
        check("the runner runs only under sandbox-exec with the kit's own profile",
              (argv[:3], os.path.realpath(argv[2]) == os.path.realpath(PROFILE)), ([SANDBOX_EXEC, "-f", PROFILE], True))
        check("every profile parameter is passed, and the role home is the one denied",
              sorted(a.split("=")[0] for a in argv if re.match(r"^[A-Z_]+=", a)),
              ["BROWSERS", "KIT_DIR", "NODE_DIR", "OUT_DIR", "PLAYWRIGHT_DIR", "READ_ROOT", "ROLE_HOME"])
        check("the node and the runner are the config's and the kit's, and --build-dir is the resolved build folder",
              (node in argv, RUNNER in argv, argv[argv.index("--build-dir") + 1] == wt + "/dist"), (True, True, True))
        check("the environment is exactly the clean set, with the browser bundle set and no inherited secret",
              (sorted(env), env["PLAYWRIGHT_BROWSERS_PATH"], "must-not-leak" in " ".join(env.values())),
              (["HOME", "LANG", "PATH", "PLAYWRIGHT_BROWSERS_PATH", "TMPDIR"], root + "/browsers", False))
        check("it runs from its own fresh folder, never the worktree", (seen["cwd"] != wt, seen["timeout"]), (True, 600))
        os.environ.pop("LINEAR_API_KEY")

        # the record: outside the worktree, with hashes
        names = _records(cfg)
        recd = json.load(open(os.path.join(rec, "plan-1", names[0])))
        want = hashlib.sha256(b"\x89PNG fake home").hexdigest()
        check("each run writes its record outside the worktree: the check, the step outcomes and the screenshot hashes",
              (len(names), recd["schema"], recd["checks"], recd["result"], recd["runs"][0]["shots"][0]["sha256"] == want,
               _inside(rec, wt)), (1, RECORD_SCHEMA, ["c1"], "passed", True, False))
        check("the record says the outcome came from the runner alone when there is no fake backend",
              recd["outcome_trust"], "runner-only")
        with open(wt + "/delivery.json", "w") as fh:
            json.dump({"acceptance": {"serve": {"mode": "files", "dir": "dist"}, "fakeBackend": "fake.mjs"}}, fh)
        look_run(conf(plan="plan-fb"), good, fake, now=9)
        fbrec = json.load(open(os.path.join(rec, "plan-fb", _records(conf(plan="plan-fb"))[0])))
        check("with a fake backend (the session's own code in the runner) the record says so",
              fbrec["outcome_trust"], "session-code-in-runner")
        with open(wt + "/delivery.json", "w") as fh:
            json.dump({"acceptance": {"serve": {"mode": "files", "dir": "dist"}}}, fh)
        check("a delivery.json that cannot be read is not trusted", _uses_fake_backend(wt + "/nope.json"), True)
        # KIT-522: the acceptance section and the fake backend come from the BASE branch, so a session's
        # change to either cannot shape the result. Real git, one repository per case.
        def gitrepo(name, files, link=None):
            """(session dir, base git dir): the base is its own repository, which the session dir does not
            hold; the session dir starts as a copy of the base's files, as a checkout would."""
            gb = os.path.join(root, name + "-base")
            gw = os.path.join(root, name)
            os.makedirs(gb)
            os.makedirs(gw + "/dist")
            genv = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}

            def g(*a):
                subprocess.run(["git", "-C", gb, *a], check=True, capture_output=True, env=genv)
            g("init", "-q", "-b", "main")
            for rel, text in files.items():
                for base in (gb, gw):
                    os.makedirs(os.path.dirname(os.path.join(base, rel)) or base, exist_ok=True)
                    with open(os.path.join(base, rel), "w") as fh:
                        fh.write(text)
            if link:
                os.symlink(link[1], os.path.join(gb, link[0]))
            g("add", "-A")
            g("commit", "-q", "-m", "base")
            os.chmod(gb + "/.git", 0o755)
            return gw, gb + "/.git"

        acc_fake = json.dumps({"acceptance": {"serve": {"mode": "files", "dir": "dist"}, "fakeBackend": "harness/fake.mjs"}})
        acc_none = json.dumps({"acceptance": {"serve": {"mode": "files", "dir": "dist"}}})
        gw, bg = gitrepo("gw-base", {"delivery.json": acc_fake, "harness/fake.mjs": "export const who = 'base'\n"})
        # the session's change: a forged fake backend, and a delivery.json that points somewhere else
        with open(gw + "/harness/fake.mjs", "w") as fh:
            fh.write("export const who = 'forged'\n")
        with open(gw + "/delivery.json", "w") as fh:
            fh.write(json.dumps({"acceptance": {"serve": {"mode": "files", "dir": "dist"}, "fakeBackend": "evil.mjs"}}))
        seen2 = {}

        def spy(argv, env, cwd, timeout):
            seen2["argv"] = argv
            out2 = argv[argv.index("--out") + 1]
            os.makedirs(out2, exist_ok=True)
            fr = argv[argv.index("--fake-root") + 1] if "--fake-root" in argv else None
            seen2["fake"] = open(os.path.join(fr, "harness", "fake.mjs")).read() if fr else None
            seen2["config"] = open(argv[argv.index("--config") + 1]).read()
            with open(os.path.join(out2, "result.json"), "w") as fh:
                json.dump({"runs": []}, fh)
            return 0, "", ""
        cfg_base = conf(worktree=gw, plan="plan-b1", base_ref="main", base_git_dir=bg)
        look_run(cfg_base, good, spy, now=20)
        check("the fake backend the runner imports is the BASE branch's, not the session's changed copy",
              seen2["fake"], "export const who = 'base'\n")
        check("the acceptance section the runner reads is the base branch's, not the worktree's",
              json.loads(seen2["config"])["acceptance"]["fakeBackend"], "harness/fake.mjs")
        rec_b = json.load(open(os.path.join(rec, "plan-b1", _records(cfg_base)[0])))
        check("a pass from the base copy no longer carries the session-influenced mark",
              (rec_b["outcome_trust"], "base_copy" in rec_b), ("base-copy", False))

        gw2, bg2 = gitrepo("gw-nosection", {"delivery.json": json.dumps({"version": 1}), "x": "x"})
        with open(gw2 + "/delivery.json", "w") as fh:
            fh.write(acc_fake)
        cfg2 = conf(worktree=gw2, plan="plan-b2", base_ref="main", base_git_dir=bg2)
        look_run(cfg2, good, spy, now=21)
        rec2 = json.load(open(os.path.join(rec, "plan-b2", _records(cfg2)[0])))
        check("a base with no acceptance section falls back to the session's copy, marked as such, and says why",
              (rec2["outcome_trust"], "no `acceptance` section" in rec2["base_copy"], "--fake-root" in seen2["argv"]),
              ("session-code-in-runner", True, False))

        gw3, bg3 = gitrepo("gw-nofake", {"delivery.json": acc_fake, "other.txt": "x"})
        cfg3 = conf(worktree=gw3, plan="plan-b3", base_ref="main", base_git_dir=bg3)
        look_run(cfg3, good, spy, now=22)
        rec3 = json.load(open(os.path.join(rec, "plan-b3", _records(cfg3)[0])))
        check("a base without the fake backend file is not trusted, and says so",
              (rec3["outcome_trust"], "base_copy" in rec3), ("session-code-in-runner", True))

        # The session writes its OWN git state: a repository in its worktree whose `main` holds a forged fake
        # backend, with a filter that would run a command. None of it is read: the base is the other clone.
        genv2 = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
                 "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                 "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
        marker_file = os.path.join(root, "filter-ran")
        subprocess.run(["git", "-C", gw, "init", "-q", "-b", "main"], check=True, capture_output=True, env=genv2)
        subprocess.run(["git", "-C", gw, "config", "filter.x.smudge", "touch " + marker_file + "; cat"], check=True,
                       capture_output=True, env=genv2)
        with open(gw + "/.git/info/attributes", "w") as fh:
            fh.write("* filter=x\n")
        subprocess.run(["git", "-C", gw, "add", "-A"], check=True, capture_output=True, env=genv2)
        subprocess.run(["git", "-C", gw, "commit", "-q", "-m", "forged"], check=True, capture_output=True, env=genv2)
        look_run(cfg_base, good, spy, now=25)
        check("a session's own git state (a forged main, a smudge filter) is never read, and never runs a command",
              (seen2["fake"], os.path.exists(marker_file)), ("export const who = 'base'\n", False))
        os.chmod(bg, 0o777)
        look_run(conf(worktree=gw, plan="plan-b1w", base_ref="main", base_git_dir=bg), good, spy, now=26)
        rec_w = json.load(open(os.path.join(rec, "plan-b1w", _records(conf(plan="plan-b1w"))[0])))
        check("a base git directory others can write is not trusted, and says so",
              (rec_w["outcome_trust"], "others can write" in rec_w["base_copy"]), ("session-code-in-runner", True))
        os.chmod(bg, 0o755)
        check("a base git directory inside the worktree is refused: the session could write it",
              "outside the worktree" in (refusal(lambda: conf(worktree=gw, base_git_dir=gw + "/.git")) or ""), True)
        look_run(conf(worktree=gw, plan="plan-b1n", base_ref="main"), good, spy, now=27)
        rec_n = json.load(open(os.path.join(rec, "plan-b1n", _records(conf(plan="plan-b1n"))[0])))
        check("no base git directory configured: no base copy, marked, and the record says why",
              (rec_n["outcome_trust"], "no base_git_dir" in rec_n["base_copy"]), ("session-code-in-runner", True))

        gw3b, bg3b = gitrepo("gw-wrongfile", {"delivery.json": acc_fake, "harness/other.mjs": "export const x = 1\n"})
        cfg3b = conf(worktree=gw3b, plan="plan-b3b", base_ref="main", base_git_dir=bg3b)
        look_run(cfg3b, good, spy, now=22)
        rec3b = json.load(open(os.path.join(rec, "plan-b3b", _records(cfg3b)[0])))
        check("a base whose fake backend folder lacks the named file is not trusted, and says so",
              (rec3b["outcome_trust"], "no fake backend file" in rec3b["base_copy"]), ("session-code-in-runner", True))

        gw4, bg4 = gitrepo("gw-link", {"delivery.json": acc_fake, "harness/fake.mjs": "export const who = 'base'\n"},
                      link=("harness/secrets.mjs", "/etc/hosts"))
        cfg4 = conf(worktree=gw4, plan="plan-b4", base_ref="main", base_git_dir=bg4)
        look_run(cfg4, good, spy, now=23)
        rec4 = json.load(open(os.path.join(rec, "plan-b4", _records(cfg4)[0])))
        check("a link inside the base's fake backend folder is refused, never followed",
              (rec4["outcome_trust"], "link" in rec4["base_copy"]), ("session-code-in-runner", True))

        gw5, bg5 = gitrepo("gw-nofakebackend", {"delivery.json": acc_none})
        cfg5 = conf(worktree=gw5, plan="plan-b5", base_ref="main", base_git_dir=bg5)
        look_run(cfg5, good, spy, now=24)
        rec5 = json.load(open(os.path.join(rec, "plan-b5", _records(cfg5)[0])))
        check("a base section with no fake backend is read from the base and needs no mark",
              (rec5["outcome_trust"], "--fake-root" in seen2["argv"]), ("runner-only", False))
        check("config 'base_ref' must be a branch name", [bool(refusal(lambda b=b: conf(base_ref=b))) for b in
                                                           ("../x", "a b", "", "-x", "main;ls")], [True] * 5)
        # KIT-550: Playwright comes from a copy the session cannot write, or the run is not a pass
        seen3 = {}

        def spy3(argv, env, cwd, timeout):
            seen3["argv"] = argv
            return fake(argv, env, cwd, timeout)
        look_run(conf(plan="plan-pw1"), good, spy3, now=30)
        ai = seen3["argv"].index("--playwright-root")
        check("the runner is told to load Playwright from the trusted copy, which the profile may read",
              (seen3["argv"][ai + 1] == pwd, "PLAYWRIGHT_DIR=" + pwd in seen3["argv"]), (True, True))
        recp = json.load(open(os.path.join(rec, "plan-pw1", _records(conf(plan="plan-pw1"))[0])))
        check("the record says the copy was trusted", recp["playwright_from"], "trusted-copy")
        os.chmod(pwd + "/node_modules/playwright", 0o777)
        for why, cfgx in (("is writable by others", conf(plan="plan-pw2")),
                          ("is not configured", conf(plan="plan-pw3", playwright_dir=None))):
            ran_it = []
            text3, _imgs = look_run(cfgx, good, lambda *a, **k: ran_it.append(1) or fake(*a, **k), now=31)
            recx = json.load(open(os.path.join(rec, cfgx["plan"], _records(cfgx)[0])))
            check("a Playwright copy that %s: no browser is run, the record says why, and it is never a pass" % why,
                  (ran_it, recx["result"], recx["playwright_from"], "no browser was started" in recx["detail"],
                   "COULD-NOT-RUN" in text3), ([], "could-not-run", "none", True, True))
            os.chmod(pwd + "/node_modules/playwright", 0o755)
        os.rename(pwd + "/node_modules/playwright-core", pwd + "/node_modules/pc-gone")
        recy = look_run(conf(plan="plan-pw4"), good, fake, now=32)[0]
        check("an incomplete Playwright copy is named and is never a pass", "COULD-NOT-RUN" in recy and "incomplete" in recy, True)
        os.rename(pwd + "/node_modules/pc-gone", pwd + "/node_modules/playwright-core")
        check("a Playwright copy inside the worktree is refused at config: the session could write it",
              "outside the worktree" in (refusal(lambda: conf(playwright_dir=wt + "/node_modules")) or ""), True)
        check("the result names the run, its status and what was attached", ("PASSED" in text, len(images), "1 of 2" in text),
              (True, 1, True))
        check("a failed run is reported with the failing step, and still recorded",
              "expect" in look_run(cfg, good, lambda *a, **k: fake(*a, rc=1, **k), now=1)[0], True)

        # the per-plan count
        said = refusal(lambda: look_run(cfg, good, fake))
        check("a plan past its run count is refused, and it says how many it used and what to do",
              ("used its 2 look runs" in (said or ""), "say what you could not check" in (said or "")), (True, True))
        check("another plan has its own count", look_run(conf(plan="plan-2"), good, fake, now=2)[0].startswith("look run 1 of 2"),
              True)
        check("a plan's runs are counted even if the runner crashed", (
            look_run(conf(plan="plan-3"), good, lambda *a, **k: (7, "", "boom"), now=3)[0].splitlines()[0].endswith("CRASHED"),
            len(_records(conf(plan="plan-3")))), (True, 1))
        check("a runner that could not run is never a pass",
              look_run(conf(plan="plan-4"), good, lambda *a, **k: fake(*a, rc=3, **k), now=4)[0].splitlines()[0].endswith(
                  "COULD-NOT-RUN"), True)
        check("a timeout is a crashed run, recorded", look_run(conf(plan="plan-5"), good, lambda *a, **k: (124, "", "stopped"),
                                                              now=5)[0].splitlines()[0].endswith("CRASHED"), True)

        # the config: what the session cannot choose, and a record it could write is refused
        check("a record folder inside the worktree is refused: the session could write the record",
              "outside the worktree" in (refusal(lambda: conf(record_dir=wt + "/rec")) or ""), True)
        check("a worktree inside the role home is refused with a clear message",
              "outside the role account's home" in (refusal(lambda: conf(role_home=root)) or ""), True)
        check("config faults are named", [bool(refusal(lambda: conf(**o))) for o in (
            {"plan": "../x"}, {"max_runs": 0}, {"node": "node"}, {"extra": 1}, {"timeout_seconds": 5})], [True] * 5)

        # MCP
        replies = []
        lines = "\n".join(json.dumps(m) for m in (
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "look_run", "arguments": {"build_dir": "x"}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "other"}},
            {"jsonrpc": "2.0", "id": 5, "method": "nope"})) + "\nnot json\n"
        out = io.StringIO()
        serve(cfg, io.StringIO(lines), out, fake)
        replies = [json.loads(l) for l in out.getvalue().splitlines()]
        check("the server speaks MCP: initialize, a notification answered with nothing, the one tool, refusals as tool errors",
              (len(replies), replies[0]["result"]["protocolVersion"], [t["name"] for t in replies[1]["result"]["tools"]],
               replies[2]["result"]["isError"], replies[3]["error"]["code"], replies[4]["error"]["code"], replies[5]["error"]["code"]),
              (6, "2025-03-26", ["look_run"], True, -32602, -32601, -32700))
        good_call = handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {
            "name": "look_run", "arguments": good}}, conf(plan="plan-6"), fake)
        check("a good call returns text and the screenshot as an image", [c["type"] for c in good_call["result"]["content"]],
              ["text", "image"])

        # the profile: every rule pinned
        with open(PROFILE, encoding="utf-8") as fh:
            prof = fh.read()
        code = "\n".join(l for l in prof.splitlines() if not l.lstrip().startswith(";"))
        check("the profile starts from deny-default and never allows by default", ("(deny default)" in code, "(allow default)" in code),
              (True, False))
        check("the profile names no keychain, Security-framework or trust service: a session's code could ask them for secrets",
              [n for n in ("securityd", "SecurityServer", "trustd", "com.apple.security") if n in code], [])
        check("the profile grants no network, no local binding and no outbound or inbound socket",
              ["network" in code, "allowLocalBinding" in code, "localhost" in code, "AF_INET" in code], [False, False, False, False])
        check("the profile has the two rendezvous lines Chromium needs, and nothing broader on mach-register",
              (code.count("(allow mach-register"), 'MachPortRendezvousServer.")' in code), (1, True))
        check("the role home is denied, then only node, the browsers, the kit's scripts and the fresh folder are re-allowed",
              (code.index('(deny file-read* (subpath (param "ROLE_HOME")))') < code.rindex('(subpath (param "BROWSERS"))'),
               code.count('(subpath (param "ROLE_HOME"))')), (True, 1))
        check("secret files are denied last, after every allow",
              code.rindex("(deny file-read*") > code.rindex("(allow file-read*"), True)
        check("the only writable place is the fresh folder, plus the null devices",
              [l.strip() for l in code.splitlines() if "file-write*" in l],
              ['(allow file-write* (subpath (param "OUT_DIR")))', '(allow file-write* (literal "/dev/null") (literal "/dev/dtracehelper"))'])
        check("the runner, the profile and this tool are on the guarded list",
              all(p in open(os.path.join(HERE, "check_grader_paths.py"), encoding="utf-8").read() for p in (
                  "scripts/look_runner.mjs", "templates/sandbox/**")), True)

        # live: only where the sandbox and node exist
        if os.path.exists(SANDBOX_EXEC) and shutil.which("node"):
            probe = os.path.join(HERE, "..", "scripts", "pipeline_look.py")
            n = os.path.realpath(shutil.which("node"))
            for d in ("liveout", "liveread", "livehome"):
                os.makedirs(os.path.join(root, d))
            with open(root + "/liveread/a.txt", "w") as fh:
                fh.write("hi")
            with open(root + "/livehome/mine", "w") as fh:
                fh.write("x")
            import socket
            srv = socket.socket()
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            js_text = ("const fs=require('fs'),net=require('net');const [o,r,h,p]=process.argv.slice(1);const R=[];"
                       "try{fs.writeFileSync(o+'/w','x');R.push('write-out-dir:ok')}catch(e){R.push('write-out-dir:no')}"
                       "try{fs.readFileSync(r);R.push('read-root:ok')}catch(e){R.push('read-root:no')}"
                       "try{fs.readFileSync(h);R.push('read-home:ok')}catch(e){R.push('read-home:no')}"
                       "const s=net.connect(+p,'127.0.0.1');s.on('connect',()=>{R.push('net:ok');console.log(R.join(' '));process.exit(0)});"
                       "s.on('error',()=>{R.push('net:no');console.log(R.join(' '));process.exit(0)})")
            argv = [SANDBOX_EXEC, "-f", PROFILE, "-D", "READ_ROOT=" + root + "/liveread", "-D", "NODE_DIR=" + os.path.dirname(os.path.dirname(n)),
                    "-D", "BROWSERS=" + root + "/browsers", "-D", "KIT_DIR=" + HERE, "-D", "OUT_DIR=" + root + "/liveout",
                    "-D", "ROLE_HOME=" + root + "/livehome", "-D", "PLAYWRIGHT_DIR=/var/empty", n, "-e", js_text, root + "/liveout", root + "/liveread/a.txt",
                    root + "/livehome/mine", str(srv.getsockname()[1])]
            done = subprocess.run(argv, capture_output=True, text=True, timeout=60)
            srv.close()
            check("LIVE under the real profile: the fresh folder and the worktree work; the role home and a loopback port do not",
                  done.stdout.strip(), "write-out-dir:ok read-root:ok read-home:no net:no")
        else:
            skip("live profile check", "needs /usr/bin/sandbox-exec and node on this machine")
        live_app = os.environ.get("LOOK_LIVE_APP")
        browsers = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or os.path.expanduser("~/Library/Caches/ms-playwright")
        pw = [os.path.join(live_app or "", "node_modules", n) for n in ("playwright", "playwright-core")]
        if not (os.path.exists(SANDBOX_EXEC) and shutil.which("node")):
            skip("live browser check", "needs /usr/bin/sandbox-exec and node on this machine")
        elif not live_app or not all(os.path.isdir(x) for x in pw):
            skip("live browser check", "set LOOK_LIVE_APP to a folder whose node_modules holds playwright and playwright-core")
        elif not os.path.isdir(browsers) or not any(n.startswith("chromium") for n in os.listdir(browsers)):
            skip("live browser check", "no Chromium bundle under %s (set PLAYWRIGHT_BROWSERS_PATH)" % browsers)
        else:
            lw = os.path.join(root, "livewt")
            os.makedirs(os.path.join(lw, "node_modules", "playwright"))
            os.makedirs(os.path.join(lw, "dist"))
            # the session's node_modules is FORGED: if the runner loaded it, no check could run
            with open(os.path.join(lw, "node_modules", "playwright", "index.js"), "w") as fh:
                fh.write("throw new Error('forged playwright from the session')\n")
            live_pw = os.path.join(root, "livepw")
            os.makedirs(os.path.join(live_pw, "node_modules"))
            for x in pw:
                shutil.copytree(x, os.path.join(live_pw, "node_modules", os.path.basename(x)), symlinks=True)
            for dirpath, dirnames, _files in os.walk(live_pw):
                os.chmod(dirpath, 0o755)
            with open(lw + "/package.json", "w") as fh:
                fh.write("{}")
            with open(lw + "/dist/index.html", "w") as fh:
                fh.write("<!doctype html><title>t</title><body><h1>hello</h1></body>")
            with open(lw + "/delivery.json", "w") as fh:
                json.dump({"acceptance": {"serve": {"mode": "files", "dir": "dist"}}}, fh)
            os.makedirs(root + "/liverec")
            os.makedirs(root + "/livehome2")
            lcfg = {"worktree": lw, "record_dir": root + "/liverec", "node": os.path.realpath(shutil.which("node")),
                    "browsers": browsers, "role_home": root + "/livehome2", "plan": "live", "max_runs": 1,
                    "playwright_dir": live_pw}
            with open(root + "/live.json", "w") as fh:
                json.dump(lcfg, fh)
            lblock = {"schema": "pipeline-browser-check/1", "checks": [{"id": "hi", "viewports": ["desktop"], "modes": ["default"],
                      "open": "/index.html", "steps": [{"do": "expect", "on": {"css": "h1"}, "text": "hello"},
                                             {"do": "screenshot", "name": "home"}]}]}
            seen_live = {}

            def live_runner(argv, env, cwd, timeout):
                got = default_runner(argv, env, cwd, timeout)
                seen_live["err"] = (got[2] or got[1])[-300:]
                return got
            try:
                look_run(load_config(root + "/live.json"), {"build_dir": "dist", "check": lblock}, live_runner)
            except Refused as exc:
                check("LIVE browser run under the real profile is not refused", str(exc), "")
            recs = _records(lcfg)
            rec = {}
            if recs:
                with open(os.path.join(root, "liverec", "live", recs[0])) as fh:
                    rec = json.load(fh)
            hashes = [sh.get("sha256") for r in rec.get("runs", []) for sh in r.get("shots", []) if isinstance(sh, dict)]
            check("LIVE browser run under the real profile: recorded as passed, with one screenshot hash",
                  (rec.get("result"), len(hashes), all(len(h or "") == 64 for h in hashes)), ("passed", 1, True))
            if rec.get("result") != "passed":
                print("      runner said: %s" % seen_live.get("err"))
            # KIT-522, with a real browser: the BASE branch's fake backend breaks the page; the session's changed copy
            # would leave it alone. The recorded result must be `failed`, and the same session with no base copy
            # (its own fake backend, which does nothing) passes, which is the very thing the base copy prevents.
            base_dir = os.path.join(root, "live-base")
            os.makedirs(os.path.join(base_dir, "harness"))
            acc_text = json.dumps({"acceptance": {"serve": {"mode": "files", "dir": "dist"},
                                                  "fakeBackend": "harness/fake.mjs"}})
            genv3 = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
                     "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                     "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
            with open(os.path.join(base_dir, "delivery.json"), "w") as fh:
                fh.write(acc_text)
            with open(os.path.join(base_dir, "harness", "fake.mjs"), "w") as fh:
                fh.write("export async function install(context) {\n  await context.addInitScript(() => {\n"
                         "    document.addEventListener('DOMContentLoaded', () => { document.querySelector('h1').textContent = 'broken' })\n"
                         "  })\n  return {}\n}\n")
            for cmd in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "base"]):
                subprocess.run(["git", "-C", base_dir, *cmd], check=True, capture_output=True, env=genv3)
            os.chmod(base_dir + "/.git", 0o755)
            os.makedirs(os.path.join(lw, "harness"))
            with open(os.path.join(lw, "harness", "fake.mjs"), "w") as fh:     # the session's forged copy
                fh.write("export async function install() { return {} }\n")
            with open(os.path.join(lw, "delivery.json"), "w") as fh:
                fh.write(acc_text)
            os.makedirs(root + "/liverec2")
            for plan, extra in (("live-base", {"base_git_dir": base_dir + "/.git", "base_ref": "main"}), ("live-own", {})):
                with open(root + "/live2.json", "w") as fh:
                    json.dump({**lcfg, "record_dir": root + "/liverec2", "plan": plan, **extra}, fh)
                try:
                    look_run(load_config(root + "/live2.json"), {"build_dir": "dist", "check": lblock})
                except Refused as exc:
                    check("LIVE base-copy run is not refused (%s)" % plan, str(exc), "")
            got2 = {}
            for plan in ("live-base", "live-own"):
                names2 = _records({"record_dir": root + "/liverec2", "plan": plan})
                if names2:
                    with open(os.path.join(root, "liverec2", plan, names2[0])) as fh:
                        r2 = json.load(fh)
                    got2[plan] = (r2.get("result"), r2.get("outcome_trust"))
            check("LIVE: a session's forged fake backend cannot turn the base's failing check into a pass; without the base "
                  "copy the same forgery does", got2, {"live-base": ("failed", "base-copy"),
                                                       "live-own": ("passed", "session-code-in-runner")})
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("\n%d/%d checks passed (%d skipped: %s)" % (ran[0] - len(fails), ran[0], len(skipped), ", ".join(skipped) or "none"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
