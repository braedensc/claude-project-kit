// The look runner (KIT-323): runs ONE browser check block against the app, in files mode, under
// the kit's seatbelt profile (templates/sandbox/look.sb), started by scripts/pipeline_look.py.
// Every step is one verb from a closed list: ticket text is data and nothing in it is evaluated.
//
// usage: node look_runner.mjs --config delivery.json --checks block.json --app-root DIR
//                             --root DIR --build-dir DIR --out DIR
// exit: 0 every run passed, 1 a run failed, 3 a run could not run (never reported as a pass)
import { createRequire } from 'node:module'
import { readFileSync, existsSync, statSync, mkdirSync, writeFileSync } from 'node:fs'
import { join, extname, normalize, resolve, isAbsolute } from 'node:path'
import { pathToFileURL } from 'node:url'

const arg = (k, d) => { const i = process.argv.indexOf(k); return i > 0 ? process.argv[i + 1] : d }
const appRoot = resolve(arg('--app-root'))
const root = resolve(arg('--root', appRoot))
const out = resolve(arg('--out'))
const only = arg('--only')
const buildDir = arg('--build-dir')
mkdirSync(out, { recursive: true })
const { chromium } = createRequire(join(appRoot, 'package.json'))('playwright')

const acc = JSON.parse(readFileSync(arg('--config'), 'utf8')).acceptance
const block = JSON.parse(readFileSync(arg('--checks'), 'utf8'))
if (block.schema !== 'pipeline-browser-check/1') throw new Error('not a pipeline-browser-check/1 block')

const DEFAULT_VIEWPORTS = { desktop: { width: 1280, height: 800 }, phone: { width: 390, height: 844, mobile: true } }
const viewports = acc.viewports || DEFAULT_VIEWPORTS
const modes = { ...(acc.modes || {}) }
for (const [k, v] of Object.entries(block.modes || {})) {
  if (k in modes) throw new Error(`mode ${k} is declared twice (delivery.json and the block)`)
  modes[k] = v
}
const scenarios = acc.scenarios || ['default']
const at = (p) => (isAbsolute(p) ? p : join(root, p))
const fake = acc.fakeBackend ? await import(pathToFileURL(at(acc.fakeBackend)).href) : null
const serve = acc.serve
const ORIGIN = serve.mode === 'files' ? serve.origin || 'http://127.0.0.1:9' : new URL(serve.url).origin
const DIR = serve.mode === 'files' ? (buildDir ? resolve(buildDir) : at(serve.dir)) + '/' : null
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png', '.woff2': 'font/woff2', '.woff': 'font/woff', '.webmanifest': 'application/manifest+json', '.json': 'application/json' }

function serveFile(route) {
  const url = new URL(route.request().url())
  let p = normalize(join(DIR, decodeURIComponent(url.pathname)))
  const missing = !p.startsWith(DIR) || !existsSync(p) || statSync(p).isDirectory()
  if (missing && serve.fallback) p = join(DIR, serve.fallback)
  else if (missing) return route.fulfill({ status: 404, body: '' })
  return route.fulfill({ status: 200, headers: { 'content-type': TYPES[extname(p)] || 'application/octet-stream', 'cache-control': 'no-store' }, body: readFileSync(p) })
}

function locate(page, t) {
  let base = t.within ? locate(page, t.within) : page
  const o = t.exact ? { exact: true } : {}
  let loc
  if (t.role) loc = base.getByRole(t.role, t.name !== undefined ? { name: t.name, ...o } : {})
  else if (t.text !== undefined) loc = base.getByText(t.text, o)
  else if (t.label !== undefined) loc = base.getByLabel(t.label, o)
  else if (t.placeholder !== undefined) loc = base.getByPlaceholder(t.placeholder, o)
  else if (t.testId !== undefined) loc = base.getByTestId(t.testId)
  else if (t.css !== undefined) loc = base.locator(t.css)
  else throw new Error('target names nothing')
  return t.nth === undefined ? loc : t.nth === -1 ? loc.last() : loc.nth(t.nth)
}

// Computed value of `prop` on the element, and of `expected` resolved in the same place
// (a probe sibling), so `rgb(var(--c-bg))` compares as the color it means.
async function cssPair(loc, prop, expected, part) {
  return loc.evaluate((el, [prop, expected, part]) => {
    const pseudo = part ? '::' + part : null
    const actual = getComputedStyle(el, pseudo).getPropertyValue(prop).trim()
    const probe = document.createElement('div')
    probe.style.setProperty(prop, expected)
    ;(el.parentElement || document.documentElement).appendChild(probe)
    const want = getComputedStyle(probe).getPropertyValue(prop).trim()
    probe.remove()
    return { actual, want: want || expected }
  }, [prop, expected, part || null])
}

async function contrastOf(loc, part) {
  return loc.evaluate((el, part) => {
    const parse = (s) => { const m = s.match(/rgba?\(([^)]+)\)/); if (!m) return null; const v = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return { r: v[0], g: v[1], b: v[2], a: v.length > 3 ? v[3] : 1 } }
    const over = (top, bot) => ({ r: top.r * top.a + bot.r * (1 - top.a), g: top.g * top.a + bot.g * (1 - top.a), b: top.b * top.a + bot.b * (1 - top.a), a: 1 })
    const layers = []
    for (let n = el; n; n = n.parentElement) { const c = parse(getComputedStyle(n).backgroundColor); if (c && c.a > 0) { layers.push(c); if (c.a >= 1) break } }
    let bg = { r: 255, g: 255, b: 255, a: 1 }
    for (const c of layers.reverse()) bg = over(c, bg)
    const fg = over(parse(getComputedStyle(el, part ? '::' + part : null).color), bg)
    const lum = ({ r, g, b }) => { const f = (x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4 }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b) }
    const [a, b] = [lum(fg), lum(bg)].sort((x, y) => y - x)
    return { ratio: Math.round(((a + 0.05) / (b + 0.05)) * 100) / 100, fg: `rgb(${fg.r | 0}, ${fg.g | 0}, ${fg.b | 0})`, bg: `rgb(${bg.r | 0}, ${bg.g | 0}, ${bg.b | 0})` }
  }, part || null)
}

const norm = (s) => String(s).replace(/\s+/g, '')
const results = []
const browser = await chromium.launch()
try {
  for (const check of block.checks) {
    if (only && check.id !== only) continue
    const scenario = check.scenario || scenarios[0]
    const fpSpecs = check.steps.filter((s) => s.do === 'expect' && s.at === 'first-paint').map((s) => ({ css: s.on.css, part: s.part || null, props: Object.keys(s.css || {}) }))
    for (const vpName of check.viewports || Object.keys(viewports)) {
      for (const modeName of check.modes || ['default']) {
        const vp = viewports[vpName]; const mode = modes[modeName] || {}
        const run = { check: check.id, viewport: vpName, mode: modeName, scenario, status: 'passed', steps: [], shots: [], aborted: [], errors: [] }
        results.push(run)
        if (!vp || (modeName !== 'default' && !modes[modeName])) { run.status = 'could-not-run'; run.reason = `unknown viewport or mode ${vpName}/${modeName}`; continue }
        const context = await browser.newContext({
          viewport: { width: vp.width, height: vp.height }, deviceScaleFactor: vp.mobile ? 2 : 1, isMobile: !!vp.mobile, hasTouch: !!vp.mobile,
          colorScheme: mode.colorScheme || 'light', reducedMotion: mode.reducedMotion || 'no-preference',
          locale: mode.locale || 'en-US', timezoneId: mode.timezoneId || 'UTC', serviceWorkers: 'block',
        })
        try {
          // Lowest priority first: Playwright runs the LAST registered matching route first.
          await context.route(() => true, (route) => { run.aborted.push(`${route.request().method()} ${route.request().url()}`); return route.abort() })
          await context.routeWebSocket(/.*/, (ws) => { run.aborted.push(`WS ${ws.url()}`); ws.close() })
          if (serve.mode === 'files') await context.route((url) => url.origin === ORIGIN, serveFile)
          else await context.route((url) => url.origin === ORIGIN, (route) => route.continue())
          // Registered last, so it wins: the module may also answer app-origin API paths,
          // and must route.fallback() whatever it does not own.
          const fakeLog = fake ? await fake.install(context, { scenario, origin: ORIGIN, viewport: vpName, mode: modeName }) : null
          if (mode.localStorage) await context.addInitScript((seed) => { try { if (!sessionStorage.getItem('__modeSeeded')) { sessionStorage.setItem('__modeSeeded', '1'); for (const [k, v] of Object.entries(seed)) localStorage.setItem(k, v) } } catch {} }, mode.localStorage)
          if (fpSpecs.length) await context.addInitScript((specs) => {
            requestAnimationFrame(() => { window.__firstPaint = specs.map((s) => { const el = document.querySelector(s.css); if (!el) return null; const cs = getComputedStyle(el, s.part ? '::' + s.part : null); return { className: el.className, values: Object.fromEntries(s.props.map((p) => [p, cs.getPropertyValue(p).trim()])) } }) })
          }, fpSpecs)
          const page = await context.newPage()
          page.on('pageerror', (e) => run.errors.push('pageerror: ' + e.message))
          page.on('console', (m) => { if (m.type() === 'error') run.errors.push('console: ' + m.text().slice(0, 200)) })
          page.setDefaultTimeout(6000)
          const settle = async () => { await page.waitForLoadState('networkidle').catch(() => {}); await page.waitForTimeout(400) }
          await page.goto(ORIGIN + (check.open || '/')); await settle()
          let fpIndex = 0
          for (const [i, s] of check.steps.entries()) {
            const rec = { i, do: s.do }
            run.steps.push(rec)
            if (s.only && !s.only.includes(vpName)) { rec.skipped = 'not this viewport'; continue }
            try {
              if (s.do === 'click') {
                // A visually hidden radio/checkbox is clicked the way a person clicks it:
                // through its label. Said in the result, never silent.
                const loc = locate(page, s.on)
                await loc.first().waitFor({ state: 'attached' }).catch(() => {})
                const labelled = ['radio', 'checkbox'].includes(s.on.role) && (await loc.count()) === 1 &&
                  (await loc.evaluate((el) => !!(el.labels && el.labels.length)))
                if (labelled) { await loc.evaluate((el) => el.labels[0].click()); rec.via = 'its label, as a person clicks it' }
                else await loc.click()
              }
              else if (s.do === 'hover') await locate(page, s.on).hover()
              else if (s.do === 'fill') await locate(page, s.on).fill(s.text)
              else if (s.do === 'press') await page.keyboard.press(s.key)
              else if (s.do === 'reload') { await page.reload(); await settle() }
              else if (s.do === 'goto') { await page.goto(ORIGIN + s.path); await settle() }
              else if (s.do === 'wait') await (s.for ? locate(page, s.for).waitFor({ state: 'visible' }) : page.waitForTimeout(Math.min(s.ms || 0, 2000)))
              else if (s.do === 'emulate') await page.emulateMedia({ colorScheme: s.colorScheme, reducedMotion: s.reducedMotion })
              else if (s.do === 'addClass' || s.do === 'removeClass') await locate(page, s.on).evaluate((el, [c, add]) => el.classList.toggle(c, add), [s.class, s.do === 'addClass'])
              else if (s.do === 'setStorage') await page.evaluate((kv) => { for (const [k, v] of Object.entries(kv)) localStorage.setItem(k, v) }, s.values)
              else if (s.do === 'screenshot') {
                const file = join(out, `${check.id}--${vpName}--${modeName}--${s.name}.png`)
                if (s.on) await locate(page, s.on).screenshot({ path: file }); else await page.screenshot({ path: file, fullPage: !!s.full })
                run.shots.push(file); rec.file = file
              } else if (s.do === 'expect') {
                const loc = locate(page, s.on)
                const fails = []
                if (s.count !== undefined) { const n = await loc.count(); rec.count = n; if (n !== s.count) fails.push(`count ${n} != ${s.count}`) }
                if (s.visible !== undefined) { const v = await loc.isVisible(); if (v !== s.visible) fails.push(`visible ${v} != ${s.visible}`) }
                if (s.at === 'first-paint') {
                  const fp = await page.evaluate((k) => (window.__firstPaint || [])[k], fpIndex++)
                  rec.firstPaint = fp
                  if (!fp) fails.push('nothing recorded at first paint')
                  else {
                    const fpClasses = String(fp.className).split(/\s+/)
                    if (s.hasClass && !fpClasses.includes(s.hasClass)) fails.push(`no class ${s.hasClass} at first paint`)
                    if (s.lacksClass && fpClasses.includes(s.lacksClass)) fails.push(`class ${s.lacksClass} present at first paint`)
                    for (const [prop, exp] of Object.entries(s.css || {})) { const { want } = await cssPair(loc, prop, exp, s.part); if (norm(fp.values[prop]) !== norm(want)) fails.push(`${prop} at first paint ${fp.values[prop]} != ${want}`) }
                  }
                } else {
                  if (s.hasClass) { const has = await loc.evaluate((el, c) => el.classList.contains(c), s.hasClass); if (!has) fails.push(`no class ${s.hasClass}`) }
                  if (s.lacksClass) { const has = await loc.evaluate((el, c) => el.classList.contains(c), s.lacksClass); if (has) fails.push(`class ${s.lacksClass} present`) }
                  if (s.text !== undefined) { const t = await loc.innerText(); if (!t.includes(s.text)) fails.push(`text lacks ${JSON.stringify(s.text)}`) }
                  for (const [k, v] of Object.entries(s.attr || {})) { const got = await loc.getAttribute(k); if (got !== v) fails.push(`${k}=${got} != ${v}`) }
                  for (const [prop, exp] of Object.entries(s.css || {})) { const { actual, want } = await cssPair(loc, prop, exp, s.part); rec[prop] = actual; if (norm(actual) !== norm(want)) fails.push(`${prop} ${actual} != ${want}`) }
                  if (s.contrast) { const c = await contrastOf(loc, s.part); rec.contrast = c; if (c.ratio < s.contrast.min) fails.push(`contrast ${c.ratio} < ${s.contrast.min} (${c.fg} on ${c.bg})`) }
                }
                if (fails.length) { rec.failed = fails; run.status = run.status === 'could-not-run' ? run.status : 'failed' }
              } else throw new Error(`unknown step ${s.do}`)
            } catch (e) {
              rec.error = String(e.message || e).split('\n')[0]
              run.status = 'could-not-run'
              break
            }
          }
          if (fakeLog && fakeLog.unknown && fakeLog.unknown.length) run.unanswered = [...new Set(fakeLog.unknown)]
        } finally {
          await context.close()
        }
      }
    }
  }
} finally {
  await browser.close()
}
for (const r of results) r.aborted = [...new Set(r.aborted)]
writeFileSync(join(out, 'result.json'), JSON.stringify({ schema: 'pipeline-look-run/1', look: Object.fromEntries(block.checks.map((c) => [c.id, c.look || null])), runs: results }, null, 1))
const tally = results.reduce((a, r) => ((a[r.status] = (a[r.status] || 0) + 1), a), {})
console.log(JSON.stringify(tally))
process.exit(tally['could-not-run'] ? 3 : tally.failed ? 1 : 0)
