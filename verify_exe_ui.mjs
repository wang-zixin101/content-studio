/**
 * 最终验收 · EXE 运行实例的 UI 实拍。
 * 用法：node verify_exe_ui.mjs <输出目录>
 *
 * 对 http://127.0.0.1:8765（正在运行的 ContentStudio.exe 内部服务）截图：
 * 主要页面 1600/1100/820 三档 + 写作/内容库/批量导入等关键页。
 */

import { spawn } from "node:child_process"
import { mkdirSync, writeFileSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import path from "node:path"

const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
const BASE = "http://127.0.0.1:8765"
const PORT = 9377
const OUT = path.resolve(process.argv[2] ?? "exe_ui_shots")
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

async function goto(p, mark) {
  await send("Page.navigate", { url: `${BASE}${p}` })
  await sleep(1200)
  await waitFor(`document.body.innerText.includes('内容工作台')`, "应用外壳")
  if (mark) await waitFor(`document.body.innerText.includes(${JSON.stringify(mark)})`, mark, 10_000)
  await sleep(500)
}

const PAGES = [
  { name: "exe_01_我的", path: "/mine", mark: "90 天目标" },
  { name: "exe_02_写作", path: "/write", mark: "我的创作" },
  { name: "exe_03_内容库", path: "/library", mark: "风格学习" },
  { name: "exe_04_作者管理", path: "/authors", mark: "新增作者" },
  { name: "exe_05_风格档案", path: "/style", mark: "我的风格档案" },
  { name: "exe_06_批量导入", path: "/import", mark: "导入来源" },
  { name: "exe_07_导入记录", path: "/imports", mark: "导入记录" },
  { name: "exe_08_外部分析", path: "/external-analysis", mark: "导出分析任务" },
  { name: "exe_09_跨作者洞察", path: "/insights", mark: "跨作者洞察" },
]

async function main() {
  console.log("启动 headless Edge（对 8765 实例截图）…")
  const edge = spawn(EDGE, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    `--remote-debugging-port=${PORT}`, `--user-data-dir=${tmpdir()}/cs_exe_${Date.now()}`,
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

  try {
    // EXE 内部窗口是 1440×900 —— 与用户实际看到的窗口一致
    await setViewport(1440, 900)
    for (const c of PAGES) {
      console.log(`· ${c.name}`)
      await goto(c.path, c.mark)
      await sleep(500)
      await shot(c.name)
    }
    // 1100 / 820 两档（写作与内容库）
    for (const w of [1100, 820]) {
      await setViewport(w, 900)
      console.log(`· exe_10_写作_${w}`)
      await goto("/write", "我的创作")
      await sleep(500)
      await shot(`exe_10_写作_${w}`)
      console.log(`· exe_11_内容库_${w}`)
      await goto("/library", "筛选")
      await sleep(500)
      await shot(`exe_11_内容库_${w}`)
    }
    console.log(`\n完成 → ${OUT}`)
  } finally {
    try { ws?.close() } catch { /* ignore */ }
    edge.kill()
    await sleep(400)
    rmSync(`${tmpdir()}/cs_exe_${Date.now()}`, { recursive: true, force: true })
  }
}

main().catch((err) => {
  console.error("\n失败：", err.message)
  process.exitCode = 1
})
