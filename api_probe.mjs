import { spawn } from "node:child_process"
import { tmpdir } from "node:os"
const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
const PORT = 9375
let ws, seq = 0; const pending = new Map()
function send(m, p = {}) { const id = ++seq; ws.send(JSON.stringify({ id, method: m, params: p }))
  return new Promise((res, rej) => { pending.set(id, { res, rej })
    setTimeout(() => { if (pending.has(id)) { pending.delete(id); rej(new Error("t")) } }, 20000) }) }
async function ev(e) { const r = await send("Runtime.evaluate", { expression: e, returnByValue: true, awaitPromise: true })
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description ?? "?"); return r.result?.value }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const edge = spawn(EDGE, ["--headless=new", "--disable-gpu", `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${tmpdir()}/ap_${Date.now()}`, "--window-size=1600,1040", "about:blank"], { stdio: "ignore" })
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
await send("Page.navigate", { url: "http://localhost:5199/imports" })
await sleep(2200)
const info = await ev(`(async () => {
  const imports = await fetch("/api/imports?limit=50").then(r => r.json());
  const items = imports.items || [];
  const n2 = items.find(b => (b.filename || "").includes("n2.json"));
  const total = await fetch("/api/contents?limit=1").then(r => r.json()).then(d => d.total);
  return { batches: items.map(b => ({ id: b.import_batch_id, file: b.filename, remaining: b.remaining, item_count: b.item_count })), n2: n2 ? { id: n2.import_batch_id, remaining: n2.remaining, analyzed: n2.analyzed, unanalyzed: n2.unanalyzed } : null, total };
})()`)
console.log("删除前：", JSON.stringify(info, null, 1))
if (info.n2) {
  const del = await ev(`(async () => {
    const r = await fetch("/imports/${info.n2.id}/delete", { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode: "all" }) });
    return { status: r.status, body: await r.json() };
  })()`)
  console.log("删除响应：", JSON.stringify(del).slice(0, 300))
}
const after = await ev(`(async () => {
  const imports = await fetch("/api/imports?limit=50").then(r => r.json());
  const items = imports.items || [];
  const total = await fetch("/api/contents?limit=1").then(r => r.json()).then(d => d.total);
  return { n2Still: items.some(b => (b.filename || "").includes("n2.json")), total,
           batches: items.map(b => ({ id: b.import_batch_id, file: b.filename, remaining: b.remaining })) };
})()`)
console.log("删除后：", JSON.stringify(after, null, 1))
edge.kill(); process.exit(0)
