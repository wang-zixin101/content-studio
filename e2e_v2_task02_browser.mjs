/**
 * V2 任务 02 端到端：真实浏览器（Chrome headless + CDP）走完整链路。
 *
 *   打开 /external-analysis
 *   → 校验「仅待分析」/「全部可导出」显示的是数据库真实条数（与 API 预览逐项比对）
 *   → 校验作者数 / 无作者数 / 预计体积
 *   → 点下载 → 校验落盘文件行数、content_id 与 API 导出完全一致
 *   → 作者选择器指定单个作者 → 校验条数收敛到该作者
 *   → 勾选「含无作者」→ 校验条数与无作者数
 *   → 恢复全部作者 → 回到完整条数
 *   → 上传结果文件 → 预检 → 确认导入 → 校验导入条数
 *
 * 零依赖：只用 Node 22 内置 WebSocket + fetch。0 AI。
 */

import { spawn } from "node:child_process"
import {
  readFileSync,
  readdirSync,
  existsSync,
  mkdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs"
import { join } from "node:path"
import { setTimeout as sleep } from "node:timers/promises"

const CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
const PORT = 9333
const APP = "http://[::1]:5173/external-analysis"
const API = "http://127.0.0.1:8000/api/external-analysis"
const TMP = "C:\\Users\\18483\\AppData\\Local\\Temp\\cs_v2task02"
const DOWNLOAD_DIR = join(TMP, "downloads")
const PROFILE = join(TMP, "chrome-profile")
const RESULT_FILE = join(TMP, "result300.jsonl")
const SHOTS = "C:\\Users\\18483\\Desktop\\x运营\\content-studio\\shots\\v2-task02"

const IMPORT_ROWS = 300

process.env.NO_PROXY = "127.0.0.1,localhost,::1"

const results = []
function check(name, pass, detail = "") {
  results.push({ name, pass, detail })
  console.log(`${pass ? "PASS" : "FAIL"}  ${name}${detail ? "  —— " + detail : ""}`)
}

rmSync(DOWNLOAD_DIR, { recursive: true, force: true })
mkdirSync(DOWNLOAD_DIR, { recursive: true })
mkdirSync(SHOTS, { recursive: true })

/* ---------------- API 侧真值 ---------------- */
async function preview(query) {
  const res = await fetch(`${API}/tasks/preview?${query}`)
  if (!res.ok) throw new Error(`preview ${query} -> ${res.status}`)
  return res.json()
}

const EXPECT = {
  pending: (await preview("scope=pending")).matching_count,
  all: (await preview("scope=all")).matching_count,
  author1: (await preview("scope=all&author_ids=1")).matching_count,
  author1NoAuthor: (await preview("scope=all&author_ids=1&include_no_author=true"))
    .matching_count,
}
const ALL = await preview("scope=all")
const PENDING = await preview("scope=pending")
console.log(
  `真值：pending=${EXPECT.pending} all=${EXPECT.all} author1=${EXPECT.author1} author1+无作者=${EXPECT.author1NoAuthor}`
)
console.log(`     作者 ${ALL.author_count} 个 · 无作者 ${ALL.no_author_count} 条\n`)

/* ---------------- Chrome / CDP ---------------- */
const chrome = spawn(
  CHROME,
  [
    "--headless=new",
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${PROFILE}`,
    "--no-proxy-server",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-gpu",
    "--window-size=1440,900",
    "about:blank",
  ],
  { stdio: "ignore" }
)

let ws = null
let sessionId = null
let msgId = 0
const pending = new Map()

function send(method, params = {}, useSession = true) {
  const id = ++msgId
  const payload = { id, method, params }
  if (useSession && sessionId) payload.sessionId = sessionId
  ws.send(JSON.stringify(payload))
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject })
    setTimeout(() => {
      if (pending.has(id)) {
        pending.delete(id)
        reject(new Error(`CDP timeout: ${method}`))
      }
    }, 60000)
  })
}

async function evaluate(expression) {
  const r = await send("Runtime.evaluate", {
    expression,
    returnByValue: true,
    awaitPromise: true,
  })
  if (r.exceptionDetails) {
    throw new Error(
      r.exceptionDetails.text + " " + (r.exceptionDetails.exception?.description ?? "")
    )
  }
  return r.result?.value
}

async function waitFor(fn, timeoutMs = 30000, label = "") {
  const deadline = Date.now() + timeoutMs
  let last = null
  while (Date.now() < deadline) {
    try {
      const v = await fn()
      if (v) return v
      last = v
    } catch (e) {
      last = e
    }
    await sleep(400)
  }
  throw new Error(`waitFor timeout: ${label} (last=${String(last)})`)
}

async function clickByText(text, selector = "button") {
  const clicked = await evaluate(
    `(function(){
      var nodes = Array.from(document.querySelectorAll(${JSON.stringify(selector)}));
      for (const n of nodes) {
        if ((n.textContent || '').includes(${JSON.stringify(text)})) { n.click(); return true; }
      }
      return false;
    })()`
  )
  if (!clicked) throw new Error(`找不到按钮：${text}`)
  await sleep(1000)
}

async function shot(name) {
  const r = await send("Page.captureScreenshot", { format: "png" })
  writeFileSync(join(SHOTS, name), Buffer.from(r.data, "base64"))
}

/** 读取页面上的「待导出 N 条」 */
async function readExportCount() {
  const t = await evaluate(`document.body.innerText`)
  const m = t.match(/待导出\s*([\d…]+)\s*条/)
  return m ? m[1] : null
}

/** 读取「作者 N 个 · 含无作者 M 条 · 预计 X」整行 */
async function readMetaLine() {
  return evaluate(
    `Array.from(document.querySelectorAll('p')).map(p=>p.innerText).find(t=>t.startsWith('作者')) || ''`
  )
}

async function main() {
  /* 1) 连 CDP */
  let version = null
  await waitFor(
    async () => {
      try {
        const res = await fetch(`http://127.0.0.1:${PORT}/json/version`)
        version = await res.json()
        return !!version.webSocketDebuggerUrl
      } catch {
        return null
      }
    },
    30000,
    "CDP /json/version"
  )

  ws = new WebSocket(version.webSocketDebuggerUrl)
  await new Promise((resolve, reject) => {
    ws.onopen = resolve
    ws.onerror = reject
  })
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data)
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id)
      pending.delete(msg.id)
      msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result)
    }
  }

  await send(
    "Browser.setDownloadBehavior",
    { behavior: "allow", downloadPath: DOWNLOAD_DIR, eventsEnabled: true },
    false
  )

  const { targetId } = await send("Target.createTarget", { url: "about:blank" }, false)
  ;({ sessionId } = await send("Target.attachToTarget", { targetId, flatten: true }, false))
  await send("Page.enable")
  await send("Runtime.enable")

  /* 2) 打开页面 */
  await send("Page.navigate", { url: APP })
  await waitFor(
    async () => evaluate(`document.body && document.body.innerText.includes('导出分析任务')`),
    30000,
    "页面加载"
  )
  await sleep(1500)

  /* 3) 默认「仅待分析」 */
  const c1 = await waitFor(
    async () => {
      const v = await readExportCount()
      return v && v !== "…" ? v : null
    },
    30000,
    "仅待分析条数"
  )
  check(
    "默认「仅待分析」= 数据库真实条数",
    Number(c1) === EXPECT.pending,
    `页面 ${c1} / API ${EXPECT.pending}`
  )
  const meta1 = await readMetaLine()
  check(
    "显示作者数 / 无作者数 / 预计体积",
    meta1.includes(`作者 ${PENDING.author_count} 个`) &&
      meta1.includes(`含无作者 ${PENDING.no_author_count} 条`) &&
      /预计/.test(meta1),
    meta1
  )
  await shot("01-pending.png")

  /* 4) 切到「全部可导出」 */
  await clickByText("全部可导出")
  const c2 = await waitFor(
    async () => {
      const v = await readExportCount()
      return v && v !== "…" && Number(v) === EXPECT.all ? v : null
    },
    30000,
    "全部可导出条数"
  )
  check("「全部可导出」= 数据库真实条数", Number(c2) === EXPECT.all, `页面 ${c2}`)

  const btn = await evaluate(
    `Array.from(document.querySelectorAll('button')).map(b=>b.innerText).find(t=>t.includes('下载'))`
  )
  check("下载按钮显示实际条数", btn === `下载 ${EXPECT.all} 条任务 JSONL`, btn)

  const chips = await evaluate(
    `Array.from(document.querySelectorAll('span')).map(s=>s.innerText.trim().replace(/\\s+/g,' ')).filter(t=>/^(未分析|失败|已分析|已取消|排队中|正在分析) \\d+$/.test(t)).join(' · ')`
  )
  check("显示各状态帖子数量", chips.length > 0, chips)

  const noLimitSelector = await evaluate(
    `document.body.innerText.includes('20 / 50 / 100') && !document.querySelector('select')`
  )
  check("页面上已不存在 20/50/100 条数选择器", !!noLimitSelector)
  await shot("02-all-scope.png")

  /* 5) 点击下载 */
  await clickByText("下载 ")
  const file = await waitFor(
    async () => {
      const files = readdirSync(DOWNLOAD_DIR).filter((f) => f.endsWith(".jsonl"))
      if (!files.length) return null
      const full = join(DOWNLOAD_DIR, files[0])
      return statSync(full).size > 0 ? full : null
    },
    90000,
    "下载落盘"
  )
  const bytes = statSync(file).size
  const raw = readFileSync(file, "utf-8").trim().split("\n")
  const lines = raw.length - 1 // 去掉首行 _meta
  check(
    "浏览器下载 JSONL 条数 = 数据库真实条数",
    lines === EXPECT.all,
    `${lines} 条 / ${(bytes / 1024 / 1024).toFixed(2)} MB`
  )
  const ids = raw.slice(1).map((x) => JSON.parse(x).content_id)
  check("下载内容无重复", new Set(ids).size === ids.length, `unique=${new Set(ids).size}`)

  const apiText = await (await fetch(`${API}/tasks/export?scope=all`)).text()
  const apiIds = apiText.trim().split("\n").slice(1).map((x) => JSON.parse(x).content_id)
  check(
    "浏览器下载文件 = API 导出（逐条同序）",
    JSON.stringify(ids) === JSON.stringify(apiIds),
    `browser=${ids.length} api=${apiIds.length}`
  )
  await shot("03-downloaded.png")

  /* 6) 指定单个作者 */
  await clickByText("添加作者")
  await waitFor(
    async () => evaluate(`document.body.innerText.includes('选择作者')`),
    20000,
    "作者选择弹窗"
  )
  await shot("04-author-picker.png")
  const picked = await evaluate(
    `(function(){
      var dlg = document.querySelector('[role="dialog"]');
      if (!dlg) return 'no-dialog';
      var btns = Array.from(dlg.querySelectorAll('button'));
      var hit = btns.find(b => (b.innerText||'').includes('H0PEYIN'));
      if (!hit) return 'no-item:' + btns.length;
      hit.click();
      return hit.innerText.split('\\n')[0];
    })()`
  )
  await sleep(1500)
  const c3 = await waitFor(
    async () => {
      const v = await readExportCount()
      return v && v !== "…" && Number(v) === EXPECT.author1 ? v : null
    },
    30000,
    "单作者条数"
  )
  check(
    "指定单个作者后条数收敛到该作者",
    Number(c3) === EXPECT.author1,
    `${picked} → 页面 ${c3} / API ${EXPECT.author1}`
  )
  const meta3 = await readMetaLine()
  check("单作者时显示 作者 1 个 且不含无作者", meta3.includes("作者 1 个") && !meta3.includes("含无作者"), meta3)
  await shot("05-single-author.png")

  /* 7) 勾选「同时包含没有关联作者的内容」 */
  await evaluate(
    `(function(){
      var lb = Array.from(document.querySelectorAll('label')).find(l => l.innerText.includes('同时包含没有关联作者的内容'));
      if (!lb) return false;
      var cb = lb.querySelector('input[type="checkbox"]');
      if (!cb) return false;
      cb.click();
      return true;
    })()`
  )
  await sleep(1500)
  const c4 = await waitFor(
    async () => {
      const v = await readExportCount()
      return v && v !== "…" && Number(v) === EXPECT.author1NoAuthor ? v : null
    },
    30000,
    "含无作者条数"
  )
  const meta4 = await readMetaLine()
  check(
    "勾选含无作者后条数 = 该作者 + 无作者内容",
    Number(c4) === EXPECT.author1NoAuthor,
    `页面 ${c4} / API ${EXPECT.author1NoAuthor}｜${meta4}`
  )
  await shot("06-include-no-author.png")

  /* 8) 恢复全部作者 */
  await clickByText("恢复全部作者")
  await waitFor(
    async () => {
      const v = await readExportCount()
      return v && v !== "…" && Number(v) === EXPECT.all ? v : null
    },
    30000,
    "恢复全部作者"
  )
  check("恢复全部作者后回到完整条数", true, String(EXPECT.all))

  /* 9) 结果回导：上传 → 预检 → 确认导入 */
  check("结果文件存在", existsSync(RESULT_FILE), `${(statSync(RESULT_FILE).size / 1024 / 1024).toFixed(2)} MB`)
  const doc = await send("DOM.getDocument", { depth: 0 })
  const { nodeId } = await send("DOM.querySelector", {
    nodeId: doc.root.nodeId,
    selector: "#external-result-file",
  })
  await send("DOM.setFileInputFiles", { files: [RESULT_FILE], nodeId })
  await sleep(1000)
  await clickByText("预检")
  await waitFor(
    async () => {
      const t = await evaluate(`document.body.innerText`)
      return t.includes("可导入") ? t : null
    },
    60000,
    "预检完成"
  )
  const previewText = await evaluate(`document.body.innerText`)
  check(
    "预检显示可导入条数",
    previewText.includes(String(IMPORT_ROWS)),
    (previewText.split("\n").find((x) => x.includes("可导入")) || "").trim()
  )
  await shot("07-result-preview.png")

  await clickByText("确认导入")
  const doneText = await waitFor(
    async () => {
      const t = await evaluate(`document.body.innerText`)
      return t.includes("已导入") ? t : null
    },
    120000,
    "确认导入"
  )
  check(
    "确认导入条数正确",
    doneText.includes(`已导入 ${IMPORT_ROWS} 条`),
    (doneText.split("\n").find((x) => x.includes("已导入")) || "").trim()
  )
  await shot("08-import-done.png")
}

try {
  await main()
} catch (err) {
  check("执行异常", false, String(err && err.message ? err.message : err))
} finally {
  const pass = results.filter((r) => r.pass).length
  console.log(`\n===== ${pass}/${results.length} passed =====`)
  try {
    await send("Browser.close", {}, false)
  } catch {
    /* ignore */
  }
  try {
    chrome.kill("SIGKILL")
  } catch {
    /* ignore */
  }
  process.exit(pass === results.length ? 0 : 1)
}
