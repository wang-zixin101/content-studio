/**
 * 任务 06 · 场景 D：创作与保存的**来源正确性**。
 *
 * 在浏览器里走完六种创作/保存方式，把行写进**预览库**（不是生产库），
 * 再由 `verify_task06_data_check.py` 直接查库断言：
 *
 *   source / provenance / style_eligible / ai_draft 基线 / final_text / edit_ratio
 *
 * 生成类请求同样用 CDP 拦截伪造，0 次真实模型调用。
 * 保存类请求（POST /contents、PUT /rewrites/{id}）放行走真实后端。
 *
 * 用法：node verify_task06_data.mjs
 */

import { spawn } from "node:child_process"
import { mkdirSync, writeFileSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import path from "node:path"

const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
const APP = "http://localhost:5199/write"
const PORT = 9336
const OUT = path.resolve("task06_screenshots")
const PROFILE = path.join(tmpdir(), `cs_t06d_${Date.now()}`)
const VIEW = { width: 1560, height: 1000 }

mkdirSync(OUT, { recursive: true })

let ws
let seq = 0
const pending = new Map()
const paused = []

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
    }, 30_000)
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
  const res = await send("Page.captureScreenshot", { format: "png" })
  writeFileSync(path.join(OUT, `${name}.png`), Buffer.from(res.data, "base64"))
  console.log("  截图 →", `${name}.png`)
}

const ok = (msg) => console.log("  ✓", msg)

const clickText = (text) => `
(() => {
  const hit = [...document.querySelectorAll('button')].reverse().find(
    (el) => el.textContent.trim() === ${JSON.stringify(text)} && el.offsetParent !== null
  );
  if (!hit) return false; hit.click(); return true;
})()`

const clickAria = (label) => `
(() => {
  const b = document.querySelector('button[aria-label=${JSON.stringify(label)}]');
  if (!b) return false; b.click(); return true;
})()`

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

const bodyHas = (t) => `document.body.innerText.includes(${JSON.stringify(t)})`

const ADOPTED_BASELINE = "<T06-BASELINE> 去年试了 17 个写作工具，最后只留 2 个。"
const ADOPTED_FINAL = "<T06-BASELINE> 去年试了 17 个写作工具，最后只留 2 个。（我又改了一版，这才是最终稿。）"

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
  await send("Fetch.enable", {
    patterns: [
      { urlPattern: "*/api/rewrites", requestStage: "Request" },
      { urlPattern: "*/api/rewrite-candidates", requestStage: "Request" },
    ],
  })

  const CANDIDATES = [
    { key: "A", label: "反差切入", text: "A 版正文（不会被采用）。", strategy_tags: [] },
    { key: "B", label: "结论前置", text: ADOPTED_BASELINE, strategy_tags: ["直接"] },
    { key: "C", label: "场景重构", text: "C 版正文（不会被采用）。", strategy_tags: [] },
  ]

  const pump = setInterval(async () => {
    if (!paused.length) return
    const ev = paused.shift()
    const url = ev.request.url
    try {
      if (ev.request.method === "POST" && /\/(rewrites|rewrite-candidates)$/.test(url)) {
        const payload = url.endsWith("rewrite-candidates")
          ? {
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
            }
          : {
              id: 900, benchmark_content_id: null,
              ai_draft: "（伪造成稿）先给结论。", final_text: null,
              is_saved_as_mine: false, saved_content_id: null, examples_used: [],
              style_profile_version: null, edit_distance: null, edit_ratio: null,
              prompt_version: "v3", personalization_state: "COLD_START",
              model: "stub-model",
              created_at: "2026-10-11T00:00:00Z", updated_at: "2026-10-11T00:00:00Z",
              personalization: null,
            }
        await send("Fetch.fulfillRequest", {
          requestId: ev.requestId, responseCode: 200,
          responseHeaders: [{ name: "Content-Type", value: "application/json" }],
          body: Buffer.from(JSON.stringify(payload)).toString("base64"),
        })
      } else {
        await send("Fetch.continueRequest", { requestId: ev.requestId })
      }
    } catch (err) {
      console.error("  拦截失败:", err.message)
    }
  }, 30)

  try {
    await send("Page.navigate", { url: APP })
    await sleep(1600)
    await waitFor(bodyHas("我的创作"), "写作页加载")
    await evaluate(`window.localStorage.clear(); window.sessionStorage.clear(); true`)
    await send("Page.reload")
    await sleep(1500)
    await waitFor(bodyHas("我的创作"), "写作页重载")

    // ---------- ① 手写原创 → 成稿 ----------
    console.log("\n① 手写原创成稿")
    await typeInto(0, "【T06手写成稿】把决策提前固化，执行会变轻。")
    await sleep(300)
    const saveAsDefault = await evaluate(`
      (() => {
        const btns = [...document.querySelectorAll('button')].filter(
          (b) => ['成稿','灵感'].includes(b.textContent.trim()) && b.offsetParent !== null
        );
        return btns.map((b) => ({ t: b.textContent.trim(), on: b.getAttribute('aria-pressed') === 'true' }));
      })()`)
    if (!saveAsDefault.find((x) => x.t === "成稿")?.on) {
      throw new Error(`默认应为「成稿」，实际 ${JSON.stringify(saveAsDefault)}`)
    }
    ok("默认保存身份是「成稿」")
    await evaluate(clickText("保存原文"))
    await waitFor(bodyHas("成稿已收录"), "成稿保存成功")
    ok("手写原创已按成稿保存")
    await shot("06_场景D_手写成稿已保存")

    // ---------- ② 个人灵感 ----------
    console.log("\n② 个人灵感")
    await sleep(400)
    await typeInto(0, "【T06灵感】只是一个还没成形的念头，先记下来。")
    await sleep(300)
    await evaluate(clickText("灵感"))
    await sleep(200)
    await evaluate(clickText("保存原文"))
    await waitFor(bodyHas("灵感已收录"), "灵感保存成功")
    ok("个人灵感已保存（不参与风格学习）")

    // ---------- ③ 主题生成（topic） ----------
    console.log("\n③ 主题生成")
    await typeInto(0, "【T06主题生成】想写一条关于「别急着买订阅」的内容。")
    await sleep(300)
    await evaluate(clickText("生成初稿"))
    await waitFor(`!document.body.innerText.includes('还没有初稿')`, "单稿生成")
    await sleep(300)
    ok("主题生成（无作者）走通")

    // ---------- ④ 采用候选 → 编辑 → 保存为我的 ----------
    console.log("\n④ 对标三候选 → 采用 B → 编辑 → 保存（ai_assisted）")
    await evaluate(clickAria("清空当前创作"))
    await sleep(500)
    await evaluate(clickText("对标改写"))
    await waitFor(bodyHas("指定素材"), "进入对标改写")
    await sleep(300)
    await typeInto(0, "【T06对标原帖】这是一条对标账号的帖子原文，用于改写。")
    await sleep(300)
    await evaluate(clickText("生成 3 个候选"))
    await waitFor(bodyHas("A · 反差切入"), "三候选生成")
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
    await waitFor(bodyHas("已采用"), "已采用 B")
    await sleep(400)
    // 编辑：把正文换成带唯一标记的终稿
    await evaluate(`
      (() => {
        const ta = [...document.querySelectorAll('textarea')]
          .filter((t) => t.offsetParent !== null && !t.disabled).pop();
        if (!ta) return false;
        ta.focus();
        ta.setSelectionRange(0, ta.value.length);
        return true;
      })()`)
    await send("Input.insertText", { text: ADOPTED_FINAL })
    await sleep(400)
    const editedShown = await evaluate(
      `[...document.querySelectorAll('textarea')].some((t) => t.value.includes('我又改了一版'))`
    )
    if (!editedShown) throw new Error("编辑没有生效")
    ok("已在采用稿上人工编辑")
    await shot("07_场景D_采用后编辑")

    await evaluate(clickText("保存为我的"))
    await waitFor(bodyHas("已保存到我的内容"), "AI 稿保存成功")
    ok("AI 采用稿已保存（应为 ai_assisted）")
    await shot("08_场景D_AI稿已保存")

    // ---------- ⑤ 对标原帖收录 ----------
    console.log("\n⑤ 对标原帖收录")
    await evaluate(clickAria("清空当前创作"))
    await sleep(500)
    await typeInto(0, "【T06对标收录】这条只收录，不生成。")
    await sleep(300)
    await evaluate(clickText("仅收录"))
    await waitFor(bodyHas("对标内容已收录"), "对标收录成功")
    ok("对标原帖已收录（source=benchmark）")

    console.log("\n浏览器侧保存流程全部走通")
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
