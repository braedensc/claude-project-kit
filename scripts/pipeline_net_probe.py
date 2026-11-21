#!/usr/bin/env python3
"""Measure the pipeline account's network path to GitHub and Linear (KIT-948). Read-only.

    python3 scripts/pipeline_net_probe.py [--account NAME | --conf runner.conf] [--attempts 3] [--timeout 5]

The role account is `--account`, or the `ROLE_ACCOUNT` line of the runner's conf (`--conf`, default
runner.conf beside this script's repo). With neither, it stops and says so.

WHY. The role account's calls to GitHub and Linear sometimes end in "[Errno 60] Operation timed
out" while the owner's own calls work. Three causes are possible and none was measured: a firewall
rule for that account that drops some of a host's current addresses, IPv6 that resolves but does
not route, or plain network loss. This prints the three facts that tell them apart, for the role
account and, beside it, for your own login:

  1. the DNS answers, IPv4 and IPv6, for api.github.com and api.linear.app;
  2. a TCP connect to each address on port 443, several times: how long, or how it failed;
  3. the packet-filter rules (main ruleset and every anchor) that apply to the role account.

It changes nothing. It runs a short program as the role account through `sudo` (your Mac password,
asked by sudo itself, never by this script) and reads the packet filter with `pfctl -s`, which
lists and never loads. A person runs it. Paste the plain-words summary at the end on the ticket.

Standard library only. Run it bare for the report, or with --selftest for its battery (which
`npm run test:install` runs).
"""
import argparse
import json
import os
import re
import subprocess
import sys

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAX_ANCHORS, MAX_DEPTH = 200, 6          # a walk past these is said, never read as done
HOSTS = ("api.github.com", "api.linear.app")

# Runs as the role account, with `-I`: argv is (attempts, timeout, host...). Prints one JSON document.
PROBE_PROGRAM = r"""
import json, socket, sys, time
attempts, timeout, hosts = int(sys.argv[1]), float(sys.argv[2]), sys.argv[3:]
out = {}
for host in hosts:
    entry = {"dns": {}, "connect": []}
    for name, fam in (("v4", socket.AF_INET), ("v6", socket.AF_INET6)):
        try:
            found = socket.getaddrinfo(host, 443, fam, socket.SOCK_STREAM)
            entry["dns"][name] = sorted({f[4][0] for f in found})
        except OSError as exc:
            entry["dns"][name] = []
            entry["dns"][name + "_error"] = str(exc)[:120]
    for name, fam in (("v4", socket.AF_INET), ("v6", socket.AF_INET6)):
        for addr in entry["dns"].get(name, []):
            for _ in range(attempts):
                started = time.time()
                try:
                    s = socket.socket(fam, socket.SOCK_STREAM)
                    s.settimeout(timeout)
                    s.connect((addr, 443))
                    s.close()
                    entry["connect"].append({"family": name, "addr": addr, "ok": True,
                                             "ms": int((time.time() - started) * 1000)})
                except OSError as exc:
                    entry["connect"].append({"family": name, "addr": addr, "ok": False,
                                             "ms": int((time.time() - started) * 1000),
                                             "error": ("Errno %s %s" % (exc.errno, exc))[:100]})
    out[host] = entry
print(json.dumps(out))
"""


def run(argv):
    """(exit code, stdout, stderr). The one seam: the selftest replaces it."""
    try:
        p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd="/",
                           timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, "", str(exc)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def probe_argv(account, attempts, timeout, hosts, python="/usr/bin/python3"):
    """The program as the role account (account None: as the caller)."""
    tail = [python, "-I", "-c", PROBE_PROGRAM, str(attempts), str(timeout)] + list(hosts)
    return tail if account is None else ["sudo", "-u", account, "-H"] + tail


def parse_probe(text):
    """The probe's document, or None when it is not one."""
    try:
        doc = json.loads((text or "").strip())
    except ValueError:
        return None
    ok = isinstance(doc, dict) and all(
        isinstance(v, dict) and isinstance(v.get("dns"), dict) and isinstance(v.get("connect"), list)
        for v in doc.values())
    return doc if ok else None


def conf_account(path):
    """The ROLE_ACCOUNT line of a KEY=value conf (its last active one), or None."""
    try:
        with open(path) as fh:
            lines = fh.read().splitlines()
    except OSError:
        return None
    found = None
    for line in lines:
        m = re.match(r"\s*ROLE_ACCOUNT\s*=\s*(.*?)\s*$", line)
        if m:
            found = m.group(1).strip("'\"") or None
    return found


def uid_of(account, runner=run):
    code, out, _err = runner(["id", "-u", account])
    return out.strip() if code == 0 and out.strip().isdigit() else None


def applies_to(rule, account, uid):
    """True for a packet-filter rule line that applies to the account: one with no `user` clause
    (it covers everyone) or one whose `user` clause covers the account. pfctl prints the clause with
    an operator (`user = 300`, `user != 501`, `user >= 300`) or a list (`user { 501 300 }`); a
    comparison it cannot settle from the account's number counts as applying, never as not."""
    found = re.search(r"\buser\s*(!=|<=|>=|=|<|>)?\s*(\{[^}]*\}|\S+)", rule)
    if not found:
        return True
    op = found.group(1) or "="
    names = set(re.findall(r"[\w.\-]+", found.group(2)))
    member = account in names or (uid is not None and uid in names)
    if op == "=":
        return member
    if op == "!=":
        return not member
    nums = [int(n) for n in names if n.isdigit()]
    if uid is None or not uid.isdigit() or len(nums) != 1:
        return True
    u, n = int(uid), nums[0]
    return {"<": u < n, ">": u > n, "<=": u <= n, ">=": u >= n}[op]


def list_anchors(runner):
    """(anchors, problems): every anchor under the main ruleset, walked one level at a time. `pfctl -s
    Anchors` shows only the anchors directly below the one named by -a (pfctl(8)), so each is asked in
    turn. A walk that fails, or goes past MAX_DEPTH or MAX_ANCHORS, names what it did not read."""
    problems, found, queue = [], [], [(None, 0)]
    while queue:
        parent, depth = queue.pop(0)
        argv = ["sudo", "pfctl", "-s", "Anchors"] + (["-a", parent] if parent else [])
        code, out, err = runner(argv)
        if code != 0:
            problems.append("could not list the anchors below %s (exit %d): %s"
                            % (parent or "the main ruleset", code, err.strip()[-160:]))
            continue
        for raw in out.splitlines():
            name = raw.strip()
            if name.endswith("/*"):
                name = name[:-2]
            if not name:
                continue
            if parent and not name.startswith(parent + "/"):
                name = parent + "/" + name
            if name in found:
                continue
            if depth + 1 > MAX_DEPTH or len(found) >= MAX_ANCHORS:
                problems.append("did not read the anchors below %s: the walk stops at depth %d and %d "
                                "anchors" % (name, MAX_DEPTH, MAX_ANCHORS))
                continue
            found.append(name)
            queue.append((name, depth + 1))
    return found, problems


def filter_rules(account, uid, runner=run):
    """(anchors, rules, problems): every anchor the filter names, and each rule line, from the main
    ruleset and from each anchor, that applies to the account and blocks or redirects. `pfctl -s`
    only lists. Anything it could not list or read is a problem, so "none" is said only of rules it
    read."""
    rules = []
    anchors, problems = list_anchors(runner)
    for anchor in [None] + anchors:
        argv = ["sudo", "pfctl", "-s", "rules"] + (["-a", anchor] if anchor else [])
        code, out, err = runner(argv)
        if code != 0:
            problems.append("could not read the rules of %s (exit %d): %s"
                            % (anchor or "the main ruleset", code, err.strip()[-160:]))
            continue
        for line in out.splitlines():
            line = line.strip()
            if re.match(r"(block|drop|rdr|nat|route-to|reply-to)\b", line) and applies_to(line, account, uid):
                rules.append("%s: %s" % (anchor or "main", line))
    return anchors, rules, problems


def summarize(label, doc):
    """Plain words for one login's probe."""
    lines = []
    for host, entry in doc.items():
        for fam, noun in (("v4", "IPv4"), ("v6", "IPv6")):
            addrs = entry["dns"].get(fam, [])
            tries = [c for c in entry["connect"] if c.get("family") == fam]
            good = [c for c in tries if c.get("ok")]
            if not addrs:
                why = entry["dns"].get(fam + "_error")
                lines.append("%s %s %s: no address%s." % (label, host, noun, " (%s)" % why if why else ""))
                continue
            slow = max((c["ms"] for c in good), default=None)
            bad = sorted({c["addr"] for c in tries if not c.get("ok")})
            lines.append("%s %s %s: %d address(es), %d of %d connects worked%s%s."
                         % (label, host, noun, len(addrs), len(good), len(tries),
                            ", slowest %d ms" % slow if slow is not None else "",
                            "; failing: %s" % ", ".join(bad) if bad else ""))
    return lines


def main(argv=None, runner=run, say=print):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--account", default=None)
    ap.add_argument("--conf", default=os.path.join(KIT, "runner.conf"))
    ap.add_argument("--attempts", type=int, default=3)
    ap.add_argument("--timeout", type=float, default=5.0)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.account is None:
        args.account = conf_account(args.conf)
    if not args.account:
        say("No role account: name it with --account, or give --conf a runner.conf with a ROLE_ACCOUNT line.")
        return 2
    if not 1 <= args.attempts <= 10 or not 1 <= args.timeout <= 30:
        say("--attempts is 1 to 10 and --timeout is 1 to 30 seconds.")
        return 2
    say("Measuring as %s, then as you. Read-only. sudo may ask for your Mac password." % args.account)
    results, failed = [], False
    for label, account in (("role account", args.account), ("you", None)):
        code, out, err = runner(probe_argv(account, args.attempts, args.timeout, HOSTS))
        doc = parse_probe(out) if code == 0 else None
        if doc is None:
            say("%s: the probe did not run (exit %d): %s" % (label, code, (err or out).strip()[-200:]))
            failed = failed or account is not None
            continue
        results.append((label, doc))
    anchors, rules, problems = filter_rules(args.account, uid_of(args.account, runner), runner)
    say("")
    for label, doc in results:
        for line in summarize(label, doc):
            say(line)
        for host, entry in doc.items():
            for fam in ("v4", "v6"):
                say("  %s %s DNS %s: %s" % (label, host, fam, ", ".join(entry["dns"].get(fam, [])) or "none"))
            for c in entry["connect"]:
                say("  %s %s %s %s: %s" % (label, host, c["family"], c["addr"],
                                           "%d ms" % c["ms"] if c.get("ok") else "FAILED %s after %d ms"
                                           % (c.get("error", ""), c["ms"])))
    say("")
    say("Packet filter anchors: %s" % (", ".join(anchors) or "none listed"))
    say("Packet filter rules that apply to %s (block, drop, redirect, route), from the main ruleset and "
        "%d anchor(s): %s" % (args.account, len(anchors),
                              "" if rules else ("none" if not problems else
                                                "none seen, but not all of it was read (NOT MEASURED below)")))
    for rule in rules:
        say("  " + rule)
    for problem in problems:
        say("NOT MEASURED: " + problem)
    say("")
    say("Paste everything above to the coordinator chat. If IPv6 resolves but its connects fail while IPv4 works, "
        "or a rule above drops an address, that is the cause. If both families connect, the "
        "timeouts are network loss, and the retry in the installer's dry run is the answer.")
    return 4 if failed or problems else 0


def selftest():
    cases = []

    def check(name, got, want):
        if got != want:
            cases.append("%s: got %r, wanted %r" % (name, got, want))

    good = {"api.github.com": {"dns": {"v4": ["1.1.1.1"], "v6": ["2001:db8::1"]},
                               "connect": [{"family": "v4", "addr": "1.1.1.1", "ok": True, "ms": 20},
                                           {"family": "v6", "addr": "2001:db8::1", "ok": False, "ms": 5000,
                                            "error": "Errno 60 timed out"}]}}
    check("parse-good", parse_probe(json.dumps(good)), good)
    check("parse-junk", (parse_probe("x"), parse_probe("[]"), parse_probe('{"a": 1}')), (None, None, None))
    words = summarize("role account", good)
    check("summary-names-the-failing-family",
          ("IPv6" in words[1] and "0 of 1 connects worked" in words[1] and "2001:db8::1" in words[1]
           and "1 of 1 connects worked, slowest 20 ms" in words[0]), True)
    check("applies-no-user-clause", applies_to("block drop out quick on en0 inet6 all", "_r", "300"), True)
    check("applies-user-by-name", applies_to("block drop out proto tcp user _r", "_r", "300"), True)
    check("applies-user-by-number-in-braces", applies_to("block out user { 501 300 }", "_r", "300"), True)
    check("other-user-is-not-ours", applies_to("block out user 501", "_r", "300"), False)
    for line, want in (("block drop out proto tcp all user = 300", True), ("block drop out all user = 501", False),
                       ("block out user != 501", True), ("block out user != 300", False),
                       ("block out user >= 300", True), ("block out user < 300", False),
                       ("block out user = _r", True), ("block out user = { 501 300 }", True)):
        check("pfctl-prints-user-with-an-operator: " + line, applies_to(line, "_r", "300"), want)
    check("a-comparison-with-no-uid-known-applies", applies_to("block out user < 300", "_r", None), True)
    check("probe-argv-as-role", probe_argv("_r", 3, 5.0, HOSTS)[:5], ["sudo", "-u", "_r", "-H", "/usr/bin/python3"])
    check("probe-argv-as-caller", probe_argv(None, 3, 5.0, HOSTS)[:2], ["/usr/bin/python3", "-I"])
    check("probe-argv-never-writes", any(w in " ".join(probe_argv("_r", 3, 5.0, HOSTS)[:5]) for w in ("tee", "rm ")),
          False)

    seen = []

    tree = {None: ["com.apple/*", "mine"], "com.apple": ["com.apple/250.ApplicationFirewall"],
            "com.apple/250.ApplicationFirewall": [], "mine": ["deep"], "mine/deep": []}
    rules_of = {None: "pass in all\nblock drop out proto tcp user 501\n", "com.apple": "pass all\n",
                "com.apple/250.ApplicationFirewall": "", "mine": "pass out all\n",
                "mine/deep": "block drop out inet6 user _r\n"}

    def make_fake(unreadable=()):
        def fake(argv):
            seen.append(argv)
            if argv[:3] == ["id", "-u", "_r"]:
                return 0, "300\n", ""
            if "-I" in argv:
                return 0, json.dumps(good), ""
            anchor = argv[argv.index("-a") + 1] if "-a" in argv else None
            if anchor in unreadable:
                return 1, "", "pfctl: anchor unreadable"
            if argv[-1] == "Anchors" or argv[3:5] == ["Anchors", "-a"]:
                return 0, "".join(x + "\n" for x in tree[anchor]), ""
            if argv[3] == "rules":
                return 0, rules_of[anchor], ""
            return 1, "", "no"
        return fake
    fake = make_fake()
    out = []
    code = main(["--account", "_r"], runner=fake, say=out.append)
    text = "\n".join(out)
    check("report-exit-ok", code, 0)
    check("report-reads-a-rule-in-a-nested-anchor-and-skips-the-other-users",
          ("mine/deep: block drop out inet6 user _r" in text, "user 501" in text), (True, False))
    check("report-names-every-anchor-nested-too",
          "com.apple, mine, com.apple/250.ApplicationFirewall, mine/deep" in text, True)
    out = []
    code = main(["--account", "_r"], runner=make_fake(unreadable=("mine",)), say=out.append)
    text_u = "\n".join(out)
    check("a-nested-anchor-it-could-not-list-or-read-is-not-measured-never-none",
          (code, "NOT MEASURED: could not list the anchors below mine" in text_u,
           "NOT MEASURED: could not read the rules of mine" in text_u, "rules that apply to _r" in text_u
           and "none seen, but not all of it was read" in text_u), (4, True, True, True))
    out = []
    main(["--account", "_r"], runner=make_fake(unreadable=("mine/deep",)), say=out.append)
    check("a-clean-walk-never-says-none-when-it-read-something-less",
          "NOT MEASURED: could not read the rules of mine/deep" in "\n".join(out), True)
    check("closing-line-points-at-the-coordinator-chat", "Paste everything above to the coordinator chat" in text, True)
    check("report-says-both-logins", ("role account api.github.com IPv6" in text, "you api.github.com IPv6" in text),
          (True, True))
    check("only-lists-never-loads",
          [a for a in seen if a[:2] == ["sudo", "pfctl"] and not (a[2] == "-s")], [])

    def broken(argv):
        return (1, "", "sudo: a password is required") if argv[:1] == ["sudo"] else (0, "300\n", "")
    out = []
    code = main(["--account", "_r"], runner=broken, say=out.append)
    check("a-probe-that-did-not-run-is-said-and-never-a-clean-pass",
          (code, "the probe did not run" in "\n".join(out), "NOT MEASURED" in "\n".join(out)), (4, True, True))
    import tempfile
    with tempfile.TemporaryDirectory(prefix="net-probe-") as tmp:
        good_conf, empty_conf = os.path.join(tmp, "runner.conf"), os.path.join(tmp, "empty.conf")
        with open(good_conf, "w") as fh:
            fh.write("# comment\nROLE_ACCOUNT=_r\nOTHER=x\n")
        with open(empty_conf, "w") as fh:
            fh.write("OTHER=x\n")
        check("conf-account-is-read", (conf_account(good_conf), conf_account(empty_conf),
                                       conf_account(os.path.join(tmp, "absent"))), ("_r", None, None))
        before = len(seen)
        said = []
        check("no-account-and-no-conf-is-refused-before-anything-runs",
              (main(["--conf", empty_conf], runner=fake, say=said.append), len(seen) == before,
               "--account" in "\n".join(said)), (2, True, True))
        out = []
        check("the-conf-names-the-account", (main(["--conf", good_conf], runner=make_fake(), say=out.append),
                                             "Measuring as _r" in out[0]), (0, True))
    check("bad-attempts-refused", main(["--attempts", "99"], runner=fake, say=lambda _l: None), 2)
    if cases:
        print("FAILED net probe selftest:\n  " + "\n  ".join(cases))
        return 1
    print("OK: net probe selftest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
