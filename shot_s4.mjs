/**
 * S4（收尾批）取证：本批受影响页面的截图 + 结构探测。
 * 用法：node shot_s4.mjs <输出目录>
 *
 * 探测：h1（数量/字号/位置）、原生 checkbox 计数、横向溢出、
 *       内容库作者 facet 文本（应为显示名而不是 username）。
 */

import { spawn } from "node:child_process"
import { mkdirSync, writeFileSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import path from "node:path"

const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
const BASE = "http://localhost:5199"
const PORT = 9365
const OUT = path.resolve(process.argv[2] ?? "s4_shots")
mkdirSync(OUT, { recursive: true })

let ws
let seq = 0
const pending = new Map()

function send(method, params = {}) {
  const id = ++seq
  ws.send(JSON.stringify({ id, method, params }))
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject })
    setTimeout(() => {
      if (pending.has(id)) {
        pending.delete(id)
        reject(new Error(`CDP 超时: ${method}`))
      }
    }, 40_000)
  })
}

async function evaluate(expression) {
  const res = await send("Runtime.evaluate", {
    expression, returnByValue: true, awaitPromise: true,
  })
  if (res.exceptionDetails) {
    throw new Error(`页面内异常: ${res.exceptionDetails.exception?.description ?? "?"}`)
  }
  return res.result?.value
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function waitFor(expr, label, timeout = 15_000) {
  const deadline = Date.now() + timeout
  for (;;) {
    if (await evaluate(expr)) return true
    if (Date.now() > deadline) throw new Error(`等待超时：${label}`)
    await sleep(150)
  }
}

const PROBE = `
(() => {
  const h1s = [...document.querySelectorAll('h1')];
  const h1 = h1s[0];
  return {
    h1Count: h1s.length,
    h1Text: h1 ? h1.textContent.trim() : null,
    h1FontSize: h1 ? getComputedStyle(h1).fontSize : null,
    h1Top: h1 ? Math.round(h1.getBoundingClientRect().top) : null,
    nativeCheckbox: document.querySelectorAll('input[type="checkbox"]').length,
    roleCheckbox: document.querySelectorAll('[role="checkbox"]').length,
    overflow: document.documentElement.scrollWidth > window.innerWidth + 1,
    bodySample: document.body.innerText.slice(0, 0),
  };
})()`

const LIBRARY_FACET = `
(() => {
  // 「作者」筛选组里的 chip 文本
  const h = [...document.querySelectorAll('p')].find((x) => x.textContent.trim() === '作者');
  if (!h) return null;
  const group = h.parentElement;
  const chips = [...group.querySelectorAll('button')].map((b) => b.textContent.trim());
  return chips;
})()`

async function setViewport(w, h) {
  await send("Emulation.setDeviceMetricsOverride", {
    width: w, height: h, deviceScaleFactor: 2, mobile: false,
  })
}

async function shot(name) {
  let lastErr = null
  for (let i = 0; i < 3; i++) {
    try {
      const res = await send("Page.captureScreenshot", { format: "png" })
      writeFileSync(path.join(OUT, `${name}.png`), Buffer.from(res.data, "base64"))
      console.log("  →", `${name}.png`)
      return
    } catch (err) { lastErr = err; await sleep(600) }
  }
  console.log(`  ⚠ 截图失败：${name} — ${lastErr.message}`)
}

async function goto(p) {
  await send("Page.navigate", { url: `${BASE}${p}` })
  await sleep(1300)
  await waitFor(`document.body.innerText.includes('内容工作台')`, "应用外壳")
  await sleep(700)
}

const CASES = [
  { name: "write_1600", path: "/write", width: 1600 },
  { name: "library_1600", path: "/library", width: 1600, facet: true },
  { name: "mine_1600", path: "/mine", width: 1600 },
  { name: "insights_1600", path: "/insights", width: 1600 },
  { name: "external_1600", path: "/external-analysis", width: 1600 },
  { name: "import_1600", path: "/import", width: 1600 },
  { name: "imports_1600", path: "/imports", width: 1600 },
  { name: "author_detail_1600", path: "/authors/1", width: 1600 },
  { name: "write_1100", path: "/write", width: 1100 },
  { name: "external_1100", path: "/external-analysis", width: 1100 },
  { name: "library_820", path: "/library", width: 820, facet: true },
]

async function main() {
  console.log("启动 headless Edge…")
  const edge = spawn(EDGE, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    `--remote-debugging-port=${PORT}`, `--user-data-dir=${tmpdir()}/cs_s4_${Date.now()}`,
    "--window-size=1600,1040", "about:blank",
  ], { stdio: "ignore" })

  let targets = null
  for (let i = 0; i < 60; i++) {
    try {
      targets = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()
      if (targets.some((t) => t.type === "page")) break
    } catch { /* 等 */ }
    await sleep(250)
  }
  if (!targets) throw new Error("Edge devtools 未就绪")
  const page = targets.find((t) => t.type === "page")

  ws = new WebSocket(page.webSocketDebuggerUrl)
  ws.addEventListener("message", (ev) => {
    const msg = JSON.parse(ev.data)
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id)
      pending.delete(msg.id)
      msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result)
    }
  })
  await new Promise((r) => ws.addEventListener("open", r, { once: true }))
  await send("Page.enable")
  await send("Runtime.enable")

  const out = []
  try {
    for (const c of CASES) {
      console.log(`· ${c.name}`)
      await setViewport(c.width, 1040)
      await goto(c.path)
      await sleep(500)
      let m = await evaluate(PROBE)
      if (c.facet) {
        await sleep(400)
        m.facetAuthors = await evaluate(LIBRARY_FACET)
      }
      await shot(c.name)
      out.push({ case: c.name, route: c.path, width: c.width, ...m })
      console.log(
        `    h1×${m.h1Count} "${m.h1Text ?? "-"}" ${m.h1FontSize ?? "-"} top=${m.h1Top ?? "-"} ` +
        `原生cb=${m.nativeCheckbox} rolecb=${m.roleCheckbox} 溢出=${m.overflow}` +
        (m.facetAuthors ? ` 作者facet=${JSON.stringify(m.facetAuthors)}` : "")
      )
    }
    writeFileSync(path.join(OUT, "measure.json"), JSON.stringify(out, null, 2))
    console.log(`\n完成 → ${OUT}`)
  } finally {
    try { ws?.close() } catch { /* ignore */ }
    edge.kill()
    await sleep(500)
    rmSync(`${tmpdir()}/cs_s4_${Date.now()}`, { recursive: true, force: true })
  }
}

main().catch((err) => {
  console.error("\n失败：", err.message)
  process.exitCode = 1
})
