/**
 * 任务 05 验收截图：随机素材页面的四个场景。
 *
 * 用 CDP 直接驱动本机 Edge（headless），不装任何依赖 ——
 * Node 22 自带全局 WebSocket，够用。
 *
 * 前置：
 *   1. preview_task05_seed.py 已生成预览库
 *   2. 后端跑在 8000（vite 的 /api 代理指向它）
 *   3. vite dev 跑在 5199
 *
 * 用法：
 *   node shot_task05.mjs
 */

import { spawn } from "node:child_process"
import { mkdirSync, writeFileSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import path from "node:path"

const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
const APP = "http://localhost:5199/write"
const PORT = 9333
const OUT = path.resolve("task05_screenshots")
const PROFILE = path.join(tmpdir(), `cs_shot_${Date.now()}`)
const VIEW = { width: 1500, height: 1000 }

mkdirSync(OUT, { recursive: true })

// ---------------------------------------------------------------- CDP 客户端

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

async function waitFor(expression, label, timeout = 15_000) {
  const deadline = Date.now() + timeout
  for (;;) {
    if (await evaluate(expression)) return true
    if (Date.now() > deadline) throw new Error(`等待超时：${label}`)
    await sleep(150)
  }
}

async function shot(name) {
  const res = await send("Page.captureScreenshot", { format: "png" })
  const file = path.join(OUT, `${name}.png`)
  writeFileSync(file, Buffer.from(res.data, "base64"))
  console.log("  截图 →", path.basename(file))
}

/** 按可见文本点一个按钮（精确匹配，取最后一个 —— 通常是当前视图里的那个）。 */
const clickByText = (text, tag = "button") => `
(() => {
  const nodes = [...document.querySelectorAll('${tag}')];
  const hit = nodes.reverse().find(
    (el) => el.textContent.trim() === ${JSON.stringify(text)} &&
            el.offsetParent !== null
  );
  if (!hit) return false;
  hit.click();
  return true;
})()`

/**
 * 点「抽取范围」里的一行。
 * 行的文本是「○ 王长富 5 条」—— 含状态圆点与条数，因此用**包含 + 「条」**定位，
 * 避免与素材卡上的作者名（同样是「王长富」）混淆。
 */
const clickRange = (label) => `
(() => {
  const hit = [...document.querySelectorAll('button')].find(
    (el) => el.textContent.includes(${JSON.stringify(label)}) &&
            el.textContent.includes('条') &&
            el.offsetParent !== null
  );
  if (!hit) return false;
  hit.click();
  return true;
})()`

/** 点动作按钮：「随机抽取」/「换一条」/「重来…」。 */
const clickDrawButton = `
(() => {
  const b = [...document.querySelectorAll('button')]
    .reverse()
    .find((el) =>
      (el.textContent.trim() === '随机抽取' ||
       el.textContent.trim() === '换一条' ||
       el.textContent.trim().startsWith('重来')) &&
      el.offsetParent !== null
    );
  if (!b) return false;
  b.click();
  return true;
})()`

/** 当前抽取范围（面板里那行「当前范围：X · N 条」）。 */
const currentRange = `
(() => {
  const m = document.body.innerText.match(/当前范围：([^\\n]*)/);
  return m ? m[1].trim() : null;
})()`

/**
 * **只看素材卡内部**的文本。
 * 整页 body 里含范围列表（会列出全部作者），拿它判断「是否混入别的作者」
 * 必然假阳性 —— 必须把断言限定在素材卡上。
 */
const cardText = `
(() => {
  const btn = [...document.querySelectorAll('button')].find(
    (b) => b.textContent.trim() === '换一条'
  );
  if (!btn) return null;
  // 注意：Button variant=outline 自身就带 bg-surface，不能用它当锚点；
  // 卡片根节点的类是 rounded-lg。
  let el = btn;
  while (el && !String(el.className || '').includes('rounded-lg')) {
    el = el.parentElement;
  }
  return el ? el.innerText : null;
})()`

const bodyText = `document.body.innerText`

const scrollReadingArea = (kind) => `
(() => {
  const areas = [...document.querySelectorAll('div')].filter(
    (d) => d.scrollHeight > d.clientHeight + 20 && d.clientHeight > 80
  );
  // ${kind}: 取正文所在的可滚动阅读区（宽度最大、文本最长的那一个）
  const best = areas
    .filter((d) => d.innerText && d.innerText.length > 60)
    .sort((a, b) => b.innerText.length - a.innerText.length)[0];
  if (!best) return null;
  best.scrollTop = best.scrollHeight;
  return { len: best.innerText.length, top: Math.round(best.scrollTop), h: best.scrollHeight };
})()`

// ---------------------------------------------------------------- 主流程

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
    { stdio: "ignore", detached: false }
  )

  // 等 devtools 端口起来
  let targets = null
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/list`, {
        // 本机直连，绕开系统代理
        dispatcher: undefined,
      })
      targets = await r.json()
      if (targets.some((t) => t.type === "page")) break
    } catch {
      /* 还没起来 */
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
    }
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
    console.log("打开写作页…")
    await send("Page.navigate", { url: APP })
    await sleep(1500)
    await waitFor(`document.body.innerText.includes('内容类型')`, "写作页加载")

    // 清掉可能残留的本地态
    await evaluate(`window.localStorage.clear(); window.sessionStorage.clear(); true`)

    // ---------- 切到「对标改写」 ----------
    await evaluate(clickByText("对标改写", "button"))
    await waitFor(`document.body.innerText.includes('抽取范围') === false`, "切换模式")
    await sleep(400)

    // ---------- 切到「随机素材」入口 ----------
    await evaluate(clickByText("随机素材", "button"))
    await waitFor(`document.body.innerText.includes('抽取范围')`, "随机素材面板")
    await sleep(600)

    console.log("场景 A：全部素材范围（未抽取）")
    await shot("01_未抽取_全部素材范围")

    // 断言：未点抽取前不得出现素材卡
    const before = await evaluate(
      `document.body.innerText.includes('使用这条') || document.body.innerText.includes('换一条')`
    )
    if (before) throw new Error("切换范围后不应自动抽取，但已出现素材卡")
    console.log("  ✓ 未点抽取时没有素材卡（不自动抽取）")

    // ---------- 场景 B：切到作者「王长富」 ----------
    console.log("场景 B：切换到作者 王长富")
    if (!(await evaluate(clickRange("王长富")))) {
      throw new Error("点不到「王长富」这一行")
    }
    await sleep(500)
    const rangeNow = await evaluate(currentRange)
    if (!String(rangeNow).includes("王长富")) {
      throw new Error(`切换作者失败，当前范围仍是 ${rangeNow}`)
    }
    console.log(`  ✓ 当前范围 = ${rangeNow}`)
    const stillEmpty = await evaluate(
      `document.body.innerText.includes('使用这条')`
    )
    if (stillEmpty) throw new Error("切换作者后自动抽取了")
    console.log("  ✓ 切换作者后仍未出结果")
    await shot("02_切换作者后未抽取")

    // 抽取，直到抽到长帖（王长富池里 5 条，最多点 6 次必覆盖）
    console.log("抽长帖…")
    let found = false
    for (let i = 0; i < 7 && !found; i++) {
      if (!(await evaluate(clickDrawButton))) {
        throw new Error("找不到抽取按钮")
      }
      await sleep(900)
      const text = await evaluate(bodyText)
      if (text.includes("先给结论：AI 工具选不对")) found = true
      else console.log(`  第 ${i + 1} 次没抽到长帖，继续换一条`)
    }
    if (!found) throw new Error("没能抽到长帖（预览库里应存在）")
    console.log("  ✓ 长帖已抽出")

    const authorOk = (await evaluate(bodyText)).includes("王长富")
    if (!authorOk) throw new Error("结果作者不是王长富")
    console.log("  ✓ 结果只属于所选作者 王长富")

    console.log("场景 C：长帖完整展示")
    await shot("03_长帖_阅读区顶部")

    // 滚到阅读区底部，证明结尾可读
    const scrolled = await evaluate(scrollReadingArea("top"))
    await sleep(400)
    await shot("04_长帖_阅读区底部")
    console.log("  阅读区滚动信息:", JSON.stringify(scrolled))

    // 长帖的三个位置都要能读到（预览库里这条长帖的收尾句）
    const marks = await evaluate(`(() => {
      const t = document.body.innerText;
      return {
        head: t.includes('先给结论：AI 工具选不对'),
        middle: t.includes('中间这一段想多说一句'),
        tail: t.includes('你会发现要选的工具其实已经浮出来了'),
      };
    })()`)
    console.log("  开头/中间/结尾可见:", JSON.stringify(marks))
    if (!marks.head || !marks.middle || !marks.tail) {
      throw new Error("长帖存在截断 —— 不是三个位置都能读到")
    }
    console.log("  ✓ 开头 / 中间 / 结尾全部可读，无截断")

    // ---------- 场景 D：使用这条 ----------
    console.log("场景 D：使用这条 → WRITE 素材")
    await evaluate(clickByText("使用这条", "button"))
    await sleep(700)
    const afterPick = await evaluate(bodyText)
    if (!afterPick.includes("已选素材")) throw new Error("没有进入已选素材状态")
    console.log("  ✓ 已进入「已选素材」状态")
    await shot("05_使用这条_已选素材完整预览")

    // 已选预览区滚到底
    await evaluate(scrollReadingArea("picked"))
    await sleep(400)
    await shot("06_已选素材_预览底部")

    // ---------- 场景 B 续：换到「老雷」只出老雷 ----------
    console.log("场景 B 续：重新抽素材 → 切到 老雷")
    await evaluate(clickByText("重新抽素材", "button"))
    await waitFor(
      `document.body.innerText.includes('抽取范围')`,
      "回到随机素材面板"
    )
    await sleep(300)
    if (!(await evaluate(clickRange("老雷")))) throw new Error("点不到「老雷」")
    await waitFor(
      `document.body.innerText.includes('当前范围：老雷')`,
      "范围切到老雷"
    )
    // 注意：范围行只在「未抽取」态出现，必须先读再抽
    const rangeLei = await evaluate(currentRange)
    if (!(await evaluate(clickDrawButton))) throw new Error("找不到抽取按钮")
    await sleep(900)
    const leiCard = await evaluate(cardText)
    if (!String(rangeLei).includes("老雷")) {
      throw new Error(`切换老雷失败：${rangeLei}`)
    }
    if (!leiCard) throw new Error("读不到素材卡")
    if (!leiCard.includes("老雷")) throw new Error("切到老雷后素材卡不含老雷")
    if (leiCard.includes("王长富")) throw new Error("素材卡里混入了王长富")
    console.log(`  ✓ 当前范围 = ${rangeLei}；素材卡作者 = 老雷，未混入王长富`)
    await shot("07_切到老雷_结果只属于老雷")

    // ---------- 空白作者范围 ----------
    console.log("附加：空白作者范围")
    if (!(await evaluate(clickRange("空白作者")))) {
      throw new Error("点不到「空白作者」")
    }
    await waitFor(
      `document.body.innerText.includes('当前范围：空白作者')`,
      "范围切到空白作者"
    )
    const rangeBlank = await evaluate(currentRange)
    if (!(await evaluate(clickDrawButton))) throw new Error("找不到抽取按钮")
    await sleep(900)
    const blankCard = await evaluate(cardText)
    if (!blankCard) throw new Error("读不到素材卡")
    if (blankCard.includes("王长富") || blankCard.includes("老雷")) {
      throw new Error("空白作者素材卡混入了正式作者")
    }
    console.log(`  ✓ 当前范围 = ${rangeBlank}；素材卡 = 空白作者`)
    await shot("08_空白作者范围")

    // ---------- 抽空当前范围：明确提示，不自动切换 ----------
    console.log("附加：抽空范围后的提示")
    for (let i = 0; i < 4; i++) {
      if (!(await evaluate(clickDrawButton))) break
      await sleep(700)
    }
    const drained = await evaluate(bodyText)
    if (drained.includes("全部抽过一遍") || drained.includes("重来")) {
      console.log("  ✓ 抽空后给出了明确提示与「重来」入口")
      await shot("09_抽空当前范围_明确提示")
    } else {
      console.log("  ⚠ 未触发抽空（空白作者池只有 2 条，可能已覆盖）")
    }

    console.log("\n全部截图完成 →", OUT)
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
