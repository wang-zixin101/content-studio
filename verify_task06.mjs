/**
 * 任务 06 验收：页面整合 + 状态保留 + × 清空 + 数据正确性。
 *
 * ------------------------------------------------------------------
 * 为什么不用真实 AI
 * ------------------------------------------------------------------
 * 场景 A/B/D 都需要「生成」。这里用 CDP 的 Fetch 域把
 * POST /api/rewrites 与 POST /api/rewrite-candidates **拦下来直接伪造响应**，
 * 于是：
 *   - 前端走的是完全真实的流程（真实状态机、真实渲染）；
 *   - 0 次真实模型调用；
 *   - 而且能顺便断言**请求体**（creation_path / reference_author_id），
 *     这比看返回更有价值。
 * 其余请求（pools / random / adopt / 保存）全部放行走真实后端（预览库，0 AI）。
 *
 * 前置：后端 8000（预览库）+ vite 5199
 * 用法：node verify_task06.mjs
 */

import { spawn } from "node:child_process"
import { mkdirSync, writeFileSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import path from "node:path"

const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
const APP = "http://localhost:5199/write"
const PORT = 9335
const OUT = path.resolve("task06_screenshots")
const PROFILE = path.join(tmpdir(), `cs_t06_${Date.now()}`)
const VIEW = { width: 1560, height: 1000 }

mkdirSync(OUT, { recursive: true })

let ws
let seq = 0
const pending = new Map()
const paused = []
/** 被拦下的生成请求体（供断言） */
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
    }, 30_000)
  })
}

async function evaluate(expression) {
  const res = await send("Runtime.evaluate", {
    expression,
    returnByValue: true,
    awaitPromise: true,
  })
  if (res.exceptionDetails) {
    throw new Error(
      `页面内异常: ${res.exceptionDetails.exception?.description ?? "?"}`
    )
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

// ---------------------------------------------------------------- 页面操作

const clickText = (text, tag = "button") => `
(() => {
  const hit = [...document.querySelectorAll('${tag}')].reverse().find(
    (el) => el.textContent.trim() === ${JSON.stringify(text)} && el.offsetParent !== null
  );
  if (!hit) return false; hit.click(); return true;
})()`

const clickContains = (text, tag = "button") => `
(() => {
  const hit = [...document.querySelectorAll('${tag}')].reverse().find(
    (el) => el.textContent.includes(${JSON.stringify(text)}) && el.offsetParent !== null
  );
  if (!hit) return false; hit.click(); return true;
})()`

/**
 * 聚焦第 n 个可见 textarea 并把光标放到末尾。
 *
 * 为什么不用「native setter + dispatchEvent('input')」那一招：
 * 实测它对 React 18 的受控 textarea **不生效** —— DOM 值变了但 store 没变，
 * 下一次重渲染就把输入擦掉了（表现为「刚输入的主题没了、路径变成 author_auto」）。
 * 改用 CDP 的 Input.insertText：那是真实输入，onChange 一定触发。
 */
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

/** 第一个可见 textarea 的当前值（左输入区 / 右编辑区要分别取）。 */
const textareas = `
[...document.querySelectorAll('textarea')]
  .filter((t) => t.offsetParent !== null)
  .map((t) => t.value)`

const bodyHas = (text) => `document.body.innerText.includes(${JSON.stringify(text)})`

/**
 * textarea 的值**不在** innerText 里（它是表单控件的 value，不是文本节点），
 * 所以凡是要断言「输入 / 编辑稿还在」的地方，都必须直接读 value。
 */
const textareaHas = (text) => `
[...document.querySelectorAll('textarea')]
  .some((t) => t.value.includes(${JSON.stringify(text)}))`

async function gotoLibraryAndBack() {
  await evaluate(clickText("内容库", "a"))
  await waitFor(`document.body.innerText.includes('内容库')`, "进入内容库")
  await sleep(500)
  await evaluate(clickText("写作", "a"))
  await waitFor(`document.body.innerText.includes('我的创作')`, "回到写作页")
  await sleep(600)
}

// ---------------------------------------------------------------- 主流程

async function main() {
  console.log("启动 headless Edge…")
  const edge = spawn(
    EDGE,
    [
      "--headless=new",
      "--disable-gpu",
      "--no-first-run",
      "--no-default-browser-check",
      `--remote-debugging-port=${PORT}`,
      `--user-data-dir=${PROFILE}`,
      `--window-size=${VIEW.width},${VIEW.height}`,
      "about:blank",
    ],
    { stdio: "ignore" }
  )

  let targets = null
  for (let i = 0; i < 60; i++) {
    try {
      targets = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()
      if (targets.some((t) => t.type === "page")) break
    } catch {
      /* 等 */
    }
    await sleep(250)
  }
  if (!targets) throw new Error("Edge devtools 未就绪")
  const page = targets.find((t) => t.type === "page")

  ws = new WebSocket(page.webSocketDebuggerUrl)
  ws.addEventListener("message", async (ev) => {
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
    width: VIEW.width,
    height: VIEW.height,
    deviceScaleFactor: 2,
    mobile: false,
  })

  // ---------------- 拦截「生成」类请求，伪造成响应 ----------------
  await send("Fetch.enable", {
    patterns: [
      { urlPattern: "*/api/rewrites", requestStage: "Request" },
      { urlPattern: "*/api/rewrite-candidates", requestStage: "Request" },
    ],
  })

  const CANDIDATES = [
    { key: "A", label: "反差切入", text: "别信「功能最强」这一说。\n\n我去年删掉的工具里，有三个是榜单第一。", strategy_tags: ["直接陈述"] },
    { key: "B", label: "结论前置", text: "去年试了 17 个写作工具，最后只留 2 个。\n\n不是因为它们最强。", strategy_tags: ["明确肯定"] },
    { key: "C", label: "场景重构", text: "前几天有人问我哪个工具最强。\n\n我没推荐，只反问了一句：你最卡在哪一步？", strategy_tags: ["观点—理由"] },
  ]

  const pump = setInterval(async () => {
    if (!paused.length) return
    const ev = paused.shift()
    const url = ev.request.url
    const method = ev.request.method
    try {
      if (method === "POST" && /\/(rewrites|rewrite-candidates)$/.test(url)) {
        genRequests.push({
          url: url.split("/api/")[1],
          body: JSON.parse(ev.request.postData || "{}"),
        })
        const payload = url.endsWith("rewrite-candidates")
          ? {
              source_content_id: null,
              reference_author_id: null,
              creation_path: "stub",
              reference_profile_used: false,
              candidates: CANDIDATES,
              context: {
                source_analysis: null, writing_count: 0,
                performance_high: [], performance_low: [],
                cross_high: [], cross_low: [], account_dependent: [],
                skipped: [], budget: null,
              },
              context_empty: true,
              prompt_version: "v3",
              model: "stub-model",
              personalization: null,
            }
          : {
              id: 900,
              benchmark_content_id: null,
              ai_draft: "（伪造成稿）先给结论：工具选不对，多半不是工具的问题。",
              final_text: null,
              is_saved_as_mine: false,
              saved_content_id: null,
              examples_used: [],
              style_profile_version: null,
              edit_distance: null,
              edit_ratio: null,
              prompt_version: "v3",
              personalization_state: "COLD_START",
              model: "stub-model",
              created_at: "2026-10-11T00:00:00Z",
              updated_at: "2026-10-11T00:00:00Z",
              personalization: null,
            }
        await send("Fetch.fulfillRequest", {
          requestId: ev.requestId,
          responseCode: 200,
          responseHeaders: [
            { name: "Content-Type", value: "application/json" },
          ],
          body: Buffer.from(JSON.stringify(payload)).toString("base64"),
        })
      } else {
        await send("Fetch.continueRequest", { requestId: ev.requestId })
      }
    } catch (err) {
      console.error("  拦截处理失败:", err.message)
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

    // ============================================================
    // 场景 E：页面控件
    // ============================================================
    console.log("\n场景 E：页面控件")
    const tabs = await evaluate(`
      [...document.querySelectorAll('button')]
        .filter((b) => ['我的创作','对标改写','灵感起稿','成稿收录','作者起稿'].includes(b.textContent.trim()))
        .map((b) => b.textContent.trim())`)
    if (JSON.stringify(tabs) !== JSON.stringify(["我的创作", "对标改写"])) {
      throw new Error(`一级入口应只有两个，实际：${JSON.stringify(tabs)}`)
    }
    ok("一级入口只有「我的创作 / 对标改写」")

    // 注意：全局导航里本来就有一个指向 /library 的「内容库」链接，
    // 所以断言必须**限定在写作页顶栏内**，不能全页搜 a[href="/library"]。
    const hasBackArrow = await evaluate(`
      (() => {
        const h1 = [...document.querySelectorAll('h1')]
          .find((e) => e.textContent.trim() === '写作');
        if (!h1) return 'no-h1';
        // h1 在 <div class="min-w-0 flex-1"> 里，再上一层就是顶栏
        const bar = h1.parentElement && h1.parentElement.parentElement;
        if (!bar) return 'no-bar';
        return Boolean(bar.querySelector('a[href="/library"]'));
      })()`)
    if (hasBackArrow !== false) {
      throw new Error(`左上角「返回内容库」箭头还在（探测结果=${hasBackArrow}）`)
    }
    ok("左上角返回箭头已删除（顶栏内无 /library 链接）")

    const emptyStateBtn = await evaluate(`
      (() => {
        const t = document.body.innerText;
        // 右侧空状态不该再出现「生成初稿」按钮（左侧动作条上是允许的）
        const btns = [...document.querySelectorAll('button')]
          .filter((b) => b.textContent.trim() === '生成初稿' && b.offsetParent !== null);
        return { count: btns.length, emptyHint: t.includes('还没有初稿') };
      })()`)
    if (emptyStateBtn.count !== 1) {
      throw new Error(`「生成初稿」应只出现在左侧动作条上，实际 ${emptyStateBtn.count} 个`)
    }
    if (!emptyStateBtn.emptyHint) throw new Error("右侧空状态文案缺失")
    ok("右侧空状态不再重复放「生成初稿」，只保留左侧动作条上那一个")
    await shot("01_我的创作_两个入口")

    // ============================================================
    // 场景 A：离开再回来
    // ============================================================
    console.log("\n场景 A：我的创作 → 输入 + 选作者 + 生成 + 改一半 → 离开再回来")
    await typeInto(0, "写一条关于「AI 工具选不对其实是问题没定义清楚」的内容。")

    // 选参考作者
    if (!(await evaluate(clickText("指定其他作者")))) {
      throw new Error("找不到「指定其他作者」")
    }
    await waitFor(`Boolean(document.querySelector('[role="dialog"]'))`, "作者弹窗")
    await sleep(400)
    await evaluate(`
      (() => {
        const dlg = document.querySelector('[role="dialog"]');
        const b = [...dlg.querySelectorAll('button')].find((x) => x.textContent.includes('王长富'));
        if (!b) return false; b.click(); return true;
      })()`)
    await waitFor(bodyHas("使用作者写作画像"), "参考作者已选中")
    ok("已选定参考作者 王长富")

    const pathBefore = await evaluate(`
      (() => {
        const b = [...document.querySelectorAll('button')].find(
          (x) => x.textContent.trim() === '生成初稿'
        );
        return b ? !b.disabled : null;
      })()`)
    if (pathBefore !== true) throw new Error("生成初稿按钮不可用")

    genRequests.length = 0
    await evaluate(clickText("生成初稿"))
    await waitFor(`!document.body.innerText.includes('还没有初稿')`, "初稿生成")
    await sleep(400)

    // 改一半：在右侧编辑区（最后一个 textarea）末尾追加 + 改开头
    await typeInto(-1, " —— 我又加了一句：先定义问题，再选工具。")

    const before = await evaluate(textareas)
    console.log("  离开前 textarea:", JSON.stringify(before))

    // 断言场景 D 的「主题 + 作者」路径
    const reqTopicAuthor = genRequests.find((r) => r.url === "rewrites")
    if (reqTopicAuthor?.body.creation_path !== "topic_author") {
      throw new Error(`主题+作者应为 topic_author，实际 ${reqTopicAuthor?.body.creation_path}`)
    }
    if (reqTopicAuthor.body.reference_author_id == null) {
      throw new Error("请求没有带上参考作者 id")
    }
    ok(`主题+作者：creation_path=topic_author，reference_author_id=${reqTopicAuthor.body.reference_author_id}`)

    await gotoLibraryAndBack()

    const after = await evaluate(textareas)
    console.log("  回来之后 textarea:", JSON.stringify(after))
    if (JSON.stringify(before) !== JSON.stringify(after)) {
      throw new Error("离开再回来后输入/编辑稿没有原样保留")
    }
    if (!(await evaluate(bodyHas("王长富")))) {
      throw new Error("回来后参考作者丢了")
    }
    const stillEdited = await evaluate(textareaHas("我又加了一句"))
    if (!stillEdited) throw new Error("回来后未保存的编辑内容丢了")
    ok("主题、参考作者、初稿与未保存的修改全部原样保留")
    await shot("02_场景A_回来之后")

    // ============================================================
    // 场景 D（上半）：仅参考作者 → AI 自主选题
    // ============================================================
    console.log("\n场景 D：仅参考作者，AI 自主选题")
    // × 按钮没有文字，用 aria-label 点
    await evaluate(`
      (() => {
        const b = document.querySelector('button[aria-label="清空当前创作"]');
        if (!b) return false; b.click(); return true;
      })()`)
    await sleep(500)
    const cleared = await evaluate(textareas)
    if (cleared.some((t) => t !== "")) {
      throw new Error(`× 之后仍有残留：${JSON.stringify(cleared)}`)
    }
    ok("× 清空：输入与编辑稿已清空（场景 C 一半）")

    // × 会把参考作者一起清掉（它属于「临时选择」），所以这里重新选一位
    if (!(await evaluate(clickText("指定其他作者")))) {
      throw new Error("找不到「指定其他作者」")
    }
    await waitFor(`Boolean(document.querySelector('[role="dialog"]'))`, "作者弹窗")
    await sleep(400)
    await evaluate(`
      (() => {
        const dlg = document.querySelector('[role="dialog"]');
        const b = [...dlg.querySelectorAll('button')].find((x) => x.textContent.includes('老雷'));
        if (!b) return false; b.click(); return true;
      })()`)
    await waitFor(bodyHas("使用作者写作画像"), "重新选定参考作者")
    ok("× 之后重新选定参考作者 老雷（× 会清掉参考作者这一临时选择）")

    // 只选参考作者（不输入任何文字）
    genRequests.length = 0
    const autoBtnEnabled = await evaluate(`
      (() => {
        const b = [...document.querySelectorAll('button')].find(
          (x) => x.textContent.trim() === '生成初稿'
        );
        return b ? !b.disabled : null;
      })()`)
    if (autoBtnEnabled !== true) {
      throw new Error("只选参考作者时「生成初稿」应可用（author_auto）")
    }
    await evaluate(clickText("生成初稿"))
    await waitFor(`!document.body.innerText.includes('还没有初稿')`, "自主选题初稿")
    await sleep(300)
    const reqAuto = genRequests.find((r) => r.url === "rewrites")
    if (reqAuto?.body.creation_path !== "author_auto") {
      throw new Error(`仅作者应为 author_auto，实际 ${reqAuto?.body.creation_path}`)
    }
    if (reqAuto.body.source_text !== undefined) {
      throw new Error("author_auto 不该带 source_text")
    }
    ok(`仅参考作者：creation_path=author_auto，且没有 source_text`)

    // ============================================================
    // 场景 B：对标创作保留
    // ============================================================
    console.log("\n场景 B：对标改写 → 抽素材 → 三候选 → 采用 B 并编辑 → 离开再回来")
    await evaluate(clickText("对标改写"))
    await waitFor(bodyHas("指定素材"), "进入对标改写")
    await sleep(400)
    await evaluate(clickText("随机素材"))
    await waitFor(bodyHas("抽取范围"), "随机素材面板")
    await sleep(300)
    await evaluate(`
      (() => {
        const b = [...document.querySelectorAll('button')].reverse().find(
          (el) => el.textContent.trim() === '随机抽取' && el.offsetParent !== null
        );
        if (!b) return false; b.click(); return true;
      })()`)
    await waitFor(bodyHas("使用这条"), "抽到素材")
    await evaluate(clickText("使用这条"))
    await waitFor(bodyHas("已选素材"), "素材已确定")
    await sleep(300)

    genRequests.length = 0
    await evaluate(clickText("生成 3 个候选"))
    await waitFor(bodyHas("反差切入"), "三候选生成")
    await sleep(300)
    const reqRewrite = genRequests.find((r) => r.url === "rewrite-candidates")
    if (reqRewrite?.body.creation_path !== "rewrite") {
      throw new Error(`原帖改写应为 rewrite，实际 ${reqRewrite?.body.creation_path}`)
    }
    if (typeof reqRewrite.body.source_content_id !== "number") {
      throw new Error("原帖改写必须带 source_content_id")
    }
    ok(`原帖改写：creation_path=rewrite，source_content_id=${reqRewrite.body.source_content_id}`)

    // 采用 B 并编辑
    await evaluate(`
      (() => {
        const sec = [...document.querySelectorAll('section')].find(
          (s) => s.innerText.startsWith('B · 结论前置')
        );
        if (!sec) return false;
        const b = [...sec.querySelectorAll('button')].find(
          (x) => x.textContent.trim() === '采用'
        );
        if (!b) return false; b.click(); return true;
      })()`)
    await waitFor(bodyHas("已采用"), "已采用 B")
    await sleep(400)
    await typeInto(-1, " （我加的最后一段：这才是重点。）")

    const bBefore = await evaluate(textareas)
    const bCands = await evaluate(`
      [...document.querySelectorAll('section')].map((s) => s.innerText.slice(0, 8)).join('|')`)
    console.log("  离开前：", JSON.stringify(bBefore), "候选:", bCands)
    await shot("03_场景B_离开前")

    await gotoLibraryAndBack()

    const bAfter = await evaluate(textareas)
    if (JSON.stringify(bBefore) !== JSON.stringify(bAfter)) {
      throw new Error("场景 B：回来后编辑稿没保留")
    }
    if (!(await evaluate(bodyHas("已采用")))) {
      throw new Error("场景 B：回来后已采用状态丢了")
    }
    if (!(await evaluate(textareaHas("我加的最后一段")))) {
      throw new Error("场景 B：回来后未保存的人工修改丢了")
    }
    // 候选本身也要还在（点「返回候选」能看到三张卡）
    await evaluate(clickText("返回候选"))
    await sleep(400)
    const cards = await evaluate(`
      ['反差切入','结论前置','场景重构'].filter((t) => document.body.innerText.includes(t)).length`)
    if (cards !== 3) throw new Error(`场景 B：回来后候选只剩 ${cards} 张`)
    ok("原帖、三候选、已采用版本与未保存编辑稿全部保留")
    await shot("04_场景B_回来之后")

    // ============================================================
    // 场景 C：× 清空（对标侧）
    // ============================================================
    console.log("\n场景 C：× 清空")
    await evaluate(`
      (() => {
        const b = document.querySelector('button[aria-label="清空当前创作"]');
        if (!b) return false; b.click(); return true;
      })()`)
    await sleep(600)
    const afterClear = await evaluate(textareas)
    if (afterClear.length && afterClear.some((t) => t !== "")) {
      throw new Error(`× 之后 textarea 仍有内容：${JSON.stringify(afterClear)}`)
    }
    // 注意：不能只搜「反差切入」——候选区空状态本来就会列出三个角色名，
    // 那是说明文字不是候选卡。用卡片标题「A · 反差切入」判定。
    if (await evaluate(bodyHas("A · 反差切入"))) {
      throw new Error("× 之后候选卡片没有清掉")
    }
    if (!(await evaluate(bodyHas("生成后会在这里比较")))) {
      throw new Error("× 之后候选区没有回到空状态")
    }
    if (await evaluate(bodyHas("已选素材"))) {
      throw new Error("× 之后素材没有清掉")
    }
    if (!(await evaluate(bodyHas("对标改写")))) {
      throw new Error("× 之后入口不应该被改变")
    }
    ok("× 清空：输入、候选、素材、编辑稿全部清空，入口保持不变")
    await shot("05_场景C_清空之后")

    console.log("\n浏览器侧断言全部通过")
  } finally {
    clearInterval(pump)
    try {
      ws?.close()
    } catch {
      /* ignore */
    }
    edge.kill()
    await sleep(500)
    rmSync(PROFILE, { recursive: true, force: true })
  }
}

main().catch((err) => {
  console.error("\n失败：", err.message)
  process.exitCode = 1
})
