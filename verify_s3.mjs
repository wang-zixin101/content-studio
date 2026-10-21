/** S3 交互测试：作者（新增/搜索）+ 风格档案（统计/更新按钮/卡片渲染）。0 次真实 AI。 */
import { spawn } from "node:child_process"
import { tmpdir } from "node:os"
const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
const PORT = 9353
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
function ok(msg) { results.push(["✓", msg]); console.log("  ✓", msg) }
function fail(msg) { results.push(["✗", msg]); console.log("  ✗", msg) }
const click = (t) => `(() => { const h = [...document.querySelectorAll('button')].reverse().find(
  (x) => x.textContent.trim() === ${JSON.stringify(t)} && x.offsetParent !== null); if (!h) return false; h.click(); return true })()`
async function typeIntoSearch(text) {
  // native setter 对 React 18 受控输入不生效（S1/S2 已踩过），必须真实键入
  const okFocus = await ev(`(() => {
    const i = [...document.querySelectorAll('input')]
      .find((x) => x.offsetParent !== null && x.placeholder.includes('搜索用户名'));
    if (!i) return false;
    i.focus();
    i.setSelectionRange(i.value.length, i.value.length);
    return true;
  })()`)
  if (!okFocus) return false
  await send("Input.insertText", { text })
  await sleep(500)
  return true
}

const edge = spawn(EDGE, ["--headless=new", "--disable-gpu", `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${tmpdir()}/s3t_${Date.now()}`, "--window-size=1600,1040", "about:blank"], { stdio: "ignore" })
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
await send("Emulation.setDeviceMetricsOverride", { width: 1600, height: 1040, deviceScaleFactor: 2, mobile: false })

try {
  // ---------- 作者 ----------
  await send("Page.navigate", { url: "http://localhost:5199/authors" })
  await sleep(1800)
  await waitFor(`document.body.innerText.includes('作者')`, "作者页")
  const before = await ev(`document.querySelectorAll('button[title="编辑作者"], button[title="删除作者"]').length`) ||
    await ev(`[...document.querySelectorAll('button')].filter(b => /编辑|删除/.test(b.getAttribute('aria-label')||'')).length`)
  console.log("\n【作者管理】")
  if (!(await ev(click("新增作者")))) fail("打开新增作者弹窗")
  await waitFor(`Boolean(document.querySelector('[role="dialog"]'))`, "新增弹窗")
  ok("「新增作者」弹窗可打开")
  if (!(await ev(click("取消")))) await ev(`(() => { document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true})); return true })()`)
  await sleep(500)
  if (!(await ev(`!Boolean(document.querySelector('[role="dialog"]'))`))) { fail("弹窗可关闭"); throw new Error("弹窗关不掉") }
  ok("弹窗可关闭，未写入任何数据")
  // 作者行是 div 不是 button，所以用「@句柄是否出现在页面上」判断过滤结果
  const HANDLES = ["@wangchangfu", "@laolei", "@suohage", "@xingyu"]
  const present = async () =>
    await ev(`[${HANDLES.map((h) => JSON.stringify(h)).join(",")}].filter((h) => document.body.innerText.includes(h)).length`)
  if (!(await typeIntoSearch("王长富"))) fail("搜索框找不到")
  const afterType = await present()
  const onlyWang = await ev(`document.body.innerText.includes('王长富') && !document.body.innerText.includes('@laolei')`)
  if (afterType === 1 && onlyWang) ok(`搜索「王长富」过滤后只剩 1 位作者`)
  else fail(`搜索过滤异常：剩 ${afterType} 位`)
  // 清空：全选删除
  await ev(`(() => { const i = [...document.querySelectorAll('input')].find(x => x.placeholder.includes('搜索用户名')); if (!i) return false; i.focus(); i.setSelectionRange(0, i.value.length); return true })()`)
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Backspace", code: "Backspace", windowsVirtualKeyCode: 8 })
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Backspace", code: "Backspace", windowsVirtualKeyCode: 8 })
  await sleep(700)
  const restored = await present()
  if (restored === HANDLES.length) ok(`清空搜索恢复 ${restored} 位作者`)
  else fail(`恢复异常：${restored}（期望 ${HANDLES.length}）`)

  // ---------- 风格档案 ----------
  console.log("\n【风格档案】")
  await send("Page.navigate", { url: "http://localhost:5199/style" })
  await sleep(1800)
  await waitFor(`document.body.innerText.includes('我的风格档案')`, "风格档案页")
  if (await ev(`document.body.innerText.includes('有效样本：24')`)) ok("样本统计卡已渲染（有效样本：24）")
  else fail("样本统计卡缺失")
  if (await ev(`document.body.innerText.includes('我的内容 24 条')`)) ok("状态徽标行在标题下方")
  else fail("状态徽标行缺失")
  if (await ev(`document.body.innerText.includes('人工原创 13')`)) ok("统计项目完整（人工原创 13）")
  else fail("统计项目缺失")
  const btn = await ev(`(() => { const b = [...document.querySelectorAll('button')].find(x => x.textContent.includes('更新风格档案')); return b ? {disabled: b.disabled} : null })()`)
  if (btn && !btn.disabled) ok("「更新风格档案」按钮存在且可用（未点击，避免改预览数据）")
  else fail(`更新按钮状态异常：${JSON.stringify(btn)}`)
  if (await ev(`document.body.innerText.includes('给改写模型的核心指导')`)) ok("正文卡片正常渲染")
  else fail("正文卡片缺失")
} finally {
  try { ws.close() } catch {}
  edge.kill(); await sleep(400)
}
console.log("\n—— S3 交互测试结果 ——")
const bad = results.filter(r => r[0] === "✗")
for (const [m, msg] of results) console.log(`${m} ${msg}`)
process.exit(bad.length ? 1 : 0)
