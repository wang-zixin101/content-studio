import { test } from "node:test"
import assert from "node:assert/strict"

import {
  clearWriteSession,
  getWriteSession,
  initialWriteSession,
  isSameEpoch,
  isFreshMaterialOpen,
  isWriteSessionPristine,
  markMaterialOpenConsumed,
  markSourceHandled,
  newMaterialOpenTicket,
  resetConsumedTickets,
  resetHandledSources,
  resetWriteSession,
  sessionEpoch,
  setPickerField,
  setWriteField,
  shouldLoadSource,
  subscribeWriteSession,
} from "./write-session.ts"

/** 每个用例从干净现场开始（单例是模块级，用例之间必须显式复位）。 */
function fresh(entry: "create" | "benchmark" = "create") {
  clearWriteSession(entry)
}

// ---------------- 初始形态 ----------------

test("06-S1 初始现场：默认入口「我的创作」，什么都没有", () => {
  const s = initialWriteSession()
  assert.equal(s.entry, "create")
  assert.equal(s.sourceText, "")
  assert.equal(s.draft, "")
  assert.equal(s.aiDraft, "")
  assert.equal(s.rewrite, null)
  assert.equal(s.candidates, null)
  assert.equal(s.adoptedKey, null)
  assert.equal(s.sourceContentId, null)
  assert.equal(s.useProfile, true, "选了参考作者默认启用画像")
  assert.equal(s.refMode, "none")
  assert.equal(s.saveAs, "final")
  assert.equal(s.epoch, 0)
  assert.deepEqual(s.picker, {
    pool: "all",
    poolAuthor: null,
    unusedFirst: true,
    material: null,
    seen: [],
  })
})

test("06-S2 初始现场里没有「在途 / 弹窗」这类瞬时字段", () => {
  const keys = Object.keys(initialWriteSession())
  for (const transient of [
    "generating",
    "saving",
    "loading",
    "generatingCandidates",
    "authorSelectorOpen",
    "refSelectorOpen",
  ]) {
    assert.ok(!keys.includes(transient), `${transient} 不该进会话`)
  }
})

// ---------------- 写入与订阅 ----------------

test("06-S3 写字段会换成新引用（useSyncExternalStore 靠它判等）", () => {
  fresh()
  const before = getWriteSession()
  setWriteField("sourceText", "写点东西")
  const after = getWriteSession()
  assert.notEqual(after, before, "必须整体替换引用")
  assert.equal(after.sourceText, "写点东西")
  assert.equal(after.draft, "", "其它字段不受影响")
})

test("06-S4 写入相同值不触发通知（避免无意义重渲染）", () => {
  fresh()
  let hits = 0
  const off = subscribeWriteSession(() => {
    hits += 1
  })
  setWriteField("draft", "同一份稿子")
  setWriteField("draft", "同一份稿子")
  off()
  assert.equal(hits, 1)
})

test("06-S5 退订后不再收到通知", () => {
  fresh()
  let hits = 0
  const off = subscribeWriteSession(() => {
    hits += 1
  })
  off()
  setWriteField("draft", "x")
  assert.equal(hits, 0)
})

test("06-S6 picker 字段存在 picker 下面", () => {
  fresh()
  setPickerField("pool", "author")
  setPickerField("poolAuthor", 7)
  const s = getWriteSession()
  assert.equal(s.picker.pool, "author")
  assert.equal(s.picker.poolAuthor, 7)
  assert.equal(s.entry, "create", "picker 写入不影响入口")
})

// ---------------- 场景 A/B：离开再回来 ----------------

test("06-S7 模拟「切到内容库再回来」：现场原样保留", () => {
  fresh()
  // 用户在我的创作里干活
  setWriteField("sourceText", "关于 AI 工具的一个想法")
  setWriteField("refMode", "fixed")
  setWriteField("fixedAuthorId", 3)
  setWriteField("useProfile", true)
  setWriteField("rewrite", {
    id: 11,
    benchmark_content_id: null,
    ai_draft: "AI 初稿",
    final_text: null,
    is_saved_as_mine: false,
    saved_content_id: null,
    examples_used: [],
    style_profile_version: null,
    edit_distance: null,
    edit_ratio: null,
    prompt_version: "v3",
    personalization_state: "COLD_START",
    model: "mimo-v2.6-pro",
    created_at: "2026-10-11T00:00:00Z",
    updated_at: "2026-10-11T00:00:00Z",
  })
  setWriteField("draft", "AI 初稿改了一半")
  setWriteField("aiDraft", "AI 初稿")

  // 组件卸载再挂载 —— 单例是模块级，不会被卸载影响
  const s = getWriteSession()
  assert.equal(s.sourceText, "关于 AI 工具的一个想法")
  assert.equal(s.refMode, "fixed")
  assert.equal(s.fixedAuthorId, 3)
  assert.equal(s.draft, "AI 初稿改了一半")
  assert.equal(s.aiDraft, "AI 初稿")
  assert.equal(s.rewrite?.id, 11)
})

test("06-S8 模拟「对标三候选保存现场」：候选与已采用都还在", () => {
  fresh("benchmark")
  setWriteField("sourceContentId", 42)
  setWriteField("sourceAuthorLabel", "王长富")
  setWriteField("candidates", [
    { key: "A", label: "反差切入", text: "A 正文", strategy_tags: [] },
    { key: "B", label: "结论前置", text: "B 正文", strategy_tags: ["直接"] },
    { key: "C", label: "场景重构", text: "C 正文", strategy_tags: [] },
  ])
  setWriteField("adoptedKey", "B")
  setWriteField("draft", "B 正文改了一半")
  setPickerField("pool", "author")
  setPickerField("poolAuthor", 1)
  setPickerField("seen", [1, 2, 3])

  const s = getWriteSession()
  assert.equal(s.entry, "benchmark")
  assert.equal(s.candidates?.length, 3)
  assert.equal(s.adoptedKey, "B")
  assert.equal(s.draft, "B 正文改了一半")
  assert.equal(s.sourceContentId, 42)
  assert.equal(s.picker.poolAuthor, 1)
  assert.deepEqual(s.picker.seen, [1, 2, 3])
})

// ---------------- 场景 C：× 清空 ----------------

test("06-S9 × 清空：回到当前入口的空白初始态，入口不变", () => {
  fresh("benchmark")
  setWriteField("sourceText", "一条原帖")
  setWriteField("sourceContentId", 42)
  setWriteField("candidates", [
    { key: "A", label: "反差切入", text: "A", strategy_tags: [] },
  ])
  setWriteField("adoptedKey", "A")
  setWriteField("draft", "改了一半")
  setPickerField("pool", "author")
  setPickerField("material", {
    content_id: 9,
    raw_text: "抽到的素材",
    author_id: 1,
    author_username: "a",
    author_display_name: "A",
    posted_at: null,
    view_count: null,
    media_type: null,
    fallback_used: false,
  })

  resetWriteSession()

  const s = getWriteSession()
  assert.equal(s.entry, "benchmark", "清空不改变当前入口")
  assert.equal(s.sourceText, "")
  assert.equal(s.draft, "")
  assert.equal(s.aiDraft, "")
  assert.equal(s.rewrite, null)
  assert.equal(s.candidates, null)
  assert.equal(s.adoptedKey, null)
  assert.equal(s.sourceContentId, null)
  assert.equal(s.saved, false)
  assert.deepEqual(s.picker, initialWriteSession().picker, "随机素材现场一起清")
})

test("06-S10 × 清空会推进代次：迟到的响应必须能被识别为过期", () => {
  fresh()
  setWriteField("draft", "生成中…")
  const oldEpoch = sessionEpoch()
  assert.equal(isSameEpoch(oldEpoch), true)

  resetWriteSession()

  assert.equal(
    isSameEpoch(oldEpoch),
    false,
    "旧代次的响应必须被判为过期，不能把清掉的内容写回来"
  )
  assert.equal(isSameEpoch(sessionEpoch()), true)
})

test("06-S11 代次只增不减（不重置为 0，避免旧请求蒙对）", () => {
  fresh()
  const e0 = sessionEpoch()
  resetWriteSession()
  const e1 = sessionEpoch()
  resetWriteSession()
  const e2 = sessionEpoch()
  assert.ok(e1 > e0 && e2 > e1, `${e0} < ${e1} < ${e2}`)
})

// ---------------- 切入口 ----------------

test("06-S12 切入口：清空现场并换到新入口", () => {
  fresh("create")
  setWriteField("sourceText", "我的想法")
  setWriteField("draft", "半成品")

  clearWriteSession("benchmark")

  const s = getWriteSession()
  assert.equal(s.entry, "benchmark")
  assert.equal(s.sourceText, "")
  assert.equal(s.draft, "")
})

test("06-S13 切入口同样推进代次（在途请求一并作废）", () => {
  fresh("create")
  const before = sessionEpoch()
  clearWriteSession("benchmark")
  assert.equal(isSameEpoch(before), false)
})

test("06-S14 保存身份：成稿 / 灵感可切换且跨页面保留", () => {
  fresh()
  assert.equal(getWriteSession().saveAs, "final")
  setWriteField("saveAs", "idea")
  assert.equal(getWriteSession().saveAs, "idea")
  // 切走再回来（单例不变）
  assert.equal(getWriteSession().saveAs, "idea")
  // × 清空后回到默认的「成稿」
  resetWriteSession()
  assert.equal(getWriteSession().saveAs, "final")
})

test("06-S15 pristine 判定：只有 picker 有状态时也不算空", () => {
  fresh()
  assert.equal(isWriteSessionPristine(), true, "全新现场应为空")
  setPickerField("pool", "author")
  assert.equal(isWriteSessionPristine(), false, "只换了范围也算做过事")
  fresh()
  setPickerField("seen", [1])
  assert.equal(isWriteSessionPristine(), false, "只抽过一条也算做过事")
})

test("06-S16 pristine 判定：入口与代次不算「做过事」", () => {
  clearWriteSession("benchmark")
  assert.equal(
    isWriteSessionPristine(),
    true,
    "切入口本身不该让 × 变成非空"
  )
})

test("06-S17 pristine 判定：任一创作字段有值即非空", () => {
  for (const [key, value] of [
    ["sourceText", "x"],
    ["draft", "x"],
    ["aiDraft", "x"],
    ["sourceContentId", 1],
    ["adoptedKey", "A"],
    ["saved", true],
    ["saveAs", "idea"],
    ["sourceUrl", "https://x.com/a"],
  ] as const) {
    fresh()
    assert.equal(isWriteSessionPristine(), true)
    setWriteField(key, value as never)
    assert.equal(isWriteSessionPristine(), false, `${key} 非空时应判为非空`)
  }
})

// ---------------- 任务 06 验收修复：URL 素材簿记（问题 1 / 2）----------------

test("06-F1 首次打开 /write/123：需要拉取", () => {
  resetHandledSources()
  assert.equal(shouldLoadSource(123), true)
})

test("06-F2 问题 1 复现：已预填过再回到同一地址 → 不拉取（也就不会卡转圈）", () => {
  resetHandledSources()
  // 第一次打开 /write/123 → 拉取 → 标记已处理
  assert.equal(shouldLoadSource(123), true)
  markSourceHandled(123)

  // 切到内容库再回来（同一地址）→ 必须不拉取。
  // 且 `loading` 的初值用的就是这个函数，因此也一定是 false —— 不会永久转圈。
  assert.equal(
    shouldLoadSource(123),
    false,
    "同一 id 再次挂载不该重新拉取"
  )
})

test("06-F3 普通 /write（无 id）永远不拉取", () => {
  resetHandledSources()
  assert.equal(shouldLoadSource(null), false)
  markSourceHandled(123)
  assert.equal(shouldLoadSource(null), false)
})

test("06-F4 问题 2 复现：× 清空后不得因 URL 自动恢复旧素材", () => {
  resetHandledSources()
  markSourceHandled(123)          // /write/123 已预填
  setWriteField("sourceText", "原帖正文")
  resetWriteSession()             // 用户点 ×
  assert.equal(getWriteSession().sourceText, "")

  // 关键：簿记**不在会话里**，× 清空不会把它一起清掉
  assert.equal(
    shouldLoadSource(123),
    false,
    "× 之后不该再拉取该 id —— 否则刚清空的正文会被填回来"
  )
})

test("06-F5 问题 2 复现：切换一级入口后同样不得恢复旧素材", () => {
  resetHandledSources()
  markSourceHandled(123)
  clearWriteSession("benchmark")   // 切换入口
  assert.equal(getWriteSession().entry, "benchmark")
  assert.equal(shouldLoadSource(123), false)
})

test("06-F6 打开**新的**素材 ID 仍然要拉取", () => {
  markSourceHandled(123)
  assert.equal(shouldLoadSource(456), true)
})

test("06-F7 簿记不在会话字段里（改了它不该触发会话变更）", () => {
  fresh()
  let hits = 0
  const off = subscribeWriteSession(() => { hits += 1 })
  markSourceHandled(999)
  off()
  assert.equal(hits, 0, "URL 簿记不属于创作现场，不该通知订阅者")
  assert.ok(!Object.keys(initialWriteSession()).includes("prefilledContentId"))
})

// ---------------- 第二轮验收：主动打开 vs 普通返回 ----------------

test("06-G1 票据唯一：每次点「改写这条」都是新票据", () => {
  const a = newMaterialOpenTicket()
  const b = newMaterialOpenTicket()
  assert.notEqual(a, b)
})

test("06-G2 主动打开：新票据判定为「新的打开」，兑现后不再算", () => {
  const t = newMaterialOpenTicket()
  assert.equal(isFreshMaterialOpen(t), true, "第一次应为新的主动打开")
  markMaterialOpenConsumed(t)
  assert.equal(isFreshMaterialOpen(t), false, "兑现过之后不再算新的打开")
})

test("06-G3 浏览器后退：history 里带回来的旧票据不得被当成新打开", () => {
  // 内容库 → 改写这条 → /write/123（票据 t 已兑现）
  const t = newMaterialOpenTicket()
  markMaterialOpenConsumed(t)
  // 切到别的页再按后退：同一个 history 条目回来，票据还是 t
  assert.equal(
    isFreshMaterialOpen(t),
    false,
    "后退必须走「恢复现场」，不能重新载入"
  )
})

test("06-G4 同一素材再次主动打开：新票据 → 重新载入（即使 × 清空过）", () => {
  resetHandledSources()
  // 第一次主动打开 123
  const t1 = newMaterialOpenTicket()
  assert.equal(isFreshMaterialOpen(t1), true)
  markMaterialOpenConsumed(t1)
  markSourceHandled(123)

  // 用户点 × 清空 —— 此时 shouldLoadSource 判定为「不用拉」
  resetWriteSession()
  assert.equal(shouldLoadSource(123), false)

  // 再次从内容库点「改写这条」→ 新票据 → 必须重新载入
  const t2 = newMaterialOpenTicket()
  assert.equal(
    isFreshMaterialOpen(t2),
    true,
    "同一素材再次主动打开必须重新载入"
  )
})

test("06-G5 空票据永远不算主动打开", () => {
  assert.equal(isFreshMaterialOpen(null), false)
  assert.equal(isFreshMaterialOpen(undefined), false)
  assert.equal(isFreshMaterialOpen(""), false)
})

test("06-G6 兑现空票据是安全的 no-op", () => {
  const t = newMaterialOpenTicket()
  markMaterialOpenConsumed(null)
  markMaterialOpenConsumed(undefined)
  assert.equal(isFreshMaterialOpen(t), true, "空票据不该污染真实票据的判定")
})

// ---------------- 第三轮验收：历史返回不得让旧票据/旧素材重新生效 ----------------

test("06-H1 依次打开 A、B，再历史返回 A：票据 A 不得重新生效", () => {
  resetConsumedTickets()
  resetHandledSources()
  const tA = newMaterialOpenTicket()
  const tB = newMaterialOpenTicket()

  // 主动打开 A
  assert.equal(isFreshMaterialOpen(tA), true)
  markMaterialOpenConsumed(tA)
  markSourceHandled(1)

  // 主动打开 B —— 「只记最后一张票据」的实现会在这里把 tA 忘掉
  assert.equal(isFreshMaterialOpen(tB), true)
  markMaterialOpenConsumed(tB)
  markSourceHandled(2)

  // 历史返回 A：票据 A 兑现过 → 绝不能再算一次主动打开
  assert.equal(
    isFreshMaterialOpen(tA),
    false,
    "已兑现的票据在历史返回时不得重新生效"
  )
})

test("06-H2 依次打开 A、B，再历史返回 A：也不得重新拉取 A", () => {
  resetConsumedTickets()
  resetHandledSources()
  markSourceHandled(1)   // A 处理过
  markSourceHandled(2)   // B 处理过

  // 「只记最后一个」的实现会得出 A !== handledB → 误判要重新载入
  assert.equal(
    shouldLoadSource(1),
    false,
    "历史返回一个更早打开过的素材，必须走恢复而不是重新载入"
  )
  assert.equal(shouldLoadSource(2), false)
})

test("06-H3 只有新票据才重新载入指定素材", () => {
  resetConsumedTickets()
  resetHandledSources()
  markSourceHandled(1)

  // 用户再次主动点「改写这条」→ 新票据 → 无视已处理标记，重新载入
  const again = newMaterialOpenTicket()
  assert.equal(isFreshMaterialOpen(again), true)

  // 而历史返回用的旧票据不行
  const old = newMaterialOpenTicket()
  markMaterialOpenConsumed(old)
  assert.equal(isFreshMaterialOpen(old), false)
})

test("06-H4 未处理过的新 id 仍然要拉取", () => {
  resetConsumedTickets()
  resetHandledSources()
  markSourceHandled(1)
  assert.equal(shouldLoadSource(2), true, "没见过的素材 id 应当照常拉取")
  assert.equal(shouldLoadSource(null), false)
})
