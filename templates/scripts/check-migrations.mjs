// check-migrations.mjs — PR-time guard for ORDERED FILE FAMILIES, ported from todoclaw
// (PRs #198/#199). It catches, deterministically and before merge, the two collision modes
// parallel branches produce in any directory of version-ordered files:
//   1. DUPLICATE version — two files sharing the same ordering key. Supabase keys migrations
//      by the 14-digit timestamp (schema_migrations primary key), so the second collides and
//      errors db push/reset.
//   2. OUT-OF-ORDER version — a new file that sorts BEFORE one already on the base branch.
//      `supabase db push` hard-stops on it ("…inserted before the last migration on remote…")
//      and applies NOTHING further — every later migration AND the function deploy silently
//      stall. On 2026-07-09 exactly this wedged todoclaw's prod deploys for hours.
//
// Both are checked against the base branch (default origin/main), judging ONLY the files this
// branch ADDS — pre-existing history is left alone — so the author gets an instant, hard
// failure with a fix hint instead of a green PR that quietly breaks the deploy after merge.
// It pairs with the deploy-side `db push --include-all` in deploy-on-green.yml: tolerate
// out-of-order at deploy time, enforce ordering at PR time.
//
// GENERALIZES beyond SQL migrations: any ordered or generated family with a shared ordering
// key has the same two failure modes under parallel branches — Rails/Flyway-style migrations,
// sequence-numbered docs, generated clients keyed by version. Point the knobs below at the
// family (or copy this script per family) and the same checks apply.
//
// Dependency-free (node + git only). Fails OPEN when the base ref can't be resolved
// (offline / shallow clone) — CI always fetches the base, so that only affects local runs.
//
// Usage:
//   node templates/scripts/check-migrations.mjs   # base = origin/main (override with BASE_REF)
//   BASE_REF=FETCH_HEAD node …                    # CI: after `git fetch origin main --depth=1`
// Exit 0 = clean, 1 = a problem.

import { existsSync, readdirSync } from 'node:fs'
import { execFileSync } from 'node:child_process'

// ── STACK-SPECIFIC (the only knobs) ─────────────────────────────────────────────
// DIR      : where the ordered family lives (env MIGRATIONS_DIR overrides).
// FAMILY   : which files in DIR belong to the family at all (others are ignored).
// NAME_RE  : the required filename shape; capture group 1 is the ordering key,
//            compared as a string — zero-padded fixed width keeps that correct.
const DIR = process.env.MIGRATIONS_DIR || 'supabase/migrations'
const FAMILY = (f) => f.endsWith('.sql')
const NAME_RE = /^(\d{14})_.+\.sql$/
// ────────────────────────────────────────────────────────────────────────────────

const BASE_REF = process.env.BASE_REF || 'origin/main'

function git(args) {
  return execFileSync('git', args, { encoding: 'utf8' }).trim()
}

// Family filenames tracked at a git ref ([] if the dir is absent at that ref).
function filesAtRef(ref) {
  try {
    const out = git(['ls-tree', '-r', '--name-only', ref, '--', DIR])
    return out ? out.split('\n').map((p) => p.slice(DIR.length + 1)) : []
  } catch {
    return null // ref unresolvable
  }
}

function versionOf(file) {
  const m = file.match(NAME_RE)
  return m ? m[1] : null
}

if (!existsSync(DIR)) {
  // Nothing to guard (yet) — lets the CI job be wired before the family exists.
  console.log(`✓ migration guard: ${DIR} does not exist; nothing to check.`)
  process.exit(0)
}

const baseFiles = filesAtRef(BASE_REF)
if (baseFiles === null) {
  // No base to compare against (offline / shallow clone without origin/main). Fail open on
  // the base-relative checks rather than block; CI always fetches the base, so this only
  // affects local runs.
  console.warn(`⚠ migration guard: could not resolve ${BASE_REF}; skipping base-relative checks.`)
}

const headFiles = readdirSync(DIR).filter(FAMILY)
const baseSet = new Set(baseFiles ?? [])
const baseVersions = new Set((baseFiles ?? []).map(versionOf).filter(Boolean))
const sortedBase = [...baseVersions].sort()
const baseMax = sortedBase.length ? sortedBase[sortedBase.length - 1] : null

// Files this branch adds = present now, absent from base (a rename shows up as its new name).
const added = headFiles.filter((f) => !baseSet.has(f))
const errors = []

// 1. Naming — every new family file must carry the ordering key we compare on.
const misnamed = added.filter((f) => !NAME_RE.test(f))
for (const f of misnamed) errors.push(`${f}: must match ${NAME_RE} (e.g. <14-digit-timestamp>_<name>.sql)`)
const valid = added.filter((f) => NAME_RE.test(f))

// 2. Duplicate version vs the base branch (an ordering-key collision — for Supabase, a
//    schema_migrations primary-key collision that errors db push/reset).
for (const f of valid) {
  if (baseVersions.has(versionOf(f))) {
    errors.push(
      `${f}: version ${versionOf(f)} already exists on ${BASE_REF} — a duplicate ordering ` +
        `key. Renumber to a unique, later version.`,
    )
  }
}

// 3. Duplicate version among the newly added files themselves (two parallel commits, same stamp).
const byVersion = new Map()
for (const f of valid) byVersion.set(versionOf(f), [...(byVersion.get(versionOf(f)) ?? []), f])
for (const [v, fs] of byVersion) {
  if (fs.length > 1)
    errors.push(`version ${v} is used by ${fs.length} new files: ${fs.join(', ')}`)
}

// 4. Ordering — a new file must sort AFTER everything already on base, or the deploy-time
//    apply refuses it (and an out-of-order apply can assume a state that doesn't exist yet).
if (baseMax) {
  for (const f of valid) {
    if (versionOf(f) <= baseMax) {
      errors.push(
        `${f}: version ${versionOf(f)} is not newer than the latest on ${BASE_REF} (${baseMax}). ` +
          `Rebase on main and renumber so every new file sorts last.`,
      )
    }
  }
}

if (errors.length) {
  console.error('✖ migration guard failed:\n')
  for (const e of errors) console.error(`  • ${e}`)
  console.error(`\nChecked ${added.length} new file(s) in ${DIR} against ${BASE_REF}.`)
  process.exit(1)
}

console.log(
  `✓ migration guard: ${added.length} new file(s) OK ` +
    `(no duplicate or out-of-order versions vs ${BASE_REF}).`,
)
