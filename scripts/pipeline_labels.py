#!/usr/bin/env python3
"""Resolve a ticket's labels to canonical keys — and say what did NOT resolve.

ONE definition, imported by every READ path: `scripts/pipeline_dispatch_local.py`
(tier 0) and the enqueue + selection loops in
`templates/workflows/pipeline-dispatch.yml`. Same doctrine as contract §3's "one
description, one parser" — a private second copy of this logic is how the two
halves of the pipeline start disagreeing about what a ticket says.

WHY IT EXISTS

    Contract §1 mandates matching labels by **ID, never by display name**, and
    that mandate is correct: an ID survives a rename in the Linear UI, where a
    name match would silently desync. The mandate is not what broke.

    What broke is the claim that came with it — "a deleted ID fails loudly at
    the API call". That is true on a WRITE: `issueAddLabel` with a dead label ID
    is rejected by Linear. On a READ it is false. A read resolves the ticket's
    live label IDs against a local dict built from `delivery.json`, and a miss
    used to be *discarded*:

        keys = {id_to_key.get(l["id"]) for l in nodes}
        keys.discard(None)                       # <- the whole bug

    So a config still carrying the IDs of deleted labels did not fail. It
    degraded: `effort:*` fell back to `M`, `provenance:*` to `human`, and
    `agent:needs-human` / `agent:blocked` — the labels a person applies to STOP
    a ticket — evaporated, so the dispatcher cheerfully dispatched work someone
    had deliberately parked. Both validators passed the whole time, because they
    check that an ID is a non-empty string, not that it still points at a label.

HOW IT DETECTS DRIFT

    Resolution stays ID-based. The **name is a diagnostic only** — it never
    resolves anything. The tracker queries already return `labels { nodes { id
    name } }`, so when a label's ID is unmapped we can ask a second question: is
    it *named* after a canonical key? If yes, `delivery.json`'s record of that
    key is wrong, and this says so instead of dropping the label.

SEVERITY

    Keyed on `linear.labels.required` — error for a required key, warning for
    anything else. That is the same required=error / optional=warning dial
    `scripts/check_delivery_config.py::check_labels` already applies to the
    sibling "recorded as ``''``" case, so projects tune one list, not two
    taxonomies. It also has to work this way: `track:*` is open-ended and
    project-named, so it can never appear in `required`, and a blanket-fatal
    rule would wedge the queue over a condition the Definition-of-Ready gate
    already rejects at intake.

    Severity is advice. THE CALLER decides what to do with it, and the two
    callers correctly differ: tier 0 binds one ticket and refuses outright,
    while the Actions dispatcher is mid-queue and must contain the damage to the
    one ticket rather than wedge everybody else's.

KNOWN BLIND SPOT

    A label that was **renamed and then recreated** is invisible here: its ID
    misses, and its name is no longer canonical, so it lands in the "somebody
    else's label" bucket and is ignored — which is the right default for every
    label a project adds for its own reasons. Closing that gap needs a liveness
    check against the tracker API, which belongs in `/setup-board` (it holds the
    API key and already reads the whole label set) and not in a guard on the
    hot path. The name heuristic catches the common case: delete-and-recreate
    with the name preserved, which is what a label rescope actually is.
"""

# Kind → what went wrong. Stable strings; callers and tests match on them.
STALE = "stale"            # recorded id no longer matches the live label
UNRESOLVED = "unresolved"  # recorded as "" — board setup never finished
NO_ID = "no-id"            # the ticket payload itself carries no label id


def resolve_label_keys(nodes, ids, required=()):
    """(keys, drift) for one ticket's labels.

    `nodes`    — the ticket's `labels.nodes`, each `{"id": ..., "name": ...}`.
    `ids`      — `linear.labels.ids`: canonical key → label ID.
    `required` — `linear.labels.required`: the keys whose failure is fatal.

    `keys` is the set of canonical keys the ticket carries, resolved by ID
    exactly as before. `drift` is a list of dicts — `severity` (`"error"` /
    `"warning"`), `kind` (one of the constants above), `key`, `message` — one
    per label that named a canonical key but could not be resolved to it.

    A label that resolves, or that matches no canonical key at all, produces no
    drift: the first is working as designed and the second is the project's own
    label, which is none of the pipeline's business.
    """
    canonical = ids if isinstance(ids, dict) else {}
    fatal = set(required or ())
    id_to_key = {v: k for k, v in canonical.items() if v}

    keys, drift = set(), []
    for node in nodes or ():
        node = node if isinstance(node, dict) else {}
        label_id = node.get("id")
        name = (node.get("name") or "").strip()
        key = id_to_key.get(label_id) if label_id else None
        if key is not None:
            keys.add(key)
            continue
        if name not in canonical:
            # Not one of ours. Never police a label a human added for their own
            # reasons — and see KNOWN BLIND SPOT for what else lands here.
            continue

        if not label_id:
            kind = NO_ID
            message = (
                "`%s` is on the ticket with NO LABEL ID. Labels resolve by id, "
                "never by name (contract §1), so it was dropped. This is the "
                "ticket payload's fault, not delivery.json's — a hand-written "
                "--ticket-file must copy `labels.nodes[].id` from the tracker."
                % name)
        elif canonical[name]:
            kind = STALE
            message = (
                "`%s` is live on the ticket as label id %r, but delivery.json "
                "records %r for it — the recorded id is STALE. That is what a "
                "deleted-and-recreated label looks like (label scope cannot be "
                "converted, so a rescope is a delete). Re-resolve the ids: "
                "/setup-board." % (name, label_id, canonical[name]))
        else:
            kind = UNRESOLVED
            message = (
                "`%s` is live on the ticket as label id %r, but delivery.json "
                "records it as \"\" — board setup NEVER RESOLVED it. A live "
                "label with exactly that name is the strongest evidence setup "
                "is unfinished. Re-resolve the ids: /setup-board." % (name, label_id))

        drift.append({
            "severity": "error" if name in fatal else "warning",
            "kind": kind,
            "key": name,
            "message": message,
        })

    return keys, drift
