/**
 * S2 取证：受影响页面的截图 + 全页字号统计（10px / 11px 元素计数）。
 *
 * 用法：node shot_s2.mjs <输出目录>
 *
 * 统计口径：遍历可见元素的计算字号，数出 10px 与 11px 的元素个数。
 * 修复前：受影响页面应能看到 10px 元素；修复后应为 0，且 11px 计数上升。
 * 同时检查横向溢出（scrollWidth > innerWidth）与关键单元格的文本裁切。
 */

import { spawn } from "node:child_process"
import { mkdirSync, writeFileSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import path from "node:path"

const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
const BASE = "http://localhost:5199"
const PORT = 9345
const OUT = path.resolve(process.argv[2] ?? "s2_shots")
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

const clickExact = (text) => `
(() => {
  const hit = [...document.querySelectorAll('button')].reverse().find(
    (el) => el.textContent.trim() === ${JSON.stringify(text)} && el.offsetParent !== null
  );
  if (!hit) return false; hit.click(); return true;
})()`

const PROBE = `
(() => {
  const els = [...document.querySelectorAll('*')].filter(
    (e) => e.offsetParent !== null && e.childNodes.length &&
           [...e.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())
  );
  let n10 = 0, n11 = 0;
  for (const e of els) {
    const fs = getComputedStyle(e).fontSize;
    if (fs === '10px') n10 += 1;
    else if (fs === '11px') n11 += 1;
  }
  // 关键小单元格：是否有文本被裁切（scrollWidth > clientWidth）
  const clipped = [...document.querySelectorAll('span')]
    .filter((s) => s.offsetParent !== null && /text-right|tabular-nums/.test(s.className))
    .filter((s) => s.scrollWidth > s.clientWidth + 1)
    .map((s) => s.textContent.trim().slice(0, 20));
  return {
    font10: n10,
    font11: n11,
    hOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
    clippedCells: clipped.slice(0, 8),
  };
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
  { name: "insights_1600", path: "/insights", width: 1600 },
  { name: "insights_1100", path: "/insights", width: 1100 },
  { name: "insights_820", path: "/insights", width: 820 },
  {
    name: "author_detail_流量画像_1600",
    path: "/authors/1",
    width: 1600,
    prepare: async () => {
      await waitFor(`document.body.innerText.includes('流量画像')`, "作者主页")
      await evaluate(clickExact("流量画像"))
      await sleep(1500)
    },
  },
  { name: "imports_1600", path: "/imports", width: 1600 },
  { name: "import_1600", path: "/import", width: 1600 },
  { name: "mine_1600_侧栏", path: "/mine", width: 1600 },
]

async function main() {
  console.log("启动 headless Edge…")
  const edge = spawn(EDGE, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    `--remote-debugging-port=${PORT}`, `--user-data-dir=${tmpdir()}/cs_s2_${Date.now()}`,
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
      if (c.prepare) await c.prepare()
      await sleep(500)
      const m = await evaluate(PROBE)
      await shot(c.name)
      out.push({ case: c.name, route: c.path, width: c.width, ...m })
      console.log(
        `    10px=${m.font10}  11px=${m.font11}  横向溢出=${m.hOverflow} ` +
        `裁切单元格=${m.clippedCells.length}`
      )
    }
    writeFileSync(path.join(OUT, "measure.json"), JSON.stringify(out, null, 2))
    console.log(`\n完成 → ${OUT}`)
  } finally {
    try { ws?.close() } catch { /* ignore */ }
    edge.kill()
    await sleep(500)
    rmSync(`${tmpdir()}/cs_s2_${Date.now()}`, { recursive: true, force: true })
  }
}

main().catch((err) => {
  console.error("\n失败：", err.message)
  process.exitCode = 1
})
