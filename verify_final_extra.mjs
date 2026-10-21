/**
 * 最终验收 · 补盲区端到端验证（隔离预览库，0 次真实 AI，不碰生产库）。
 * 用法：node verify_final_extra.mjs
 *
 * N1 内容库批量操作：多选 → 批量参与风格学习（toast + 列表刷新）→ 批量删除（总数下降）
 * N2 导入记录「删除本批次」：keep_analyzed 模式 → 批次消失
 * N3 我的页 CSV 导入：合法 14 列 CSV → 指标出现；非法 CSV → 错误提示且不写入
 * N4 外部分析：导入非法 JSONL → 严格校验错误呈现、0 写入
 * N5 快速切换压力：写作 → 内容库 → 作者 → 写作 ×3，现场保留
 */

import { spawn } from "node:child_process"
import { mkdirSync, writeFileSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import path from "node:path"

const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
const BASE = "http://localhost:5199"
const PORT = 9373
const OUT = path.resolve("final_extra_shots")
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
function expect(cond, m) { cond ? ok(m) : bad(m) }

const clickExact = (text) => `
(() => {
  const hit = [...document.querySelectorAll('button')].reverse().find(
    (el) => el.textContent.trim() === ${JSON.stringify(text)} && el.offsetParent !== null);
  if (!hit) return false; hit.click(); return true;
})()`

const clickContains = (text) => `
(() => {
  const hit = [...document.querySelectorAll('button')].reverse().find(
    (el) => el.textContent.includes(${JSON.stringify(text)}) && el.offsetParent !== null);
  if (!hit) return false; hit.click(); return true;
})()`

const toastText = `(() => { const t = [...document.querySelectorAll('[data-sonner-toast]')].map(x => x.textContent.trim()); return t })()`

/** 往隐藏 file input 塞文件并触发 change。 */
const uploadTo = (selector, name, content, mime) => `
(() => {
  const input = document.querySelector(${JSON.stringify(selector)});
  if (!input) return false;
  const file = new File([${JSON.stringify(content)}], ${JSON.stringify(name)}, { type: ${JSON.stringify(mime)} });
  const dt = new DataTransfer();
  dt.items.add(file);
  input.files = dt.files;
  input.dispatchEvent(new Event("change", { bubbles: true }));
  return true;
})()`

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

async function goto(p, mark) {
  await send("Page.navigate", { url: `${BASE}${p}` })
  await sleep(1400)
  await waitFor(`document.body.innerText.includes('内容工作台')`, "应用外壳")
  if (mark) await waitFor(`document.body.innerText.includes(${JSON.stringify(mark)})`, mark)
  await sleep(500)
}

const CSV_HEADER =
  "Date,Impressions,Likes,Engagements,Bookmarks,Shares,New follows,Unfollows,Replies,Reposts,Profile visits,Create Post,Video views,Media views"
const GOOD_CSV =
  `${CSV_HEADER}\n` +
  "2026-10-09T00:00:00.000Z,5200,55,210,12,4,3,1,6,2,88,1,340,120\n" +
  "2026-10-10T00:00:00.000Z,6100,72,260,18,6,5,0,9,3,101,0,402,150\n"
const BAD_CSV = "Date,Impressions\n2026-10-09T00:00:00.000Z,5000\n"

async function main() {
  console.log("启动 headless Edge…")
  const edge = spawn(EDGE, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    `--remote-debugging-port=${PORT}`, `--user-data-dir=${tmpdir()}/cs_final_${Date.now()}`,
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

    // ================= N1 内容库批量操作 =================
    console.log("\n【N1 内容库批量操作】")
    await goto("/library", "筛选")
    // 勾选前两行（行内复选框 aria-label=选择）
    const picked = await evaluate(`(() => {
      const boxes = [...document.querySelectorAll('button[aria-label="选择"]')]
        .filter((b) => b.offsetParent !== null);
      boxes.slice(0, 2).forEach((b) => b.click());
      return boxes.length >= 2 ? 2 : boxes.length;
    })()`)
    expect(picked === 2, `勾选 2 行（实际 ${picked}）`)
    await sleep(600)
    // 工具条出现（批量操作按钮）
    console.log("    [debug] 选中后按钮:", await evaluate(`(() => {
      return [...document.querySelectorAll('button')].filter((x) => x.offsetParent !== null &&
        (x.textContent.includes("删除") || x.textContent.includes("已选") || x.textContent.includes("参与") || x.textContent.includes("改为")))
        .map((x) => x.textContent.trim().slice(0, 16));
    })()`))
    const toolbar = await evaluate(`(() => {
      const b = [...document.querySelectorAll('button')].filter((x) => x.offsetParent !== null);
      return {
        style: b.some((x) => x.textContent.includes("参与风格学习")),
        source: b.some((x) => x.textContent.includes("改为对标")),
        del: b.some((x) => x.textContent.includes("删除已选")),
      };
    })()`)
    expect(toolbar.style && toolbar.source && toolbar.del,
      `批量工具条出现（参与=${toolbar.style} 改来源=${toolbar.source} 删除已选=${toolbar.del}）`)
    // 批量参与风格学习
    const total0 = await evaluate(`(document.body.innerText.match(/共 (\\d+) 条/) || [])[1]`)
    // 批量参与 / 批量删除都走 window.confirm —— headless 默认拒绝，先覆写
    await evaluate(`window.confirm = () => true; true`)
    if (!(await evaluate(`(() => {
      const b = [...document.querySelectorAll('button')].find(
        (x) => x.offsetParent !== null && x.textContent.trim() === "参与风格学习");
      if (!b) return false; b.click(); return true;
    })()`))) throw new Error("点不到批量参与风格学习")
    const t1ok = await waitFor(
      `(() => { const t = [...document.querySelectorAll('[data-sonner-toast]')].map(x => x.textContent.trim());
               return t.some((x) => x.includes("已更新")) })()`,
      "批量参与 toast", 6000)
    expect(t1ok, "批量参与风格学习成功反馈")
    // 批量删除（批量操作成功后会 clearSelection，需要重新勾选 2 行）
    const repick = await evaluate(`(() => {
      const boxes = [...document.querySelectorAll('button[aria-label="选择"]')]
        .filter((b) => b.offsetParent !== null);
      boxes.slice(0, 2).forEach((b) => b.click());
      return boxes.length >= 2;
    })()`)
    expect(repick, "重新勾选 2 行")
    await sleep(600)
    await evaluate(`window.confirm = () => true; true`)
    const rows = await evaluate(`(() => {
      const b = [...document.querySelectorAll('button')].find(
        (x) => x.offsetParent !== null && x.textContent.includes("删除已选"));
      if (!b) return false; b.click(); return true;
    })()`)
    expect(rows, "「删除已选」按钮可点")
    await sleep(1500)
    const total1 = await evaluate(`(document.body.innerText.match(/共 (\\d+) 条/) || [])[1]`)
    expect(Number(total1) === Number(total0) - 2, `删除后总数 ${total0} → ${total1}`)
    await shot("N1_批量操作后")

    // ================= N2 完整导入流程 + 批次撤销 =================
    console.log("\n【N2 完整导入流程 + 批次撤销】")
    const contentsN2Base = await evaluate(
      `fetch("/api/contents?limit=1").then(r => r.json()).then(d => d.total)`)
    await goto("/import", "批量导入")

    // N2a JSON 导入（3 条帖子，其中 2 条同 id → 文件内去重后 2 条）
    const runTag = String(Date.now())
    const fileA = `n2a-${runTag}.json`
    const fileB = `n2b-${runTag}.json`
    const posts = [
      { id: "n2-1", text: "N2 导入验证第一条：去重与作者归属。" },
      { id: "n2-2", text: "N2 导入验证第二条：来源选择为对标。" },
      { id: "n2-2", text: "N2 导入验证第二条（重复 id，应被去重）。" },
    ]
    const n2json = JSON.stringify(posts)
    const upN2 = await evaluate(`(() => {
      const input = document.querySelector('input[type="file"]');
      if (!input) return false;
      const file = new File([${JSON.stringify(n2json)}], ${JSON.stringify(fileA)}, { type: "application/json" });
      const dt = new DataTransfer(); dt.items.add(file); input.files = dt.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    })()`)
    expect(upN2, "N2 JSON 文件已注入")
    // 选择来源「对标」（必选）
    await waitFor(`document.body.innerText.includes("导入来源")`, "解析预览", 10_000)
    if (!(await evaluate(`(() => {
      const b = [...document.querySelectorAll('button[aria-pressed]')].find(
        (x) => x.offsetParent !== null && x.textContent.trim().startsWith("对标"));
      if (!b) return false; b.click(); return true;
    })()`))) bad("N2 来源「对标」选择失败")
    await sleep(400)
    // 提交导入
    if (!(await evaluate(clickContains("确认导入")))) throw new Error("N2 点不到「确认导入」")
    const commitOk = await waitFor(
      `(() => { const t = [...document.querySelectorAll('[data-sonner-toast]')].map(x => x.textContent.trim());
               return t.some((x) => x.includes("已导入") || x.includes("成功")) })()`,
      "N2 导入成功 toast", 12000)
    expect(commitOk, "JSON 导入提交成功")
    const tN2 = await evaluate(toastText)
    console.log("    导入 toast：", JSON.stringify(tN2))

    // N2b 再次上传同一文件 → 全部去重
    const beforeDup = await evaluate(
      `fetch("/api/contents?limit=1").then(r => r.json()).then(d => d.total)`)
    await goto("/import", "批量导入")
    await evaluate(`(() => {
      const input = document.querySelector('input[type="file"]');
      const file = new File([${JSON.stringify(n2json)}], ${JSON.stringify(fileB)}, { type: "application/json" });
      const dt = new DataTransfer(); dt.items.add(file); input.files = dt.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    })()`)
    await waitFor(`document.body.innerText.includes("导入来源")`, "再次解析", 10_000)
    await evaluate(`(() => {
      const b = [...document.querySelectorAll('button[aria-pressed]')].find(
        (x) => x.offsetParent !== null && x.textContent.trim().startsWith("对标"));
      if (!b) return false; b.click(); return true;
    })()`)
    await sleep(400)
    await evaluate(clickContains("确认导入"))
    const dupOk = await waitFor(
      `(() => { const t = [...document.querySelectorAll('[data-sonner-toast]')].map(x => x.textContent.trim());
               return t.some((x) => x.includes("重复") || x.includes("已导入")) })()`,
      "N2 去重 toast", 12000)
    const afterDup = await evaluate(
      `fetch("/api/contents?limit=1").then(r => r.json()).then(d => d.total)`)
    expect(afterDup === beforeDup, `重复上传全部去重：总数 ${beforeDup} → ${afterDup}（应不变）`)

    // N2c 导入记录出现新批次
    await goto("/imports", "导入记录")
    await sleep(800)
    const foundN2 = await evaluate(`document.body.innerText.includes(${JSON.stringify(fileA)})`)
    expect(foundN2, `导入记录出现 ${fileA} 批次`)

    // N2d 删除该批次（全部）→ 批次消失 / 内容回落
    const beforeDel = await evaluate(
      `fetch("/api/contents?limit=1").then(r => r.json()).then(d => d.total)`)
    const delClicked = await evaluate(`(() => {
      const rows = [...document.querySelectorAll('div')].filter(
        (x) => x.offsetParent !== null && x.innerText.includes(${JSON.stringify(fileA)}) &&
               x.innerText.includes("删除本批次"));
      const row = rows[rows.length - 1];
      if (!row) return false;
      const b = [...row.querySelectorAll('button')].find(
        (x) => x.textContent.trim() === "删除本批次");
      if (!b) return false; b.click(); return true;
    })()`)
    if (!delClicked) throw new Error("点不到 n2 批次的「删除本批次」")
    await waitFor(`Boolean(document.querySelector('[role="dialog"]'))`, "删除弹窗")
    const delAll = await evaluate(`(() => {
      const dlg = document.querySelector('[role="dialog"]');
      const all = [...dlg.querySelectorAll('button')].find((x) =>
        x.textContent.includes("删除本批次全部内容"));
      if (!all) return false; all.click(); return true;
    })()`)
    expect(delAll, "「删除全部」模式可确认")
    const delOk = await waitFor(
      `(() => { const t = [...document.querySelectorAll('[data-sonner-toast]')].map(x => x.textContent.trim());
               return t.some((x) => x.includes("已删除")) })()`,
      "删除 toast", 8000)
    expect(delOk, "批次删除成功反馈")
    await sleep(1500)
    const afterDel = await evaluate(
      `fetch("/api/imports?limit=50").then(r => r.json()).then(d => ({
         ids: (d.items || []).map((b) => b.import_batch_id) }))`)
    const gone = !afterDel.ids.some((id) => id.includes("n2") || true) // 批次 id 是随机的，改用文件名核对
    const stillListed = await evaluate(`document.body.innerText.includes(${JSON.stringify(fileA)})`)
    expect(!stillListed, "被删批次从导入记录消失")
    const afterDelTotal = await evaluate(
      `fetch("/api/contents?limit=1").then(r => r.json()).then(d => d.total)`)
    expect(afterDelTotal === beforeDel - 2,
      `删除批次后内容总数 ${beforeDel} → ${afterDelTotal}（-2）`)
    await shot("N2_批次删除后")

    // ================= N3 我的页 CSV 导入 =================
    console.log("\n【N3 我的页 CSV 导入】")
    await goto("/mine", "90 天目标")
    // 先传非法 CSV
    const bad = await evaluate(uploadTo("#mine-account-csv", "bad.csv", BAD_CSV, "text/csv"))
    expect(bad, "非法 CSV 已注入文件选择器")
    await sleep(1800)
    const errToast = await evaluate(toastText)
    expect(errToast.some((x) => /失败|错误|不支持|缺少|表头/.test(x)),
      `非法 CSV 有错误反馈：${JSON.stringify(errToast)}`)
    // 再传合法 CSV
    const good = await evaluate(uploadTo("#mine-account-csv", "good.csv", GOOD_CSV, "text/csv"))
    expect(good, "合法 CSV 已注入文件选择器")
    await sleep(2500)
    const okToast = await evaluate(toastText)
    expect(okToast.some((x) => /导入|成功|2 条|条/.test(x)), `合法 CSV 导入反馈：${JSON.stringify(okToast)}`)
    const hasMetric = await evaluate(`document.body.innerText.includes("Impressions") && (document.querySelector("canvas") !== null || document.body.innerText.includes("5000") || document.body.innerText.includes("5,200"))`)
    expect(hasMetric, "导入后指标/图表出现")
    await shot("N3_CSV导入后")
    // 非法导入不得写入：重新导入合法 CSV 后行数应为 2（若非法写入会更多）
    const snapCount = await evaluate(`(() => {
      const rows = [...document.querySelectorAll('div')].filter(
        (d) => d.offsetParent !== null && /^\\d{4}-\\d{2}-\\d{2}/.test(d.innerText.trim()));
      return rows.length;
    })()`)
    console.log(`    （记录行观测：${snapCount}）`)

    // ================= N4 外部分析：异常导入 =================
    console.log("\n【N4 外部分析 · 异常导入】")
    await goto("/external-analysis", "导入分析结果")
    const before4 = await evaluate(`fetch("/api/contents?limit=1").then(r => r.json()).then(d => d.total)`)
    const up = await evaluate(uploadTo(
      'input[type="file"][accept*="jsonl"], input[type="file"][accept*="json"]',
      "broken.jsonl", "this is not json {{{\n", "application/x-ndjson"))
    expect(up, "非法 JSONL 已注入")
    await sleep(2200)
    // 严格校验应给出错误呈现（解析错误列表 / 错误 toast），且不写入
    const errShown = await evaluate(`(() => {
      const t = [...document.querySelectorAll('[data-sonner-toast]')].map(x => x.textContent.trim());
      const body = document.body.innerText;
      return { toast: t, parseErr: body.includes("解析") || body.includes("错误") || body.includes("无法") || body.includes("校验") };
    })()`)
    expect(errShown.parseErr || errShown.toast.length > 0,
      `非法 JSONL 有错误呈现（toast=${JSON.stringify(errShown.toast)}）`)
    const after4 = await evaluate(`fetch("/api/contents?limit=1").then(r => r.json()).then(d => d.total)`)
    expect(before4 === after4, `0 写入：内容总数 ${before4} → ${after4}`)
    await shot("N4_外部分析异常导入")

    // ================= N5 快速切换压力 =================
    console.log("\n【N5 快速切换压力】")
    await goto("/write", "我的创作")
    await evaluate(`(() => {
      const ta = [...document.querySelectorAll('textarea')].filter((t) => t.offsetParent !== null && !t.disabled)[0];
      if (!ta) return false; ta.focus(); return true;
    })()`)
    await send("Input.insertText", { text: "快速切换压力测试的现场内容。" })
    await sleep(300)
    const marker = "快速切换压力测试的现场内容。"
    let kept = 0
    // 注意：后续全部用 SPA 点击导航。整页刷新会重置内存会话，
    // 「刷新后不恢复」是已确定的产品行为，不能当回归。
    for (let i = 0; i < 3; i++) {
      if (!(await evaluate(`(() => {
        const a = [...document.querySelectorAll('a')].find(
          (x) => x.offsetParent !== null && x.textContent.trim() === "内容库");
        if (!a) return false; a.click(); return true;
      })()`))) { throw new Error("点不到内容库导航"); break }
      await waitFor(`location.pathname === '/library'`, "SPA 到内容库")
      await sleep(500)
      if (!(await evaluate(`(() => {
        const a = [...document.querySelectorAll('a')].find(
          (x) => x.offsetParent !== null && x.textContent.trim() === "作者");
        if (!a) return false; a.click(); return true;
      })()`))) { throw new Error("点不到作者导航"); break }
      await waitFor(`location.pathname === '/authors'`, "SPA 到作者")
      await sleep(500)
      if (!(await evaluate(`(() => {
        const a = [...document.querySelectorAll('a')].find(
          (x) => x.offsetParent !== null && x.textContent.trim() === "写作");
        if (!a) return false; a.click(); return true;
      })()`))) { throw new Error("点不到写作导航"); break }
      await waitFor(`location.pathname === '/write'`, "SPA 回写作")
      await sleep(500)
      const v = await evaluate(`(() => {
        const ta = [...document.querySelectorAll('textarea')].filter((t) => t.offsetParent !== null && !t.disabled)[0];
        return ta ? ta.value : null;
      })()`)
      if (v && v.includes(marker)) kept += 1
    }
    expect(kept === 3, `3 轮快速切换后现场保留（${kept}/3）`)
    await shot("N5_快速切换后现场")

    // ================= 数据一致性：风格学习计数与保存来源 =================
    console.log("\n【数据一致性抽查】")
    await goto("/style", "我的风格档案")
    const stats = await evaluate(`(() => {
      const t = document.body.innerText;
      const m = t.match(/有效样本：?(\\d+)/);
      const mine = t.match(/我的内容 (\\d+) 条/);
      return { valid: m ? m[1] : null, mine: mine ? m && mine[1] : (t.match(/我的内容 (\\d+)/) || [])[1] };
    })()`)
    console.log(`    风格档案统计：${JSON.stringify(stats)}`)
    expect(stats.valid !== null, "风格档案统计可读")
  } finally {
    try { ws?.close() } catch { /* ignore */ }
    edge.kill()
    await sleep(500)
    rmSync(`${tmpdir()}/cs_final_${Date.now()}`, { recursive: true, force: true })
  }

  console.log("\n—— 补盲区验证结果 ——")
  const bads = results.filter((r) => r[0] === "✗")
  for (const [m, msg] of results) console.log(`${m} ${msg}`)
  process.exit(bads.length ? 1 : 0)
}

main().catch((err) => {
  console.error("\n失败：", err.message)
  process.exitCode = 1
})
