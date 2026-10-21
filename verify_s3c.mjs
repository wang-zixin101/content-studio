/**
 * S3 第三批交互测试（0 次真实 AI，不写数据库）：
 *   作者主页：唯一 h1、四标签切换、返回 / 作者管理 导航、元信息可读
 *   回归：既有 PageHeader 使用方（我的 / 跨作者洞察 / 外部分析 / 作者管理 / 风格档案 / 批量导入 / 导入记录）
 */
import { spawn } from "node:child_process"
import { tmpdir } from "node:os"
const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
const PORT = 9361
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
const clickTab = (t) => `(() => { const b = [...document.querySelectorAll('button, [role="tab"]')].find(
  (x) => x.textContent.trim() === ${JSON.stringify(t)} && x.offsetParent !== null); if (!b) return false; b.click(); return true })()`
const clickLink = (t) => `(() => { const a = [...document.querySelectorAll('a')].find(
  (x) => x.offsetParent !== null && x.textContent.trim() === ${JSON.stringify(t)}); if (!a) return false; a.click(); return true })()`
async function goto(path) {
  await send("Page.navigate", { url: `http://localhost:5199${path}` })
  await sleep(1700)
}
const H1 = `(() => { const h = document.querySelectorAll('h1'); return {
  n: h.length, t: h[0] ? h[0].textContent.trim() : null,
  fs: h[0] ? getComputedStyle(h[0]).fontSize : null } })()`

const edge = spawn(EDGE, ["--headless=new", "--disable-gpu", `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${tmpdir()}/s3c3_${Date.now()}`, "--window-size=1600,1040", "about:blank"], { stdio: "ignore" })
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
  console.log("\n【作者主页】")
  await goto("/authors/1")
  await waitFor(`document.body.innerText.includes('规模')`, "作者主页")
  const h = await ev(H1)
  if (h.n === 1 && h.fs === "22px") ok(`唯一 H1「${h.t}」22px`)
  else bad(`H1 异常：${JSON.stringify(h)}`)
  if (await ev(`document.body.innerText.includes('@wangchangfu')`)) ok("@handle 可读")
  else bad("@handle 缺失")
  if (await ev(`document.body.innerText.includes('最近更新')`)) ok("「最近更新」可读")
  else bad("「最近更新」缺失")

  // 四个标签切换。
  // 不猜各面板的文案（那是数据驱动的），改用结构性断言：
  //   · 切走后「规模」（概览独有）必须消失
  //   · 帖子面板有专属搜索框（placeholder 在 DOM 里，innerText 不含 placeholder）
  //   · 流量画像面板有自己的标题文案
  // 「规模」不是概览独有（帖子列表等处也会出现），用概览独有的「内容归属」
  const overviewGone = `!document.body.innerText.includes('内容归属')`
  const postsInput = `Boolean(document.querySelector('input[placeholder*="帖子中搜索"]'))`
  const cases = [
    ["帖子", `Boolean(document.querySelector('input[placeholder*="帖子中搜索"]')) && ${overviewGone}`],
    ["写作画像", `${overviewGone} && !Boolean(document.querySelector('input[placeholder*="帖子中搜索"]'))`],
    ["流量画像", `${overviewGone} && document.body.innerText.includes('流量画像')`],
    ["概览", `document.body.innerText.includes('内容归属')`],
  ]
  for (const [tab, mark] of cases) {
    if (!(await ev(clickTab(tab)))) { bad(`标签「${tab}」点不到`); continue }
    await sleep(1100)
    if (await ev(mark)) ok(`标签「${tab}」切换后面板正确`)
    else bad(`标签「${tab}」切换后面板异常`)
  }

  // 返回 → /library
  if (!(await ev(clickLink("返回") ))) { /* 返回是图标按钮 aria-label */ }
  const backClicked = await ev(`(() => {
    const b = [...document.querySelectorAll('a')].find(
      (x) => x.offsetParent !== null && x.getAttribute('href') === '/library' &&
             x.querySelector('svg.lucide-arrow-left'));
    if (!b) return false; b.click(); return true;
  })()`)
  await sleep(1000)
  if (backClicked && (await ev(`location.pathname === '/library'`))) ok("「返回」导航到 /library")
  else bad("「返回」导航异常")

  // 作者管理 → /authors
  await goto("/authors/1")
  await waitFor(`document.body.innerText.includes('规模')`, "作者主页")
  const mClicked = await ev(`(() => {
    const a = [...document.querySelectorAll('a')].find(
      (x) => x.offsetParent !== null && x.textContent.trim() === '作者管理');
    if (!a) return false; a.click(); return true;
  })()`)
  await sleep(1000)
  if (mClicked && (await ev(`location.pathname === '/authors'`))) ok("「作者管理」导航到 /authors")
  else bad("「作者管理」导航异常")

  console.log("\n【既有 PageHeader 使用方回归】")
  for (const [name, path, mark] of [
    ["我的", "/mine", "90 天目标"],
    ["跨作者洞察", "/insights", "跨作者洞察"],
    ["外部分析", "/external-analysis", "导出分析任务"],
    ["作者管理", "/authors", "新增作者"],
    ["风格档案", "/style", "我的风格档案"],
    ["批量导入", "/import", "导入来源"],
    ["导入记录", "/imports", "导入记录"],
  ]) {
    await goto(path)
    const r = await ev(H1)
    const body = await ev(`document.body.innerText.includes(${JSON.stringify(mark)})`)
    if (r.n === 1 && r.fs === "22px" && body) ok(`${name}：h1「${r.t}」22px、内容正常`)
    else bad(`${name}：${JSON.stringify(r)} mark=${body}`)
  }
} finally {
  try { ws.close() } catch {}
  edge.kill(); await sleep(400)
}
console.log("\n—— S3 第三批交互测试结果 ——")
const bads = results.filter(r => r[0] === "✗")
for (const [m, msg] of results) console.log(`${m} ${msg}`)
process.exit(bads.length ? 1 : 0)
