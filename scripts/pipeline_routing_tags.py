#!/usr/bin/env python3
"""Routing tags: the one definition of the dispatcher directives every ticket maker removes.

    python3 scripts/pipeline_routing_tags.py --selftest

THE DISPATCHER READS ROUTING DIRECTIVES OUT OF A TICKET'S DESCRIPTION (KIT-41). A bracketed
`[repo=…]` (optionally `#branch`) or an unbracketed `repo=`/`repos=` picks the repository
entry and branch a session works in, and `[model=…]` / `[agent=…]` pick its runner (cyrus
0.2.73 RepositoryRouter.js parseRepoTagsFromDescription; RunnerSelectionService.js:90-95).
Everything the kit copies into a ticket from text it did not write, an idea, a planning
session's plan, a review's quoted criteria, a session's finding, has every directive
replaced with a mark that holds none, so that text cannot pick where or on what a session
runs once a person hands the ticket over.

JUDGED AS LINEAR WILL STORE IT (KIT-381). Linear parses a description as CommonMark and
writes it back: backslash escapes of ASCII punctuation and character references (decimal,
hex, named) come back decoded, so `[agent\\=codex]` or `[agent&#61;codex]`, which no
literal pattern matches, is stored as `\\[agent=codex\\]`, a live tag. Raw HTML, comments,
zero-width and fullwidth characters are kept as written (both measured live, KIT-382 and
KIT-383, 2 Nov 2026). So the patterns run on the text as it reads after those decodes,
and the matching slice of the ORIGINAL text is replaced. The view is decoded up to
DECODE_ROUNDS times, because a later save of the ticket in an editor decodes again
(`&amp;#61;` is `=` after two). Code is judged like the rest, though Linear leaves escapes
in code alone: a wrong guess at where code starts would leave a live tag, and replacing an
escaped tag inside code costs nothing real.

The patterns are deliberately WIDER than the dispatcher's: any case for `repo`, spaces
inside the brackets, and an unbracketed `repo=` after any non-word character, not just
whitespace. A diff line reads `+repo=x`, and a parser one version newer may well accept it.

The plan executor, the planner job, the review poller and the finding poller import this
file; none keeps a copy.
"""
import bisect
import html.entities
import re
import sys

BRACKET_TAG_RE = re.compile(r"\\?\[\s*(?:repos?|model|agent)\s*=[^\]\n]*\\?\]", re.IGNORECASE)
UNBRACKETED_REPO_RE = re.compile(r"(^|[^A-Za-z0-9_])repos?=[A-Za-z0-9_\-/.#,]+",
                                 re.IGNORECASE | re.MULTILINE)
ROUTING_TAG_MARK = "(removed-routing-tag)"

# CommonMark 0.31: a backslash escapes any ASCII punctuation character, and an entity or
# numeric character reference is replaced by its character, outside code.
ASCII_PUNCT = frozenset("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
CHAR_REF_RE = re.compile(r"&(?:#([0-9]{1,7})|#[xX]([0-9a-fA-F]{1,6})|([A-Za-z][A-Za-z0-9]{0,31}));")
NAMED_REFS = dict((name[:-1], text) for name, text in html.entities.html5.items()
                  if name.endswith(";"))
DECODE_ROUNDS = 4
SETTLE_PASSES = 8


def _ref_text(match):
    if match.group(3) is not None:
        return NAMED_REFS.get(match.group(3))
    code = int(match.group(1)) if match.group(1) is not None else int(match.group(2), 16)
    if code == 0 or code > 0x10FFFF or 0xD800 <= code <= 0xDFFF:
        return "\ufffd"
    return chr(code)


def decode_once(text):
    """(decoded, spans): `text` after one CommonMark decode, each decoded character mapped to
    the slice of `text`, as (start, end), it came from."""
    out, spans, i, n = [], [], 0, len(text)
    while i < n:
        c = text[i]
        if c == "\\" and i + 1 < n and text[i + 1] in ASCII_PUNCT:
            out.append(text[i + 1])
            spans.append((i, i + 2))
            i += 2
            continue
        if c == "&":
            match = CHAR_REF_RE.match(text, i)
            value = _ref_text(match) if match else None
            if value:
                for ch in value:
                    out.append(ch)
                    spans.append((i, match.end()))
                i = match.end()
                continue
        out.append(c)
        spans.append((i, i + 1))
        i += 1
    return "".join(out), spans


def stored_view(text):
    """(view, spans): `text` as it reads after up to DECODE_ROUNDS decodes, each character
    of the view mapped to the slice of `text` it came from."""
    view, spans = text, [(i, i + 1) for i in range(len(text))]
    for _ in range(DECODE_ROUNDS):
        decoded, src = decode_once(view)
        if decoded == view:
            break
        spans = [(spans[a][0], spans[b - 1][1]) for a, b in src]
        view = decoded
    return view, spans


def _view_spans(view):
    bracketed = [m.span() for m in BRACKET_TAG_RE.finditer(view)]
    starts = [a for a, _ in bracketed]
    found = list(bracketed)
    for m in UNBRACKETED_REPO_RE.finditer(view):
        span = (m.start() + len(m.group(1)), m.end())
        # Bracketed hits are in order and never overlap, so the only one that can hold this
        # span is the last to start at or before it: a search, not a scan of every hit.
        k = bisect.bisect_right(starts, span[0]) - 1
        if k < 0 or bracketed[k][1] < span[1]:
            found.append(span)
    return sorted(found)


def directive_spans(text):
    """Every directive the dispatcher could read in `text`, written or once decoded, as the
    (start, end) slices of `text` they come from, in order. A bracketed tag also matches the
    unbracketed pattern inside itself; that is counted once."""
    text = str(text or "")
    view, spans = stored_view(text)
    return [(spans[a][0], spans[b - 1][1]) for a, b in _view_spans(view)]


def neutralize_routing(text):
    """`text` with every directive the dispatcher could read, written or once decoded,
    replaced with ROUTING_TAG_MARK. The mark holds none, so the result can be asserted
    clean, and text with no directive comes back byte for byte."""
    if not text:
        return text
    out = str(text)
    for _ in range(SETTLE_PASSES):
        cuts = []
        for start, end in directive_spans(out):
            if cuts and start < cuts[-1][1]:
                cuts[-1] = (cuts[-1][0], max(cuts[-1][1], end))
            else:
                cuts.append((start, end))
        if not cuts:
            return out
        for start, end in reversed(cuts):
            out = out[:start] + ROUTING_TAG_MARK + out[end:]
    raise ValueError("routing directives did not settle in %d passes" % SETTLE_PASSES)


# --------------------------------------------------------------------------- #
# Selftest. The four files that import this one run it inside their own, so CI runs it.
# --------------------------------------------------------------------------- #
# The dispatcher's own patterns, copied from cyrus 0.2.73, to judge the output by: the
# description tags (RunnerSelectionService.js:90-95, case-insensitive) and the repo tags
# (RepositoryRouter.js parseRepoTagsFromDescription, case-sensitive).
_DISPATCHER_TAG_RE = re.compile(r"\\?\[(agent|model)=([a-zA-Z0-9_.:/-]+)\\?\]", re.IGNORECASE)
_DISPATCHER_REPO_RE = re.compile(r"\\?\[repo=([a-zA-Z0-9_\-/.#]+)\\?\]")
_DISPATCHER_BARE_RE = re.compile(r"(?:^|[\s\n])repos?=([a-zA-Z0-9_\-/.#,]+)", re.MULTILINE)


def dispatcher_reads(stored):
    """Every directive the dispatcher parses out of a stored description."""
    return ([m.group(0) for m in _DISPATCHER_TAG_RE.finditer(stored)]
            + [m.group(0) for m in _DISPATCHER_REPO_RE.finditer(stored)]
            + [m.group(0).strip() for m in _DISPATCHER_BARE_RE.finditer(stored)])


def decoded(text, rounds=1):
    """`text` after `rounds` decodes: Linear's store is one, each later save another."""
    for _ in range(rounds):
        text = decode_once(text)[0]
    return text


# Every spelling the two live probes sent through Linear's API (KIT-382, KIT-383), with the
# literal controls and two that only a later save decodes.
MEASURED_SPELLINGS = (
    "[agent\\=codex]", "[agent&#61;codex]", "[agent&equals;codex]", "&#91;agent=codex]",
    "[repo\\=product#evil-branch]", "[model\\=haiku]", "repo\\=other#evil",
    "repo&#61;other#evil", "[&#97;gent=codex]", "[agent=codex\\]",
    "&#x5B;agent&#x3D;codex&#x5D;", "[agent&#X3D;codex]", "[&#0097;gent=codex]",
    "\\[agent\\=codex\\]", "[repo&#61;evil#branch]", "[AGENT=codex]", "[agent=codex]",
    "[agent&amp;#61;codex]", "&amp;#91;agent=codex]",
)
# Text with no directive in it, which must come back byte for byte.
UNTOUCHED = (
    "a regex `\\d+\\.\\d+` and \\\\ and &amp; and &lt;b&gt;",
    "```\nre.compile(r\"\\[x\\]=\\d\")\n```",
    "email a@b.com, AT&T, 5 &lt; 6", "[model] [agent] [repo] model=opus",
    "a repository=foo field", "nested [[x]] and \\[not a tag\\]", "",
    "unicode \uff3bagent\uff1dcodex\uff3d and agent\u200b=x",
    "[agent<span></span>=codex] and [ag&shy;ent=codex]",
)


def selftest_cases():
    """(cases, failures). Every measured spelling, in plain text and in code, is replaced, and
    nothing the dispatcher parses is left after zero to three decodes; text with no
    directive is unchanged; a seeded fuzz of mixed spellings finds nothing left; the mark
    holds no directive; and the spans name the original slices."""
    import random
    failures, cases = [], [0]

    def check(name, ok, detail=""):
        cases[0] += 1
        if not ok:
            failures.append("%s: %s" % (name, detail))

    for form in MEASURED_SPELLINGS:
        for frame in ("%s", "before %s after", "one\n%s\nthree", "`%s`", "```\n%s\n```"):
            text = frame % form
            out = neutralize_routing(text)
            left = [r for rounds in range(4) for r in dispatcher_reads(decoded(out, rounds))]
            check("measured:%r:%r" % (form, frame), not left and ROUTING_TAG_MARK in out,
                  (out, left))
        # …and replaced whole, with nothing of it left over.
        out = neutralize_routing("before %s after" % form)
        check("measured-whole:%r" % form, out == "before %s after" % ROUTING_TAG_MARK, out)
    for text in UNTOUCHED:
        check("untouched:%r" % text, neutralize_routing(text) == text, neutralize_routing(text))
    check("mark-holds-no-directive",
          neutralize_routing(ROUTING_TAG_MARK) == ROUTING_TAG_MARK
          and directive_spans(ROUTING_TAG_MARK) == [], ROUTING_TAG_MARK)
    sample = "a [agent\\=codex] b [repo=x] c repo&#61;y d [model&amp;#61;z]"
    sliced = [sample[a:b] for a, b in directive_spans(sample)]
    check("spans-name-the-original-slices",
          sliced == ["[agent\\=codex]", "[repo=x]", "repo&#61;y", "[model&amp;#61;z]"], sliced)
    # Each directive keeps a mark of its own, as before KIT-381, even when two touch.
    check("touching-directives-keep-a-mark-each",
          neutralize_routing("[repo=a][agent\\=b]repo=c") == ROUTING_TAG_MARK * 3,
          neutralize_routing("[repo=a][agent\\=b]repo=c"))
    rng = random.Random(381)

    def spell(ch):
        forms = [ch, "&#%d;" % ord(ch), "&#%s%d;" % ("0" * rng.randint(1, 3), ord(ch)),
                 "&#x%x;" % ord(ch), "&#X%X;" % ord(ch)]
        if ch in ASCII_PUNCT:
            forms.append("\\" + ch)
        forms += ["&%s;" % k for k, v in NAMED_REFS.items() if v == ch]
        if ch == "=":
            forms.append("&amp;#61;")
        return rng.choice(forms)

    for tag in ("[agent=codex]", "[model=haiku]", "[repo=evil#main]", " repo=evil#main",
                " repos=a,b#main", "[Agent=codex]"):
        left = []
        for _ in range(600):
            text = "x" + "".join(spell(c) for c in tag) + " y"
            out = neutralize_routing(text)
            left += [(text, r) for rounds in range(3)
                     for r in dispatcher_reads(decoded(out, rounds))]
        check("fuzz:%r" % tag, not left, left[:3])
    # One definition: no other script here compiles its own copy of the patterns, as three
    # did before KIT-381, each pinned to the next by a parity test that agreed on the miss.
    import os
    import shutil
    import tempfile
    copies = copies_in(os.path.dirname(os.path.abspath(__file__)))
    check("no-other-script-keeps-a-copy", copies == [], copies)
    planted = tempfile.mkdtemp(prefix="routing-tags-selftest-")
    try:
        for name, src in (("pipeline_routing_tags.py", 'X = r"%s"\n' % BRACKET_TAG_RE.pattern),
                          ("a_copy.py", 'X = r"%s"\n' % BRACKET_TAG_RE.pattern),
                          ("b_copy.py", 'Y = r"%s"\n' % UNBRACKETED_REPO_RE.pattern),
                          ("clean.py", "Z = 1\n"), ("notes.txt", BRACKET_TAG_RE.pattern)):
            with open(os.path.join(planted, name), "w", encoding="utf-8") as fh:
                fh.write(src)
        check("a-planted-copy-is-found", copies_in(planted) == ["a_copy.py", "b_copy.py"],
              copies_in(planted))
    finally:
        shutil.rmtree(planted, ignore_errors=True)
    return cases[0], failures


def copies_in(directory):
    """The scripts in `directory`, other than this one, that carry their own copy of either
    pattern's distinctive source text."""
    import os
    found = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".py") or name == "pipeline_routing_tags.py":
            continue
        with open(os.path.join(directory, name), encoding="utf-8") as fh:
            src = fh.read()
        if "(?:repos?|model|agent)" in src or "repos?=[A-Za-z0-9_" in src:
            found.append(name)
    return found


def selftest():
    cases, failures = selftest_cases()
    for failure in failures:
        print("FAIL " + failure)
    print("%s: pipeline_routing_tags selftest (%d cases)"
          % ("OK" if not failures else "FAILED", cases))
    return 1 if failures else 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        sys.exit(selftest())
    print(__doc__.strip().splitlines()[2].strip())
    sys.exit(2)
