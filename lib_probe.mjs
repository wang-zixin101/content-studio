import { spawn } from "node:child_process"
import { tmpdir } from "node:os"
const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
const PORT = 9369
let ws, seq = 0; const pending = new Map()
function send(m, p = {}) { const id = ++seq; ws.send(JSON.stringify({ id, method: m, params: p }))
  return new Promise((res, rej) => { pending.set(id, { res, rej })
    setTimeout(() => { if (pending.has(id)) { pending.delete(id); rej(new Error("t")) } }, 20000) }) }
async function ev(e) { const r = await send("Runtime.evaluate", { expression: e, returnByValue: true })
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description ?? "?"); return r.result?.value }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const edge = spawn(EDGE, ["--headless=new", "--disable-gpu", `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${tmpdir()}/lc2_${Date.now()}`, "--window-size=1600,1040", "about:blank"], { stdio: "ignore" })
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
await send("Page.navigate", { url: "http://localhost:5199/library" })
await sleep(2200)
console.log("点击前 chip 态:", await ev(`(() => { const h = [...document.querySelectorAll('p')].find(x => x.textContent.trim() === '作者');
  return [...h.parentElement.querySelectorAll('button')].map(b => ({ t: b.textContent.slice(0, 6), active: b.className.includes('bg-accent') })) })()`))
console.log("点击:", await ev(`(() => { const h = [...document.querySelectorAll('p')].find(x => x.textContent.trim() === '作者');
  const b = [...h.parentElement.querySelectorAll('button')].find(x => x.textContent.includes('王长富')); if (!b) return false; b.click(); return true })()`))
await sleep(3500)
console.log("点击后 chip 态:", await ev(`(() => { const h = [...document.querySelectorAll('p')].find(x => x.textContent.trim() === '作者');
  return [...h.parentElement.querySelectorAll('button')].map(b => ({ t: b.textContent.slice(0, 6), active: b.className.includes('bg-accent') })) })()`))
console.log("列表头:", await ev(`(() => { const m = document.body.innerText.match(/共 [\\d]+ 条/g); return m })()`))
console.log("可见内容行数:", await ev(`[...document.querySelectorAll('button')].filter(b => b.offsetParent !== null && (b.innerText||'').includes('已分析') && !b.className.includes('border')).length`))
console.log("网络里最近的 contents:", await ev(`performance.getEntriesByType('resource').filter(r => r.name.includes('/api/contents')).slice(-1).map(r => decodeURIComponent(r.name)).pop()`))
console.log("h1/facet 区域文本:", await ev(`document.body.innerText.slice(0, 120).replace(/\\n/g, ' | ')`))
edge.kill(); process.exit(0)
