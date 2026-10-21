/**
 * 任务 06 独立验收修复的浏览器回归（问题 1 / 2 / 3）。
 *
 * 覆盖「从内容库打开 /write/:id」的**完整真实流程**：
 *   内容库 → 点一行 → 详情面板 → 「改写这条」→ /write/:id → 预填
 *
 * F1  首次打开：预填成功，且不卡加载
 * F2  切到别的页 → 浏览器后退回同一地址：**不永久转圈**，现场还在
 * F3  再次从内容库进同一条：不卡加载（旧实现在这里会永久转圈）
 * F4  点 × 之后停在同一 URL：不得因 URL 把原帖填回来
 * F5  切换一级入口：同样不得恢复
 * F6  采用候选的迟到响应：请求在途时点 ×，放行后不得把编辑稿写回
 *
 * 采用请求用 CDP 拦截挂住，**0 次真实 AI**。
 * 前置：后端 8000（预览库）+ vite 5199
 * 用法：node verify_task06_fix.mjs
 */

import { spawn } from "node:child_process"
import { mkdirSync, writeFileSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import path from "node:path"

const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
const BASE = "http://localhost:5199"
const PORT = 9337
const OUT = path.resolve("task06_screenshots")
const PROFILE = path.join(tmpdir(), `cs_t06fix_${Date.now()}`)
const VIEW = { width: 1560, height: 1000 }
const HOLD_MS = 3500

mkdirSync(OUT, { recursive: true })

let ws
let seq = 0
const pending = new Map()
const paused = []
let holdAdopt = false
const heldAdopt = []
/** 被拦下的生成请求体（用于断言 creation_path） */
const genRequests = []

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

async function waitFor(expression, label, timeout = 20_000) {
  const deadline = Date.now() + timeout
  for (;;) {
    if (await evaluate(expression)) return true
    if (Date.now() > deadline) throw new Error(`等待超时：${label}`)
    await sleep(120)
  }
}

async function shot(name) {
  // headless 偶发 capture 超时：重试两次，不算产品失败
  let lastErr = null
  for (let i = 0; i < 3; i++) {
    try {
      const res = await send("Page.captureScreenshot", { format: "png" })
      writeFileSync(path.join(OUT, `${name}.png`), Buffer.from(res.data, "base64"))
      console.log("  截图 →", `${name}.png`)
      return
    } catch (err) {
      lastErr = err
      await sleep(600)
    }
  }
  console.log(`  ⚠ 截图失败（跳过，不影响断言）：${name} — ${lastErr.message}`)
}

const ok = (msg) => console.log("  ✓", msg)

const clickText = (text, tag = "button") => `
(() => {
  const hit = [...document.querySelectorAll('${tag}')].reverse().find(
    (el) => el.textContent.trim() === ${JSON.stringify(text)} && el.offsetParent !== null
  );
  if (!hit) return false; hit.click(); return true;
})()`

const clickAria = (label) => `
(() => {
  const b = document.querySelector('button[aria-label=${JSON.stringify(label)}]');
  if (!b) return false; b.click(); return true;
})()`

const bodyHas = (t) => `document.body.innerText.includes(${JSON.stringify(t)})`

/** 聚焦第 n 个可见 textarea 并把光标放到末尾（负数从后往前数）。 */
const focusTextarea = (index = 0) => `
(() => {
  const tas = [...document.querySelectorAll('textarea')]
    .filter((t) => t.offsetParent !== null && !t.disabled);
  const i = ${index} < 0 ? tas.length + ${index} : ${index};
  const ta = tas[i];
  if (!ta) return false;
  ta.focus();
  ta.setSelectionRange(ta.value.length, ta.value.length);
  return true;
})()`

async function typeInto(index, text) {
  if (!(await evaluate(focusTextarea(index)))) {
    throw new Error(`找不到第 ${index} 个可输入 textarea`)
  }
  await send("Input.insertText", { text })
  await sleep(250)
}

const leftTextarea = `
(() => {
  const ta = [...document.querySelectorAll('textarea')]
    .filter((t) => t.offsetParent !== null && !t.disabled)[0];
  return ta ? ta.value : null;
})()`

const allTextareas = `
[...document.querySelectorAll('textarea')]
  .filter((t) => t.offsetParent !== null).map((t) => t.value)`

/** textarea 的值不在 innerText 里，断言"稿子还在"必须直接读 value。 */
const textareaHas = (text) => `
[...document.querySelectorAll('textarea')]
  .some((t) => t.value.includes(${JSON.stringify(text)}))`

/**
 * 页面是否「真渲染出来了」。
 *
 * 不能用文字判断：加载分支只渲染一个转圈图标、**没有任何文案**，
 * 所以拿 innerText 找关键词在卡死时同样为 false，会假通过。
 * 判据必须是「有可见 textarea」+「没有转圈图标」。
 */
const pageState = `
(() => {
  const tas = [...document.querySelectorAll('textarea')]
    .filter((t) => t.offsetParent !== null);
  return {
    textareas: tas.length,
    spinner: Boolean(document.querySelector('.animate-spin')),
    shell: document.body.innerText.includes('我的创作') ||
           document.body.innerText.includes('对标改写'),
  };
})()`

/**
 * 点内容库列表里的第一条内容行。
 *
 * ContentRow 的可点主体是一个 `<button>`（行容器只是 div），
 * 所以按「可见 + 文本足够长」挑第一个按钮 —— 列表行外没有这种按钮。
 */
const openRow = (index) => `
(() => {
  const nav = document.querySelector('aside');
  const btns = [...document.querySelectorAll('button')].filter(
    (b) => b.offsetParent !== null &&
           !nav?.contains(b) &&
           (b.innerText || '').trim().length > 20
  );
  const row = btns[${index}];
  if (!row) return false;
  row.click();
  return true;
})()`

/**
 * 打开内容库里的第 `ordinal` 个**对标**内容行。
 *
 * 「改写这条」只出现在 source=benchmark 的详情面板上 ——
 * 预览库里排在最前面的可能是「我的」内容（详情里没有这个按钮），
 * 所以不能"点第一行"就完事：逐行打开、确认有按钮、否则关掉换下一行。
 */
const OPEN_ROW_AT = (index) => `
(() => {
  const nav = document.querySelector('aside');
  const btns = [...document.querySelectorAll('button')].filter(
    (b) => b.offsetParent !== null && !nav?.contains(b) &&
           (b.innerText || '').trim().length > 20
  );
  const row = btns[${index}];
  if (!row) return false;
  row.click();
  return true;
})()`

const CLOSE_SHEET = `
(() => {
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  const b = [...document.querySelectorAll('button')].find(
    (x) => x.getAttribute('aria-label') === 'Close' || x.textContent.trim() === '关闭'
  );
  if (b) b.click();
  return true;
})()`

const ROW_COUNT = `
(() => {
  const nav = document.querySelector('aside');
  return [...document.querySelectorAll('button')].filter(
    (b) => b.offsetParent !== null && !nav?.contains(b) &&
           (b.innerText || '').trim().length > 20
  ).length;
})()`

/** 依序尝试内容行，直到打开一个带「改写这条」的详情（= 对标内容）。 */
async function openBenchmarkRow(ordinal) {
  await evaluate(clickText("内容库", "a"))
  await waitFor(`location.pathname === '/library'`, "进入内容库")
  await sleep(500)
  const total = await waitFor(ROW_COUNT, "内容行渲染", 12_000)
    ? await evaluate(ROW_COUNT)
    : 0
  if (!total) throw new Error("内容库一行都没有")
  let found = 0
  for (let i = 0; i < Math.min(total, 30); i++) {
    if (!(await evaluate(OPEN_ROW_AT(i)))) continue
    await sleep(700)
    const has = await evaluate(`document.body.innerText.includes('改写这条')`)
    console.log(`    [openBenchmarkRow] 行${i} 改写这条=${has} found=${found}`)
    if (has) {
      if (found === ordinal) {
        await evaluate(clickText("改写这条"))
        if (!(await waitFor(`location.pathname.startsWith('/write/')`, 4000))) {
          // 偶发：按钮在抽屉收起动画里被卸载，点了个寂寞 → 重点一次
          await evaluate(clickText("改写这条"))
          await waitFor(`location.pathname.startsWith('/write/')`, 8000)
        }
        await sleep(1200)
        return
      }
      found += 1
      console.log(`    [openBenchmarkRow] 跳过第 ${found - 1} 个对标（非目标序号）`)
    }
    // 不是对标（或还没轮到）→ 关掉详情，试下一行
    await evaluate(CLOSE_SHEET)
    await sleep(400)
  }
  throw new Error(`内容库里没找到第 ${ordinal} 个对标内容`)
}

/** 从内容库按序号主动打开一条素材（走「改写这条」，带票据）。 */
async function openMaterialFromLibrary(index) {
  await evaluate(clickText("内容库", "a"))
  await waitFor(`location.pathname === '/library'`, "进入内容库")
  // 列表是异步取回来的，必须等行真的渲染出来再点
  await sleep(400)
  await waitFor(openRow(index), `第 ${index} 条内容行出现`)
  await sleep(400)
  await evaluate(clickText("改写这条"))
  await waitFor(`location.pathname.startsWith('/write/')`, "进入 /write/:id")
  await sleep(1200)
}

async function main() {
  console.log("启动 headless Edge…")
  const edge = spawn(EDGE, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    `--remote-debugging-port=${PORT}`, `--user-data-dir=${PROFILE}`,
    `--window-size=${VIEW.width},${VIEW.height}`, "about:blank",
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
  await send("Emulation.setDeviceMetricsOverride", {
    width: VIEW.width, height: VIEW.height, deviceScaleFactor: 2, mobile: false,
  })
  // 只拦 adopt；生成类请求本轮用不到
  await send("Fetch.enable", {
    patterns: [
      { urlPattern: "*/api/rewrites/adopt", requestStage: "Request" },
      // 生成类请求：伪造成响应，用来断言 creation_path（0 次真实 AI）
      { urlPattern: "*/api/rewrites", requestStage: "Request" },
      { urlPattern: "*/api/rewrite-candidates", requestStage: "Request" },
    ],
  })
  const STUB_REWRITE = {
    id: 901, benchmark_content_id: null,
    ai_draft: "（伪造成稿）", final_text: null, is_saved_as_mine: false,
    saved_content_id: null, examples_used: [], style_profile_version: null,
    edit_distance: null, edit_ratio: null, prompt_version: "v3",
    personalization_state: "COLD_START", model: "stub-model",
    created_at: "2026-10-11T00:00:00Z", updated_at: "2026-10-11T00:00:00Z",
    personalization: null,
  }

  const CANDIDATES = [
    { key: "A", label: "反差切入", text: "A 版正文。", strategy_tags: [] },
    { key: "B", label: "结论前置", text: "B 版正文：去年试了 17 个工具，最后只留 2 个。", strategy_tags: [] },
    { key: "C", label: "场景重构", text: "C 版正文。", strategy_tags: [] },
  ]

  const pump = setInterval(async () => {
    if (!paused.length) return
    const ev = paused.shift()
    try {
      if (holdAdopt && ev.request.url.includes("/api/rewrites/adopt")) {
        heldAdopt.push(ev.requestId)     // 挂住不放
        return
      }
      if (ev.request.method === "POST" && /\/api\/rewrites$/.test(ev.request.url)) {
        genRequests.push({ url: "rewrites", body: JSON.parse(ev.request.postData || "{}") })
        await send("Fetch.fulfillRequest", {
          requestId: ev.requestId, responseCode: 200,
          responseHeaders: [{ name: "Content-Type", value: "application/json" }],
          body: Buffer.from(JSON.stringify(STUB_REWRITE)).toString("base64"),
        })
        return
      }
      if (ev.request.method === "POST" && /\/api\/rewrite-candidates$/.test(ev.request.url)) {
        genRequests.push({
          url: "rewrite-candidates",
          body: JSON.parse(ev.request.postData || "{}"),
        })
        await send("Fetch.fulfillRequest", {
          requestId: ev.requestId, responseCode: 200,
          responseHeaders: [{ name: "Content-Type", value: "application/json" }],
          body: Buffer.from(JSON.stringify({
            source_content_id: null, reference_author_id: null,
            creation_path: "stub", reference_profile_used: false,
            candidates: [
              { key: "A", label: "反差切入", text: "A 版正文。", strategy_tags: [] },
              { key: "B", label: "结论前置", text: "B 版正文。", strategy_tags: [] },
              { key: "C", label: "场景重构", text: "C 版正文。", strategy_tags: [] },
            ],
            context: {
              source_analysis: null, writing_count: 0, performance_high: [],
              performance_low: [], cross_high: [], cross_low: [],
              account_dependent: [], skipped: [], budget: null,
            },
            context_empty: true, prompt_version: "v3",
            model: "stub-model", personalization: null,
          })).toString("base64"),
        })
        return
      }
      await send("Fetch.continueRequest", { requestId: ev.requestId })
    } catch (err) {
      console.error("  拦截失败:", err.message)
    }
  }, 30)

  try {
    // ---------- 准备：从内容库打开 /write/:id ----------
    await send("Page.navigate", { url: `${BASE}/library` })
    await sleep(1800)
    await waitFor(bodyHas("内容库"), "内容库加载")
    await evaluate(`window.localStorage.clear(); window.sessionStorage.clear(); true`)

    console.log("\n准备：从内容库点进一条对标内容 → 改写这条")
    await openBenchmarkRow(0)
    const path = await evaluate(`location.pathname`)
    console.log("  当前地址：", path)

    // ---------- F1：首次打开预填 ----------
    console.log("\nF1 首次打开 /write/:id：预填 + 不卡加载")
    await waitFor(
      `(() => {
         const ta = [...document.querySelectorAll('textarea')]
           .filter((t) => t.offsetParent !== null)[0];
         return Boolean(ta && ta.value.length > 10);
       })()`,
      "素材已预填"
    )
    const prefilled = await evaluate(leftTextarea)
    ok(`已预填素材（${prefilled.length} 字）`)

    // 本轮验收问题 1：从素材进入必须落在「对标改写」，否则原帖会被当成主题
    {
      const active = await evaluate(`
        (() => {
          const b = [...document.querySelectorAll('button')].find(
            (x) => x.textContent.trim() === '对标改写'
          );
          return b ? b.getAttribute('aria-pressed') === 'true' : null;
        })()`)
      if (active !== true) {
        throw new Error('从内容库进 /write/:id 后入口不是「对标改写」（问题 1 未修好）')
      }
      if (!(await evaluate(bodyHas("对标内容")))) {
        throw new Error('左侧素材区没有出现「对标内容」标识')
      }
      ok('入口已自动切到「对标改写」')
    }
    {
      const st = await evaluate(pageState)
      if (st.textareas < 1 || st.spinner) {
        throw new Error(`预填后仍处于加载态：${JSON.stringify(st)}`)
      }
    }
    await shot("09_F1_从内容库打开写入")

    // 生成一次，断言走的是 rewrite 而不是 topic。
    // 对标改写入口只提供「生成 3 个候选」，所以断言候选请求。
    genRequests.length = 0
    await evaluate(clickText("生成 3 个候选"))
    await sleep(1200)
    {
      const req = genRequests.find((r) => r.url === "rewrite-candidates")
      if (!req) throw new Error("没拦到生成请求")
      if (req.body.creation_path !== "rewrite") {
        throw new Error(
          `从素材进入应走 rewrite，实际 ${req.body.creation_path}（问题 1 未修好）`
        )
      }
      if (req.body.source_content_id !== Number(path.split("/").pop())) {
        throw new Error(`source_content_id 不对：${req.body.source_content_id}`)
      }
      if (!req.body.source_text || req.body.source_text.length < 5) {
        throw new Error("source_text 不是完整原文")
      }
      ok(`生成路径正确：creation_path=rewrite，source_content_id=${req.body.source_content_id}`)
    }

    // 写点东西，便于验证「恢复」
    await evaluate(`
      (() => {
        const ta = [...document.querySelectorAll('textarea')]
          .filter((t) => t.offsetParent !== null && !t.disabled)[0];
        if (!ta) return false;
        ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); return true;
      })()`)
    await send("Input.insertText", { text: " 【T06F-我加的一句】" })
    await sleep(300)
    const withEdit = await evaluate(leftTextarea)
    if (!withEdit.includes("【T06F-我加的一句】")) throw new Error("输入没有生效")

    // ---------- F2：切页 → 浏览器后退回同一地址 ----------
    console.log("\nF2 切到「作者」再后退回同一地址")
    await evaluate(clickText("作者", "a"))
    await waitFor(`location.pathname.startsWith('/authors')`, "离开写作页")
    await sleep(700)
    await evaluate(`history.back()`)
    await waitFor(`location.pathname.startsWith('/write/')`, "后退回 /write/:id")
    await sleep(1200)

    {
      const st = await evaluate(pageState)
      if (st.textareas < 1 || st.spinner || !st.shell) {
        throw new Error(`后退回来卡在加载动画上（问题 1 未修好）：${JSON.stringify(st)}`)
      }
    }
    const restored = await evaluate(leftTextarea)
    if (restored !== withEdit) {
      throw new Error(`后退回来现场没有恢复：${JSON.stringify(restored)}`)
    }
    ok("后退回来不卡加载，且输入现场原样恢复")
    await shot("10_F2_后退回来不卡加载")

    // ---------- F3：再次从内容库进同一条 ----------
    console.log("\nF3 再次从内容库点「改写这条」同一条素材：主动打开 → 必须重新载入")
    await evaluate(clickText("内容库", "a"))
    await waitFor(bodyHas("内容库"), "回到内容库")
    await sleep(900)
    await openBenchmarkRow(0)
    await sleep(400)
    await evaluate(clickText("改写这条"))
    await waitFor(`location.pathname.startsWith('/write/')`, "再次进入 /write/:id")
    await sleep(1500)
    {
      const st = await evaluate(pageState)
      if (st.textareas < 1 || st.spinner || !st.shell) {
        throw new Error(`再次进入时卡在加载动画上（问题 1 未修好）：${JSON.stringify(st)}`)
      }
    }
    // 第二轮验收：从内容库点「改写这条」是一次**主动打开**，
    // 按新要求必须重新载入指定素材（覆盖现场），这与「普通切页返回」正好相反。
    const again = await evaluate(leftTextarea)
    if (again !== prefilled) {
      throw new Error(
        `再次主动打开没有重新载入素材：${JSON.stringify(again)}（应为 ${JSON.stringify(prefilled)}）`
      )
    }
    if (again.includes("【T06F-我加的一句】")) {
      throw new Error("主动打开没有覆盖现场，仍带着上一次的编辑")
    }
    {
      const st = await evaluate(pageState)
      if (st.spinner || !st.shell) {
        throw new Error(`再次进入时卡在加载态：${JSON.stringify(st)}`)
      }
    }
    ok("再次主动打开：重新载入原素材（主动打开与普通返回行为相反）")
    await shot("11_F3_再次进入不卡加载")

    // ---------- F4：× 之后不得因 URL 恢复 ----------
    console.log("\nF4 点 × 之后停在同一 URL：不得把原帖填回来")
    await evaluate(clickAria("清空当前创作"))
    await sleep(300)
    const clearedNow = await evaluate(allTextareas)
    if (clearedNow.some((t) => t !== "")) {
      throw new Error(`× 之后没有清空：${JSON.stringify(clearedNow)}`)
    }
    // 等一段时间：旧实现会在这里因为 effect 重新触发而把原帖填回来
    await sleep(1800)
    const afterWait = await evaluate(allTextareas)
    if (afterWait.some((t) => t !== "")) {
      throw new Error(`× 之后 URL 把原帖填回来了（问题 2 未修好）：${JSON.stringify(afterWait)}`)
    }
    ok("× 之后 1.8 秒内没有任何内容被填回")

    // F4b：更强的形式 —— × 之后**离开再回到这个 URL**，同样不得恢复。
    // 这一条才真正锁住「标记必须活在会话之外」：如果标记被 × 清掉，
    // 重新挂载时就会再去拉一次原帖，把用户刚清掉的内容填回来。
    console.log("\nF4b × 之后切走再回到同一 URL：仍不得恢复")
    await evaluate(clickText("内容库", "a"))
    await waitFor(`location.pathname === '/library'`, "离开写作页")
    await sleep(700)
    await evaluate(`history.back()`)
    await waitFor(`location.pathname.startsWith('/write/')`, "回到 /write/:id")
    await sleep(1500)
    const afterRoundTrip = await evaluate(allTextareas)
    if (afterRoundTrip.some((t) => t !== "")) {
      throw new Error(
        `× 之后离开再回来，URL 把原帖恢复了（问题 2 未修好）：${JSON.stringify(afterRoundTrip)}`
      )
    }
    ok("× 之后离开再回到同一 URL，内容仍为空 —— 标记确实活在会话之外")
    await shot("12_F4_x之后不恢复")

    // ---------- F5：切换一级入口同样不恢复 ----------
    console.log("\nF5 切换一级入口后同样不得恢复")
    await evaluate(clickText("对标改写"))
    await sleep(400)
    const afterSwitch = await evaluate(allTextareas)
    if (afterSwitch.some((t) => t !== "")) {
      throw new Error(`切换入口后内容被填回来：${JSON.stringify(afterSwitch)}`)
    }
    ok("切换入口后没有被 URL 填回内容")

    // ---------- F7：同一素材「再次主动打开」必须重新载入 ----------
    // 与 F4b 相对：F4b 是普通返回（不许恢复），F7 是主动打开（必须恢复）。
    // 两者路由完全相同，只能靠导航票据区分。
    console.log("\nF7 × 之后从内容库再次点「改写这条」同一素材：必须重新载入")
    await evaluate(clickText("内容库", "a"))
    await waitFor(`location.pathname === '/library'`, "回到内容库")
    await sleep(900)
    await openBenchmarkRow(0)
    await sleep(400)
    await evaluate(clickText("改写这条"))
    await waitFor(`location.pathname.startsWith('/write/')`, "再次进入 /write/:id")
    await waitFor(
      `(() => {
         const ta = [...document.querySelectorAll('textarea')]
           .filter((t) => t.offsetParent !== null)[0];
         return Boolean(ta && ta.value.length > 5);
       })()`,
      "素材被重新载入"
    )
    const reopened = await evaluate(leftTextarea)
    if (reopened !== prefilled) {
      throw new Error(
        `再次主动打开后素材不一致：${JSON.stringify(reopened)} vs ${JSON.stringify(prefilled)}`
      )
    }
    {
      const active = await evaluate(`
        (() => {
          const b = [...document.querySelectorAll('button')].find(
            (x) => x.textContent.trim() === '对标改写'
          );
          return b ? b.getAttribute('aria-pressed') === 'true' : null;
        })()`)
      if (active !== true) throw new Error("再次主动打开后入口不是「对标改写」")
    }
    ok("× 之后再次主动打开同一素材：重新载入成功，且落在「对标改写」")
    await shot("14_F7_再次主动打开重新载入")

    // ---------- F6：采用候选的迟到响应 ----------
    console.log("\nF6 采用候选请求在途时点 ×：迟到响应不得写回")
    // 造候选：直接注入请求拦截不划算，改为让前端进入候选态
    // —— 用 3 个候选的伪造响应（同任务 06 的做法）
    await send("Fetch.disable")
    await send("Fetch.enable", {
      patterns: [
        { urlPattern: "*/api/rewrites/adopt", requestStage: "Request" },
        { urlPattern: "*/api/rewrite-candidates", requestStage: "Request" },
      ],
    })
    // 让 candidates 走伪造
    paused.length = 0
    const fakeCandidate = setInterval(async () => {
      const i = paused.findIndex((e) => e.request.url.endsWith("rewrite-candidates"))
      if (i < 0) return
      const ev = paused.splice(i, 1)[0]
      try {
        await send("Fetch.fulfillRequest", {
          requestId: ev.requestId, responseCode: 200,
          responseHeaders: [{ name: "Content-Type", value: "application/json" }],
          body: Buffer.from(JSON.stringify({
            source_content_id: null, reference_author_id: null,
            creation_path: "stub", reference_profile_used: false,
            candidates: CANDIDATES,
            context: {
              source_analysis: null, writing_count: 0, performance_high: [],
              performance_low: [], cross_high: [], cross_low: [],
              account_dependent: [], skipped: [], budget: null,
            },
            context_empty: true, prompt_version: "v3",
            model: "stub-model", personalization: null,
          })).toString("base64"),
        })
      } catch (err) {
        console.error("  候选伪造失败:", err.message)
      }
    }, 30)

    // 从内容库重新进一条，生成候选
    await evaluate(clickText("我的创作"))
    await sleep(300)
    await evaluate(clickText("对标改写"))
    await sleep(300)
    await evaluate(clickText("指定素材"))
    await sleep(300)
    await evaluate(`
      (() => {
        const ta = [...document.querySelectorAll('textarea')]
          .filter((t) => t.offsetParent !== null && !t.disabled)[0];
        if (!ta) return false;
        ta.focus(); return true;
      })()`)
    await send("Input.insertText", { text: "【T06F6】原帖素材正文。" })
    await sleep(300)
    await evaluate(clickText("生成 3 个候选"))
    await waitFor(bodyHas("A · 反差切入"), "候选已生成")
    await sleep(300)
    clearInterval(fakeCandidate)

    // 挂住 adopt
    holdAdopt = true
    paused.length = 0
    await evaluate(`
      (() => {
        const sec = [...document.querySelectorAll('section')].find(
          (s) => s.innerText.startsWith('B · 结论前置')
        );
        if (!sec) return false;
        const b = [...sec.querySelectorAll('button')].find((x) => x.textContent.trim() === '采用');
        if (!b) return false; b.click(); return true;
      })()`)
    // 等 adopt 被挂住
    {
      const deadline = Date.now() + 8000
      while (Date.now() < deadline && heldAdopt.length === 0) await sleep(80)
    }
    if (heldAdopt.length === 0) throw new Error("没有拦到 adopt 请求")
    ok("采用请求已挂起（在途）")

    // 在途时点 ×
    await evaluate(clickAria("清空当前创作"))
    await sleep(300)
    const clearedDuring = await evaluate(allTextareas)
    if (clearedDuring.some((t) => t !== "")) {
      throw new Error("× 没有清空")
    }

    // 放行迟到的采用响应
    await sleep(400)
    for (const id of heldAdopt.splice(0)) {
      await send("Fetch.continueRequest", { requestId: id })
    }
    await sleep(HOLD_MS)
    const afterLate = await evaluate(allTextareas)
    if (afterLate.some((t) => t !== "")) {
      throw new Error(`迟到的采用响应把编辑稿写回来了（问题 3 未修好）：${JSON.stringify(afterLate)}`)
    }
    if (await evaluate(bodyHas("已采用"))) {
      throw new Error("迟到的采用响应把「已采用」状态写回来了")
    }
    ok("迟到 3.5 秒的采用响应被丢弃，没有写回编辑稿 / 已采用状态")
    await shot("13_F6_迟到采用响应被丢弃")

    // F6 用的「挂住 adopt」到此结束 —— 必须复位，否则后面的「采用」会被一直挂住
    holdAdopt = false
    for (const id of heldAdopt.splice(0)) {
      try { await send("Fetch.continueRequest", { requestId: id }) } catch { /* ignore */ }
    }
    await sleep(300)

    // ============================================================
    // K1：主动打开新素材必须清掉上一条的创作
    // ============================================================
    console.log("\nK1 素材 A 生成候选 + 采用 + 编辑 → 主动打开素材 B：必须干净接收")
    await openMaterialFromLibrary(0)
    const textA = await evaluate(leftTextarea)
    {
      const st = await evaluate(pageState)
      if (st.spinner) throw new Error("打开 A 时卡在加载态")
    }

    // 在 A 上生成三候选 → 采用 B → 编辑
    await evaluate(clickText("生成 3 个候选"))
    await waitFor(bodyHas("A · 反差切入"), "A 的候选已生成")
    await sleep(300)
    await evaluate(`
      (() => {
        const sec = [...document.querySelectorAll('section')].find(
          (s) => s.innerText.startsWith('B · 结论前置')
        );
        if (!sec) return false;
        const b = [...sec.querySelectorAll('button')].find((x) => x.textContent.trim() === '采用');
        if (!b) return false; b.click(); return true;
      })()`)
    await waitFor(bodyHas("已采用"), "已采用 A 的候选 B")
    await sleep(400)
    await typeInto(-1, " 【K1-在A上改的稿子】")
    if (!(await evaluate(textareaHas("【K1-在A上改的稿子】")))) {
      throw new Error("A 上的编辑没有生效")
    }
    ok("素材 A：已生成候选、已采用 B、已编辑")

    // 主动打开素材 B
    await openMaterialFromLibrary(1)
    const textB = await evaluate(leftTextarea)
    if (textB === textA) throw new Error("第二次打开的还是同一条素材")
    if (textB !== textB.trim() || textB.length < 5) {
      throw new Error(`素材 B 没有正确载入：${JSON.stringify(textB)}`)
    }
    ok(`素材 B 已载入（${textB.length} 字，与 A 不同）`)

    // 上一条的现场必须全清
    if (await evaluate(bodyHas("A · 反差切入"))) {
      throw new Error("新素材载入后旧的候选还在（K1 未修好）")
    }
    if (await evaluate(bodyHas("已采用"))) {
      throw new Error("新素材载入后旧的「已采用」状态还在")
    }
    if (await evaluate(textareaHas("【K1-在A上改的稿子】"))) {
      throw new Error("新素材载入后旧编辑稿还在（K1 未修好）")
    }
    {
      const tas = await evaluate(allTextareas)
      // 右侧编辑区必须为空 —— 新素材应该是「还没生成初稿」的干净态
      if (tas.length > 1 && tas.slice(1).some((t) => t !== "")) {
        throw new Error(`新素材载入后右侧编辑区仍有内容：${JSON.stringify(tas)}`)
      }
    }
    {
      const active = await evaluate(`
        (() => {
          const b = [...document.querySelectorAll('button')].find(
            (x) => x.textContent.trim() === '对标改写'
          );
          return b ? b.getAttribute('aria-pressed') === 'true' : null;
        })()`)
      if (active !== true) throw new Error("新素材载入后入口不是「对标改写」")
    }
    ok("旧候选 / 已采用状态 / 编辑稿 / 关联记录全部清除，入口仍是「对标改写」")
    await shot("15_K1_主动打开新素材_现场干净")

    // ============================================================
    // K2：历史返回不得让旧票据/旧素材重新生效
    // ============================================================
    console.log("\nK2 依次主动打开 A、B，再历史返回 A：旧票据不得重新生效")
    // 此刻 URL = B，现场 = B。再主动打开 A，形成 A → B → A 的历史
    await openMaterialFromLibrary(0)
    const pathA = await evaluate(`location.pathname`)
    if ((await evaluate(leftTextarea)) !== textA) {
      throw new Error("再次主动打开 A 没有重新载入")
    }
    ok(`再次主动打开 A 已重新载入（${pathA}）`)

    // 再主动打开 B —— 历史栈：[library, /write/A, /library, /write/B]
    await openMaterialFromLibrary(1)
    if ((await evaluate(leftTextarea)) !== textB) {
      throw new Error("再次主动打开 B 没有重新载入")
    }
    ok("再次主动打开 B 已重新载入")

    // 历史返回两条回到 A 那次打开
    await evaluate(`history.go(-2)`)
    await waitFor(`location.pathname === ${JSON.stringify(pathA)}`, "历史返回 A")
    await sleep(1500)
    {
      const st = await evaluate(pageState)
      if (st.spinner || !st.shell) {
        throw new Error(`历史返回 A 时卡在加载态：${JSON.stringify(st)}`)
      }
    }
    const afterBack = await evaluate(leftTextarea)
    if (afterBack === textA) {
      throw new Error(
        "历史返回 A 时旧票据重新生效、把旧 A 原文覆盖了当前现场（K2 未修好）"
      )
    }
    if (afterBack !== textB) {
      throw new Error(
        `历史返回 A 后现场既不是 A 也不是 B：${JSON.stringify(afterBack)}`
      )
    }
    ok("历史返回 A：旧票据未重新生效，现场仍是 B（未被旧 A 原文覆盖）")
    await shot("16_K2_历史返回不覆盖现场")

    // 用户明确再次点击 A → 新票据 → 必须重新载入
    await openMaterialFromLibrary(0)
    if ((await evaluate(leftTextarea)) !== textA) {
      throw new Error("再次主动点击 A 没有重新载入（K2 未修好）")
    }
    ok("再次主动点击 A：新票据生效，重新载入 A")

    console.log("\n本轮修复的浏览器回归全部通过")
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
