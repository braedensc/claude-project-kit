# Dev container

A minimal, official-feature devcontainer so the kit (or a project made from it) opens
in a reproducible container with Claude Code preinstalled. Verified against
[the Dev Container docs](https://code.claude.com/docs/en/devcontainer), 2026-07-04.

`devcontainer.json` uses the official feature
`ghcr.io/anthropics/devcontainer-features/claude-code:1.0` (always installs the latest
CLI; pin via a Dockerfile + `DISABLE_AUTOUPDATER=1` if you need a fixed version). The
named-volume mount persists `~/.claude` (auth) across rebuilds.

Kept as **strict JSON** on purpose — the kit's CI validates every tracked `.json`, and
`//` comments would break that. Explanation lives here instead.

## Autonomous / sandboxed runs (the hardened option)

The base setup is for *interactive* use. For **unattended runs**
(`--dangerously-skip-permissions`), harden it first — copy Anthropic's reference
[`.devcontainer/` in `anthropics/claude-code`](https://github.com/anthropics/claude-code/tree/main/.devcontainer):
a `Dockerfile` + an `init-firewall.sh` that sets a **default-deny egress firewall**
(allowlisting only npm, the Anthropic API, GitHub's published ranges, etc.), wired via
`runArgs: ["--cap-add=NET_ADMIN","--cap-add=NET_RAW"]` and
`postStartCommand: "sudo /usr/local/bin/init-firewall.sh"` + `waitFor: postStartCommand`.

**Security caveats (from the docs — load-bearing for any unattended mode):**
- **Only run trusted repositories this way.** Even sandboxed, a malicious project run
  with `--dangerously-skip-permissions` can exfiltrate anything the container can reach,
  including the credentials in `~/.claude`. Pair with the firewall; monitor activity.
- **Never mount host secrets** (`~/.ssh`, cloud credential files). Use repo-scoped or
  short-lived tokens (a Codespaces secret, `CLAUDE_CODE_OAUTH_TOKEN`, or workload
  identity) instead.
- `--dangerously-skip-permissions` is refused when Claude Code runs as **root** — keep a
  non-root `remoteUser` (the base image's `vscode` user qualifies).
- To forbid bypass mode entirely, deliver `permissions.disableBypassPermissionsMode:
  "disable"` via **managed settings** (admin console / MDM) — not a repo-committed
  Dockerfile, which anyone with write access could edit out.

Note: the kit's own PreToolUse hooks still run inside the container, so its guards apply
there too — the firewall is an *additional*, OS-level layer for untrusted code.
