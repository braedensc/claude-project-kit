#!/usr/bin/env python3
"""The generation-side gate: a document that does not conform is never written.

THE FAILURE THIS EXISTS TO PREVENT

    `schemas/` and `docs/PIPELINE-CONTRACT.md` §12 already say what each pipeline
    document must look like, and `scripts/check_schemas.py` will tell you whether
    a file conforms. But every producer asked for the shape in PROSE and checked
    it — if at all — AFTER the file had already landed. A check that runs after
    the write is a diagnosis, not a guard:

      • `/setup-board` once emitted a `delivery.json` sharing zero field names
        with §1. A version-less config is BROKEN, not off (§2), so the PreToolUse
        hook failed closed on every tool call — in the one file the agent would
        have needed to edit to repair it. Human-only terminal recovery.
      • A session that appends one malformed request to its safe-outputs batch
        loses the WHOLE batch (§8 is all-or-nothing) — its telemetry and its move
        to review with it — and only finds out in a job that runs after it ended.

    Both are the same class: the document was produced, then judged. This script
    inverts that. It validates a CANDIDATE and writes the destination only if the
    candidate conforms, so a malformed document is not a file anyone has to find.

WHAT IT IS NOT

    Shape is not semantics, and this changes nothing about that. A schema-valid
    `delivery.json` can still carry a UUID that resolves to nothing; a schema-
    valid safe-outputs batch can still name a ticket the session was never pinned
    to. EVERY consumer-side check stays exactly where it is — the hook's BROKEN
    classification (§2), `scripts/check_delivery_config.py`'s semantic rules, and
    §8's validation against the DISPATCHER-SUPPLIED pinned ID, which is the one
    that matters. Defense in depth. Conforming to a schema earns a document
    nothing; it only stops the malformed ones being written at all.

THE PRODUCERS THAT CANNOT ROUTE THROUGH HERE

    A model that hands over its FINAL MESSAGE is constrained at generation
    instead (`--json-schema`, or the SDK's `outputFormat`). A model that writes a
    FILE with a tool is constrained by neither, so it is refused at the boundary
    that reads the file — §14's findings file is that case, and its gate is a
    workflow step. The selftest below EXTRACTS AND EXECUTES that step, so
    "every producer is gated" is one battery's answer rather than five
    scattered claims. The pin's two dispatchers are checked the same way, from
    `scripts/pipeline_dispatch_local.py`, where the workflow parser already lives.

Usage:
    emit_document.py --schema NAME --candidate FILE --install DEST
    emit_document.py --append REQUEST [--into BATCH]     # safe-outputs only
    emit_document.py --list
    emit_document.py --selftest

Exit: 0 = written (or already correct), 1 = REFUSED, nothing written,
      2 = usage/IO error.
"""
import argparse
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_schemas as cs  # noqa: E402
import telemetry_block as tb  # noqa: E402

# One registry, not two: the schemas this can emit are exactly the ones
# check_schemas.py holds to contract parity. A schema added there is emittable
# here the moment it exists, and one that is dropped stops being emittable —
# neither half can grow a document type the other has never heard of.
SCHEMAS = cs.SCHEMAS

SAFE_OUTPUTS = "safe-outputs"
EMPTY_BATCH = {"schema": tb.SAFE_OUTPUTS_SCHEMA, "requests": []}


def die(message, code=2):
    print("FAIL: %s" % message, file=sys.stderr)
    sys.exit(code)


def read_json(path, what):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except OSError as exc:
        die("cannot read %s (%s): %s" % (what, path, exc))
    except ValueError as exc:
        die("%s (%s) is not valid JSON: %s" % (what, path, exc), 1)


def problems_against(document, name):
    """Every way `document` fails schemas/<name>.schema.json, human-readable.

    Deliberately a one-line delegate rather than its own implementation: the
    definition lives in check_schemas.py, which is also what the two dispatchers
    and the review emitter call. A gate with a private idea of "conforms" would
    be a fifth rendering of a contract that already has two.
    """
    return cs.document_problems(document, name)


def telemetry_problems(batch):
    """Every §4 block inside a §8 batch, held to `schemas/telemetry-block.schema.json`.

    §8 requires exactly one valid block per batch, and the credential-holding
    validator already enforces that. It enforces it REMOTELY, though, in a job
    that runs after the session ended — so a session that mis-shapes its own
    telemetry learns about it by having its entire batch rejected with nobody
    left to fix it. Checking here means the block is refused while the session
    that wrote it is still running.

    `require_telemetry=False` on purpose: a batch mid-assembly legitimately has
    no block yet (`/work` queues it last, after `/ship` has produced the PR
    number). The exactly-one rule is the validator's, and it stays there.
    """
    return tb.gate(tb.comment_bodies(batch), require_telemetry=False)["errors"]


def atomic_write(path, document):
    """Write `document` where a reader never sees a half-written file.

    Temp file in the destination's own directory, fsync, rename. The rename is
    the only thing that publishes, and a failure anywhere before it leaves the
    destination exactly as it was — which is the whole promise this script makes
    to a producer whose previous output was valid.
    """
    parent = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".emit-", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def refuse(name, dest, problems, extra=""):
    print("REFUSED: the document this run built violates schemas/%s (%d problem(s)):"
          % (SCHEMAS[name][0], len(problems)), file=sys.stderr)
    for problem in problems:
        print("  - %s" % problem, file=sys.stderr)
    print("\nNothing was written. %s is unchanged.%s" % (dest, extra), file=sys.stderr)
    return 1


# --------------------------------------------------------------------------- #
# Modes
# --------------------------------------------------------------------------- #
def install(candidate_path, name, dest):
    """Validate a candidate, then install it — or write nothing at all."""
    candidate = read_json(candidate_path, "the candidate document")
    problems = problems_against(candidate, name)
    if problems:
        return refuse(name, dest, problems,
                      "\nFix the candidate (%s) and run this again — a document that "
                      "lands and is judged afterwards is the failure this gate exists "
                      "to prevent." % candidate_path)
    atomic_write(dest, candidate)
    print("OK    %s installed at %s (validated against schemas/%s)"
          % (candidate_path, dest, SCHEMAS[name][0]))
    print("      Shape only. The semantic layer still runs: %s" % _semantic_note(name))
    return 0


def _semantic_note(name):
    return {
        "delivery": "python3 scripts/check_delivery_config.py",
        "pin": "the hook that reads the pin back, and the dispatcher's own MUSTs",
        SAFE_OUTPUTS: "the credential-holding validator, against the PINNED ticket id",
        "telemetry-block": "the safe-outputs validator and the collector",
    }.get(name, "every consumer-side check named in docs/PIPELINE-CONTRACT.md")


def append(request_path, into):
    """Append one §8 request — or leave the batch exactly as it was.

    The property that earns this its place: a malformed request costs the
    session THAT REQUEST, not the batch. §8 is all-or-nothing at the validator,
    so a bad request written to the file would take the telemetry block and the
    move to review down with it, remotely, after the session ended.
    """
    request = read_json(request_path, "the request object")
    if not isinstance(request, dict):
        die("the request (%s) must be one JSON object, not %s"
            % (request_path, type(request).__name__), 1)

    batch = EMPTY_BATCH
    if os.path.exists(into):
        batch = read_json(into, "the existing batch")
        if not isinstance(batch, dict) or batch.get("schema") != tb.SAFE_OUTPUTS_SCHEMA:
            die("refusing to append to an unrecognized schema: %r. A reader that does "
                "not recognize the schema refuses; it does not guess (§8)."
                % (batch.get("schema") if isinstance(batch, dict) else batch), 1)
        if not isinstance(batch.get("requests"), list):
            die("%s has no `requests` array to append to." % into, 1)

    candidate = {"schema": tb.SAFE_OUTPUTS_SCHEMA,
                 "requests": list(batch.get("requests") or []) + [request]}

    problems = problems_against(candidate, SAFE_OUTPUTS) + telemetry_problems(candidate)
    if problems:
        return refuse(SAFE_OUTPUTS, into, problems,
                      "\nThe %d request(s) already queued are untouched — §8 rejects a "
                      "batch as a whole, so one bad request written here would have "
                      "cost you the telemetry block and the state move too."
                      % len(batch.get("requests") or []))

    atomic_write(into, candidate)
    print("queued %s (%d request(s) pending, validated against schemas/%s)"
          % (request.get("type"), len(candidate["requests"]), SCHEMAS[SAFE_OUTPUTS][0]))
    print("      Shape only. The validator still compares every ticket_id against the "
          "DISPATCHER-SUPPLIED pinned id, and that is the check that matters.")
    return 0


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #
GOOD_REQUEST = {"type": "ticket-comment", "ticket_id": "ENG-123", "body": "the plan"}
GOOD_STATE = {"type": "ticket-state", "ticket_id": "ENG-123", "to": "review"}


def _fence(document):
    return "before\n\n```json\n%s\n```\n\nafter" % json.dumps(document, indent=2)


def _run(argv):
    import subprocess
    return subprocess.run([sys.executable, os.path.abspath(__file__), *argv],
                          capture_output=True, text=True, timeout=120)


REVIEW_WORKFLOW = os.path.join("templates", "workflows", "pipeline-review.yml")


def _review_normalize_block():
    """The review workflow's `Normalize the findings` step, as runnable Python.

    §14's gate is a workflow step, not a call into this script — the model there
    writes a FILE, and neither `--json-schema` nor `outputFormat` constrains a
    tool call. A producer that cannot route through this gate still has to BE
    gated, so the step is extracted and actually executed below rather than
    grepped: a comment about validating findings must not be able to pass.
    """
    import re
    import textwrap
    path = os.path.join(cs.REPO_ROOT, REVIEW_WORKFLOW)
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    blocks = [textwrap.dedent(body) for _, body in
              re.findall(r"\n( *)python3 - <<'PY'\n(.*?)\n\1PY\n", source, re.S)]
    return next((b for b in blocks if "REVIEW-FINDINGS.json" in b), None)


def _run_normalize(block, findings, tmpdir, stage_gate=True):
    """Run the extracted step over one findings file. Returns its artifact.

    `stage_gate` mirrors what the prep step does with `git show origin/$base:…`:
    the schema and the vendored validator are staged from the DEFAULT BRANCH,
    outside the worktree the reviewer session could write to.
    """
    import shutil
    import subprocess
    root = tempfile.mkdtemp(dir=tmpdir)
    runner_temp = os.path.join(root, "runner")
    if stage_gate:
        os.makedirs(os.path.join(runner_temp, "gate", "scripts"))
        os.makedirs(os.path.join(runner_temp, "gate", "schemas"))
        for name in ("jsonschema_mini.py", "check_schemas.py"):
            shutil.copy(os.path.join(cs.REPO_ROOT, "scripts", name),
                        os.path.join(runner_temp, "gate", "scripts", name))
        shutil.copy(os.path.join(cs.SCHEMA_DIR, "review-findings.schema.json"),
                    os.path.join(runner_temp, "gate", "schemas", "review-findings.schema.json"))
    # What the step's own shell does before handing over to Python.
    os.makedirs(os.path.join(runner_temp, "findings"), exist_ok=True)
    if findings is not None:
        with open(os.path.join(root, "REVIEW-FINDINGS.json"), "w", encoding="utf-8") as handle:
            handle.write(findings if isinstance(findings, str) else json.dumps(findings))
    env = dict(os.environ,
               RUNNER_TEMP=runner_temp, TICKET="ENG-123", DISPATCH_ID="d_01JAV8Q2S6",
               PR="41", THRESHOLD="medium", AGENT_RESULT="success")
    result = subprocess.run([sys.executable, "-c", block], cwd=root, env=env,
                            capture_output=True, text=True, timeout=120)
    artifact = os.path.join(runner_temp, "findings", "findings.json")
    if result.returncode != 0 or not os.path.exists(artifact):
        return {"__crashed__": result.returncode, "stderr": result.stderr[-400:]}
    with open(artifact, encoding="utf-8") as handle:
        return json.load(handle)


def _quiet(function, *args):
    """Run `function`, swallowing its report.

    Most of this selftest asserts REFUSALS, and every refusal prints a paragraph
    explaining itself. Left unmuffled, a green run buries its own verdict under
    thirty stanzas of expected failure, and CI output nobody reads is CI output
    nobody notices going red.
    """
    import contextlib
    import io
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        return function(*args)


def selftest():
    import shutil
    failures, counted = [], [0]

    def expect(condition, message):
        counted[0] += 1
        if not condition:
            failures.append(message)

    def strays(directory):
        return [n for n in os.listdir(directory) if n.startswith(".emit-")]

    good_delivery = os.path.join(cs.REPO_ROOT, "delivery.example.json")
    tmpdir = tempfile.mkdtemp(prefix="emit-selftest-")
    try:
        # -- install: the happy path, and the shape it is held to -------------- #
        dest = os.path.join(tmpdir, "delivery.json")
        expect(_quiet(install, good_delivery, "delivery", dest) == 0,
               "install refused delivery.example.json, which is the spec's own shape")
        with open(dest, encoding="utf-8") as handle:
            installed = json.load(handle)
        with open(good_delivery, encoding="utf-8") as handle:
            expect(installed == json.load(handle),
                   "install wrote something other than the candidate it validated")

        # -- install: THE regression that earned this ticket ------------------- #
        # The shape /setup-board once emitted. Under the old order it landed and
        # was judged afterwards, by which point the hook was failing closed on
        # every tool call. Here it must never reach the destination.
        bad = os.path.join(tmpdir, "bad-delivery.json")
        with open(bad, "w", encoding="utf-8") as handle:
            json.dump(cs.SETUP_BOARD_REGRESSION, handle)
        virgin = os.path.join(tmpdir, "never-written.json")
        expect(_quiet(install, bad, "delivery", virgin) == 1,
               "install ACCEPTED the malformed /setup-board shape — the gate has no teeth")
        expect(not os.path.exists(virgin),
               "install created %s while refusing the candidate — a refused document "
               "must not be a file anyone has to find" % virgin)

        # …and it must not clobber a destination that was already good.
        expect(_quiet(install, bad, "delivery", dest) == 1, "install accepted the malformed shape")
        with open(dest, encoding="utf-8") as handle:
            expect(json.load(handle) == installed,
                   "a REFUSED install still modified the destination")
        expect(strays(tmpdir) == [], "a refused install left a .emit-*.tmp behind")

        # -- install: pin, the dispatcher's document --------------------------- #
        pin_path = os.path.join(tmpdir, "pin.json")
        good_pin = os.path.join(tmpdir, "cand-pin.json")
        with open(good_pin, "w", encoding="utf-8") as handle:
            json.dump(cs.VALID_PIN, handle)
        expect(_quiet(install, good_pin, "pin", pin_path) == 0, "install refused a valid pin")
        # The branch guard is `[a-z0-9-]` only, so an uppercased team key in the
        # pinned branch blocks the session before its first edit. Shape catches it.
        bad_pin = os.path.join(tmpdir, "cand-pin-bad.json")
        with open(bad_pin, "w", encoding="utf-8") as handle:
            json.dump(dict(cs.VALID_PIN, branch="feat/ENG-123-x"), handle)
        after = os.path.join(tmpdir, "pin2.json")
        expect(_quiet(install, bad_pin, "pin", after) == 1,
               "install accepted a pin whose branch the live guard rejects")
        expect(not os.path.exists(after), "install wrote a pin it had refused")

        # -- append: builds a batch, and keeps a good one good ----------------- #
        batch = os.path.join(tmpdir, "safe-outputs", "requests.json")
        req = os.path.join(tmpdir, "req.json")

        def write_request(document):
            with open(req, "w", encoding="utf-8") as handle:
                json.dump(document, handle)
            return req

        expect(_quiet(append, write_request(GOOD_REQUEST), batch) == 0,
               "append refused a valid ticket-comment into a fresh batch")
        expect(_quiet(append, write_request(GOOD_STATE), batch) == 0,
               "append refused a valid ticket-state")
        with open(batch, encoding="utf-8") as handle:
            two = json.load(handle)
        expect(len(two["requests"]) == 2 and two["schema"] == tb.SAFE_OUTPUTS_SCHEMA,
               "append did not build the §8 envelope it was asked for: %r" % two)

        # Each of these is a request the remote validator would reject — which,
        # §8 being all-or-nothing, would cost the session the two GOOD requests
        # above as well. Every one must be refused here, with the batch intact.
        for label, malformed in (
            ("unknown type", {"type": "ticket-delete", "ticket_id": "ENG-123"}),
            ("empty body", {"type": "ticket-comment", "ticket_id": "ENG-123", "body": "  "}),
            ("self-approval", {"type": "ticket-state", "ticket_id": "ENG-123", "to": "ready"}),
            ("claimed merge", {"type": "ticket-state", "ticket_id": "ENG-123", "to": "done"}),
            ("own supervision", {"type": "ticket-label", "ticket_id": "ENG-123",
                                 "add": ["agent:blocked"], "remove": []}),
            ("clearing supervision", {"type": "ticket-label", "ticket_id": "ENG-123",
                                      "add": [], "remove": ["agent:blocked"]}),
            ("hooks-change", {"type": "ticket-label", "ticket_id": "ENG-123",
                              "add": ["hooks-change"], "remove": []}),
            ("missing body", {"type": "ticket-comment", "ticket_id": "ENG-123"}),
            ("lowercase ticket", {"type": "ticket-comment", "ticket_id": "eng-123",
                                  "body": "x"}),
            ("invented field", {"type": "ticket-comment", "ticket_id": "ENG-123",
                                "body": "x", "priority": "urgent"}),
        ):
            expect(_quiet(append, write_request(malformed), batch) == 1,
                   "append ACCEPTED a request the validator would reject (%s)" % label)
            with open(batch, encoding="utf-8") as handle:
                expect(json.load(handle) == two,
                       "a REFUSED append (%s) modified the batch — the point of this "
                       "gate is that one bad request costs the request, not the batch"
                       % label)

        # -- append: the telemetry block is checked HERE, not only remotely ---- #
        valid_block = _fence(cs.VALID_TELEMETRY)
        broken_block = _fence(dict(cs.VALID_TELEMETRY,
                                   runs=[dict(cs.VALID_TELEMETRY["runs"][0], turns=-1)]))
        unfenced = "queued: " + json.dumps(cs.VALID_TELEMETRY)

        expect(_quiet(append, write_request({"type": "ticket-comment", "ticket_id": "ENG-123",
                                     "body": broken_block}), batch) == 1,
               "append accepted a comment carrying a MALFORMED telemetry block")
        expect(_quiet(append, write_request({"type": "ticket-comment", "ticket_id": "ENG-123",
                                     "body": unfenced}), batch) == 1,
               "append accepted a telemetry marker outside a ```json fence — the "
               "collector scans fences, so those rows would never be collected")
        with open(batch, encoding="utf-8") as handle:
            expect(json.load(handle) == two, "a refused telemetry append modified the batch")
        expect(_quiet(append, write_request({"type": "ticket-comment", "ticket_id": "ENG-123",
                                     "body": valid_block}), batch) == 0,
               "append refused a VALID telemetry block — no new noise on good input")

        # -- append: §8's caps, and an envelope it must not guess about -------- #
        full = os.path.join(tmpdir, "full.json")
        with open(full, "w", encoding="utf-8") as handle:
            json.dump({"schema": tb.SAFE_OUTPUTS_SCHEMA,
                       "requests": [GOOD_REQUEST] * 20}, handle)
        expect(_quiet(append, write_request(GOOD_REQUEST), full) == 1,
               "append accepted a 21st request past §8's cap of 20")

        alien = os.path.join(tmpdir, "alien.json")
        with open(alien, "w", encoding="utf-8") as handle:
            json.dump({"schema": "pipeline-safe-outputs/2", "requests": []}, handle)
        result = _run(["--append", write_request(GOOD_REQUEST), "--into", alien])
        expect(result.returncode == 1,
               "append did not refuse an unrecognized batch schema (exit %d)"
               % result.returncode)
        with open(alien, encoding="utf-8") as handle:
            expect(json.load(handle)["schema"] == "pipeline-safe-outputs/2",
                   "append rewrote a batch whose schema it did not recognize")

        expect(strays(os.path.dirname(batch)) == [],
               "a refused append left a .emit-*.tmp in the safe-outputs directory")

        # -- §14: the producer that CANNOT route through this gate ------------- #
        # The review's model writes a FILE, and neither `--json-schema` nor
        # `outputFormat` constrains a tool call — so its gate is a workflow step.
        # It is EXECUTED here, not grepped: a step that merely mentions the schema
        # would pass a text check while gating nothing.
        block = _review_normalize_block()
        expect(block is not None,
               "%s: no embedded Python step reads REVIEW-FINDINGS.json — the review "
               "gate moved or was renamed, and these checks now assert nothing"
               % REVIEW_WORKFLOW)
        if block:
            verdict = _run_normalize(block, cs.VALID_REVIEW_FINDINGS, tmpdir)
            expect(verdict.get("usable") is True,
                   "the review gate rejected a §14-VALID findings file — no new noise "
                   "on good input is the other half of this: %r" % verdict)
            expect(verdict.get("max_severity") == "high"
                   and verdict.get("meets_threshold") is True,
                   "the review gate mis-ranked a valid file: %r" % verdict)

            # `cs._with`/`cs._without` are the fixture mutators the schema battery
            # already uses; the fixtures under test are the same ones, so the two
            # batteries cannot disagree about what a valid finding looks like.
            for label, broken in (
                # THE regression. A capitalized severity used to be dropped from
                # the list while the run still reported itself usable — a review
                # that had found something, reporting that it had not.
                ("a capitalized severity",
                 cs._with(cs.VALID_REVIEW_FINDINGS, "findings.0.severity", "Critical")),
                ("an invented severity",
                 cs._with(cs.VALID_REVIEW_FINDINGS, "findings.0.severity", "blocker")),
                ("a finding with no detail",
                 cs._without(cs.VALID_REVIEW_FINDINGS, "findings", 0, "detail")),
                ("a field the schema does not define",
                 cs._with(cs.VALID_REVIEW_FINDINGS, "confidence", "high")),
                ("a model claiming its own review usable",
                 cs._with(cs.VALID_REVIEW_FINDINGS, "usable", True)),
                ("an unrecognized marker",
                 cs._with(cs.VALID_REVIEW_FINDINGS, "schema", "pipeline-review/2")),
                ("prose instead of a document", "I reviewed it and it looks fine."),
                ("no file at all", None),
            ):
                verdict = _run_normalize(block, broken, tmpdir)
                expect(verdict.get("usable") is False,
                       "the review gate accepted %s. Malformed must be UNUSABLE, never "
                       "quietly smaller: %r" % (label, verdict))
                expect("UNREVIEWED" in str(verdict.get("summary", "")),
                       "the review gate refused %s without saying the PR is UNREVIEWED "
                       "— an unusable review that does not say so reads as clean: %r"
                       % (label, verdict))

            # An unreachable gate is itself a shape failure. A step that cannot
            # check the document must not be the step that calls it good.
            verdict = _run_normalize(block, cs.VALID_REVIEW_FINDINGS, tmpdir, stage_gate=False)
            expect(verdict.get("usable") is False,
                   "the review gate reported a findings file usable while its schema "
                   "was unreachable — it judged what it could not check: %r" % verdict)

        # -- CLI surface: the ways a producer wires this in wrong --------------- #
        env_free = dict(os.environ)
        env_free.pop("PIPELINE_SAFE_OUTPUTS", None)
        import subprocess
        result = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--append", req],
            capture_output=True, text=True, env=env_free, timeout=120)
        expect(result.returncode == 2,
               "--append with no --into and no $PIPELINE_SAFE_OUTPUTS should be a "
               "usage error (got %d) — §8: do not invent a path" % result.returncode)

        result = _run(["--schema", "delivery", "--candidate", good_delivery])
        expect(result.returncode == 2, "--candidate without --install should be usage")

        result = _run(["--list"])
        expect(result.returncode == 0 and "safe-outputs" in result.stdout,
               "--list did not print the emittable schemas")

        # Every schema check_schemas.py holds to parity must be emittable, or the
        # gate covers a subset of the documents the contract defines and nobody
        # is told which.
        for name in SCHEMAS:
            counted[0] += 1
            try:
                cs.load_schema(name)
            except (OSError, ValueError) as exc:
                failures.append("schema %r is registered but unloadable: %s" % (name, exc))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    if failures:
        print("FAIL: emit_document selftest")
        for failure in failures:
            print("  - %s" % failure)
        return 1
    print("OK: emit_document selftest (%d checks)" % counted[0])
    return 0


# --------------------------------------------------------------------------- #
def main():
    parser = argparse.ArgumentParser(add_help=True, description=__doc__.splitlines()[0])
    parser.add_argument("--schema", choices=sorted(SCHEMAS),
                        help="which schema the candidate is held to")
    parser.add_argument("--candidate", help="the document to validate")
    parser.add_argument("--install", metavar="DEST",
                        help="where to write it, if and only if it conforms")
    parser.add_argument("--append", metavar="REQUEST",
                        help="one §8 request object to add to a safe-outputs batch")
    parser.add_argument("--into", metavar="BATCH",
                        help="the batch to append to (default: $PIPELINE_SAFE_OUTPUTS)")
    parser.add_argument("--list", action="store_true", dest="list_schemas")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    if args.list_schemas:
        for name, (filename, number, _) in sorted(SCHEMAS.items()):
            print("%-16s schemas/%-32s contract §%s" % (name, filename, number.rstrip(".")))
        return 0

    if args.append:
        if args.candidate or args.install or args.schema:
            die("--append is its own mode; it does not take --candidate/--install/--schema")
        into = args.into or os.environ.get("PIPELINE_SAFE_OUTPUTS") or ""
        if not into:
            die("no batch to append to: pass --into, or set $PIPELINE_SAFE_OUTPUTS.\n"
                "An unset $PIPELINE_SAFE_OUTPUTS means no validator is collecting — "
                "print the batch you would have emitted and say plainly that it was "
                "not delivered. Do NOT invent a path, and never write it inside the "
                "worktree (contract §8).")
        return _quiet(append, args.append, into)

    if not (args.candidate and args.install and args.schema):
        die("--candidate, --install and --schema are used together.\n"
            "  emit_document.py --schema delivery --candidate cand.json --install delivery.json")
    return _quiet(install, args.candidate, args.schema, args.install)


if __name__ == "__main__":
    sys.exit(main())
