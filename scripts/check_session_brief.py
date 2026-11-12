#!/usr/bin/env python3
"""The session brief's machine formats match the machines that read them (KIT-231).

    python3 scripts/check_session_brief.py              # check docs/SESSION-BRIEF.md
    python3 scripts/check_session_brief.py --selftest

docs/SESSION-BRIEF.md shows a session three things a machine reads: the escalation mark the
notifier pages on, the `pipeline-finding/1` block the finding job files, and the
`plan-needs-change/1` note the bridge reads from a piece of an approved plan. A session
copies them word for word, so a brief that drifts from either reader turns every real
escalation into silence. This reads the brief and judges each example with the readers'
OWN code, imported:

  * every `pipeline-escalation` mark the brief shows is one the notifier's `find_mark`
    reads, and one its `is_authorised` accepts from a session (some marks it takes only
    from the plan executor, the heartbeat monitor or the bounce driver); `agent:blocked`
    and `needs-human-session` are both shown (KIT-419: the brief told sessions to write the
    older `agent:needs-human`, which every notifier pages in general words, until every
    notifier that reads these sessions knew the new mark; the older one still pages); and
    each is one the GitHub Actions dispatch template's own reader matches;
  * the brief's worked example comment carries its mark where `find_mark` looks for it:
    the comment's first line;
  * the brief's fenced finding example parses with the finding poller's `parse_finding`;
  * the brief's fenced plan note example is a note the bridge's own `plan_note` reads
    (KIT-320), and it is never judged as a finding;
  * the brief and the `/work` skill show the same three gate lines a session ends with, one
    per declared command: `Gate <name>: ran`, `could not run` or `not asked` (KIT-315). A
    person reads these on the ticket, not a machine, so the two documents are pinned to
    each other: each shows all three forms, and their indented example blocks match line
    for line. A session told one form by the skill and another by the brief writes neither
    reliably, and a skipped check reads as "nothing left to do".

Exit 0 when all five hold, 1 with every problem named.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pipeline_notify_local as notify  # noqa: E402
import pipeline_finding_poller as finding  # noqa: E402
import pipeline_bridge_plans as plans  # noqa: E402  (KIT-320: the plan note's own reader)

BRIEF = os.path.join(os.path.dirname(HERE), "docs", "SESSION-BRIEF.md")
# The GitHub Actions lane's dispatcher reads a session's mark with its own regex, which is the
# other machine a copied mark must reach (KIT-282). Its alternation, on its one line.
DISPATCH_TEMPLATE = os.path.join(os.path.dirname(HERE), "templates", "workflows",
                                 "pipeline-dispatch.yml")
_TEMPLATE_READER_RE = re.compile(r'r"<!--\\s\*pipeline-escalation:\\s\*\(([^)]*)\)\\s\*-->"')
_SHOWN_MARK_RE = re.compile(r"<!--\s*pipeline-escalation:[^>]*-->")
_FINDING_FENCE_RE = re.compile(r"````markdown\n(```json\n.*?\n```)\n````", re.S)
# A fenced example holding this word is the plan note, judged by the bridge's reader (KIT-320).
PLAN_NOTE_WORD = '"schema": "%s"' % plans.PLAN_NOTE_SCHEMA
# A worked example comment: a ```markdown block. Only one showing a mark is judged as an
# escalation, so the finding example and any plain comment example are left alone.
_EXAMPLE_RE = re.compile(r"```markdown\n(.*?)\n```", re.S)
# The notifier takes some marks only from the plan executor, the heartbeat monitor or the
# bounce driver. A session is none of them, so its marks are judged as coming from an author
# on no list.
SESSION_AUTHOR = "a-session"
_OTHER_ACTORS = {"executor_actor_ids": ["the-plan-executor"],
                 "monitor_actor_ids": ["the-heartbeat-monitor"],
                 "bounce_actor_ids": ["the-bounce-driver"]}
# The /work skill tells a session how to end; the brief says why (KIT-315).
WORK_SKILL = os.path.join(os.path.dirname(HERE), ".claude", "skills", "work", "SKILL.md")
GATE_FORMS = ("ran", "could not run", "not asked")
_GATE_LINE_RE = re.compile(r"\bGate [a-z][a-z0-9-]*: (ran|could not run|not asked)\b")
# The worked example: the gate lines indented four spaces, as a code block.
_GATE_EXAMPLE_RE = re.compile(r"^    (Gate [a-z][a-z0-9-]*: .+?)\s*$", re.M)


def gate_line_problems(brief_text, skill_text):
    """Each document shows all three gate-line forms, and their examples match (KIT-315)."""
    out = []
    for where, text in (("the session brief", brief_text), ("the /work skill", skill_text)):
        shown = set(_GATE_LINE_RE.findall(text or ""))
        for form in GATE_FORMS:
            if form not in shown:
                out.append("%s never shows a `Gate <name>: %s` line, so a session is not told "
                           "how to report a gate command that way" % (where, form))
    brief_example = _GATE_EXAMPLE_RE.findall(brief_text or "")
    skill_example = _GATE_EXAMPLE_RE.findall(skill_text or "")
    if not brief_example or not skill_example:
        out.append("the %s shows no indented example of the gate lines to compare"
                   % ("session brief" if not brief_example else "/work skill"))
    elif brief_example != skill_example:
        out.append("the session brief and the /work skill show different example gate lines: "
                   "brief %r, skill %r" % (brief_example, skill_example))
    return out


def all_problems(brief_text, skill_text, template=None):
    """Everything `main` judges: the brief against its readers, then the gate lines."""
    return problems(brief_text, template) + gate_line_problems(brief_text, skill_text)


def template_reads(template):
    """The marks the dispatch template's escalation reader matches, or None when its one
    reader line cannot be found: a check that finds no reader judges nothing."""
    found = _TEMPLATE_READER_RE.findall(template)
    if len(found) != 1:
        return None
    return set(found[0].split("|"))


def problems(text, template=None):
    out = []
    if template is None:
        with open(DISPATCH_TEMPLATE, encoding="utf-8") as fh:
            template = fh.read()
    reads = template_reads(template)
    if reads is None:
        out.append("the GitHub Actions dispatch template's escalation reader could not be "
                   "found, so the marks it reads cannot be checked")
    shown = _SHOWN_MARK_RE.findall(text)
    if not shown:
        out.append("the brief shows no escalation mark")
    named = set()
    for mark in shown:
        name = notify.find_mark(mark)
        if not name:
            out.append("the brief shows %r, which the notifier does not read" % mark)
        elif not notify.is_authorised(name, SESSION_AUTHOR, _OTHER_ACTORS):
            out.append("the brief tells a session to write %r, which the notifier refuses "
                       "from a session" % mark)
        else:
            named.add(name)
            if reads is not None and name not in reads:
                out.append("the brief tells a session to write %r, which the GitHub Actions "
                           "dispatch template does not read" % mark)
    for needed in (notify.ESC_BLOCKED, notify.ESC_NEEDS_HUMAN_SESSION):
        if needed not in named:
            out.append("the brief never shows the %s mark" % needed)
    examples = [b for b in _EXAMPLE_RE.findall(text) if "pipeline-escalation" in b]
    if not examples:
        out.append("the brief shows no example comment carrying an escalation mark")
    for body in examples:
        if not notify.find_mark(body):
            out.append("the brief's example comment does not carry its mark on the first "
                       "line, so the notifier would not read it")
    blocks = _FINDING_FENCE_RE.findall(text)
    fences = [b for b in blocks if PLAN_NOTE_WORD not in b]
    if not fences:
        out.append("the brief shows no fenced pipeline-finding/1 example")
    for block in fences:
        if finding.parse_finding(block) is None:
            out.append("the brief's finding example does not parse with the finding job's "
                       "own parser")
    notes = [b for b in blocks if PLAN_NOTE_WORD in b]
    if not notes:
        out.append("the brief shows no fenced plan-needs-change/1 example")
    for block in notes:
        if plans.plan_note(block) is None:
            out.append("the brief's plan note example is not one the bridge's own reader reads")
    return out


def selftest():
    failures = []
    text = open(BRIEF, encoding="utf-8").read()
    if problems(text):
        failures.append("the committed brief: %s" % "; ".join(problems(text)))
    reworded = text.replace("pipeline-escalation: agent:blocked", "pipeline-escalation: blocked")
    if not problems(reworded):
        failures.append("a reworded mark passed")
    no_human = text.replace("needs-human-session -->", "agent:blocked -->")
    if not any("needs-human-session" in p for p in problems(no_human)):
        failures.append("a brief without the needs-human-session mark passed")
    # KIT-419: the older mark is no longer what the brief shows a session.
    if "agent:needs-human -->" in text:
        failures.append("the brief still tells a session to write the older agent:needs-human")
    # KIT-282: a brief telling a session to write the bounce driver's mark is refused: the
    # notifier would page it only in agent:needs-human's general words.
    spent = text.replace("needs-human-session -->", "fixes-spent -->")
    if not any("refuses from a session" in p for p in problems(spent)):
        failures.append("a brief telling a session to write the bounce driver's fixes-spent "
                        "mark passed")
    # The GitHub Actions lane reads a session's mark too, with its own regex: a mark the brief
    # shows that it does not read leaves that lane's ticket unlabelled.
    template = open(DISPATCH_TEMPLATE, encoding="utf-8").read()
    behind = template.replace("needs-human-session|", "", 1)
    if behind == template:
        failures.append("could not find the template's reader to build a case on")
    elif not any("dispatch template does not read" in p for p in problems(text, behind)):
        failures.append("a dispatch template that does not read needs-human-session passed")
    if not any("reader could not be found" in p
               for p in problems(text, template.replace("pipeline-escalation:", "x:"))):
        failures.append("a dispatch template whose reader the check cannot find passed")
    broken = text.replace('{"schema": "pipeline-finding/1"', '{"schema": "pipeline-finding/2"')
    if not problems(broken):
        failures.append("a finding example of the wrong schema passed")
    # KIT-320: the plan note's example is read by the bridge's own reader, never as a finding.
    if [p for p in problems(text) if "plan note" in p or "finding" in p]:
        failures.append("the brief's own plan note or finding example was refused")
    for name, bad in (("of another kind", ('"kind": "reorder"', '"kind": "cancel"')),
                      ("with a word the reader does not read", ('"reason": ', '"why": '))):
        note_at = text.find('{"schema": "plan-needs-change/1"')
        drifted = text[:note_at] + text[note_at:].replace(bad[0], bad[1], 1)
        if note_at < 0 or not any("plan note example" in p for p in problems(drifted)):
            failures.append("a plan note example %s passed" % name)
    if not any("no fenced plan-needs-change/1 example" in p for p in problems(
            text.replace('"schema": "plan-needs-change/1"', '"schema": "plan-note/1"'))):
        failures.append("a brief with no plan note example passed")

    # The two drifts a pattern check alone misses. Each silences every copied escalation.
    # 1. The worked example's mark moved off the first line: the notifier reads only that.
    opening = "\n```markdown\n"
    if opening not in text:
        failures.append("could not find the brief's example comment to build a case on")
    else:
        start = text.index(opening) + len(opening)
        end = text.index("\n```", start)
        first, rest = text[start:end].split("\n", 1)
        off_first = text[:start] + rest + "\n" + first + text[end:]
        if not any("first line" in p for p in problems(off_first)):
            failures.append("an example comment with its mark off the first line passed")
    # 2. A row telling a session to write a mark the notifier takes only from the executor.
    risky_row = "| The change would touch a path the project marks as risky |"
    executor_row = ("| You need the owner's input on the plan | "
                    "`<!-- pipeline-escalation: planning-needs-input -->` |\n")
    if risky_row not in text:
        failures.append("could not find the brief's mark table to build a case on")
    elif not any("refuses from a session" in p
                 for p in problems(text.replace(risky_row, executor_row + risky_row, 1))):
        failures.append("a mark the notifier refuses from a session passed")
    # 3. An example the check can no longer find is a check that judges nothing.
    renamed = text.replace(opening, "\n```md\n", 1)
    if not any("no example comment" in p for p in problems(renamed)):
        failures.append("a brief whose example comment the check cannot find passed")
    # …while an example that is not an escalation (a plain comment) is not judged as one.
    plain = text + "\n```markdown\nOpened PR #12 for this ticket.\n```\n"
    if problems(plain):
        failures.append("an example comment with no mark was judged as an escalation: %s"
                        % "; ".join(problems(plain)))

    # KIT-315: the gate lines a session ends with, pinned between the brief and the skill.
    skill = open(WORK_SKILL, encoding="utf-8").read()
    if gate_line_problems(text, skill):
        failures.append("the committed brief and skill: %s"
                        % "; ".join(gate_line_problems(text, skill)))
    every = ("    Gate lint: ran, passed.\n    Gate e2e: could not run, no browser.\n"
             "    Gate preview: not asked.\n")
    if gate_line_problems(every, every):
        failures.append("two documents showing all three forms were refused")
    # The examples are pinned line for line, not just by form (Stage E's note on KIT-315).
    drifted = skill.replace("    Gate test: ran, 212 passed, 0 failed.", "    Gate test: ran, passed.")
    if drifted == skill:
        failures.append("could not find the /work skill's example to build a case on")
    elif not any("different example gate lines" in p for p in gate_line_problems(text, drifted)):
        failures.append("a /work skill whose example drifted from the brief's passed")
    unindented = re.sub(r"(?m)^    (Gate )", r"\1", skill)
    if not any("/work skill shows no indented example" in p
               for p in gate_line_problems(text, unindented)):
        failures.append("a /work skill with no example block passed")
    # Each form case names the form check's own message, so the example comparison (which
    # also fires when a line changes) cannot stand in for it.
    def missing(found, where, form):
        return any(p.startswith("%s never shows a `Gate <name>: %s` line" % (where, form))
                   for p in found)
    no_asked = re.sub(r"Gate [a-z][a-z0-9-]*: not asked", "Gate preview: skipped", skill)
    if not missing(gate_line_problems(text, no_asked), "the /work skill", "not asked"):
        failures.append("a /work skill without the not-asked line passed")
    no_could = re.sub(r"Gate [a-z][a-z0-9-]*: could not run", "Gate e2e: was skipped", text)
    if not missing(gate_line_problems(no_could, skill), "the session brief", "could not run"):
        failures.append("a brief without the could-not-run line passed")
    reworded = re.sub(r"(Gate [a-z][a-z0-9-]*): ran\b", r"\1: passed", text)
    if not missing(gate_line_problems(reworded, skill), "the session brief", "ran"):
        failures.append("a brief whose ran line was reworded passed")
    # What `main` judges carries the gate lines too, so the command line cannot skip them.
    if not missing(all_problems(text, no_asked), "the /work skill", "not asked"):
        failures.append("the check main runs ignored a /work skill without the not-asked line")

    if failures:
        print("FAIL: check_session_brief selftest")
        for f in failures:
            print("  - %s" % f)
        return 1
    print("OK: check_session_brief selftest (20 cases)")
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--selftest" in argv:
        return selftest()
    found = all_problems(open(BRIEF, encoding="utf-8").read(),
                         open(WORK_SKILL, encoding="utf-8").read())
    if found:
        print("FAIL: docs/SESSION-BRIEF.md disagrees with the machines that read it, or with "
              "the /work skill:")
        for p in found:
            print("  - %s" % p)
        return 1
    print("OK: the session brief's marks, finding block and plan note match their readers, and "
          "its gate lines match the /work skill")
    return 0


if __name__ == "__main__":
    sys.exit(main())
