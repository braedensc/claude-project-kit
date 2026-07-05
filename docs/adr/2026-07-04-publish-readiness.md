# Publish readiness: MIT, no shipped PATH, fail-closed interpreter, deny-list precision

**Date:** 2026-07-04 · **Context:** 4-dimension pre-publication audit (consistency,
portability, stranger-test, genericity)

## Decision

Prepare the kit for public promotion:

1. **MIT LICENSE** (+ `license`/`engines` in package.json). A template's whole point is
   copying files into other projects; unlicensed = all-rights-reserved = unusable.
2. **Stop shipping a `PATH` override in `settings.json`** — the committed
   `env.PATH` (macOS-Homebrew-flavored, NODE_BIN_PATH placeholder) *replaced* the
   user's PATH, hiding `gh`/`node` on non-Mac layouts and silently failing guards open.
   Machine PATH now lives only in gitignored `settings.local.json`; the placeholder is
   retired (22 tokens remain).
3. **Fail closed on a missing interpreter**: the PreToolUse command is wrapped in
   `command -v python3 … || exit 2`. A missing hook *script* already failed closed
   (python exit 2), but a missing *python3* exited 127 = non-blocking = every guard
   silently gone while bypassPermissions stayed on — found by the stranger-test audit.
4. **Enumerate the native deny list instead of `Read(.env.*)`** — the wildcard denied
   `.env.example`, the one env file that's *meant* to be read/edited (verified live:
   it blocked bootstrap's own step 5). Deny rules can't express exceptions, so the
   list names real env files; exotic variants stay covered by the hook layer.
5. **Stranger-proof the docs**: prerequisites table + Windows-unsupported statement +
   first-open expectations (trust dialog, bypass warning, `acceptEdits` opt-out) in
   README; stack-specificity map ("one repo, don't fork variants"); project-type
   question + non-Node layer-2 swap note in bootstrap; `docs/examples/protection.json`
   shipped (was referenced but didn't exist); personal-workflow references
   (secure-bootstrap/iCloud/memory files) removed; provenance-citation convention
   glossed; CONTRIBUTING.md; main-is-latest versioning; repo topics.

## Why

- The audit's verdict on genericity was **one repo, no variant forks** — the coupled
  surface is small and already fenced; forks would fork the battery. The map table
  makes that explicit instead of implicit.
- Findings 2–4 share a theme: **defaults that were correct on the author's machine
  and wrong on a stranger's**. A public template's committed defaults must be
  machine-neutral; machine-specific config belongs in gitignored local files.
- Deliberately NOT added (over-engineering traps the audit named): stack-variant
  forks, a generator CLI, placeholder-tokenizing the worked examples, rewriting hooks
  in Node, semver/CHANGELOG automation, CODE_OF_CONDUCT/issue templates, native
  Windows support.

## Verified

- Battery 92/92 after the settings change (no guard logic changed); placeholder
  integrity 22/22 post-retirement; all new JSON parses; `.env.example` readable again
  under the enumerated deny list while `.env`/`.env.local`/`.env.production` stay
  denied. Settings applied via the self-protection terminal flow.
