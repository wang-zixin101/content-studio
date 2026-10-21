/**
 * S3 第三批取证：作者主页页头（1600 / 1100 / 820）。
 * 用法：node shot_s3c.mjs <输出目录>
 *
 * 探测：
 *   - h1 数量（必须为 1）、文本、字号、字重、顶部位置
 *   - 「返回」与「作者管理」按钮的存在与位置
 *   - 四个标签页的存在
 *   - 账号元信息（@username / 最近更新）可读性
 *   - 横向溢出
 */

import { spawn } from "node:child_process"
import { mkdirSync, writeFileSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import path from "node:path"

const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
const BASE = "http://localhost:5199"
const PORT = 9359
const OUT = path.resolve(process.argv[2] ?? "s3c_shots")
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
  const btns = [...document.querySelectorAll('button, a')].filter((b) => b.offsetParent !== null);
  const back = btns.find((b) => b.getAttribute('aria-label') === '返回' ||
    (b.querySelector('svg.lucide-arrow-left') && b.tagName === 'A'));
  const manage = btns.find((b) => b.textContent.trim() === '作者管理');
  const tabs = ['概览', '帖子', '写作画像', '流量画像'].map((t) => ({
    t, present: document.body.innerText.includes(t),
  }));
  const meta = {
    handle: /@\\w+/.test(document.body.innerText),
    updated: document.body.innerText.includes('最近更新'),
  };
  return {
    h1Count: h1s.length,
    h1Text: h1 ? h1.textContent.trim() : null,
    h1FontSize: h1 ? getComputedStyle(h1).fontSize : null,
    h1Weight: h1 ? getComputedStyle(h1).fontWeight : null,
    h1Top: h1 ? Math.round(h1.getBoundingClientRect().top) : null,
    hasBack: Boolean(back),
    hasManage: Boolean(manage),
    manageRight: manage ? Math.round(window.innerWidth - manage.getBoundingClientRect().right) : null,
    tabs,
    meta,
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

async function main() {
  console.log("启动 headless Edge…")
  const edge = spawn(EDGE, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    `--remote-debugging-port=${PORT}`, `--user-data-dir=${tmpdir()}/cs_s3c_${Date.now()}`,
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
    for (const width of [1600, 1100, 820]) {
      const name = `author_detail_${width}`
      console.log(`· ${name}`)
      await setViewport(width, 1040)
      await send("Page.navigate", { url: `${BASE}/authors/1` })
      await sleep(1500)
      await waitFor(`document.body.innerText.includes('规模')`, "作者主页")
      await sleep(500)
      let m = await evaluate(PROBE)
      await shot(name)
      out.push({ case: name, route: "/authors/1", width, ...m })
      console.log(
        `    h1×${m.h1Count} "${m.h1Text}" ${m.h1FontSize}/${m.h1Weight} top=${m.h1Top} ` +
        `返回=${m.hasBack} 作者管理=${m.hasManage} 溢出=${m.overflow}`
      )
    }
    writeFileSync(path.join(OUT, "measure.json"), JSON.stringify(out, null, 2))
    console.log(`\n完成 → ${OUT}`)
  } finally {
    try { ws?.close() } catch { /* ignore */ }
    edge.kill()
    await sleep(500)
    rmSync(`${tmpdir()}/cs_s3c_${Date.now()}`, { recursive: true, force: true })
  }
}

main().catch((err) => {
  console.error("\n失败：", err.message)
  process.exitCode = 1
})
