#!/usr/bin/env python3
"""
SessionEnd hook — a session removes its own scratch folder when it ends (KIT-460).

Claude Code gives every session a folder of its own under its temp root:

    <tmp>/claude-<uid>/<project>/<session-id>/scratchpad/   the session's working files
    <tmp>/claude-<uid>/<project>/<session-id>/tasks/        its background tasks' output

Nothing removed that folder but a restart. On 4 Nov 2026 those folders held 49.5 GB on the
owner's Mac, one build session 24.7 GB of it, and the disk ran out. This hook removes the
ending session's own folder, and nothing else.

WHAT IT REMOVES. Exactly one folder: the parent of the `scratchpad_dir` Claude Code passes in
the hook input (Claude Code 2.1.257 and later), and only when all of these hold:
  * `session_id` is a UUID, and the folder is named by it;
  * the path reads `claude-<this account's uid>/<project>/<session-id>/scratchpad`;
  * none of `claude-<uid>`, `<project>` and `<session-id>` is a symlink, and this account
    owns all three.
Anything else — no `scratchpad_dir`, a path of another shape, a symlink, a folder another
account owns — removes nothing and says why on stderr. It never builds a path of its own,
so it cannot reach another session's folder or a person's files. Inside the folder a
symlink is removed, never followed.

Claude Code passes `scratchpad_dir` only to a session that has a scratchpad: on in the
desktop app, which is where the 49.5 GB was. A session without one (a headless `claude -p`
run, measured on 2.1.287 and 2.1.309) gets no such field, and this hook removes nothing for
it. Its root also moves with CLAUDE_CODE_TMPDIR, one more reason not to rebuild the path.

HOW. It first renames the folder to `.ended-<session-id>.<pid>` beside it, in one step, so
a session resumed at once gets a fresh, empty folder and cannot lose a file to the removal.
Then, before it removes anything itself, it starts a detached child that removes that
folder and any an earlier end of the same session set aside and never finished. SessionEnd
hooks share a short budget (1.5 s unless a hook's own `timeout` raises it), and a hook
stopped right after the rename must not strand the folder. The child re-checks each path's
shape and owner before it removes anything. Only if the child cannot start does the hook
remove what it can itself, in one second.

WHAT A RESUMED SESSION SEES. An empty scratchpad, as after a restart. The scratchpad is
temporary by contract: anything a session needs again belongs in a persistent path.

Fires for every end reason (`clear`, `resume`, `logout`, `prompt_input_exit`, `other`), in
a desktop session and on the dispatcher's lane alike, since both load this repo's
`.claude/settings.json`. Never blocks (SessionEnd cannot) and always exits 0.

SELF-PROTECTED, like the Stop hook. It runs after the session could have edited it, as a
hook process outside the Bash sandbox, so an editable copy would let a sandboxed session run
code of its choosing at its own end. Its wiring lives in `.claude/settings.json`, which is
self-protected too; a person applies both (`.claude/hooks/README.md`, "SessionEnd").
"""
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time

UUID_TEXT = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
UUID = re.compile(r"^%s$" % UUID_TEXT)
ENDED = ".ended-"
ASIDE = re.compile(r"^\.ended-(%s)\.\d+$" % UUID_TEXT)
INLINE_SECONDS = 1.0                  # only when the detached remover cannot start


def _plain_own_folders(paths, uid):
    """None when every path is a real folder (no symlink) this account owns, else why."""
    for path in paths:
        try:
            st = os.lstat(path)
        except OSError:
            return "%s is gone" % path
        if not stat.S_ISDIR(st.st_mode):
            return "%s is not a plain folder" % path
        if st.st_uid != uid:
            return "%s belongs to another account" % path
    return None


def set_aside(path, uid=None):
    """True when `path` is a folder this hook set aside: claude-<uid>/<project>/.ended-<id>.<n>."""
    uid = os.getuid() if uid is None else uid
    path = os.path.normpath(path)
    project = os.path.dirname(path)
    root = os.path.dirname(project)
    return (os.path.isabs(path) and bool(ASIDE.match(os.path.basename(path)))
            and os.path.basename(root) == "claude-%d" % uid
            and _plain_own_folders((root, project, path), uid) is None)


def session_folder(data, uid=None):
    """(folder, None) when the input names this session's own folder, else (None, why)."""
    uid = os.getuid() if uid is None else uid
    if not isinstance(data, dict):
        return None, "the hook input is not an object"
    sid = data.get("session_id")
    pad = data.get("scratchpad_dir")
    if not isinstance(sid, str) or not UUID.match(sid):
        return None, "the session id is not a UUID"
    if not isinstance(pad, str) or not pad.strip():
        return None, "Claude Code did not say where this session's scratchpad is"
    if not os.path.isabs(pad):
        return None, "the scratchpad path is not absolute"
    pad = os.path.normpath(pad)
    folder = os.path.dirname(pad)
    project = os.path.dirname(folder)
    root = os.path.dirname(project)
    if os.path.basename(pad) != "scratchpad":
        return None, "the scratchpad path does not end in /scratchpad"
    if os.path.basename(folder) != sid:
        return None, "the scratchpad's folder is not named by this session's id"
    if os.path.basename(root) != "claude-%d" % uid:
        return None, "the scratchpad is not under claude-%d" % uid
    why = _plain_own_folders((root, project, folder), uid)
    return (None, why) if why else (folder, None)


def remove_tree(path, deadline=None):
    """Remove a folder, even one left without permissions, never following a symlink.

    Stops at `deadline` (a time.time() value) when one is given, the walk that gives
    folders their rights back included. True when the path is gone."""
    def late():
        return deadline is not None and time.time() > deadline

    if not os.path.lexists(path):
        return True
    if os.path.islink(path) or not os.path.isdir(path):
        try:
            os.unlink(path)
        except OSError:
            pass
        return not os.path.lexists(path)
    try:
        os.chmod(path, stat.S_IRWXU)
    except OSError:
        pass
    # A folder left without permissions (mode 000) gets its owner's rights back before the
    # walk goes into it, or nothing under it could be removed.
    for root, dirs, _ in os.walk(path):
        if late():
            return False
        for d in dirs:
            full = os.path.join(root, d)
            if not os.path.islink(full):
                try:
                    os.chmod(full, stat.S_IRWXU)
                except OSError:
                    pass
    for root, dirs, files in os.walk(path, topdown=False):
        if late():
            return False
        for name in files:
            try:
                os.unlink(os.path.join(root, name))
            except OSError:
                pass
        for name in dirs:
            full = os.path.join(root, name)
            try:
                if os.path.islink(full):
                    os.unlink(full)
                else:
                    os.rmdir(full)
            except OSError:
                pass
    try:
        os.rmdir(path)
    except OSError:
        shutil.rmtree(path, ignore_errors=True)
    return not os.path.lexists(path)


def end(data, uid=None, inline_seconds=INLINE_SECONDS, spawn=True):
    """Remove this session's own folder. Returns what it did, as one line."""
    folder, why = session_folder(data, uid)
    if folder is None:
        return "nothing removed: " + why
    project, sid = os.path.split(folder)
    aside = os.path.join(project, "%s%s.%d" % (ENDED, sid, os.getpid()))
    try:
        os.rename(folder, aside)
    except OSError as exc:
        return "nothing removed: could not set %s aside (%s)" % (folder, exc.strerror)
    # This end's folder, and any an earlier end of this same session set aside and never
    # finished (its remover was killed): all of them this session's own.
    mine = [aside] + sorted(
        os.path.join(project, n) for n in os.listdir(project)
        if n != os.path.basename(aside) and ASIDE.match(n) and ASIDE.match(n).group(1) == sid)
    # The remover starts before anything is removed here: Claude Code may stop this hook
    # at any moment after the rename, and a folder set aside with no remover running would
    # stay until a restart.
    note = ""
    if spawn:
        try:
            subprocess.Popen([sys.executable, os.path.abspath(__file__), "--remove"] + mine,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            return "removing %s in the background" % folder
        except OSError as exc:
            note = "could not start the remover (%s); " % exc.strerror
    deadline = time.time() + inline_seconds
    left = [p for p in mine if not remove_tree(p, deadline=deadline)]
    if not left:
        return note + "removed %s" % folder
    return note + "set %s aside; %d folder(s) left" % (aside, len(left))


def main(argv):
    if argv[:1] == ["--remove"]:
        # The detached child. It removes only folders this hook set aside, checked again.
        for path in argv[1:]:
            if set_aside(path):
                remove_tree(path)
        return 0
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = None
    said = end(data)
    if not said.startswith(("removed", "removing")):
        print("session-end: " + said, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
