import { test } from "node:test"
import assert from "node:assert/strict"

import {
  beginDraw,
  canApplyDraw,
  drawButtonLabel,
  drawScopeError,
  invalidateDraws,
  materialRangeOptions,
  newDrawSession,
  rangeCount,
  rangeEmptyNotice,
  rangeSwitchDisabled,
  rangeTitle,
  shouldOfferRestart,
  ALL_RANGE_LABEL,
  BLANK_RANGE_LABEL,
  type MaterialPoolsLike,
} from "./material-range.ts"

const labelOf = (d: string | null, u: string) => (d && d.trim()) || `@${u}`

/** 三个范围：全部 7 条 / 空白 2 条 / 兴雨 3 条 / 老雷 2 条。 */
const POOLS: MaterialPoolsLike = {
  all: 7,
  blank: 2,
  authors: [
    { author_id: 1, username: "xingyu", display_name: "兴雨", count: 3 },
    { author_id: 2, username: "laolei", display_name: "老雷", count: 2 },
  ],
}

// ---------------- 范围选项 ----------------

test("05-1 范围选项：全部 + 空白 + 每个作者（含条数）", () => {
  const rows = materialRangeOptions(POOLS, labelOf)
  assert.deepEqual(
    rows.map((r) => [r.key, r.label, r.count, r.authorId]),
    [
      ["all", ALL_RANGE_LABEL, 7, null],
      ["blank", BLANK_RANGE_LABEL, 2, null],
      ["author", "兴雨", 3, 1],
      ["author", "老雷", 2, 2],
    ]
  )
})

test("05-2 空池返回空数组（不能崩）", () => {
  assert.deepEqual(materialRangeOptions(null, labelOf), [])
  assert.deepEqual(
    materialRangeOptions({ all: 0, blank: 0, authors: [] }, labelOf).length,
    2
  )
})

test("05-3 count=0 的作者不进列表（点进去必然是死路）", () => {
  const pools: MaterialPoolsLike = {
    all: 1,
    blank: 0,
    authors: [
      { author_id: 1, username: "a", display_name: null, count: 1 },
      { author_id: 2, username: "b", display_name: null, count: 0 },
    ],
  }
  const rows = materialRangeOptions(pools, labelOf)
  assert.deepEqual(
    rows.filter((r) => r.key === "author").map((r) => r.authorId),
    [1]
  )
})

test("05-4 display_name 为空时退化为 @username", () => {
  const pools: MaterialPoolsLike = {
    all: 1,
    blank: 0,
    authors: [{ author_id: 9, username: "nobody", display_name: null, count: 1 }],
  }
  assert.equal(materialRangeOptions(pools, labelOf)[2].label, "@nobody")
})

// ---------------- 范围标题 / 计数 ----------------

test("05-5 rangeTitle 精确匹配到当前范围", () => {
  const rows = materialRangeOptions(POOLS, labelOf)
  assert.equal(rangeTitle(rows, "all", null), "全部素材")
  assert.equal(rangeTitle(rows, "blank", null), "空白作者")
  assert.equal(rangeTitle(rows, "author", 1), "兴雨")
  assert.equal(rangeTitle(rows, "author", 2), "老雷")
})

test("05-6 rangeCount 跟随当前范围", () => {
  const rows = materialRangeOptions(POOLS, labelOf)
  assert.equal(rangeCount(rows, "all", null), 7)
  assert.equal(rangeCount(rows, "author", 1), 3)
  assert.equal(rangeCount(rows, "author", 2), 2)
})

test("05-7 作者不在选项里时不冒充别的作者", () => {
  const rows = materialRangeOptions(POOLS, labelOf)
  // authorId=99 不在列表 → 标题退化为中性的「指定作者」，计数为 0
  assert.equal(rangeTitle(rows, "author", 99), "指定作者")
  assert.equal(rangeCount(rows, "author", 99), 0)
})

// ---------------- 抽空提示 ----------------

test("05-8 抽完提示必须点名范围（这是「不自动切换」的前提）", () => {
  const msg = rangeEmptyNotice({ title: "兴雨", count: 3, seenCount: 3 })
  assert.ok(msg.includes("兴雨"), "提示里必须出现范围名")
  assert.ok(msg.includes("3 条"))
  assert.ok(msg.includes("已经全部抽过一遍"))
})

test("05-9 范围本身为 0 条时的提示与抽空区分开", () => {
  const msg = rangeEmptyNotice({ title: "老雷", count: 0, seenCount: 0 })
  assert.ok(msg.includes("没有可抽取的帖子"))
  assert.ok(msg.includes("不会自动切换"))
})

// ---------------- 参数校验 ----------------

test("05-10 author 池必须带 author_id（与后端同一条规则）", () => {
  assert.equal(
    drawScopeError("author", null),
    "请先选择一位作者，再抽取素材。"
  )
  assert.equal(drawScopeError("author", 1), null)
  assert.equal(drawScopeError("all", null), null)
  assert.equal(drawScopeError("blank", null), null)
})

// ---------------- 按钮文案 / 重来 ----------------

test("05-11 按钮文案：未抽到 = 随机抽取，抽到后 = 换一条", () => {
  assert.equal(drawButtonLabel(false), "随机抽取")
  assert.equal(drawButtonLabel(true), "换一条")
})

test("05-12 只在抽失败且确实有已看记录时才给「重来」", () => {
  assert.equal(shouldOfferRestart({ failed: true, seenCount: 3 }), true)
  assert.equal(shouldOfferRestart({ failed: true, seenCount: 0 }), false)
  assert.equal(shouldOfferRestart({ failed: false, seenCount: 3 }), false)
})

// ---------------- 第二轮验收：慢响应下的作者范围隔离 ----------------

test("05-R2-1 在途请求：正常返回时结果允许落地", () => {
  const begun = beginDraw(newDrawSession())
  assert.equal(canApplyDraw(begun.session, begun.token), true)
})

test("05-R2-2 复现场景：抽 A 的请求在途时切到 B，A 的响应必须被丢弃", () => {
  // ① 用户在「王长富」范围点抽取 —— 请求在途
  let session = newDrawSession()
  const drawA = beginDraw(session)
  session = drawA.session
  assert.equal(canApplyDraw(session, drawA.token), true, "此刻 A 的结果仍有效")

  // ② 响应回来之前，用户切到「老雷」
  session = invalidateDraws(session)

  // ③ A 的慢响应这时才回来 —— 必须整段丢弃
  assert.equal(
    canApplyDraw(session, drawA.token),
    false,
    "旧作者的响应绝不能落到新范围下"
  )

  // ④ 在「老雷」范围重新抽取，新结果正常落地
  const drawB = beginDraw(session)
  session = drawB.session
  assert.equal(canApplyDraw(session, drawB.token), true)
  assert.equal(
    canApplyDraw(session, drawA.token),
    false,
    "B 抽取后 A 的 token 仍然过期"
  )
})

test("05-R2-3 连续两次抽取：先发的响应晚到也不能覆盖后发的", () => {
  let session = newDrawSession()
  const first = beginDraw(session)
  session = first.session
  const second = beginDraw(session)
  session = second.session

  // 第二次先回来 → 落地
  assert.equal(canApplyDraw(session, second.token), true)
  // 第一次后回来 → 过期
  assert.equal(canApplyDraw(session, first.token), false)
})

test("05-R2-4 每一次抽取 / 切换都会推进序号（token 不会重复）", () => {
  let session = newDrawSession()
  const tokens: number[] = []
  for (let i = 0; i < 3; i++) {
    const b = beginDraw(session)
    session = b.session
    tokens.push(b.token)
    session = invalidateDraws(session)
  }
  assert.equal(new Set(tokens).size, 3, "token 必须严格递增且不重复")
})

test("05-R2-5 抽取期间禁用范围切换", () => {
  assert.equal(rangeSwitchDisabled(true), true)
  assert.equal(rangeSwitchDisabled(false), false)
})

test("05-R2-6 抽空失败也走同一个守卫（旧范围的错误不显示在新范围下）", () => {
  let session = newDrawSession()
  const drawA = beginDraw(session)
  session = drawA.session
  session = invalidateDraws(session)
  // A 的请求以失败告终 —— 同样不能被当作当前范围的错误展示
  assert.equal(canApplyDraw(session, drawA.token), false)
})
