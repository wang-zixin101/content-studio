/**
 * S3 第二批交互测试（0 次真实 AI，不写数据库）：
 *   批量导入：来源选择（我的 / 对标）+ 文件选择入口存在
 *   导入记录：批次列表渲染 + 「刷新」可用
 *   参照页：我的 / 跨作者洞察 / 外部分析 页头未受影响
 */
import { spawn } from "node:child_process"
import { tmpdir } from "node:os"
const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
const PORT = 9357
let ws, seq = 0; const pending = new Map()
const results = []
function send(m, p = {}) { const id = ++seq; ws.send(JSON.stringify({ id, method: m, params: p }))
  return new Promise((res, rej) => { pending.set(id, { res, rej })
    setTimeout(() => { if (pending.has(id)) { pending.delete(id); rej(new Error("timeout " + m)) } }, 30000) }) }
async function ev(e) { const r = await send("Runtime.evaluate", { expression: e, returnByValue: true, awaitPromise: true })
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description ?? "?"); return r.result?.value }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
async function waitFor(e, l, t = 15000) { const d = Date.now() + t
  for (;;) { if (await ev(e)) return true; if (Date.now() > d) throw new Error("超时:" + l); await sleep(150) } }
function ok(m) { results.push(["✓", m]); console.log("  ✓", m) }
function bad(m) { results.push(["✗", m]); console.log("  ✗", m) }
const clickText = (t) => `(() => { const h = [...document.querySelectorAll('button')].reverse().find(
  (x) => x.textContent.includes(${JSON.stringify(t)}) && x.offsetParent !== null); if (!h) return false; h.click(); return true })()`

const edge = spawn(EDGE, ["--headless=new", "--disable-gpu", `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${tmpdir()}/s3b2_${Date.now()}`, "--window-size=1600,1040", "about:blank"], { stdio: "ignore" })
let t = null
for (let i = 0; i < 60; i++) { try { t = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()
  if (t.some(x => x.type === "page")) break } catch {} await sleep(250) }
const page = t.find(x => x.type === "page")
ws = new WebSocket(page.webSocketDebuggerUrl)
ws.addEventListener("message", (e) => { const m = JSON.parse(e.data)
  if (m.id && pending.has(m.id)) { const { res, rej } = pending.get(m.id); pending.delete(m.id)
    m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result) } })
await new Promise((r) => ws.addEventListener("open", r, { once: true }))
await send("Page.enable"); await send("Runtime.enable")

try {
  console.log("\n【批量导入】")
  await send("Page.navigate", { url: "http://localhost:5199/import" })
  await sleep(1800)
  await waitFor(`document.body.innerText.includes('批量导入')`, "导入页")
  // 页头：说明文字与强调都在
  if (await ev(`document.body.innerText.includes('支持 JSON / TXT / Markdown / CSV')`))
    ok("页头说明完整")
  else bad("页头说明缺失")
  if (await ev(`document.body.innerText.includes('不会自动调用 AI')`))
    ok("内联强调「不会自动调用 AI」保留")
  else bad("内联强调丢失")
  // 文件入口：input[type=file] + 拖拽区 + 「选择文件」
  const fileEntry = await ev(`(() => {
    const inp = document.querySelector('input[type="file"]');
    const btn = [...document.querySelectorAll('button')].find(b => b.textContent.includes('选择文件'));
    const drop = document.body.innerText.includes('拖入文件，或');
    return { hasInput: Boolean(inp), hasBtn: Boolean(btn), drop,
             accept: inp ? inp.accept : null, multiple: inp ? inp.multiple : null };
  })()`)
  if (fileEntry.hasInput && fileEntry.hasBtn && fileEntry.drop && fileEntry.multiple)
    ok(`文件选择入口正常（input[type=file] multiple=${fileEntry.multiple}，拖拽区 + 按钮都在）`)
  else bad(`文件入口异常：${JSON.stringify(fileEntry)}`)
  // 来源选择卡：button + aria-pressed（「我的」= mine / 「对标」= benchmark）
  const pickSource = async (label) => {
    const clicked = await ev(`(() => {
      const b = [...document.querySelectorAll('button[aria-pressed]')].find(
        (x) => x.offsetParent !== null && x.textContent.trim().startsWith(${JSON.stringify("")} + ${JSON.stringify("我的")} ) || false
      ); return false;
    })()`)
    return clicked
  }
  const pickSourceCard = async (label) => {
    const clicked = await ev(`(() => {
      const b = [...document.querySelectorAll('button[aria-pressed]')].find(
        (x) => x.offsetParent !== null && x.textContent.trim().startsWith(${JSON.stringify(label)}));
      if (!b) return false; b.click(); return true;
    })()`)
    await sleep(300)
    return clicked
  }
  const sourceState = async () => await ev(`(() => {
    const cards = [...document.querySelectorAll('button[aria-pressed]')]
      .filter((x) => x.offsetParent !== null);
    return cards.map((c) => ({ key: c.textContent.slice(0, 2), pressed: c.getAttribute('aria-pressed') === 'true' }));
  })()`)

  const st0 = await sourceState()
  if (st0.length === 2 && st0.every((c) => c.pressed === false)) ok("来源选择卡默认都未选中（必选，无默认值）")
  else bad(`来源默认态异常：${JSON.stringify(st0)}`)

  if (await pickSourceCard("我的")) {
    const st1 = await sourceState()
    const mineOn = st1.find((c) => c.key === "我的")
    if (mineOn?.pressed) ok("来源「我的」可点选，且选中态可见（aria-pressed）")
    else bad(`「我的」选中态异常：${JSON.stringify(st1)}`)
  } else bad("来源卡点不到")

  if (await pickSourceCard("对标")) {
    const st2 = await sourceState()
    const benchOn = st2.find((c) => c.key === "对标")
    if (benchOn?.pressed) ok("来源「对标」可点选，且选中态可见")
    else bad(`「对标」选中态异常：${JSON.stringify(st2)}`)
  } else bad("来源卡点不到")

  console.log("\n【导入记录】")
  await send("Page.navigate", { url: "http://localhost:5199/imports" })
  await sleep(1800)
  await waitFor(`document.body.innerText.includes('导入记录')`, "导入记录页")
  const batches = await ev(`document.body.innerText.match(/xporter-[^\\n]*/g)`)
  if (batches && batches.length >= 1) ok(`批次列表渲染 ${batches.length} 条（${batches[0].slice(0, 34)}…）`)
  else bad("批次列表为空")
  // 「刷新」按钮在页头 actions 里
  const refresh = await ev(`(() => {
    const h1 = document.querySelector('h1');
    const bar = h1 ? h1.parentElement.parentElement : null;
    const b = bar ? [...bar.querySelectorAll('button')].find(x => x.textContent.includes('刷新')) : null;
    return b ? { disabled: b.disabled } : null;
  })()`)
  if (refresh && !refresh.disabled) ok("页头里的「刷新」按钮存在且可用")
  else bad(`刷新按钮异常：${JSON.stringify(refresh)}`)
  // 点一次刷新，列表仍在
  if (refresh) {
    await ev(clickText("刷新"))
    await sleep(900)
    if (await ev(`document.body.innerText.includes('导入记录')`)) ok("点击刷新后页面正常")
    else bad("刷新后页面异常")
  }

  console.log("\n【参照页页头未受影响】")
  for (const [name, path, expect] of [
    ["我的", "/mine", "目标进度、合格浏览量账本"],
    ["跨作者洞察", "/insights", "跨作者"],
    ["外部分析", "/external-analysis", "导出分析任务"],
  ]) {
    await send("Page.navigate", { url: `http://localhost:5199${path}` })
    await sleep(1600)
    const r = await ev(`(() => {
      const h1 = document.querySelector('h1');
      return { t: h1 ? h1.textContent.trim() : null,
               fs: h1 ? getComputedStyle(h1).fontSize : null,
               ok: document.body.innerText.includes(${JSON.stringify(expect)}) };
    })()`)
    if (r.t && r.fs === "22px" && r.ok) ok(`${name}：h1「${r.t}」22px 正常`)
    else bad(`${name}：${JSON.stringify(r)}`)
  }
} finally {
  try { ws.close() } catch {}
  edge.kill(); await sleep(400)
}
console.log("\n—— S3 第二批交互测试结果 ——")
const bads = results.filter(r => r[0] === "✗")
for (const [m, msg] of results) console.log(`${m} ${msg}`)
process.exit(bads.length ? 1 : 0)
