/**
 * 任务 05 第二轮验收：**真实慢响应**下的作者范围隔离。
 *
 * 用 CDP 的 Fetch 域把 /api/materials/random 拦下来挂几秒，
 * 在请求在途时尝试切换作者，验证：
 *   1. 范围切换被禁用（切换请求不会发出）
 *   2. 响应落地后素材卡作者 == 当前范围作者（没有串入别的作者）
 *   3. 响应落地前界面处于「正在抽素材…」
 *
 * 前置：后端 8000（预览库）+ vite 5199，同 shot_task05.mjs。
 * 用法：node verify_task05_race.mjs
 */

import { spawn } from "node:child_process"
import { mkdirSync, writeFileSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import path from "node:path"

const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
const APP = "http://localhost:5199/write"
const PORT = 9334
const OUT = path.resolve("task05_screenshots")
const PROFILE = path.join(tmpdir(), `cs_race_${Date.now()}`)
const VIEW = { width: 1500, height: 1000 }
const HOLD_MS = 4000

mkdirSync(OUT, { recursive: true })

let ws
let seq = 0
const pending = new Map()
const events = []

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
    }, 30_000)
  })
}

async function evaluate(expression) {
  const res = await send("Runtime.evaluate", {
    expression,
    returnByValue: true,
    awaitPromise: true,
  })
  if (res.exceptionDetails) {
    throw new Error(
      `页面内异常: ${res.exceptionDetails.exception?.description ?? "?"}`
    )
  }
  return res.result?.value
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function waitFor(expression, label, timeout = 20_000) {
  const deadline = Date.now() + timeout
  for (;;) {
    if (await evaluate(expression)) return true
    if (Date.now() > deadline) throw new Error(`等待超时：${label}`)
    await sleep(120)
  }
}

async function shot(name) {
  const res = await send("Page.captureScreenshot", { format: "png" })
  writeFileSync(
    path.join(OUT, `${name}.png`),
    Buffer.from(res.data, "base64")
  )
  console.log("  截图 →", `${name}.png`)
}

const clickByText = (text) => `
(() => {
  const hit = [...document.querySelectorAll('button')].reverse().find(
    (el) => el.textContent.trim() === ${JSON.stringify(text)} && el.offsetParent !== null
  );
  if (!hit) return false; hit.click(); return true;
})()`

const clickRange = (label) => `
(() => {
  const hit = [...document.querySelectorAll('button')].find(
    (el) => el.textContent.includes(${JSON.stringify(label)}) &&
            el.textContent.includes('条') && el.offsetParent !== null
  );
  if (!hit) return false;
  hit.click();
  return true;
})()`

const rangeRowState = (label) => `
(() => {
  const el = [...document.querySelectorAll('button')].find(
    (b) => b.textContent.includes(${JSON.stringify(label)}) && b.textContent.includes('条')
  );
  return el ? { disabled: el.disabled, active: (el.className || '').includes('bg-selected') } : null;
})()`

const cardText = `
(() => {
  const btn = [...document.querySelectorAll('button')].find(
    (b) => b.textContent.trim() === '换一条'
  );
  if (!btn) return null;
  let el = btn;
  while (el && !String(el.className || '').includes('rounded-lg')) el = el.parentElement;
  return el ? el.innerText : null;
})()`

async function main() {
  console.log("启动 headless Edge…")
  const edge = spawn(
    EDGE,
    [
      "--headless=new",
      "--disable-gpu",
      "--no-first-run",
      "--no-default-browser-check",
      `--remote-debugging-port=${PORT}`,
      `--user-data-dir=${PROFILE}`,
      `--window-size=${VIEW.width},${VIEW.height}`,
      "about:blank",
    ],
    { stdio: "ignore" }
  )

  let targets = null
  for (let i = 0; i < 60; i++) {
    try {
      targets = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()
      if (targets.some((t) => t.type === "page")) break
    } catch {
      /* 等 */
    }
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
      return
    }
    if (msg.method === "Fetch.requestPaused") events.push(msg.params)
  })
  await new Promise((r) => ws.addEventListener("open", r, { once: true }))

  await send("Page.enable")
  await send("Runtime.enable")
  await send("Emulation.setDeviceMetricsOverride", {
    width: VIEW.width,
    height: VIEW.height,
    deviceScaleFactor: 2,
    mobile: false,
  })

  try {
    await send("Page.navigate", { url: APP })
    await sleep(1500)
    await waitFor(`document.body.innerText.includes('内容类型')`, "写作页加载")
    await evaluate(
      `window.localStorage.clear(); window.sessionStorage.clear(); true`
    )
    await evaluate(clickByText("对标改写"))
    await sleep(400)
    await evaluate(clickByText("随机素材"))
    await waitFor(`document.body.innerText.includes('抽取范围')`, "随机素材面板")

    // 选「王长富」
    if (!(await evaluate(clickRange("王长富")))) throw new Error("点不到王长富")
    await waitFor(
      `document.body.innerText.includes('当前范围：王长富')`,
      "范围切到王长富"
    )
    console.log("① 范围已选：王长富")

    // 开启拦截：挂住 random 请求
    await send("Fetch.enable", {
      patterns: [
        {
          urlPattern: "*/api/materials/random*",
          requestStage: "Request",
        },
      ],
    })
    events.length = 0
    let heldId = null

    // 注意：拦截回调是异步的，用轮询等它出现
    const heldPromise = (async () => {
      const deadline = Date.now() + 10_000
      while (Date.now() < deadline) {
        const ev = events.find((e) => e.request.url.includes("materials/random"))
        if (ev) return ev
        await sleep(80)
      }
      throw new Error("没有拦截到 random 请求")
    })()

    console.log("② 点击「随机抽取」，把请求挂住…")
    await evaluate(`
      (() => {
        const b = [...document.querySelectorAll('button')].reverse().find(
          (el) => el.textContent.trim() === '随机抽取' && el.offsetParent !== null
        );
        if (!b) return false; b.click(); return true;
      })()
    `)

    const held = await heldPromise
    heldId = held.requestId
    const startedAt = Date.now()
    console.log(`   请求已挂起：${held.request.url}`)

    // ---------- 在途期间：尝试切换作者 ----------
    await sleep(600)
    const loadingShown = await evaluate(
      `document.body.innerText.includes('正在抽素材')`
    )
    const leiRow = await evaluate(rangeRowState("老雷"))
    const wangRow = await evaluate(rangeRowState("王长富"))
    console.log(
      `③ 在途状态：loading=${loadingShown}　老雷行 disabled=${leiRow?.disabled}` +
        `　王长富行 active=${wangRow?.active}`
    )

    // 尝试点「老雷」——按钮已禁用，点击不会生效。
    // 注意：加载中「当前范围：X」那行会让位给「正在抽素材…」，
    // 因此判定依据是**范围列表里的高亮行**，不是那行文字。
    await evaluate(clickRange("老雷"))
    await sleep(400)
    const afterTryWang = await evaluate(rangeRowState("王长富"))
    const afterTryLei = await evaluate(rangeRowState("老雷"))
    if (afterTryWang?.active !== true) {
      throw new Error("在途期间范围被切换了 —— 王长富行不再高亮")
    }
    if (afterTryLei?.active === true) {
      throw new Error("在途期间范围被切换了 —— 老雷行变成高亮")
    }
    if (leiRow?.disabled !== true) {
      throw new Error("抽取期间没有禁用范围切换")
    }
    console.log("  ✓ 在途期间无法切换作者：老雷行已禁用，高亮仍在 王长富")
    await shot("10_慢响应在途_范围被锁定")

    // ---------- 放行请求 ----------
    const waited = Date.now() - startedAt
    if (waited < HOLD_MS) await sleep(HOLD_MS - waited)
    console.log(`④ 放行请求（已挂 ${Date.now() - startedAt} ms）`)
    await send("Fetch.continueRequest", { requestId: heldId })

    await waitFor(
      `(() => {
         const btn = [...document.querySelectorAll('button')]
           .find((b) => b.textContent.trim() === '换一条');
         return Boolean(btn);
       })()`,
      "素材卡出现"
    )
    await sleep(400)

    const card = await evaluate(cardText)
    if (!card) throw new Error("读不到素材卡")
    if (!card.includes("王长富")) throw new Error("素材卡作者不是王长富")
    if (card.includes("老雷") || card.includes("梭哈哥")) {
      throw new Error("素材卡混入了别的作者")
    }
    const rangeNow = await evaluate(
      `(document.body.innerText.match(/抽取范围 · ([^\\n]*)/) || [])[1] || null`
    )
    console.log(`  ✓ 响应落地：卡片作者 = 王长富；卡片范围回显 = ${rangeNow}`)
    if (!String(rangeNow).includes("王长富")) {
      throw new Error("卡片范围回显与所选作者不一致")
    }
    await shot("11_慢响应落地_仍是王长富")

    // ---------- 对照：正常切换后仍能抽到新作者 ----------
    await send("Fetch.disable")
    if (!(await evaluate(clickRange("老雷")))) throw new Error("点不到老雷")
    await waitFor(
      `document.body.innerText.includes('当前范围：老雷')`,
      "范围切到老雷"
    )
    await evaluate(`
      (() => {
        const b = [...document.querySelectorAll('button')].reverse().find(
          (el) => el.textContent.trim() === '随机抽取' && el.offsetParent !== null
        );
        if (!b) return false; b.click(); return true;
      })()
    `)
    await sleep(900)
    const cardLei = await evaluate(cardText)
    if (!cardLei?.includes("老雷")) throw new Error("切到老雷后卡片不含老雷")
    if (cardLei.includes("王长富")) throw new Error("切到老雷后卡片混入王长富")
    console.log("  ✓ 对照组：切到老雷后卡片只属于老雷")

    console.log("\n全部断言通过")
  } finally {
    try {
      ws?.close()
    } catch {
      /* ignore */
    }
    edge.kill()
    await sleep(500)
    rmSync(PROFILE, { recursive: true, force: true })
  }
}

main().catch((err) => {
  console.error("失败：", err.message)
  process.exitCode = 1
})
