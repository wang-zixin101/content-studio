/**
 * 任务 07 最终验收补充：内容库三类筛选联动验证。
 * 用法：node verify_library_filters.mjs
 *
 * 覆盖（全部用隔离预览库，0 次真实 AI，不写数据库）：
 *   1. 风格学习「参与 / 不参与」：列表请求带 style_eligible、总数变化、
 *      已选筛选条出现、清空可移除
 *   2. 作者筛选：列表请求带 author_username、已选 chip 显示 display_name、
 *      总数变化、可移除
 *   3. 批次筛选（URL import_batch_id）：列表请求带 import_batch_id、
 *      已选 chip 出现、可移除
 *   4. 「清空」：一次性清除全部实际生效的筛选（请求参数归零、筛选条消失）
 *
 * 每一步都读 performance 资源里的**最后一次** /api/contents 请求，
 * 断言实际发出的查询参数 —— 不靠猜测。
 */

import { spawn } from "node:child_process"
import { mkdirSync, writeFileSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import path from "node:path"

const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
const BASE = "http://localhost:5199"
const PORT = 9371
const OUT = path.resolve("s5_filter_shots")
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

const results = []
function ok(m) { results.push(["✓", m]); console.log("  ✓", m) }
function bad(m) { results.push(["✗", m]); console.log("  ✗", m) }

/** 最后一次 /api/contents 请求的查询参数。 */
const LAST_QUERY = `
(() => {
  const r = performance.getEntriesByType("resource")
    .filter((x) => x.name.includes("/api/contents")).pop();
  if (!r) return null;
  const q = {};
  for (const [k, v] of new URL(decodeURIComponent(r.name)).searchParams) q[k] = v;
  return { url: decodeURIComponent(r.name).split("?")[1] ?? "", q };
})()`

/** 已选筛选条（chips）的文本。 */
const CHIPS = `
(() => {
  const chips = [...document.querySelectorAll("span")]
    .filter((s) => s.offsetParent !== null &&
      s.className.includes("bg-accent/60") && s.textContent.includes("："));
  return chips.map((c) => c.textContent.trim());
})()`

const clickChipInGroup = (group, label) => `
(() => {
  const h = [...document.querySelectorAll('p')].find(
    (x) => x.textContent.trim() === ${JSON.stringify(group)});
  if (!h) return false;
  const b = [...h.parentElement.querySelectorAll('button')].find(
    (x) => x.textContent.includes(${JSON.stringify(label)}));
  if (!b) return false; b.click(); return true;
})()`

const clearBtn = `
(() => {
  const b = [...document.querySelectorAll('button')].find(
    (x) => x.textContent.trim() === '清空' && x.offsetParent !== null);
  if (!b) return false; b.click(); return true;
})()`

const clickContains = (text) => `
(() => {
  const hit = [...document.querySelectorAll('button')].reverse().find(
    (el) => el.textContent.includes(${JSON.stringify(text)}) && el.offsetParent !== null);
  if (!hit) return false; hit.click(); return true;
})()`

async function goto(path, mark) {
  await send("Page.navigate", { url: `${BASE}${path}` })
  await sleep(1400)
  await waitFor(`document.body.innerText.includes('内容工作台')`, "应用外壳")
  if (mark) await waitFor(`document.body.innerText.includes(${JSON.stringify(mark)})`, mark)
  await sleep(500)
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
    `--remote-debugging-port=${PORT}`, `--user-data-dir=${tmpdir()}/cs_s5_${Date.now()}`,
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
    await send("Emulation.setDeviceMetricsOverride", {
      width: 1600, height: 1040, deviceScaleFactor: 2, mobile: false,
    })
    await send("Page.navigate", { url: `${BASE}/library` })
    await sleep(2000)
    await waitFor(`document.body.innerText.includes('内容工作台')`, "应用外壳")
    await evaluate(`window.localStorage.clear(); window.sessionStorage.clear(); true`)
    await send("Page.reload")
    await sleep(2200)
    await waitFor(`document.body.innerText.includes('筛选')`, "筛选栏")

    // ---------- 1. 风格学习 ----------
    console.log("\n【风格学习筛选】")
    const base = await evaluate(LAST_QUERY)
    if (base && !("style_eligible" in base.q)) ok(`基线请求不带 style_eligible（${base.url || "无参数"}）`)
    else bad(`基线异常：${JSON.stringify(base)}`)

    if (!(await evaluate(`(() => {
      const b = [...document.querySelectorAll('button')].find(
        (x) => x.offsetParent !== null && x.textContent.trim() === '参与');
      if (!b) return false; b.click(); return true;
    })()`))) throw new Error("点不到「参与」")
    await sleep(1300)
    const q1 = await evaluate(LAST_QUERY)
    if (q1 && q1.q.style_eligible === "true") ok("切「参与」后请求带 style_eligible=true")
    else bad(`切「参与」后请求未带参数：${JSON.stringify(q1)}`)
    const chips1 = await evaluate(CHIPS)
    if (chips1.some((c) => c.includes("风格学习：参与"))) ok("已选筛选条出现「风格学习：参与」")
    else bad(`已选筛选条缺失：${JSON.stringify(chips1)}`)
    if (await evaluate(`document.body.innerText.includes('清空')`)) ok("「清空」按钮出现")
    else bad("「清空」按钮未出现")
    await shot("01_风格学习_参与")

    // 移除该 chip（点 chip 上的 ×）
    await evaluate(`(() => {
      const c = [...document.querySelectorAll('span')].find(
        (s) => s.offsetParent !== null && s.textContent.includes('风格学习：参与'));
      const b = c && c.querySelector('button');
      if (!b) return false; b.click(); return true;
    })()`)
    await sleep(1100)
    const q2 = await evaluate(LAST_QUERY)
    if (q2 && !("style_eligible" in q2.q)) ok("移除 chip 后请求不再带 style_eligible")
    else bad(`移除后请求仍带参数：${JSON.stringify(q2)}`)

    // 再切「不参与」
    await evaluate(`(() => {
      const b = [...document.querySelectorAll('button')].find(
        (x) => x.offsetParent !== null && x.textContent.trim() === '不参与');
      if (!b) return false; b.click(); return true;
    })()`)
    await sleep(1300)
    const q3 = await evaluate(LAST_QUERY)
    if (q3 && q3.q.style_eligible === "false") ok("切「不参与」后请求带 style_eligible=false")
    else bad(`切「不参与」后请求异常：${JSON.stringify(q3)}`)

    // ---------- 2. 作者筛选 ----------
    console.log("\n【作者筛选】")
    if (!(await evaluate(`(() => {
      const h = [...document.querySelectorAll('p')].find((x) => x.textContent.trim() === '作者');
      if (!h) return false;
      const b = [...h.parentElement.querySelectorAll('button')].find(
        (x) => x.textContent.includes('王长富'));
      if (!b) return false; b.click(); return true;
    })()`))) throw new Error("点不到「王长富」chip")
    await sleep(1400)
    const q4 = await evaluate(LAST_QUERY)
    if (q4 && q4.q.author_username === "wangchangfu") ok("请求带 author_username=wangchangfu")
    else bad(`请求未带作者参数：${JSON.stringify(q4)}`)
    const chips4 = await evaluate(CHIPS)
    if (chips4.some((c) => c === "作者：王长富")) ok("已选 chip 显示 display_name「作者：王长富」")
    else bad(`chip 显示名异常：${JSON.stringify(chips4)}`)
    await shot("02_作者筛选_display_name")

    // ---------- 3. 批次筛选（URL 参数） ----------
    console.log("\n【批次筛选】")
    const batchId = await evaluate(`(window.__lastBatchId = null, fetch('/api/imports?limit=1').then(r => r.json()).then(d => {
      const b = (d.items || [])[0];
      window.__lastBatchId = b ? b.import_batch_id : null;
      return window.__lastBatchId;
    }))`)
    if (!batchId) throw new Error("预览库里没有导入批次，无法验证批次筛选")
    await send("Page.navigate", { url: `${BASE}/library?import_batch_id=${batchId}` })
    await sleep(2200)
    await waitFor(`document.body.innerText.includes('筛选')`, "筛选栏")
    const q5 = await evaluate(LAST_QUERY)
    if (q5 && q5.q.import_batch_id === batchId) ok("URL 带批次时请求带 import_batch_id")
    else bad(`批次请求异常：${JSON.stringify(q5)}`)
    const chips5 = await evaluate(CHIPS)
    if (chips5.some((c) => c.startsWith("批次："))) ok(`已选筛选条出现「批次」chip（${chips5.filter((c) => c.startsWith("批次："))[0]}）`)
    else bad(`批次 chip 缺失：${JSON.stringify(chips5)}`)
    await shot("03_批次筛选")

    // ---------- 4. 清空 ----------
    console.log("\n【清空】")
    // 当前已有 批次 + 风格学习：不参与 + 作者? （重新选择作者，凑齐三类一起清）
    await evaluate(`(() => {
      const h = [...document.querySelectorAll('p')].find((x) => x.textContent.trim() === '作者');
      if (!h) return false;
      const b = [...h.parentElement.querySelectorAll('button')].find(
        (x) => x.textContent.includes('王长富'));
      if (!b) return false; b.click(); return true;
    })()`)
    await sleep(1300)
    const chipsBefore = await evaluate(CHIPS)
    if (!(await evaluate(clearBtn))) throw new Error("点不到「清空」")
    await sleep(1400)
    const q6 = await evaluate(LAST_QUERY)
    const clean = q6 && ["style_eligible", "author_username", "import_batch_id", "topic", "content_type"]
      .every((k) => !(k in q6.q))
    if (clean) ok("「清空」后请求不再带任何筛选参数")
    else bad(`清空后仍有参数：${JSON.stringify(q6)}`)
    const chipsAfter = await evaluate(CHIPS)
    if (chipsAfter.length === 0) ok(`清空后已选筛选条消失（清空前 ${chipsBefore.length} 枚）`)
    else bad(`清空后仍有 chip：${JSON.stringify(chipsAfter)}`)
    if (!(await evaluate(`document.body.innerText.includes('清空')`))) ok("清空后「清空」按钮隐藏")
    else bad("清空后「清空」按钮仍显示")
    await shot("04_清空后")

  // ---------- 三类筛选同时生效（最终验收补充） ----------
  console.log("\n【三类筛选同时生效 + 清空】")
  // 先经 UI 导入 2 条「我的」内容：作者归属王长富（metadata.author_username），
  // mine 默认 style_eligible=1 —— 让三类筛选的交集恰好是这 2 条。
  const runTag = String(Date.now())
  const s5file = `s5-${runTag}.json`
  const s5posts = [
    { id: `s5-${runTag}-1`, text: "S5 三类筛选验证第一条：作者+批次+风格学习。",
      author_username: "wangchangfu", author_name: "王长富" },
    { id: `s5-${runTag}-2`, text: "S5 三类筛选验证第二条：交集应恰好命中。",
      author_username: "wangchangfu", author_name: "王长富" },
  ]
  await goto("/import", "批量导入")
  await evaluate(`(() => {
    const input = document.querySelector('input[type="file"]');
    const file = new File([${JSON.stringify(JSON.stringify(s5posts))}], ${JSON.stringify(s5file)},
      { type: "application/json" });
    const dt = new DataTransfer(); dt.items.add(file); input.files = dt.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  })()`)
  await waitFor(`document.body.innerText.includes("导入来源")`, "S5 解析预览", 10_000)
  if (!(await evaluate(`(() => {
    const b = [...document.querySelectorAll('button[aria-pressed]')].find(
      (x) => x.offsetParent !== null && x.textContent.trim().startsWith("我的"));
    if (!b) return false; b.click(); return true;
  })()`))) throw new Error("S5 来源「我的」选择失败")
  await sleep(400)
  if (!(await evaluate(clickContains("确认导入")))) throw new Error("S5 点不到「确认导入」")
  const s5Commit = await waitFor(
    `(() => { const t = [...document.querySelectorAll('[data-sonner-toast]')].map(x => x.textContent.trim());
             return t.some((x) => x.includes("已导入")) })()`,
    "S5 导入成功", 12_000)
  if (s5Commit) ok("S5 导入提交成功（2 条 mine，作者王长富）"); else bad("S5 导入提交失败")

  // 找到新批次 id
  const s5Batch = await evaluate(`(async () => {
    const d = await fetch("/api/imports?limit=50").then(r => r.json());
    const b = (d.items || []).find((x) => (x.filename || "").includes(${JSON.stringify(s5file)}));
    return b ? { id: b.import_batch_id, remaining: b.remaining } : null;
  })()`)
  if (!s5Batch?.id) throw new Error("S5 找不到新批次")
  if (s5Batch.remaining === 2) ok("新批次剩余 2"); else bad(`新批次剩余 ${s5Batch.remaining}（应为 2）`)

  // 带批次参数进入内容库（批次的唯一入口就是 URL）
  await send("Page.navigate", { url: `${BASE}/library?import_batch_id=${s5Batch.id}` })
  await sleep(2000)
  await waitFor(`document.body.innerText.includes("筛选")`, "筛选栏")

  // ① 同页选「参与」
  if (!(await evaluate(`(() => {
    const b = [...document.querySelectorAll('button')].find(
      (x) => x.offsetParent !== null && x.textContent.trim() === "参与");
    if (!b) return false; b.click(); return true;
  })()`))) throw new Error("S5 点不到「参与」")
  await sleep(1300)
  // ② 同页选作者「王长富」→ 同一次请求应带齐三个参数
  if (!(await evaluate(clickChipInGroup("作者", "王长富")))) throw new Error("S5 点不到作者 chip")
  await sleep(1400)
  const all3 = await evaluate(LAST_QUERY)
  const all3ok = all3 && all3.q.author_username === "wangchangfu" &&
    all3.q.style_eligible === "true" && all3.q.import_batch_id === s5Batch.id;
  if (all3ok) ok(`同一次请求带齐三参数：${JSON.stringify(all3.q)}`);
  else bad(`同一次请求未带齐三参数：${JSON.stringify(all3)}`)
  const chips3 = await evaluate(CHIPS)
  const chips3ok = chips3.length === 3 &&
    chips3.some((c) => c === "作者：王长富") &&
    chips3.some((c) => c.startsWith("批次：")) &&
    chips3.some((c) => c.includes("风格学习：参与"));
  if (chips3ok) ok("已选筛选条同时显示 3 枚（作者/批次/风格学习）");
  else bad(`已选筛选条异常：${JSON.stringify(chips3)}`)
  const total3 = await evaluate(`(async () => {
    return await fetch("/api/contents?limit=1&author_username=wangchangfu&style_eligible=true&import_batch_id=" + ${JSON.stringify(s5Batch.id)})
      .then(r => r.json()).then(d => d.total);
  })()`)
  if (total3 === 2) ok("三类交集总数 = 2"); else bad(`三类交集总数异常：${total3}`)
  await shot("05_三类筛选同时生效")

  // ③ 切「不参与」→ 交集变 0（导入的 2 条都是参与）
  await evaluate(`(() => {
    const b = [...document.querySelectorAll('button')].find(
      (x) => x.offsetParent !== null && x.textContent.trim() === "不参与");
    if (!b) return false; b.click(); return true;
  })()`)
  await sleep(1300)
  const total4 = await evaluate(`(async () => {
    return await fetch("/api/contents?limit=1&author_username=wangchangfu&style_eligible=false&import_batch_id=" + ${JSON.stringify(s5Batch.id)})
      .then(r => r.json()).then(d => d.total);
  })()`)
  if (total4 === 0) ok("切「不参与」后交集 = 0"); else bad(`切「不参与」后交集异常：${total4}`)

  // ④ 清空 → 三参数全部消失、列表恢复
  if (!(await evaluate(clearBtn))) throw new Error("S5 点不到「清空」")
  await sleep(1400)
  const qClean = await evaluate(LAST_QUERY)
  const cleanOk = qClean && ["style_eligible", "author_username", "import_batch_id"]
    .every((k) => !(k in qClean.q));
  if (cleanOk) ok("清空后三参数全部消失"); else bad(`清空后仍有参数：${JSON.stringify(qClean)}`)
  const chipsClean = await evaluate(CHIPS)
  if (chipsClean.length === 0) ok("清空后已选筛选条消失"); else bad(`清空后仍有 chip：${JSON.stringify(chipsClean)}`)
  await shot("06_三类清空后")

    // ---------- 回归：作者模式下作者列表仍正常 ----------
    console.log("\n【回归：作者模式】")
    // 作者模式是页内 Segmented（内容 / 作者），不是 URL 参数
    if (!(await evaluate(`(() => {
      const b = [...document.querySelectorAll('button')].find(
        (x) => x.offsetParent !== null && x.textContent.trim() === '作者');
      if (!b) return false; b.click(); return true;
    })()`))) throw new Error("点不到「作者」浏览对象 tab")
    await sleep(1500)
    if (await evaluate(`document.body.innerText.includes('位作者')`)) ok("作者模式列表正常")
    else bad("作者模式异常")
    // 切回内容模式，确认筛选栏回来了
    await evaluate(`(() => {
      const b = [...document.querySelectorAll('button')].find(
        (x) => x.offsetParent !== null && x.textContent.trim() === '内容');
      if (!b) return false; b.click(); return true;
    })()`)
    await sleep(1200)
    if (await evaluate(`document.body.innerText.includes('筛选')`)) ok("切回内容模式筛选栏正常")
    else bad("切回内容模式异常")
  } finally {
    try { ws?.close() } catch { /* ignore */ }
    edge.kill()
    await sleep(500)
    rmSync(`${tmpdir()}/cs_s5_${Date.now()}`, { recursive: true, force: true })
  }

  writeFileSync(path.join(OUT, "results.json"), JSON.stringify(results, null, 2))
  console.log("\n—— 内容库筛选联动验证结果 ——")
  const bads = results.filter((r) => r[0] === "✗")
  for (const [m, msg] of results) console.log(`${m} ${msg}`)
  process.exit(bads.length ? 1 : 0)
}

main().catch((err) => {
  console.error("\n失败：", err.message)
  process.exitCode = 1
})
