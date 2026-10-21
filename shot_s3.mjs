/**
 * S3 取证：作者 / 风格档案 两页页头 + 参照页（我的 / 跨作者洞察 / 外部分析）。
 * 用法：node shot_s3.mjs <输出目录>
 *
 * 每张图同时实测：h1 字号、h1 位置（top）、首个统计块/徽标行相对 h1 的位置。
 * 用于验证「统计信息在标题下方」与「标题风格与我的页一致」。
 */

import { spawn } from "node:child_process"
import { mkdirSync, writeFileSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import path from "node:path"

const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
const BASE = "http://localhost:5199"
const PORT = 9349
const OUT = path.resolve(process.argv[2] ?? "s3_shots")
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
  const h1 = document.querySelector('h1');
  if (!h1) return { h1: null };
  const r = h1.getBoundingClientRect();
  const cs = getComputedStyle(h1);
  // h1 之后、同层级里第一个「非页头」的块（统计条 / 徽标行 / 卡片）
  let after = null;
  let el = h1.parentElement;
  const stop = 12;
  let hops = 0;
  while (el && hops++ < stop && !after) {
    const sibs = [...el.children];
    const idx = sibs.indexOf(h1.closest('div') ?? h1);
    for (const s of sibs.slice(idx + 1)) {
      if (s.getBoundingClientRect().height > 8) { after = s; break; }
    }
    el = el.parentElement;
  }
  const a = after ? after.getBoundingClientRect() : null;
  return {
    h1Text: h1.textContent.trim(),
    h1FontSize: cs.fontSize,
    h1Weight: cs.fontWeight,
    h1Top: Math.round(r.top),
    firstBlockTop: a ? Math.round(a.top) : null,
    firstBlockBelowTitle: a && a.top >= r.bottom - 2,
    overflow: document.documentElement.scrollWidth > window.innerWidth + 1,
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
  { name: "authors_1600", path: "/authors", width: 1600 },
  { name: "authors_1100", path: "/authors", width: 1100 },
  { name: "authors_820", path: "/authors", width: 820 },
  { name: "style_1600", path: "/style", width: 1600 },
  { name: "style_1100", path: "/style", width: 1100 },
  { name: "style_820", path: "/style", width: 820 },
  // 参照页：确认公共页头没被影响
  { name: "ref_mine_1600", path: "/mine", width: 1600 },
  { name: "ref_insights_1600", path: "/insights", width: 1600 },
  { name: "ref_external_1600", path: "/external-analysis", width: 1600 },
]

async function main() {
  console.log("启动 headless Edge…")
  const edge = spawn(EDGE, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    `--remote-debugging-port=${PORT}`, `--user-data-dir=${tmpdir()}/cs_s3_${Date.now()}`,
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
      if (c.path === "/authors/1") {
        await evaluate(clickExact("流量画像"))
        await sleep(1200)
      }
      await sleep(400)
      let m = await evaluate(PROBE)
      if (!m || !m.h1) { await sleep(800); m = await evaluate(PROBE) }
      await shot(c.name)
      out.push({ case: c.name, route: c.path, width: c.width, ...m })
      if (m.h1Text) {
        console.log(
          `    h1 "${m.h1Text}" ${m.h1FontSize}/${m.h1Weight} top=${m.h1Top} ` +
          `次块在标题下方=${m.firstBlockBelowTitle} 溢出=${m.overflow}`
        )
      } else {
        console.log("    （无 h1）")
      }
    }
    writeFileSync(path.join(OUT, "measure.json"), JSON.stringify(out, null, 2))
    console.log(`\n完成 → ${OUT}`)
  } finally {
    try { ws?.close() } catch { /* ignore */ }
    edge.kill()
    await sleep(500)
    rmSync(`${tmpdir()}/cs_s3_${Date.now()}`, { recursive: true, force: true })
  }
}

main().catch((err) => {
  console.error("\n失败：", err.message)
  process.exitCode = 1
})
