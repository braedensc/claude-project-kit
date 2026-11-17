#!/usr/bin/env python3
"""The validator's verdict as a required check, posted by its own GitHub app (KIT-540).
Design: docs/adr/2026-11-05-the-validator-approves-inside-limits-that-code-enforces.md, "The ruleset
changes": the ruleset requires this check, and names the validator's app as its only source.

    from pipeline_validator_check import post_verdict
    post_verdict(owner_repo, number, head, ledger_path, cfg)   THE ONE FUNCTION: post the check

    python3 scripts/pipeline_validator_check.py show --ledger-file PATH --repo OWNER/NAME \\
        --pr N --head SHA                                      what it would post; reads only
    python3 scripts/pipeline_validator_check.py --selftest

WHAT IT POSTS.  One completed check run named `Validator` on a pull request's head, as the
validator's own GitHub app. It is the ledger's decision for that pull request at that exact head
(contract §24), and nothing else: `success` only when the ledger records an approval of the merge,
`failure` for any other decision recorded for that head. Its title is the decision, and its
summary each layer's verdict, in closed words. No pull request text, and not the agent's words.

THE RULES, each one tested.
  * One head. The row it posts from is for this pull request at this head, and the run goes on that
    head. A head with no decision gets no check: a push leaves the new head without one until it
    is judged.
  * The ledger first. A ledger that cannot be read or trusted posts nothing, and says why: never a
    success it cannot show.
  * Its own app. The app's key is read only by `openssl`, from its file, through the shared helper
    (scripts/pipeline_github_app.py). The config names the app's id and the NAME of the
    environment variable that holds the key file's path. The key file must be a plain file only its
    owner can read, outside every git working tree. Neither the path nor the key is ever printed,
    logged or put in the ledger.
  * A post that fails holds, in words. The answer is `posted`, `nothing` or `held`: never a quiet
    pass.

WHAT IT NEVER DOES (asserted in --selftest): write a file, start a process of its own (the helper's
`openssl` signs the app's token), call GitHub but through the helper, or put a credential in its
answer.
"""
import argparse
import json
import os
import re
import stat
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    import pipeline_github_app as gha
    import pipeline_validator_ledger as ledger
except ImportError as exc:  # a packaging accident, never a state to run in
    sys.exit("FAIL: %s. The check is posted through those files and keeps no copy." % exc)

CHECK_NAME = "Validator"
POST, POSTED, NOTHING, HELD = "post", "posted", "nothing", "held"
ENV_NAME_RE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
TITLES = {"approved": "approved", "sent-back": "sent back to the dev session",
          "escalated": "escalated to the owner", "escalated-urgent": "escalated to the owner, urgent",
          "held": "held"}
EXIT_OK, EXIT_HELD, EXIT_USAGE = 0, 1, 2


def _answer(result, why, head=None, conclusion=None, run=None):
    return {"result": result, "conclusion": conclusion, "head": head, "run": run, "why": why}


def decision(rows, owner_repo, number, head):
    """The ledger's row for a merge of `owner_repo#number` at `head`, or None. The ledger holds one
    verdict per head; a row for another head, or another pull request, is not this one."""
    ref = "%s#%d" % (owner_repo, number)
    found = [r for r in rows if isinstance(r, dict) and r.get("subject") == "merge"
             and r.get("ref") == ref and r.get("head") == head]
    return found[-1] if found else None


def plan(owner_repo, number, head, ledger_path):
    """What post_verdict() would post, read from the ledger alone, with no network and no
    credential: `post` with its conclusion, title and summary, or `nothing`, or `held`."""
    if (not isinstance(owner_repo, str) or not gha.REPO_RE.fullmatch(owner_repo)
            or isinstance(number, bool) or not isinstance(number, int) or number < 1):
        return _answer(HELD, "nothing posted: the pull request is not OWNER/NAME and a number")
    if not isinstance(head, str) or not gha.HEAD_RE.fullmatch(head):
        return _answer(HELD, "nothing posted: the head is not a full commit id")
    got = ledger.read(ledger_path)
    if got["rows"] is None:
        return _answer(HELD, "nothing posted: %s" % got["why"], head)
    row = decision(got["rows"], owner_repo, number, head)
    if row is None:
        return _answer(NOTHING, "no decision is recorded for this head, so it has no check yet",
                       head)
    kind = row.get("kind")
    layers = row.get("layers") if isinstance(row.get("layers"), dict) else {}
    out = _answer(POST, "the ledger's decision for this head", head,
                  "success" if kind == "approved" else "failure")
    out["title"] = TITLES.get(kind, "held")
    out["summary"] = ", ".join("%s %s" % (name.replace("_", " "), layers.get(name))
                               for name in ledger.LAYERS)
    return out


def credential(cfg, environ):
    """(app id, key file path, None), or (None, None, why). `why` names the variable, never what it
    holds. The key file is the credential: a plain file only its owner can read, outside every git
    working tree, read only by `openssl`."""
    if not isinstance(cfg, dict):
        return None, None, "the check's config is not an object"
    app_id, env = cfg.get("app_id"), cfg.get("key_file_env")
    if isinstance(app_id, bool) or not isinstance(app_id, int) or app_id < 1:
        return None, None, "the config's app_id is not a positive whole number"
    if not isinstance(env, str) or not ENV_NAME_RE.fullmatch(env):
        return None, None, "the config's key_file_env is not the name of an environment variable"
    path = environ.get(env) if isinstance(environ, dict) else None
    if not isinstance(path, str) or not path.strip():
        return None, None, "$%s is unset: it names the validator app's key file" % env
    if not os.path.isabs(path) or any(ord(c) < 32 for c in path):
        return None, None, "$%s does not hold an absolute path" % env
    try:
        st = os.lstat(path)
    except OSError:
        return None, None, "the key file $%s names cannot be found" % env
    if (not stat.S_ISREG(st.st_mode) or st.st_mode & 0o077
            or ledger.inside_git_worktree(os.path.dirname(path))):
        return None, None, ("the key file $%s names is not a plain file only its owner can read, "
                            "outside every git working tree" % env)
    return app_id, path, None


def post_verdict(owner_repo, number, head, ledger_path, cfg, environ=None, call=None,
                 signer=None, now=None):
    """THE ONE FUNCTION: post the ledger's decision for `owner_repo#number` at `head` as the
    `Validator` check, as the validator's own app. {"result": posted | nothing | held, "conclusion",
    "head", "run", "why"}: fields and fixed words, never a credential."""
    call = call or gha._call
    signer = signer or gha.openssl_signer
    p = plan(owner_repo, number, head, ledger_path)
    if p["result"] != POST:
        return _answer(p["result"], p["why"], p["head"])
    app_id, key_file, why = credential(cfg, os.environ if environ is None else environ)
    if why:
        return _answer(HELD, "nothing posted: " + why, head)
    try:
        jwt = gha.make_jwt(app_id, signer(key_file), now)
        installation = gha.installation_for(owner_repo, jwt, call)
        if installation is None:
            return _answer(HELD, "nothing posted: the validator's app is not installed on %s"
                           % owner_repo, head)
        token = gha.installation_token(installation, jwt, call)
        run = gha.post_check_run(owner_repo, head, CHECK_NAME, p["conclusion"], p["title"],
                                 p["summary"], token, call)
    except gha.GitHubAppError as exc:
        return _answer(HELD, "nothing posted: %s" % exc, head)
    return _answer(POSTED, "posted %s on the head" % p["conclusion"], head, p["conclusion"],
                   run["id"])


def main(argv=None):
    ap = argparse.ArgumentParser(description="The validator's verdict as the Validator check.")
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("show", help="what it would post for one head; reads the ledger only")
    s.add_argument("--ledger-file", required=True)
    s.add_argument("--repo", required=True)
    s.add_argument("--pr", type=int, required=True)
    s.add_argument("--head", required=True)
    args = ap.parse_args(argv)
    if args.selftest:
        from scratch_run import contained
        return contained(selftest, "validator-check-selftest-")
    if args.cmd == "show":
        path = os.path.expanduser(args.ledger_file)
        if not os.path.isabs(path):
            print("COULD NOT RUN: --ledger-file must be an absolute path, or start with ~/.")
            return EXIT_USAGE
        p = plan(args.repo, args.pr, args.head, path)
        if p["result"] == POST:
            print("would post %s on %s: %s; %s" % (p["conclusion"], p["head"], p["title"],
                                                    p["summary"]))
        elif p["result"] == NOTHING:
            print("nothing: %s" % p["why"])
        else:
            print("HELD: %s" % p["why"])
        return EXIT_HELD if p["result"] == HELD else EXIT_OK
    ap.print_help()
    return EXIT_USAGE


# ======================================================================================
# SELFTEST: nothing below the line above is run by a reader, and the source scan stops there.
# ======================================================================================
def selftest():
    import contextlib
    import io
    import tempfile
    failures, total = [], [0]

    def check(name, got, want):
        total[0] += 1
        if got != want:
            failures.append("FAIL %s: got %r, want %r" % (name, str(got)[:300], str(want)[:300]))

    H, H2, H3 = "a" * 40, "b" * 40, "c" * 40
    TOKEN = "ghs_SECRETTOKEN" + "t" * 30
    CFG = {"app_id": 42, "key_file_env": "VALIDATOR_APP_KEY_FILE"}
    approved = {"subject": "merge", "ref": "acme/app#12", "head": H, "kind": "approved",
                "layers": {"hard_limits": "pass", "evidence": "pass", "agent": "pass"},
                "settings_on": ["approve-merges"],
                "size_limits": {"max_changed_lines": 400, "max_files": 20},
                "agent_says": "SECRET-AGENT-WORDS: the diff does what the criteria ask."}
    sent_back = dict(approved, head=H2, kind="sent-back",
                     layers={"hard_limits": "pass", "evidence": "pass", "agent": "fail"},
                     agent_says="SECRET-AGENT-WORDS: add a test for the empty case.")
    sent = []

    def host(answers):
        """A stubbed code host: GitHub's answers by (method, path); every call is kept."""
        def call(method, path, auth=None, body=None):
            sent.append((method, path, auth, body))
            got = answers.get((method, path), (500, {}))
            if isinstance(got, Exception):
                raise got
            return got
        return call
    GOOD = {("GET", "/repos/acme/app/installation"): (200, {"id": 7}),
            ("POST", "/app/installations/7/access_tokens"): (201, {"token": TOKEN}),
            ("POST", "/repos/acme/app/check-runs"): (201, {"id": 501,
                                                           "app": {"slug": "acme-validator"}})}

    def signer(path):
        return lambda data: b"s" * 256

    with tempfile.TemporaryDirectory() as tmp:
        os.chmod(tmp, 0o700)
        led = os.path.join(tmp, "ledger.json")
        clock = iter(range(1_800_000_000, 1_800_000_100))
        for row in (approved, sent_back, dict(sent_back, ref="acme/app#13", head=H)):
            ledger.append(led, row, lambda: next(clock))
        key = os.path.join(tmp, "KEYPATH-SECRET.pem")
        with open(key, "w", encoding="utf-8") as fh:
            fh.write("not a key: the signer is stubbed\n")
        os.chmod(key, 0o600)
        ENV = {"VALIDATOR_APP_KEY_FILE": key}

        def post(number=12, head=H, answers=None, cfg=None, env=None, ledger_path=None, sign=None):
            del sent[:]
            return post_verdict("acme/app", number, head, ledger_path or led,
                                CFG if cfg is None else cfg, ENV if env is None else env,
                                host(GOOD if answers is None else answers), sign or signer,
                                now=1_800_000_000)

        # 1. An approval at this head is a success, on this head, with closed words only.
        got = post()
        check("an approval at this head posts success on it",
              got, {"result": POSTED, "conclusion": "success", "head": H, "run": 501,
                    "why": "posted success on the head"})
        check("...through the app's installation, with the app's token, one run",
              [(m, p) for m, p, _a, _b in sent],
              [("GET", "/repos/acme/app/installation"),
               ("POST", "/app/installations/7/access_tokens"),
               ("POST", "/repos/acme/app/check-runs")])
        body = sent[-1][3]
        check("...named Validator, on the judged head, completed, with the decision's words",
              body, {"name": "Validator", "head_sha": H, "status": "completed",
                     "conclusion": "success",
                     "output": {"title": "approved",
                                "summary": "hard limits pass, evidence pass, agent pass"}})
        check("...and the agent's words are never in it",
              "SECRET-AGENT-WORDS" in json.dumps(body), False)

        # 2. Any other decision is a failure.
        got = post(head=H2)
        check("a send-back at its head posts failure",
              (got["result"], got["conclusion"], sent[-1][3]["output"]),
              (POSTED, "failure", {"title": "sent back to the dev session",
                                   "summary": "hard limits pass, evidence pass, agent fail"}))
        for kind in ("escalated", "escalated-urgent", "held"):
            other = os.path.join(tmp, "ledger-%s.json" % kind)
            ledger.append(other, dict(approved, kind=kind, agent_says=None,
                                      layers={"hard_limits": "pass", "evidence": "fail",
                                              "agent": "not-run"}), lambda: 1_800_000_000)
            got = post(ledger_path=other)
            check("a decision `%s` posts failure, titled as it is" % kind,
                  (got["result"], got["conclusion"], sent[-1][3]["output"]["title"]),
                  (POSTED, "failure", TITLES[kind]))

        # 3. One head: a verdict counts only for the head and pull request it was for.
        for label, number, head in (("a push: the new head has no decision", 12, H3),
                                    ("another pull request at the same head", 14, H)):
            got = post(number=number, head=head)
            check("%s: nothing is posted, and nothing is sent" % label,
                  (got["result"], got["conclusion"], sent), (NOTHING, None, []))
        check("another pull request's row at this head is its own, not this one's",
              post(number=13, head=H)["conclusion"], "failure")

        # 4. The ledger first: one that cannot be read or trusted posts nothing, ever.
        bad = os.path.join(tmp, "bad-ledger.json")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("{no")
        os.chmod(bad, 0o600)
        loose = os.path.join(tmp, "loose-ledger.json")
        with open(loose, "w", encoding="utf-8") as fh, open(led, encoding="utf-8") as src:
            fh.write(src.read())
        os.chmod(loose, 0o620)
        for label, path in (("not readable JSON", bad), ("writable by group", loose),
                            ("a relative path", "ledger.json")):
            got = post(ledger_path=path)
            check("a ledger %s: held, nothing sent, never a success" % label,
                  (got["result"], got["conclusion"], sent, "ledger" in got["why"]),
                  (HELD, None, [], True))
        got = post(ledger_path=os.path.join(tmp, "none.json"))
        check("no ledger yet: no decision, so no check",
              (got["result"], sent), (NOTHING, []))

        # 5. Its own app: the key file through an environment variable the config names.
        os.chmod(key, 0o640)
        open_key = post()
        os.chmod(key, 0o600)
        tree = os.path.join(tmp, "tree")
        os.makedirs(os.path.join(tree, ".git"))
        in_tree = os.path.join(tree, "key.pem")
        with open(in_tree, "w", encoding="utf-8") as fh:
            fh.write("x\n")
        os.chmod(in_tree, 0o600)
        for label, got in (
                ("the variable unset", post(env={})),
                ("the variable empty", post(env={"VALIDATOR_APP_KEY_FILE": " "})),
                ("a relative path", post(env={"VALIDATOR_APP_KEY_FILE": "key.pem"})),
                ("a key file that is not there",
                 post(env={"VALIDATOR_APP_KEY_FILE": os.path.join(tmp, "gone.pem")})),
                ("a key file group can read", open_key),
                ("a key file inside a git working tree",
                 post(env={"VALIDATOR_APP_KEY_FILE": in_tree})),
                ("a folder for a key file", post(env={"VALIDATOR_APP_KEY_FILE": tmp})),
                ("an app id that is not one", post(cfg=dict(CFG, app_id="42"))),
                ("a variable name of another shape", post(cfg=dict(CFG, key_file_env="key"))),
                ("no config", post(cfg="none"))):
            check("credential: %s: held, nothing sent" % label,
                  (got["result"], got["conclusion"], sent), (HELD, None, []))
        for label, env in (("unset", {}), ("blank", {"VALIDATOR_APP_KEY_FILE": " "})):
            check("credential: a variable %s is named as unset, never what it holds" % label,
                  post(env=env)["why"],
                  "nothing posted: $VALIDATOR_APP_KEY_FILE is unset: it names the validator "
                  "app's key file")

        # 6. A post that fails holds, in words: never a quiet pass.
        refused = dict(GOOD)
        refused[("POST", "/repos/acme/app/check-runs")] = (422, {"message": "x"})
        no_token = dict(GOOD)
        no_token[("POST", "/app/installations/7/access_tokens")] = (500, {})
        cut = dict(GOOD)
        cut[("POST", "/repos/acme/app/check-runs")] = gha.GitHubAppError(
            "GitHub could not be reached (URLError)")
        not_installed = dict(GOOD)
        not_installed[("GET", "/repos/acme/app/installation")] = (404, {})

        def bad_signer(path):
            def sign(data):
                raise gha.GitHubAppError("openssl could not sign with the app's key file (exit 1)")
            return sign
        for label, got, word in (
                ("GitHub refuses the check run", post(answers=refused), "HTTP 422"),
                ("GitHub gives no token", post(answers=no_token), "token"),
                ("GitHub cannot be reached", post(answers=cut), "could not be reached"),
                ("the app is not installed", post(answers=not_installed), "not installed"),
                ("the key cannot sign", post(sign=bad_signer), "could not sign")):
            check("a failed post: %s: held, said, never posted" % label,
                  (got["result"], got["conclusion"], word in got["why"]), (HELD, None, True))

        # 7. No credential in any answer, any body, or any line show prints.
        answers = [post(), post(answers=refused), post(env={}), post(answers=cut)]
        bodies = json.dumps([b for _m, _p, _a, b in sent])
        check("no answer carries the token, the key file's path or the agent's words",
              [w for w in ("SECRETTOKEN", "KEYPATH-SECRET", "SECRET-AGENT-WORDS", tmp)
               if w in json.dumps(answers) + bodies], [])
        check("the token rides only in the Authorization header of its own calls",
              all(a is None or a.startswith(("Bearer ", "token ")) for _m, _p, a, _b in sent),
              True)

        # 8. Shapes refused before anything is read.
        for label, args in (("a short head", ("acme/app", 12, "abc")),
                            ("a repository of another shape", ("acme app", 12, H)),
                            ("a number that is not one", ("acme/app", True, H))):
            del sent[:]
            got = post_verdict(args[0], args[1], args[2], led, CFG, ENV, host(GOOD), signer)
            check("%s: held, nothing sent" % label, (got["result"], sent), (HELD, []))

        # 9. show: what it would post, from the ledger alone, and the exit code a script reads.
        def run_main(argv):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(argv)
            return code, out.getvalue().strip()
        base = ["show", "--ledger-file", led, "--repo", "acme/app", "--pr", "12", "--head"]
        check("show: an approval", run_main(base + [H]),
              (EXIT_OK, "would post success on %s: approved; hard limits pass, evidence pass, "
                        "agent pass" % H))
        check("show: no decision for the head",
              run_main(base + [H3]), (EXIT_OK, "nothing: no decision is recorded for this head, "
                                               "so it has no check yet"))
        code, text = run_main(["show", "--ledger-file", bad, "--repo", "acme/app", "--pr", "12",
                               "--head", H])
        check("show: a ledger it cannot read holds, exit 1", (code, text.startswith("HELD: ")),
              (EXIT_HELD, True))
        check("show: a relative ledger path is a usage error",
              run_main(["show", "--ledger-file", "x.json", "--repo", "acme/app", "--pr", "12",
                        "--head", H])[0], EXIT_USAGE)

    # 10. It writes nothing, starts no process of its own, and calls GitHub only through the helper.
    with open(os.path.abspath(__file__), encoding="utf-8") as fh:
        src = fh.read()
    above = src.split("\n# SELFTEST: " + "nothing below", 1)[0].split('"""', 2)[2]
    banned = ("os.replace", "os.rename", "os.unlink", "os.remove", "shutil", "os.makedirs",
              "os.chmod", "json.dump(", ".write(", "subprocess", "os.system", "socket", "urllib",
              "urlopen", "eval(", "exec(", "Popen", "open(")
    check("it writes and opens nothing, starts no process, and has no network of its own",
          [b for b in banned if b in above], [])
    check("the check's name is fixed in one place", above.count('"Validator"'), 1)

    if failures:
        for line in failures:
            print(line)
        print("validator check selftest: %d of %d checks failed" % (len(failures), total[0]))
        return 1
    print("OK: validator check selftest (%d checks)" % total[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
