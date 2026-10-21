/**
 * 任务 07 阶段 1：全应用逐页取证（只读，不改任何业务源码）。
 *
 * ------------------------------------------------------------------
 * 与上一版的区别（本轮修正的四处取证缺陷）
 * ------------------------------------------------------------------
 * 1. 素材范围不再用「精确按钮文本」点 —— 范围行里还有计数（"王长富 14 条"），
 *    精确匹配必然点不中。改用「包含作者名 + 含『条』」定位。
 * 2. 「随机抽取 / 换一条」按**当前实际按钮**执行，并校验每次抽取真的换了内容；
 *    抽不到目标就继续抽，而不是把上一次的结果当成本次结果截图。
 * 3. 每个准备步骤都带**状态断言**：没进到目标状态就抛错，绝不"截了就算完成"。
 * 4. 三候选拆成两张：`未生成空状态` 与 `已生成候选`。生成走 CDP 伪造响应，
 *    **0 次真实 AI**。
 *
 * 另外每张截图都会记录**实测布局**（视口 / 侧栏 / 内容容器宽度），
 * 写进 `task07_audit/manifest.json` —— 让"大屏留白"这类判断有数字支撑，
 * 而不是靠看图猜。
 *
 * 用法：node audit_task07.mjs
 */

import { spawn } from "node:child_process"
import { mkdirSync, writeFileSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import path from "node:path"

const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
const BASE = "http://localhost:5199"
const PORT = 9339
const OUT = path.resolve("task07_audit")
const PROFILE = path.join(tmpdir(), `cs_audit_${Date.now()}`)
const LONG_POST_MARK = "先给结论：AI 工具选不对"

mkdirSync(OUT, { recursive: true })

let ws
let seq = 0
const pending = new Map()
const paused = []
const results = []

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
    throw new Error(
      `页面内异常: ${res.exceptionDetails.exception?.description ?? "?"}`
    )
  }
  return res.result?.value
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function waitFor(expr, timeout = 15_000) {
  const deadline = Date.now() + timeout
  for (;;) {
    if (await evaluate(expr)) return true
    if (Date.now() > deadline) return false
    await sleep(150)
  }
}

/** 准备步骤的硬断言：没到目标状态就抛错，不产出"名不副实"的截图。 */
async function must(expr, label, timeout = 15_000) {
  if (await waitFor(expr, timeout)) return
  throw new Error(`准备步骤未达成：${label}`)
}

// ---------------------------------------------------------------- 点击辅助

const clickExact = (text, tag = "button") => `
(() => {
  const hit = [...document.querySelectorAll('${tag}')].reverse().find(
    (el) => el.textContent.trim() === ${JSON.stringify(text)} && el.offsetParent !== null
  );
  if (!hit) return false; hit.click(); return true;
})()`

/**
 * 点素材范围行。
 * 范围行的文本形如「王长富 14 条」——**不能**用精确匹配；
 * 同时用「含条数 + 长度上限」把素材卡上的作者名排除掉。
 */
const clickRange = (label) => `
(() => {
  const hit = [...document.querySelectorAll('button')].find(
    (el) => el.offsetParent !== null &&
            el.textContent.includes(${JSON.stringify(label)}) &&
            /\\d+\\s*条/.test(el.textContent) &&
            el.textContent.length < 40
  );
  if (!hit) return false; hit.click(); return true;
})()`

const CARD_TEXT = `
(() => {
  const btn = [...document.querySelectorAll('button')].find(
    (b) => b.textContent.trim() === '换一条'
  );
  if (!btn) return null;
  let el = btn;
  while (el && !String(el.className || '').includes('rounded-lg')) el = el.parentElement;
  return el ? el.innerText : null;
})()`

/**
 * 抽一条素材：**按当前实际按钮**决定点「随机抽取」还是「换一条」。
 * 返回本次点的是哪个；两个都不在时返回 null。
 */
const drawOnce = `
(() => {
  const btns = [...document.querySelectorAll('button')].filter(
    (b) => b.offsetParent !== null && !b.disabled
  );
  const swap = btns.find((b) => b.textContent.trim() === '换一条');
  if (swap) { swap.click(); return '换一条'; }
  const draw = btns.find((b) => b.textContent.trim() === '随机抽取');
  if (draw) { draw.click(); return '随机抽取'; }
  return null;
})()`

const hasButton = (text) => `
Boolean([...document.querySelectorAll('button')].find(
  (b) => b.textContent.trim() === ${JSON.stringify(text)} && b.offsetParent !== null
))`

/**
 * 实测布局。
 *
 * 只量「主区第一个子元素」是不够的：像洞察页外层是满宽 wrapper、
 * 内层才是 max-w-3xl 的真内容列 —— 那样会误报成"满宽无留白"。
 * 这里以 **h1 所在的那个较窄祖先**作为真正的内容列，
 * 并给出左右两侧的实际留白，用来判断"留白是否对称 / 是否与内容类型匹配"。
 */
const LAYOUT = `
(() => {
  const aside = document.querySelector('aside');
  const asideW = aside ? aside.getBoundingClientRect().width : 0;
  const avail = window.innerWidth - asideW;
  const h1 = document.querySelector('h1');
  const t = h1 ? h1.getBoundingClientRect() : null;

  // 取**最宽的那个受限祖先**：h1 的直接父元素通常只是标题块（min-w-0），
  // 一路向上找、保留最宽的一个，才是真正的内容列。
  let col = null;
  if (h1) {
    let el = h1.parentElement;
    while (el && el !== document.body) {
      const w = el.getBoundingClientRect().width;
      if (w > 0 && w <= avail - 40 && (!col || w > col.getBoundingClientRect().width)) {
        col = el;
      }
      el = el.parentElement;
    }
  }
  // 没有 h1 的页面（内容库是「筛选列 + 列表列」的工作台式布局）：
  // 取主区里最宽的受限子元素，并在 layoutMode 上标注出来。
  let mode = "single"
  if (!col) {
    mode = "workbench"
    const main = (aside ? aside.nextElementSibling : null) || document.body;
    const kids = [...(main?.children ?? [])].filter(
      (el) => el.offsetParent !== null && el.getBoundingClientRect().width > 0
    )
    for (const el of kids) {
      const w = el.getBoundingClientRect().width
      if (w > 0 && w <= avail - 40 && (!col || w > col.getBoundingClientRect().width)) col = el
    }
  }
  const c = col ? col.getBoundingClientRect() : null;

  return {
    viewport: window.innerWidth,
    sidebar: Math.round(asideW),
    available: Math.round(avail),
    layoutMode: mode,
    titleTop: t ? Math.round(t.top) : null,
    columnWidth: c ? Math.round(c.width) : null,
    gapLeft: c ? Math.round(c.left - asideW) : null,
    gapRight: c ? Math.round(window.innerWidth - c.right) : null,
  };
})()`

async function setViewport(width, height) {
  await send("Emulation.setDeviceMetricsOverride", {
    width, height, deviceScaleFactor: 2, mobile: false,
  })
}

async function shot(name) {
  const res = await send("Page.captureScreenshot", { format: "png" })
  writeFileSync(path.join(OUT, `${name}.png`), Buffer.from(res.data, "base64"))
  console.log("  →", `${name}.png`)
}

async function goto(pathname) {
  await send("Page.navigate", { url: `${BASE}${pathname}` })
  await sleep(1300)
  await must(`document.body.innerText.includes('内容工作台')`, "应用外壳", 12_000)
  await sleep(700)
}

// ---------------------------------------------------------------- 截图清单

/** 三候选的伪造响应（隔离测试用，不调用真实 AI）。 */
const STUB_CANDIDATES = {
  source_content_id: null,
  reference_author_id: null,
  creation_path: "stub",
  reference_profile_used: false,
  candidates: [
    { key: "A", label: "反差切入", text: "别信「功能最强」这一说。\n\n我去年删掉的工具里，有三个是榜单第一。", strategy_tags: ["直接陈述"] },
    { key: "B", label: "结论前置", text: "去年试了 17 个写作工具，最后只留 2 个。\n\n不是因为它们最强。", strategy_tags: ["明确肯定"] },
    { key: "C", label: "场景重构", text: "前几天有人问我哪个工具最强。\n\n我没推荐，只反问了一句：你最卡在哪一步？", strategy_tags: ["观点—理由"] },
  ],
  context: {
    source_analysis: null, writing_count: 0, performance_high: [],
    performance_low: [], cross_high: [], cross_low: [],
    account_dependent: [], skipped: [], budget: null,
  },
  context_empty: true,
  prompt_version: "v3",
  model: "stub-model",
  personalization: null,
}

const SHOTS = [
  {
    name: "01_我的",
    path: "/mine",
    expect: [`document.body.innerText.includes('90 天目标')`, "我的：目标区块已渲染"],
  },
  {
    name: "02_写作_我的创作",
    path: "/write",
    expect: [`document.body.innerText.includes('我的创作')`, "写作：我的创作入口已激活"],
  },
  {
    name: "03_写作_对标改写_指定素材",
    path: "/write",
    prepare: async () => {
      await evaluate(clickExact("对标改写"))
      await must(`document.body.innerText.includes('素材作者')`, "对标改写·指定素材面板")
    },
    expect: [`document.body.innerText.includes('素材作者')`, "指定素材控件可见"],
  },
  {
    name: "04_写作_对标改写_随机素材未抽取",
    path: "/write",
    prepare: async () => {
      await evaluate(clickExact("对标改写"))
      await must(`document.body.innerText.includes('素材作者')`, "进入对标改写")
      await evaluate(clickExact("随机素材"))
      await must(`document.body.innerText.includes('抽取范围')`, "随机素材面板")
      await must(`/当前范围：/.test(document.body.innerText)`, "未抽取态：当前范围行可见")
      await must(hasButton("随机抽取"), "未抽取态：随机抽取按钮在")
    },
    expect: [
      `!${hasButton("换一条")}`,
      "未抽取：没有素材卡（不自动抽取）",
    ],
  },
  {
    name: "05_写作_随机素材_完整正文",
    path: "/write",
    prepare: async () => {
      await evaluate(clickExact("对标改写"))
      await must(`document.body.innerText.includes('素材作者')`, "进入对标改写")
      await evaluate(clickExact("随机素材"))
      await must(`document.body.innerText.includes('抽取范围')`, "随机素材面板")

      // 靠「包含作者名 + 含条数」定位范围行（精确文本必然点不中）
      if (!(await evaluate(clickRange("王长富")))) {
        throw new Error("点不到「王长富」范围行")
      }
      await must(`document.body.innerText.includes('当前范围：王长富')`, "范围切到王长富")

      /*
        连续抽取直到抽到长帖。

        范围池是随机序（不是固定顺序），所以「抽 12 次」不保证覆盖到那一条；
        而且池子抽空后「换一条」会消失、只剩提示与「重来」。
        因此这里做的是：能抽就抽 → 抽空了就点「重来」清空已看记录继续抽。
        每次都要确认内容真的换了，避免把上一次的结果当成本次结果。
      */
      let prev = null
      let hit = false
      let restarts = 0
      for (let i = 1; i <= 40; i++) {
        const clicked = await evaluate(drawOnce)
        if (!clicked) {
          // 池子抽空 → 点「重来」清空已看记录，继续
          if (!(await evaluate(clickExact("重来")))) {
            throw new Error(`第 ${i} 次抽取既无可用按钮、也点不到「重来」`)
          }
          restarts += 1
          if (restarts > 4) throw new Error("反复重来仍抽不到长帖")
          await sleep(400)
          continue
        }
        await must(hasButton("换一条"), `第 ${i} 次抽取后素材卡出现`)
        const card = await evaluate(CARD_TEXT)
        if (!card) throw new Error("读不到素材卡内容")
        if (card.includes(LONG_POST_MARK)) {
          hit = true
          console.log(`    第 ${i} 次抽到长帖（重来 ${restarts} 次）`)
          break
        }
        if (card === prev) throw new Error(`第 ${i} 次抽取内容没有变化（连续抽取失效）`)
        prev = card
      }
      if (!hit) throw new Error("40 次抽取都没抽到长帖")

      const marks = await evaluate(`(() => {
        const t = document.body.innerText;
        return {
          head: t.includes('先给结论：AI 工具选不对'),
          middle: t.includes('中间这一段想多说一句'),
          tail: t.includes('你会发现要选的工具其实已经浮出来了'),
        };
      })()`)
      if (!marks.head || !marks.middle || !marks.tail) {
        throw new Error(`长帖有截断：${JSON.stringify(marks)}`)
      }
    },
    expect: [`document.body.innerText.includes('中间这一段想多说一句')`, "长帖完整正文可见"],
  },
  {
    name: "06a_写作_三候选_未生成空状态",
    path: "/write",
    prepare: async () => {
      await evaluate(clickExact("对标改写"))
      await must(`document.body.innerText.includes('素材作者')`, "进入对标改写")
      await must(
        `document.body.innerText.includes('生成后会在这里比较')`,
        "三候选空状态文案"
      )
    },
    expect: [
      `!['A · 反差切入','B · 结论前置','C · 场景重构'].some((t) => document.body.innerText.includes(t))`,
      "未生成：没有候选卡",
    ],
  },
  {
    name: "06b_写作_三候选_已生成",
    path: "/write",
    prepare: async () => {
      await evaluate(clickExact("对标改写"))
      await must(`document.body.innerText.includes('素材作者')`, "进入对标改写")
      const focused = await evaluate(`
        (() => {
          const ta = [...document.querySelectorAll('textarea')]
            .filter((t) => t.offsetParent !== null && !t.disabled)[0];
          if (!ta) return false;
          ta.focus();
          return true;
        })()`)
      if (!focused) throw new Error("找不到素材输入框")
      await send("Input.insertText", { text: "（取证用素材）一条对标原帖正文。" })
      await sleep(300)
      await evaluate(clickExact("生成 3 个候选"))
      // 候选走 CDP 伪造响应 → 0 次真实 AI
      await must(
        `document.body.innerText.includes('A · 反差切入')`,
        "三候选已生成（伪造响应）",
        20_000
      )
    },
    expect: [
      `['反差切入','结论前置','场景重构'].every((t) => document.body.innerText.includes(t))`,
      "已生成：三张候选卡齐全",
    ],
  },
  {
    name: "07_内容库",
    path: "/library",
    expect: [`document.body.innerText.includes('共 ')`, "内容库：列表已加载"],
  },
  {
    name: "08_内容库_详情面板",
    path: "/library",
    prepare: async () => {
      await must(`document.body.innerText.includes('共 ')`, "内容列表已加载")
      const opened = await evaluate(`
        (() => {
          const nav = document.querySelector('aside');
          const btn = [...document.querySelectorAll('button')].find(
            (b) => b.offsetParent !== null && !nav?.contains(b) &&
                   (b.innerText || '').trim().length > 20
          );
          if (!btn) return false; btn.click(); return true;
        })()`)
      if (!opened) throw new Error("点不到内容行")
      await must(`document.body.innerText.includes('重新分析')`, "详情抽屉已打开")
    },
    expect: [`document.body.innerText.includes('内容分析')`, "详情面板：分析区块可见"],
  },
  {
    name: "09_作者",
    path: "/authors",
    expect: [`document.body.innerText.includes('王长富')`, "作者列表已加载"],
  },
  {
    name: "10_作者主页",
    path: "/authors/1",
    prepare: async () => {
      await must(`document.body.innerText.includes('规模')`, "作者主页概览已渲染")
    },
    expect: [`document.body.innerText.includes('常写主题')`, "作者主页：主题分布可见"],
  },
  {
    name: "11_跨作者洞察",
    path: "/insights",
    expect: [`document.body.innerText.includes('跨作者洞察')`, "洞察页已渲染"],
  },
  {
    name: "12_风格档案",
    path: "/style",
    prepare: async () => {
      await must(`document.body.innerText.includes('有效样本')`, "风格档案已渲染")
    },
    expect: [`document.body.innerText.includes('我的风格档案')`, "风格档案标题可见"],
  },
  {
    name: "13_批量导入",
    path: "/import",
    prepare: async () => {
      await must(`document.body.innerText.includes('导入来源')`, "导入页已渲染")
    },
    expect: [`document.body.innerText.includes('拖入文件')`, "拖拽区可见"],
  },
  {
    name: "14_外部分析",
    path: "/external-analysis",
    prepare: async () => {
      await must(`document.body.innerText.includes('导出分析任务')`, "外部分析已渲染")
    },
    expect: [`document.body.innerText.includes('导入分析结果')`, "两个面板都在"],
  },
  {
    name: "15_导入记录",
    path: "/imports",
    expect: [`document.body.innerText.includes('导入记录')`, "导入记录页已渲染"],
  },
]

async function main() {
  console.log("启动 headless Edge…")
  const edge = spawn(EDGE, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    `--remote-debugging-port=${PORT}`, `--user-data-dir=${PROFILE}`,
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
      return
    }
    if (msg.method === "Fetch.requestPaused") paused.push(msg.params)
  })
  await new Promise((r) => ws.addEventListener("open", r, { once: true }))
  await send("Page.enable")
  await send("Runtime.enable")

  // 三候选：拦下来伪造响应，0 次真实 AI
  await send("Fetch.enable", {
    patterns: [{ urlPattern: "*/api/rewrite-candidates", requestStage: "Request" }],
  })
  const pump = setInterval(async () => {
    if (!paused.length) return
    const ev = paused.shift()
    try {
      await send("Fetch.fulfillRequest", {
        requestId: ev.requestId,
        responseCode: 200,
        responseHeaders: [{ name: "Content-Type", value: "application/json" }],
        body: Buffer.from(JSON.stringify(STUB_CANDIDATES)).toString("base64"),
      })
    } catch (err) {
      console.error("  候选伪造失败:", err.message)
    }
  }, 30)

  try {
    await setViewport(1600, 1040)
    await goto("/mine")
    await evaluate(`window.localStorage.clear(); window.sessionStorage.clear(); true`)

    console.log("\n【宽窗口 1600px】")
    for (const s of SHOTS) {
      console.log(`· ${s.name}`)
      await goto(s.path)
      if (s.prepare) await s.prepare()
      await sleep(500)

      // 截图前断言：状态不对就不出图
      const checked = []
      const exp = s.expect ?? []
      for (let i = 0; i + 1 < exp.length; i += 2) {
        if (!(await evaluate(exp[i]))) {
          throw new Error(`截图状态不符（${s.name}）：${exp[i + 1]}`)
        }
        checked.push(exp[i + 1])
      }
      const layout = await evaluate(LAYOUT)
      await shot(s.name)
      results.push({
        file: `${s.name}.png`, route: s.path, viewport: 1600, layout, verified: checked,
      })
    }

    console.log("\n【窄窗口 1100px】")
    await setViewport(1100, 1040)
    for (const s of [
      { name: "N1_我的_1100", path: "/mine" },
      { name: "N2_写作_我的创作_1100", path: "/write" },
      { name: "N3_内容库_1100", path: "/library" },
      { name: "N4_作者主页_1100", path: "/authors/1" },
    ]) {
      console.log(`· ${s.name}`)
      await goto(s.path)
      await sleep(600)
      const layout = await evaluate(LAYOUT)
      await shot(s.name)
      results.push({ file: `${s.name}.png`, route: s.path, viewport: 1100, layout, verified: [] })
    }

    console.log("\n【窄窗口 820px】")
    await setViewport(820, 1040)
    await goto("/mine")
    await sleep(600)
    results.push({
      file: "N5_我的_820.png", route: "/mine", viewport: 820,
      layout: await evaluate(LAYOUT), verified: [],
    })
    await shot("N5_我的_820")

    writeFileSync(
      path.join(OUT, "manifest.json"),
      JSON.stringify({ generatedAt: new Date().toISOString(), shots: results }, null, 2)
    )
    console.log(`\n全部截图完成（${results.length} 张）→ ${OUT}`)
    console.log("实测布局已写入 manifest.json")
  } finally {
    clearInterval(pump)
    try { ws?.close() } catch { /* ignore */ }
    edge.kill()
    await sleep(500)
    rmSync(PROFILE, { recursive: true, force: true })
  }
}

main().catch((err) => {
  console.error("\n失败：", err.message)
  process.exitCode = 1
})
