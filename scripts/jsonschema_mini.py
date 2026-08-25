#!/usr/bin/env python3
"""A minimal JSON Schema (draft 2020-12) validator — stdlib only, vendored.

WHY THIS EXISTS RATHER THAN `pip install jsonschema`

The kit's Python is stdlib-only and its CI installs no Python packages beyond
`pyyaml` for one parse step. Every guard, gate and collector here is a single
file you can read end to end. Adding a runtime dependency to the *validator that
decides whether the pipeline may run* trades that for a supply-chain edge and a
CI install step, to gain keywords these schemas do not use. So the subset lives
here, in ~400 lines, and the schemas are held to it mechanically by
`check_schema()` below.

THE ONE RULE THAT MAKES A VENDORED SUBSET SAFE

    An unrecognized keyword is an ERROR, never a silent no-op.

A validator that ignores what it does not understand is worse than no validator:
a schema author writes `"minimum": 1`, the field is never checked, and the
schema reads as enforcement while enforcing nothing. `check_schema()` walks a
schema and rejects any keyword outside SUPPORTED, so a keyword this file cannot
enforce cannot reach a schema file. `scripts/check_schemas.py` runs it over
every shipped schema in CI.

SUPPORTED KEYWORDS

    core        $schema $id $ref $defs $comment title description
                default examples deprecated            (annotations, no-ops)
    any         type enum const
    object      properties patternProperties additionalProperties propertyNames
                required minProperties maxProperties
    array       items prefixItems minItems maxItems uniqueItems
    number      minimum maximum exclusiveMinimum exclusiveMaximum multipleOf
    string      minLength maxLength pattern

    Plus any `x-*` key, which is an annotation this file carries but never
    asserts. The kit's schemas use `x-rule`, `x-fix`, `x-tier` and
    `x-tier-<keyword>` so a consumer can render a violation in its own
    vocabulary (see `scripts/check_delivery_config.py`).

DELIBERATE DEVIATIONS, all in the direction of "fewer surprises"

  * `$ref` resolves only within the current document, as `#` or `#/$defs/<name>`.
    Remote refs would mean network I/O inside a guard. `check_schema()` rejects
    anything else.
  * A `$ref` may carry sibling keys, but ONLY annotations and `description` —
    the referring site refines how a violation is *reported*, never what is
    *valid*. `check_schema()` rejects a validation keyword beside a `$ref`,
    because merging two `required` lists silently is exactly the kind of
    quiet behavior this file exists to avoid.
  * `format` is not implemented at all. Use `pattern`: it is portable, exact,
    and it cannot be read as an assertion in one tool and an annotation in
    another.
  * Booleans are never numbers. `True` is not a valid `integer` here even
    though Python says `isinstance(True, int)`.
  * `1.0` IS a valid `integer` (JSON Schema: a number with zero fractional
    part). Callers that need a JSON integer *literal* keep their own check —
    `check_delivery_config.py` does, via `is_int()`.

Usage:
    from jsonschema_mini import validate, check_schema
    errors = validate(instance, schema)      # [] when the instance conforms

    jsonschema_mini.py --selftest
"""
import json
import math
import re
import sys

# --------------------------------------------------------------------------- #
# The keyword surface. Nothing outside this may appear in a shipped schema.
# --------------------------------------------------------------------------- #
ANNOTATIONS = frozenset(
    {"$schema", "$id", "$comment", "title", "description", "default", "examples", "deprecated"}
)
APPLICATORS = frozenset({"properties", "patternProperties", "additionalProperties", "propertyNames",
                         "items", "prefixItems", "oneOf"})
ASSERTIONS = frozenset({
    "type", "enum", "const",
    "required", "minProperties", "maxProperties",
    "minItems", "maxItems", "uniqueItems",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minLength", "maxLength", "pattern",
})
SUPPORTED = ANNOTATIONS | APPLICATORS | ASSERTIONS | {"$ref", "$defs"}

TYPE_NAMES = ("null", "boolean", "object", "array", "number", "integer", "string")

REF_RE = re.compile(r"^#(?:/\$defs/([A-Za-z0-9_.-]+))?$")


class SchemaError(Exception):
    """The SCHEMA is wrong — distinct from the instance being invalid."""


# --------------------------------------------------------------------------- #
# Instance validation
# --------------------------------------------------------------------------- #
def validate(instance, schema, root=None):
    """Return a list of error dicts; empty means the instance conforms.

    Each error carries:
        path     dotted instance path ("" = the whole document)
        pointer  RFC-6901 JSON pointer to the same place
        keyword  the keyword that failed
        message  the detail, phrased WITHOUT the path so a caller can prefix it
        chain    every schema node enclosing the failure, outermost first, so a
                 caller can resolve an `x-*` annotation from the most specific
                 node that defines one (see `annotation()`)
    """
    if root is None:
        root = schema
    return _validate(instance, schema, root, "", "", [])


def annotation(error, name, default=None):
    """The value of `name` from the most specific schema node that defines it."""
    for node in reversed(error["chain"]):
        if isinstance(node, dict) and name in node:
            return node[name]
    return default


def _err(path, pointer, keyword, message, chain):
    return {"path": path, "pointer": pointer or "", "keyword": keyword,
            "message": message, "chain": chain}


def _child(path, pointer, key):
    """Extend an instance path/pointer by one object key or array index."""
    key = str(key)
    dotted = f"{path}.{key}" if path else key
    escaped = key.replace("~", "~0").replace("/", "~1")
    return dotted, f"{pointer}/{escaped}"


def _resolve(ref, root):
    match = REF_RE.match(ref)
    if not match:
        raise SchemaError(f"$ref {ref!r} is not a local reference (#/$defs/<name> or #)")
    if match.group(1) is None:
        return root
    defs = root.get("$defs") if isinstance(root, dict) else None
    if not isinstance(defs, dict) or match.group(1) not in defs:
        raise SchemaError(f"$ref {ref!r} does not resolve in this document")
    return defs[match.group(1)]


def _effective(schema, root):
    """A `$ref` node flattened into the node it points at.

    Sibling keys win, but `check_schema()` has already guaranteed the only
    siblings are annotations — so this refines REPORTING, never VALIDITY.
    """
    if not isinstance(schema, dict) or "$ref" not in schema:
        return schema
    merged = dict(_resolve(schema["$ref"], root))
    merged.update({k: v for k, v in schema.items() if k != "$ref"})
    return merged


def _kind(value):
    """The JSON type name of a Python value. `bool` is never a number."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    raise SchemaError(f"value of type {type(value).__name__} is not JSON")


def _type_ok(value, name):
    kind = _kind(value)
    if name == "number":
        return kind in ("integer", "number")
    if name == "integer":
        # Spec: a number with zero fractional part is an integer.
        if kind == "integer":
            return True
        return kind == "number" and math.isfinite(value) and float(value).is_integer()
    return kind == name


def _describe(value):
    """A short rendering of a value for a message. Long strings are clipped."""
    if isinstance(value, (dict, list)):
        return _kind(value)
    text = json.dumps(value)
    return text if len(text) <= 60 else text[:57] + "…"


def _validate(instance, schema, root, path, pointer, chain):
    if schema is True:
        return []
    if schema is False:
        return [_err(path, pointer, "schema", "no value is valid here", chain)]
    if not isinstance(schema, dict):
        raise SchemaError(f"schema at {path or '<root>'} is {type(schema).__name__}, not an object")

    node = _effective(schema, root)
    chain = chain + [node]
    errors = []

    kind = _kind(instance)

    # -- type ---------------------------------------------------------------- #
    if "type" in node:
        names = node["type"]
        names = [names] if isinstance(names, str) else list(names)
        if not any(_type_ok(instance, n) for n in names):
            want = names[0] if len(names) == 1 else " or ".join(names)
            errors.append(_err(path, pointer, "type",
                               f"is {kind} ({_describe(instance)}); expected {want}", chain))
            # A wrong type makes every other keyword's verdict noise.
            return errors

    # -- enum / const -------------------------------------------------------- #
    if "enum" in node and instance not in node["enum"]:
        errors.append(_err(path, pointer, "enum",
                           f"is {_describe(instance)}; allowed values are "
                           f"{json.dumps(node['enum'])}", chain))
    if "const" in node and instance != node["const"]:
        errors.append(_err(path, pointer, "const",
                           f"is {_describe(instance)}; must be "
                           f"{json.dumps(node['const'])}", chain))

    # -- oneOf --------------------------------------------------------------- #
    # Present only for tagged unions (the §8 request types). Reporting the union
    # of every branch's complaints is useless noise, so a branch that VALIDATES
    # ends it, and otherwise the closest branch — fewest complaints, which for a
    # `const`-discriminated union is always the branch the author meant — is the
    # one reported.
    if "oneOf" in node:
        attempts = [_validate(instance, branch, root, path, pointer, chain)
                    for branch in node["oneOf"]]
        passing = [i for i, errs in enumerate(attempts) if not errs]
        if len(passing) > 1:
            errors.append(_err(path, pointer, "oneOf",
                               f"matches {len(passing)} of the alternatives; exactly "
                               f"one must match", chain))
        elif not passing:
            errors += min(attempts, key=len)

    if kind == "object":
        errors += _object(instance, node, root, path, pointer, chain)
    elif kind == "array":
        errors += _array(instance, node, root, path, pointer, chain)
    elif kind == "string":
        errors += _string(instance, node, path, pointer, chain)
    elif kind in ("integer", "number"):
        errors += _number(instance, node, path, pointer, chain)

    return errors


def _object(instance, node, root, path, pointer, chain):
    errors = []
    properties = node.get("properties", {})

    # `required` is reported AT THE MISSING CHILD, not at the parent. A caller
    # tiering findings per field then treats "absent" and "malformed" as the
    # same field — which is what a human reading the report expects.
    for key in node.get("required", []):
        if key not in instance:
            child_path, child_pointer = _child(path, pointer, key)
            sub = properties.get(key)
            sub_chain = chain + ([_effective(sub, root)] if isinstance(sub, dict) else [])
            errors.append(_err(child_path, child_pointer, "required",
                               "is missing", sub_chain))

    if "minProperties" in node and len(instance) < node["minProperties"]:
        errors.append(_err(path, pointer, "minProperties",
                           f"has {len(instance)} key(s); at least "
                           f"{node['minProperties']} required", chain))
    if "maxProperties" in node and len(instance) > node["maxProperties"]:
        errors.append(_err(path, pointer, "maxProperties",
                           f"has {len(instance)} key(s); at most "
                           f"{node['maxProperties']} allowed", chain))

    patterns = node.get("patternProperties", {})
    additional = node.get("additionalProperties")
    names_schema = node.get("propertyNames")

    for key, value in instance.items():
        child_path, child_pointer = _child(path, pointer, key)
        matched = False

        if names_schema is not None:
            for error in _validate(key, names_schema, root, child_path, child_pointer, chain):
                error["keyword"] = "propertyNames"
                error["message"] = f"is not a valid property name: {error['message']}"
                errors.append(error)

        if key in properties:
            matched = True
            errors += _validate(value, properties[key], root, child_path, child_pointer, chain)
        for pattern, sub in patterns.items():
            if re.search(pattern, key):
                matched = True
                errors += _validate(value, sub, root, child_path, child_pointer, chain)
        if not matched and additional is not None:
            if additional is False:
                errors.append(_err(child_path, child_pointer, "additionalProperties",
                                   "is not a defined property here", chain))
            else:
                errors += _validate(value, additional, root, child_path, child_pointer, chain)

    return errors


def _array(instance, node, root, path, pointer, chain):
    errors = []
    prefix = node.get("prefixItems", [])
    for index, sub in enumerate(prefix):
        if index >= len(instance):
            break
        child_path, child_pointer = _child(path, pointer, index)
        errors += _validate(instance[index], sub, root, child_path, child_pointer, chain)
    if "items" in node:
        for index in range(len(prefix), len(instance)):
            child_path, child_pointer = _child(path, pointer, index)
            errors += _validate(instance[index], node["items"], root,
                                child_path, child_pointer, chain)
    if "minItems" in node and len(instance) < node["minItems"]:
        errors.append(_err(path, pointer, "minItems",
                           f"has {len(instance)} item(s); at least "
                           f"{node['minItems']} required", chain))
    if "maxItems" in node and len(instance) > node["maxItems"]:
        errors.append(_err(path, pointer, "maxItems",
                           f"has {len(instance)} item(s); at most "
                           f"{node['maxItems']} allowed", chain))
    if node.get("uniqueItems"):
        seen = []
        for item in instance:
            if item in seen:
                errors.append(_err(path, pointer, "uniqueItems",
                                   f"repeats {_describe(item)}; items must be unique", chain))
                break
            seen.append(item)
    return errors


def _string(instance, node, path, pointer, chain):
    errors = []
    if "minLength" in node and len(instance) < node["minLength"]:
        errors.append(_err(path, pointer, "minLength",
                           f"is {_describe(instance)}; at least "
                           f"{node['minLength']} character(s) required", chain))
    if "maxLength" in node and len(instance) > node["maxLength"]:
        errors.append(_err(path, pointer, "maxLength",
                           f"is {len(instance)} characters; at most "
                           f"{node['maxLength']} allowed", chain))
    if "pattern" in node and not re.search(node["pattern"], instance):
        errors.append(_err(path, pointer, "pattern",
                           f"is {_describe(instance)}, which does not match "
                           f"/{node['pattern']}/", chain))
    return errors


def _number(instance, node, path, pointer, chain):
    errors = []
    for keyword, ok, phrase in (
        ("minimum", lambda v, b: v >= b, "at least"),
        ("maximum", lambda v, b: v <= b, "at most"),
        ("exclusiveMinimum", lambda v, b: v > b, "greater than"),
        ("exclusiveMaximum", lambda v, b: v < b, "less than"),
    ):
        if keyword in node and not ok(instance, node[keyword]):
            errors.append(_err(path, pointer, keyword,
                               f"is {_describe(instance)}; must be {phrase} "
                               f"{node[keyword]}", chain))
    if "multipleOf" in node:
        divisor = node["multipleOf"]
        quotient = instance / divisor
        if not math.isclose(quotient, round(quotient), rel_tol=0, abs_tol=1e-9):
            errors.append(_err(path, pointer, "multipleOf",
                               f"is {_describe(instance)}; must be a multiple of "
                               f"{divisor}", chain))
    return errors


# --------------------------------------------------------------------------- #
# Schema validation — the guarantee that makes a vendored subset honest
# --------------------------------------------------------------------------- #
def check_schema(schema, root=None, path="#"):
    """Return a list of problems with the SCHEMA itself. Empty means safe here.

    Rejects any keyword this file cannot enforce, so "the schema says minimum
    and nothing checks it" is impossible by construction.
    """
    problems = []
    if root is None:
        root = schema
    if isinstance(schema, bool):
        return problems
    if not isinstance(schema, dict):
        return [f"{path}: schema is {type(schema).__name__}, not an object or boolean"]

    for key in schema:
        if key.startswith("x-") or key in SUPPORTED:
            continue
        problems.append(
            f"{path}: unsupported keyword {key!r}. scripts/jsonschema_mini.py "
            f"cannot enforce it, and a keyword that is not enforced makes the "
            f"schema read as a guarantee it does not give. Express the rule with "
            f"a supported keyword, or move it to the semantic layer."
        )

    if "$ref" in schema:
        try:
            _resolve(schema["$ref"], root)
        except SchemaError as e:
            problems.append(f"{path}: {e}")
        siblings = [k for k in schema
                    if k != "$ref" and not k.startswith("x-") and k not in ANNOTATIONS]
        if siblings:
            problems.append(
                f"{path}: $ref carries validation keyword(s) {sorted(siblings)}. "
                f"Only annotations may sit beside a $ref — a referring site "
                f"refines how a violation is reported, never what is valid."
            )

    if "type" in schema:
        names = schema["type"]
        names = [names] if isinstance(names, str) else names
        for name in names:
            if name not in TYPE_NAMES:
                problems.append(f"{path}: unknown type {name!r}; expected one of {list(TYPE_NAMES)}")
    for keyword in ("pattern",):
        if keyword in schema:
            try:
                re.compile(schema[keyword])
            except re.error as e:
                problems.append(f"{path}: {keyword} is not a valid regex: {e}")
    if "required" in schema and not isinstance(schema["required"], list):
        problems.append(f"{path}: required must be an array")

    for keyword in ("properties", "patternProperties", "$defs"):
        for key, sub in (schema.get(keyword) or {}).items():
            problems += check_schema(sub, root, f"{path}/{keyword}/{key}")
    for keyword in ("additionalProperties", "propertyNames", "items"):
        if keyword in schema:
            problems += check_schema(schema[keyword], root, f"{path}/{keyword}")
    for index, sub in enumerate(schema.get("prefixItems") or []):
        problems += check_schema(sub, root, f"{path}/prefixItems/{index}")
    if "oneOf" in schema:
        if not isinstance(schema["oneOf"], list) or len(schema["oneOf"]) < 2:
            problems.append(f"{path}: oneOf must be an array of at least two alternatives")
        else:
            for index, sub in enumerate(schema["oneOf"]):
                problems += check_schema(sub, root, f"{path}/oneOf/{index}")

    return problems


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #
def selftest():
    failures = []
    counted = [0]

    def case(name, instance, schema, want_paths, want_keywords=None):
        counted[0] += 1
        errors = validate(instance, schema)
        got = sorted(e["path"] for e in errors)
        if got != sorted(want_paths):
            failures.append(f"{name}: expected paths {sorted(want_paths)}, got {got}")
            return
        if want_keywords is not None:
            got_kw = sorted(e["keyword"] for e in errors)
            if got_kw != sorted(want_keywords):
                failures.append(f"{name}: expected keywords {sorted(want_keywords)}, got {got_kw}")

    obj = {"type": "object", "required": ["a"], "properties": {"a": {"type": "integer"}},
           "additionalProperties": False}
    case("object-ok", {"a": 1}, obj, [])
    case("object-missing", {}, obj, ["a"], ["required"])
    case("object-wrong-type", {"a": "1"}, obj, ["a"], ["type"])
    case("object-extra", {"a": 1, "b": 2}, obj, ["b"], ["additionalProperties"])

    # A `required` violation reports at the CHILD, and carries the child's
    # annotations — the whole reason check_delivery_config can tier per field.
    counted[0] += 1
    annotated = {"type": "object", "required": ["band"],
                 "properties": {"band": {"type": "object", "x-tier": "warning"}}}
    errors = validate({}, annotated)
    if len(errors) != 1 or annotation(errors[0], "x-tier") != "warning":
        failures.append("required violation did not pick up the child's x-tier")

    # Booleans are not numbers, and 1.0 is an integer.
    case("bool-is-not-integer", {"a": True}, obj, ["a"], ["type"])
    case("float-integral-is-integer", {"a": 1.0}, obj, [])
    case("float-fractional-is-not", {"a": 1.5}, obj, ["a"], ["type"])

    # Strings.
    blank = {"type": ["string", "null"], "pattern": r"\S"}
    case("blank-string-fails", "   ", blank, [""], ["pattern"])
    case("empty-string-fails", "", blank, [""], ["pattern"])
    case("null-skips-pattern", None, blank, [])
    case("real-string-passes", "npm test", blank, [])

    # Arrays.
    arr = {"type": "array", "minItems": 1, "items": {"enum": ["epic"]}, "uniqueItems": True}
    case("array-ok", ["epic"], arr, [])
    case("array-empty", [], arr, [""], ["minItems"])
    case("array-bad-item", ["epic", "human"], arr, ["1"], ["enum"])
    case("array-dupes", ["epic", "epic"], arr, [""], ["uniqueItems"])

    # Numbers.
    num = {"type": "integer", "minimum": 1}
    case("number-ok", 3, num, [])
    case("number-below", 0, num, [""], ["minimum"])

    # patternProperties + a permissive additionalProperties.
    counted[0] += 1
    ids = {"type": "object", "additionalProperties": {"type": "string"}}
    if validate({"track:platform": "", "effort:S": "x"}, ids):
        failures.append("label-id map rejected legitimate empty-string values")
    counted[0] += 1
    if len(validate({"agent:queued": {"id": "x"}}, ids)) != 1:
        failures.append("label-id map accepted an object value")

    # $ref, and annotation precedence: the referring site wins.
    counted[0] += 1
    refdoc = {
        "$defs": {"band": {"type": "object", "x-rule": "band", "x-tier": "error"}},
        "type": "object",
        "properties": {"S": {"$ref": "#/$defs/band", "x-tier": "warning"}},
    }
    errors = validate({"S": []}, refdoc)
    if len(errors) != 1:
        failures.append(f"$ref: expected 1 error, got {len(errors)}")
    elif annotation(errors[0], "x-tier") != "warning" or annotation(errors[0], "x-rule") != "band":
        failures.append("$ref annotation precedence: referring site must win, $defs must fall back")

    # oneOf as a tagged union: the branch the author MEANT is the one reported.
    union = {"oneOf": [
        {"type": "object", "required": ["type", "body"], "additionalProperties": False,
         "properties": {"type": {"const": "comment"}, "body": {"type": "string"}}},
        {"type": "object", "required": ["type", "to"], "additionalProperties": False,
         "properties": {"type": {"const": "state"}, "to": {"type": "string"}}},
    ]}
    case("oneOf-first-branch", {"type": "comment", "body": "hi"}, union, [])
    case("oneOf-second-branch", {"type": "state", "to": "review"}, union, [])
    counted[0] += 1
    errors = validate({"type": "comment"}, union)
    if [e["path"] for e in errors] != ["body"] or errors[0]["keyword"] != "required":
        failures.append(
            f"oneOf did not report the discriminated branch's own complaint: "
            f"{[(e['path'], e['keyword']) for e in errors]}"
        )
    # No branch's discriminator matches, so the closest one is arbitrary — but
    # its `const` complaint still names the tag, which is the actionable half.
    case("oneOf-no-branch", {"type": "nope"}, union, ["body", "type"], ["required", "const"])

    # check_schema: the guarantee.
    counted[0] += 1
    problems = check_schema({"type": "string", "format": "uuid"})
    if not problems or "format" not in problems[0]:
        failures.append("check_schema did not reject an unsupported keyword")
    counted[0] += 1
    if check_schema({"type": "object", "properties": {"a": {"type": "integer", "minimum": 0}}}):
        failures.append("check_schema rejected a schema made only of supported keywords")
    counted[0] += 1
    problems = check_schema({"$defs": {"b": {"type": "string"}},
                             "properties": {"a": {"$ref": "#/$defs/b", "minLength": 2}}})
    if not any("$ref carries validation keyword" in p for p in problems):
        failures.append("check_schema allowed a validation keyword beside a $ref")
    counted[0] += 1
    if not check_schema({"properties": {"a": {"$ref": "#/$defs/nope"}}}):
        failures.append("check_schema allowed an unresolvable $ref")
    counted[0] += 1
    if not check_schema({"type": "strng"}):
        failures.append("check_schema allowed an unknown type name")
    counted[0] += 1
    if not check_schema({"pattern": "([unclosed"}):
        failures.append("check_schema allowed an invalid regex")

    # Deep paths are dotted, so a caller can tier and message on them.
    counted[0] += 1
    deep = {"type": "object", "properties": {"budgets": {"type": "object", "properties": {
        "perEffort": {"type": "object", "properties": {
            "S": {"type": "object", "properties": {"maxTurns": {"type": "integer"}}}}}}}}}
    errors = validate({"budgets": {"perEffort": {"S": {"maxTurns": "x"}}}}, deep)
    if len(errors) != 1 or errors[0]["path"] != "budgets.perEffort.S.maxTurns":
        failures.append(f"deep path: got {[e['path'] for e in errors]}")
    elif errors[0]["pointer"] != "/budgets/perEffort/S/maxTurns":
        failures.append(f"deep pointer: got {errors[0]['pointer']}")

    if failures:
        print("FAIL: jsonschema_mini selftest")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(f"OK: jsonschema_mini selftest ({counted[0]} cases)")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    print(__doc__.splitlines()[0])
    sys.exit(0)
