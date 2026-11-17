#!/usr/bin/env python3
"""A comment-triggered agent workflow lets only a trusted author start it (KIT-672).

    python3 scripts/check_agent_trigger_author.py            # check this repo's workflows and templates
    python3 scripts/check_agent_trigger_author.py --selftest # the battery CI runs

WHY. `templates/workflows/claude.yml` runs an agent with write permissions and the
repository's API key when a comment or issue body contains `@claude`. On a public
repository anyone can write that comment. Whether the action itself refuses a
non-writer is not something the template should rely on.

WHAT IS CHECKED. Every job that runs `anthropics/claude-code-action` from an
`issue_comment`, `pull_request_review_comment` or `issues` trigger has its `if:`
EVALUATED, not searched for names. The condition is run (a small evaluator for the subset
of GitHub's expression language the template uses) against a synthetic event for each
trigger and each author_association: a mention from an OWNER, MEMBER or COLLABORATOR must
start the job, and a mention from anyone else (NONE, CONTRIBUTOR, FIRST_TIME_CONTRIBUTOR,
FIRST_TIMER, MANNEQUIN) must not. A comment event is also run with a trusted issue author,
so that the issue's author cannot vouch for a stranger's comment. A condition that names
the roles but does not restrict anyone fails, as does one with syntax the evaluator cannot
read.

Exit 0 = every such job restricts its trigger. Exit 1 = one does not (file and job named).
Exit 2 = nothing could be checked, which is never a pass. Needs PyYAML (CI installs it).
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FOLDERS = (".github/workflows", "templates/workflows")
ACTION = "anthropics/claude-code-action"
TRIGGERS = ("issue_comment", "pull_request_review_comment", "issues")
TRUSTED = ("OWNER", "MEMBER", "COLLABORATOR")
UNTRUSTED = ("NONE", "CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER", "MANNEQUIN")
MENTION = "please look, @claude"

TOKEN = re.compile(r"\s*(?:('(?:[^']|'')*')|(&&|\|\||==|!=|[()!,])|([A-Za-z_][\w.\-]*|\d+(?:\.\d+)?))")


class Unreadable(ValueError):
    """The condition uses syntax this evaluator does not know."""


def _tokens(text):
    out, pos = [], 0
    text = text.strip()
    while pos < len(text):
        m = TOKEN.match(text, pos)
        if not m:
            raise Unreadable("cannot read %r" % text[pos:pos + 20])
        s, op, word = m.groups()
        out.append(("str", s[1:-1].replace("''", "'")) if s is not None
                   else ("op", op) if op is not None else ("word", word))
        pos = m.end()
    return out


def _truthy(v):
    return v not in (False, None, "", 0)


def _eq(a, b):
    if isinstance(a, str) and isinstance(b, str):
        return a.lower() == b.lower()
    return a == b


def evaluate(expr, ctx):
    """Evaluate a GitHub Actions `if:` expression over `ctx` (nested dicts)."""
    toks = _tokens(expr)
    pos = [0]

    def peek():
        return toks[pos[0]] if pos[0] < len(toks) else (None, None)

    def take(kind=None, value=None):
        t = peek()
        if t[0] is None or (kind and t[0] != kind) or (value and t[1] != value):
            raise Unreadable("expected %s" % (value or kind))
        pos[0] += 1
        return t

    def p_or():
        left = p_and()
        while peek() == ("op", "||"):
            take()
            right = p_and()
            left = left if _truthy(left) else right
        return left

    def p_and():
        left = p_eq()
        while peek() == ("op", "&&"):
            take()
            right = p_eq()
            left = right if _truthy(left) else left
        return left

    def p_eq():
        left = p_unary()
        if peek() in (("op", "=="), ("op", "!=")):
            op = take()[1]
            right = p_unary()
            return _eq(left, right) if op == "==" else not _eq(left, right)
        return left

    def p_unary():
        if peek() == ("op", "!"):
            take()
            return not _truthy(p_unary())
        return p_primary()

    def p_primary():
        kind, value = peek()
        if (kind, value) == ("op", "("):
            take()
            v = p_or()
            take("op", ")")
            return v
        if kind == "str":
            take()
            return value
        if kind != "word":
            raise Unreadable("unexpected %r" % (value,))
        take()
        if value in ("true", "false", "null"):
            return {"true": True, "false": False, "null": None}[value]
        if re.fullmatch(r"\d+(\.\d+)?", value):
            return float(value)
        if peek() == ("op", "("):
            take()
            args = []
            if peek() != ("op", ")"):
                args.append(p_or())
                while peek() == ("op", ","):
                    take()
                    args.append(p_or())
            take("op", ")")
            return call(value, args)
        node = ctx
        for part in value.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        return node

    def call(name, args):
        if name == "contains" and len(args) == 2:
            search, item = args
            if isinstance(search, list):
                return any(_eq(item, x) for x in search)
            return str(item if item is not None else "").lower() in str(search if search is not None else "").lower()
        if name == "fromJSON" and len(args) == 1:
            try:
                return json.loads(args[0])
            except (TypeError, ValueError):
                raise Unreadable("fromJSON of a non-JSON string")
        raise Unreadable("function %s is not supported" % name)

    result = p_or()
    if pos[0] != len(toks):
        raise Unreadable("trailing %r" % (toks[pos[0]][1],))
    return result


def _event(trigger, assoc, other_assoc):
    """A synthetic event: the mention is written by someone with `assoc`; the other party
    (the issue's author, for a comment event) has `other_assoc`."""
    if trigger == "issues":
        return {"github": {"event_name": "issues", "event": {
            "issue": {"body": MENTION, "author_association": assoc}}}}
    return {"github": {"event_name": trigger, "event": {
        "comment": {"body": MENTION, "author_association": assoc},
        "issue": {"body": MENTION, "author_association": other_assoc}}}}


def _triggers(doc):
    on = doc.get("on", doc.get(True))      # YAML 1.1 reads a bare `on` as True
    if isinstance(on, str):
        return [on]
    if isinstance(on, list):
        return [str(x) for x in on]
    return [str(k) for k in on] if isinstance(on, dict) else []


def unchecked(text):
    """[(job, why)] for each agent job whose `if:` does not restrict who can start it."""
    import yaml
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict):
        return []
    fired = [t for t in _triggers(doc) if t in TRIGGERS]
    found = []
    for name, job in sorted((doc.get("jobs") or {}).items()):
        if not isinstance(job, dict):
            continue
        steps = job.get("steps") or []
        if not any(isinstance(s, dict) and str(s.get("uses", "")).startswith(ACTION) for s in steps):
            continue
        cond = job.get("if")
        expr = "true" if cond is None else str(cond).strip()
        if expr.startswith("${{") and expr.endswith("}}"):
            expr = expr[3:-2].strip()
        for trig in fired:
            try:
                if not _truthy(evaluate(expr, _event(trig, "COLLABORATOR", "COLLABORATOR"))):
                    found.append((name, "`if:` never starts the job for a trusted author on %s" % trig))
                for assoc in UNTRUSTED:
                    # A comment event is judged with a TRUSTED issue author: the issue's author
                    # must not vouch for a stranger's comment.
                    if _truthy(evaluate(expr, _event(trig, assoc, "OWNER"))):
                        found.append((name, "`if:` starts the job for an author_association of "
                                            "%s on %s" % (assoc, trig)))
            except Unreadable as e:
                found.append((name, "`if:` uses syntax this check cannot evaluate (%s); keep to "
                                    "contains, fromJSON, ==, !=, &&, ||, !" % e))
                break
    return found


def check(root):
    problems, seen = [], 0
    for folder in FOLDERS:
        base = os.path.join(root, folder)
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            if not name.endswith((".yml", ".yaml")):
                continue
            seen += 1
            with open(os.path.join(base, name), encoding="utf-8") as fh:
                for job, why in unchecked(fh.read()):
                    problems.append(f"{folder}/{name} (job {job}): {why}")
    return problems, seen


def verdict(root):
    problems, seen = check(root)
    if not seen:
        return 2, ["COULD NOT CHECK: no workflow files found; this is not a pass"]
    if problems:
        return 1, ["FAIL: " + p for p in problems]
    return 0, [f"OK: {seen} workflow file(s), every agent trigger restricts its author"]


def selftest():
    failures = []

    def expect(cond, msg):
        if not cond:
            failures.append(msg)

    roles = "contains(fromJSON('[\"OWNER\",\"MEMBER\",\"COLLABORATOR\"]'), %s)"
    comment_ok = ("contains(github.event.comment.body, '@claude') && "
                  + roles % "github.event.comment.author_association")
    issue_ok = ("contains(github.event.issue.body, '@claude') && "
                + roles % "github.event.issue.author_association")
    good = ("((github.event_name == 'issue_comment' || github.event_name == 'pull_request_review_comment')"
            " && %s) || (github.event_name == 'issues' && %s)" % (comment_ok, issue_ok))

    def wf(cond, on="[issue_comment, issues]", uses=ACTION + "@v1"):
        iff = "    if: '%s'\n" % cond.replace("'", "''") if cond is not None else ""
        return ("on: %s\njobs:\n  c:\n%s    runs-on: ubuntu-24.04\n    steps:\n      - uses: %s\n"
                % (on, iff, uses))
    cases = (
        (wf(good), False, "a condition that restricts both events passes"),
        (wf(None), True, "no if: at all fails"),
        (wf("contains(github.event.comment.body, '@claude')"), True, "a mention-only if: fails"),
        # Names the field and all three roles, but restricts nobody:
        (wf("contains(github.event.comment.body, '@claude') || " + roles % "github.event.comment.author_association"
            + " || " + roles % "github.event.issue.author_association"), True,
         "a condition that names the roles but ORs them with the mention restricts nobody"),
        (wf("true || " + good), True, "a condition that is always true fails"),
        (wf("github.event.comment.author_association != 'NONE' && contains(github.event.comment.body, '@claude')"
            " && 'OWNER MEMBER COLLABORATOR' != ''"), True,
         "a deny-list of one role, with the trusted names in a string, fails"),
        (wf(good.replace("github.event.issue.author_association", "github.event.comment.author_association")), True,
         "an issue event judged on the comment's field (null) never starts for anyone"),
        (wf(comment_ok, on="[issue_comment]"), False,
         "a comment-only workflow judging the comment's author passes"),
        (wf("contains(github.event.comment.body, '@claude') && "
            + roles % "github.event.issue.author_association", on="[issue_comment]"), True,
         "a comment judged by the issue's author lets a stranger's comment through"),
        (wf(good.replace("COLLABORATOR", "x")), True, "a missing trusted role fails"),
        (wf("startsWith(github.event.comment.body, '@claude')"), True, "syntax it cannot read fails, never passes"),
        (wf(None, on="[push]"), False, "a non-comment trigger is not this check's business"),
        (wf(None, uses="actions/checkout@v4"), False, "a job without the agent action is ignored"),
    )
    for text, bad, msg in cases:
        got = unchecked(text)
        expect(bool(got) == bad, f"{msg}: findings {got}, want {'some' if bad else 'none'}")
    # The evaluator itself, on the semantics the condition relies on.
    ev = lambda e, **c: evaluate(e, c)  # noqa: E731
    expect(ev("contains(fromJSON('[\"A\",\"B\"]'), x.y)", x={"y": "b"}) is True, "contains an array, case-insensitive")
    expect(ev("contains(fromJSON('[\"A\",\"B\"]'), x.y)", x={}) is False, "a missing field is in no array")
    expect(ev("'a' == 'A' && !false") is True, "string equality ignores case; ! negates")
    expect(ev("false || 'x'") == "x" and ev("1 && 'y'") == "y", "|| and && return an operand")
    real_problems, real_seen = check(ROOT)
    expect(real_seen >= 10, f"found only {real_seen} workflow files in {ROOT}")
    expect(not real_problems, "this repo has an unrestricted agent trigger: " + "; ".join(real_problems))
    with open(os.path.join(ROOT, "templates/workflows/claude.yml"), encoding="utf-8") as fh:
        expect(ACTION in fh.read(), "the template this check exists for must still be there")
    if failures:
        print("check_agent_trigger_author selftest: FAIL")
        for f in failures:
            print("  -", f)
        return 1
    print(f"check_agent_trigger_author selftest: OK ({len(cases) + 4} cases)")
    return 0


def main(argv):
    try:
        if "--selftest" in argv:
            return selftest()
        code, lines = verdict(ROOT)
    except ImportError:
        print("FAIL: PyYAML is required (python3 -m pip install pyyaml)")
        return 2
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
