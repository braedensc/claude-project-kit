"""The pipeline's own credential shapes: one list for the two layers that judge them (KIT-683).

The PreToolUse hook refuses to write one of these into a file, and the secret scan fails a
commit or pull request that holds one. Each layer keeps its own rules. The hook is a
self-protected, standalone script that reads no file a session can edit, so it cannot read
this one; the scan's rules are secretlint's. What keeps the two from drifting is this list,
which both batteries read:

  - `.claude/hooks/test_hooks.py` asserts the hook refuses each sample in a Write and an
    Edit, naming its label, and lets each near miss through;
  - `scripts/check_secret_scan.py --selftest` asserts the real scanner finds each sample by
    the rule named here, and finds nothing in any near miss.

A credential the pipeline starts to hold goes here first; both batteries then fail until both
layers know it. Every value is built at run time from pieces: a literal key in this file
would be refused by the hook and failed by the scan, which is the point.
"""


def _cat(*parts):
    return "".join(parts)


# (what it is, the label the hook's block names, the secretlint rule that finds it,
#  a value of the real shape, a near miss that is no credential)
SHAPES = (
    ("Linear API key", "Linear API key", "secretlint-rule-linear",
     _cat("lin", "_api_", "A1b2C3d4E5f6G7h8I9j0" * 2),                 # 40 letters and digits
     _cat("lin", "_api_", "keyname1")),                                  # a name, not a key
    ("Slack bot token", "Slack token", "secretlint-rule-slack",
     _cat("xox", "b-", "1234567890123", "-", "1234567890123", "-", "AbCdEfGhIjKlMnOpQrStUvWx"),
     _cat("xox", "b-", "placeholder")),
    ("Slack user token", "Slack token", "secretlint-rule-slack",
     _cat("xox", "p-", "1234567890123-" * 3, "0123456789abcdef" * 2),
     _cat("xox", "p-", "example")),
    ("Slack app-level token", "Slack token", "secretlint-rule-slack",
     _cat("xa", "pp-", "1-", "A0123456789", "-", "1234567890123", "-", "0123456789abcdef" * 4),
     _cat("xa", "pp-", "SECRETAPPTOKEN")),
    ("GitHub fine-grained token", "GitHub fine-grained token", "secretlint-rule-github",
     _cat("github", "_pat_", "A1b2C3d4E5f6G7h8I9j0K1", "_", "a1B2" * 14, "c3D"),  # 22 + 1 + 59
     _cat("github", "_pat_", "A1b2" * 20)),                             # 80: a token is 82
)
