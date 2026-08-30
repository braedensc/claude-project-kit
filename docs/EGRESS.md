# Egress allowlist — what the dispatcher's sessions may reach

**This is about the agentic delivery pipeline only.** It documents the network policy that
constrains a dispatched session's shell commands. If `delivery.json` does not exist at the
repo root, none of this is running and you can skip the file.

> **Not to be confused with the kit's own egress guard.** `.claude/hooks/pre-tool-use.py`
> has a guard by the same name that matches *raw Bash command text* for exfil-shaped
> commands. That one is **advisory** — a respelling gets past it, as
> `docs/SECURITY.md` § *What the pattern guards actually carry* measures. This document
> describes a different, **durable** layer: a proxy at the network boundary that does not
> care how a command was spelled. SECURITY.md's own table names it as the durable
> counterpart to the pattern matcher.

---

## What it actually is

The dispatcher runs a proxy on loopback. Every shell command in a session has its HTTP
proxy variables pointed at it. When a command opens a connection, the proxy compares the
destination hostname against an allowlist and either connects or refuses.

Configured in `~/.cyrus/config.json` (or `<cyrus-home>/config.json`):

```json
"sandbox": {
  "enabled": true,
  "logRequests": true,
  "networkPolicy": {
    "allow": {
      "github.com": [{}],
      "*.supabase.co": [{}]
    }
  }
}
```

### Five mechanics that are easy to get wrong

| | |
| --- | --- |
| **Deny-by-default is triggered by the *presence* of `allow`** | `{"enabled": true}` with no `networkPolicy`, or `"networkPolicy": {}`, is **allow-all**. Silently. |
| **Values are arrays of objects** | `"github.com": [{}]`. A list of strings is invalid and will not parse. |
| **Wildcards work, and do not include the bare domain** | `*.example.com` matches `sub.example.com` but **not** `example.com`. If you need both, list both. |
| **`subnets` rules parse but are not enforced** | Do not rely on them. |
| **The policy is global, not per-repository** | Every managed repo shares one list. Adding a Python project's hosts widens the list a Node project's sessions run under. |

**Verify it took.** The config is never schema-validated — a typo produces silence and an
open policy. The startup banner is the only signal:

```
[EgressProxy] Policy: deny-all with 10 allowed domain(s)     <- correct
[EgressProxy] Policy: allow-all (no domain restrictions)     <- your allow block did not parse
```

---

## What it covers, and what it does not

**Covers:** shell commands. `git`, `curl`, `npm`, `pip`, build scripts, and — importantly —
**dependency install scripts**.

**Does not cover:**

- Claude's own connection to the model
- MCP servers, including the tracker
- built-in file reads and writes

That second point matters more than it looks. **A deny-all policy does not stop information
leaving through a tracker comment**, because that path never reaches the proxy.

### So what is this control actually worth?

Be precise about it, because it is easy to over-trust.

**Against the agent itself: very little.** A session must be able to push to your source
host — that is the job. A source host is a complete exfiltration channel: anything the
session's token can write to, it can write anything into. The list's ceiling is set by its
most permissive necessary entry, and that entry is already maximal.

**Against a compromised dependency: this is the real protection, and it is significant.**
Every `npm install` executes `postinstall` scripts from the whole transitive tree — code
written by strangers. **The daemon's entire environment is visible to those subprocesses**
(there is no environment scrubbing), which on a configured pipeline includes the source-host
token, the tracker's client and webhook secrets, and the model credential. A malicious
package can read all of them from its own environment.

The only thing between that script and an arbitrary server is whether it can open a
connection to an unknown host. That is this list, and nothing else.

A *sophisticated* package would exfiltrate via the allowed source host instead. So this
stops the lazy attack and not the targeted one — a real filter with a real ceiling, not a
wall. The **account separation** remains the load-bearing control.

---

## Finding the hosts your stack needs

You do not have to predict this. For a stack you own it is directly discoverable, usually
in two commands.

**1. Ask the repo what it references:**

```bash
grep -rhoE 'https?://[a-zA-Z0-9._-]+' package.json deno.json scripts .github/workflows \
  2>/dev/null | sed -E 's|https?://||' | sort | uniq -c | sort -rn
```

**2. Ask the tooling what it is.** Package manager implies registry; a container-based test
stack implies a container registry; a hosted backend implies its API domain.

**3. Let a real run tell you the rest.** With `logRequests: true`, a refusal is logged
explicitly:

```
[EgressProxy] ✗ BLOCKED example.invalid — domain not in allow list
```

Grep the daemon log for `BLOCKED` after a failed session and you have the missing entry,
with evidence.

> ⚠️ **A blocked host does not present to the session as a blocked host.** It sees a
> connection failure and will often produce a confident, wrong diagnosis rather than
> "this host was refused". Expect to read the log yourself rather than trust the session's
> explanation.

---

## Turning it off

Legitimate, and for some deployments it is the right call.

**To disable the network policy while keeping everything else**, omit `networkPolicy`:

```json
"sandbox": { "enabled": true, "logRequests": true }
```

That keeps filesystem confinement (a session may only write inside its own worktree) and
`allowUnsandboxedCommands: false`. **You lose only the network filter.**

**Do not set `"enabled": false`** unless you mean to drop the filesystem confinement too.

### When disabling is reasonable

**On a VM or dedicated hardware.** If the dispatcher is the only thing on the machine, and
the machine holds nothing you would mind losing, the network filter is defending an
interior boundary that no longer matters much — the machine boundary is already the
isolation. A compromised dependency there can steal the pipeline's own credentials, which
you can rotate, rather than reaching anything else you own.

**When the friction is causing worse behaviour.** A pipeline whose sessions routinely stall
on network refusals gets debugged by loosening things in a hurry. A deliberate, documented
allow-all beats a list nobody trusts.

### When it is not reasonable

**On a machine you also use.** The daemon runs alongside your own work. A dependency that
can reach any host can ship the pipeline's credentials off the box, and those credentials
have write access to your repositories.

**Whenever you cannot rotate the credentials quickly.** The filter buys time; without it,
a compromise is immediate and silent.

Whichever you choose, **write down that you chose it.** An absent `networkPolicy` and a
forgotten one look identical.

---

## Starter list

`templates/egress-allowlist.json` carries a **core** list: the pipeline's own dependencies
plus the universal source and package hosts. It is deliberately not the union of every
ecosystem — an allowlist that allows everything is a comment, not a control.

Add your ecosystem from the menu below.

### Node / JavaScript

```
"registry.npmjs.org": [{}]          "registry.yarnpkg.com": [{}]
"cdn.playwright.dev": [{}]          "playwright.azureedge.net": [{}]
"deno.land": [{}]                   "jsr.io": [{}]
"esm.sh": [{}]                      "cdn.jsdelivr.net": [{}]
"unpkg.com": [{}]                   "nodejs.org": [{}]
```

### Python

```
"pypi.org": [{}]                    "files.pythonhosted.org": [{}]
"*.pythonhosted.org": [{}]
```

### Rust

```
"crates.io": [{}]                   "static.crates.io": [{}]
"index.crates.io": [{}]             "static.rust-lang.org": [{}]
```

### Go

```
"proxy.golang.org": [{}]            "sum.golang.org": [{}]
"go.dev": [{}]                      "storage.googleapis.com": [{}]
```

### Ruby / JVM / PHP

```
"rubygems.org": [{}]                "index.rubygems.org": [{}]
"repo1.maven.org": [{}]             "plugins.gradle.org": [{}]
"repo.packagist.org": [{}]          "packagist.org": [{}]
```

### Containers

Only if the daemon can actually run a container runtime — on macOS under a role account it
usually cannot, since the desktop runtimes need a GUI session.

```
"registry-1.docker.io": [{}]        "auth.docker.io": [{}]
"index.docker.io": [{}]             "production.cloudflare.docker.com": [{}]
"ghcr.io": [{}]                     "pkg-containers.githubusercontent.com": [{}]
```

### Hosted services

Project-specific. Add only what you use. Note the bare-domain rule — a wildcard does not
cover the apex.

```
"*.supabase.co": [{}]               "supabase.com": [{}]
"api.supabase.com": [{}]            "*.vercel.app": [{}]
"api.vercel.com": [{}]              "*.netlify.app": [{}]
"api.clerk.com": [{}]               "api.stripe.com": [{}]
```

---

## Keeping it honest

- **Add hosts with a recorded reason**, in the PR that needs them. A list nobody can explain
  is a list nobody will trim.
- **Do not add speculatively.** Every unused entry is surface with no benefit, and the
  discipline erodes one convenient exception at a time.
- **Re-read the banner after every change.** A typo does not fail loudly; it opens the
  policy.
