/**
 * S1 取证：写作页底部动作条在 1600 / 1100 / 820px × 两个入口下的实拍与实测。
 *
 * 用法：node shot_s1.mjs <输出目录>
 * 输出：<目录>/*.png + <目录>/measure.json
 *
 * 断言（每张图都测，写进 measure.json）：
 *   - 提示文字的行盒高度（用于判断是否被压成"一列单字"）
 *   - 提示文字与每个按钮的包围盒（用于判断是否重叠 / 越界）
 *   - 按钮是否全部可见且可点
 */

import { spawn } from "node:child_process"
import { mkdirSync, writeFileSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import path from "node:path"

const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
const BASE = "http://localhost:5199"
const PORT = 9341
const OUT = path.resolve(process.argv[2] ?? "s1_shots")

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

/**
 * 定位底部动作条：包含「字」状态提示、且含有生成/保存按钮的那一行。
 * 用提示 span 反查它的 flex 容器，最稳。
 */
const PROBE = `
(() => {
  const spans = [...document.querySelectorAll('span')].filter(
    (s) => s.offsetParent !== null &&
           (/字$/.test(s.textContent.trim()) ||
            s.textContent.includes('未输入内容'))
  );
  const hint = spans.find((s) => {
    const p = s.parentElement;
    return p && [...p.children].some((c) => c.tagName === 'BUTTON' || c.querySelector?.('button'));
  });
  if (!hint) return null;
  const bar = hint.parentElement;
  const hr = hint.getBoundingClientRect();
  const buttons = [...bar.querySelectorAll('button')].filter((b) => b.offsetParent !== null);
  const brs = buttons.map((b) => {
    const r = b.getBoundingClientRect();
    return { text: b.textContent.trim(), left: Math.round(r.left), right: Math.round(r.right),
             top: Math.round(r.top), height: Math.round(r.height), width: Math.round(r.width) };
  });
  const barR = bar.getBoundingClientRect();
  // 行盒高度：12px 字 + leading-snug ≈ 17px/行；被压成"一列单字"时会到几百 px
  const cs = getComputedStyle(hint);
  const overlap = brs.some((b) => !(b.right <= hr.left || b.left >= hr.right)) &&
                  brs.some((b) => !(b.top + b.height <= hr.top || b.top >= hr.top + hr.height));
  return {
    hintText: hint.textContent.trim(),
    hintWidth: Math.round(hr.width),
    hintHeight: Math.round(hr.height),
    lineHeightPx: cs.lineHeight,
    barWidth: Math.round(barR.width),
    buttons: brs,
    overlapWithHint: overlap,
    anyButtonClipped: brs.some((b) => b.right > window.innerWidth || b.left < 0),
  };
})()`

async function setViewport(w, h) {
  await send("Emulation.setDeviceMetricsOverride", {
    width: w, height: h, deviceScaleFactor: 2, mobile: false,
  })
}

async function shot(name) {
  const res = await send("Page.captureScreenshot", { format: "png" })
  writeFileSync(path.join(OUT, `${name}.png`), Buffer.from(res.data, "base64"))
  console.log("  →", `${name}.png`)
}

const CASES = [
  { entry: "create", label: "我的创作", widths: [1600, 1100, 820] },
  { entry: "benchmark", label: "对标改写", widths: [1600, 1100, 820] },
]

async function main() {
  console.log("启动 headless Edge…")
  const edge = spawn(EDGE, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    `--remote-debugging-port=${PORT}`, `--user-data-dir=${tmpdir()}/cs_s1_${Date.now()}`,
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
      for (const width of c.widths) {
        const name = `${c.label}_${width}`
        console.log(`· ${name}`)
        await setViewport(width, 1040)
        await send("Page.navigate", { url: `${BASE}/write` })
        await sleep(1300)
        await waitFor(`document.body.innerText.includes('内容工作台')`, "应用外壳")
        await evaluate(`window.localStorage.clear(); window.sessionStorage.clear(); true`)
        await send("Page.reload")
        await sleep(1500)
        await waitFor(`document.body.innerText.includes('我的创作')`, "写作页")

        if (c.entry === "benchmark") {
          if (!(await evaluate(clickExact("对标改写")))) {
            throw new Error("点不到「对标改写」")
          }
          await waitFor(`document.body.innerText.includes('素材作者')`, "对标改写面板")
        }
        await sleep(600)

        const m = await evaluate(PROBE)
        if (!m) throw new Error(`${name}：找不到底部动作条`)
        await shot(name)
        out.push({ case: name, width, entry: c.entry, ...m })
        console.log(
          `    提示 "${m.hintText}" 宽${m.hintWidth} 高${m.hintHeight}px ` +
          `(行高${m.lineHeightPx}) 按钮${m.buttons.length}个 ` +
          `重叠=${m.overlapWithHint} 越界=${m.anyButtonClipped}`
        )
      }
    }

    writeFileSync(path.join(OUT, "measure.json"), JSON.stringify(out, null, 2))
    console.log(`\n完成 → ${OUT}`)
  } finally {
    try { ws?.close() } catch { /* ignore */ }
    edge.kill()
    await sleep(500)
    rmSync(`${tmpdir()}/cs_s1_${Date.now()}`, { recursive: true, force: true })
  }
}

main().catch((err) => {
  console.error("\n失败：", err.message)
  process.exitCode = 1
})
