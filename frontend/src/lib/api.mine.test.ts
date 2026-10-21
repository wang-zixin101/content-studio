import { afterEach, describe, it } from "node:test"
import assert from "node:assert"

import { api } from "./api.ts"
import type { GoalState } from "./types.ts"

/**
 * 「我的」目标接口的接线测试。
 *
 * 页面保存后做的事就是 `setGoal(await api.updateMineGoal(...))` ——
 * 所以「PATCH 真的发出去了」＋「拿到的就是新状态」这两条，
 * 等价于「保存成功后 goal 被刷新」。这里用被替换的 fetch 直接验证。
 */

const realFetch = globalThis.fetch

interface Call {
  url: string
  init: RequestInit | undefined
}

function stubFetch(body: unknown, status = 200): Call[] {
  const calls: Call[] = []
  globalThis.fetch = (async (input: unknown, init?: RequestInit) => {
    calls.push({ url: String(input), init })
    return new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json" },
    })
  }) as unknown as typeof fetch
  return calls
}

/** 后端 PATCH 之后回给前端的完整目标状态。 */
const updated: GoalState = {
  exists: true,
  goal: {
    id: 1,
    start_date: "2026-10-07",
    baseline_qualified_impressions: 4700,
    created_at: "2026-10-07T14:31:35+08:00",
  },
  target: 500000,
  duration_days: 90,
  baseline: 4700,
  current: 4700,
  gained: 0,
  remaining: 495300,
  progress: 0.0094,
  status: "active",
  elapsed_days: 0,
  remaining_days: 90,
  current_daily_required: 5504,
  original_daily_required: 5504,
  planned_today: 4700,
  ahead_behind: 0,
  snapshots: [],
  chart: { line: [], snapshots: [] },
}

const bodyOf = (call: Call): Record<string, unknown> =>
  JSON.parse(String(call.init?.body)) as Record<string, unknown>

afterEach(() => {
  globalThis.fetch = realFetch
})

describe("api.updateMineGoal", () => {
  it("走 PATCH /api/mine/goal，不是新建（POST）", async () => {
    const calls = stubFetch(updated)
    await api.updateMineGoal({
      start_date: "2026-10-07",
      baseline_qualified_impressions: 4700,
    })
    assert.equal(calls.length, 1)
    assert.equal(calls[0].url, "/api/mine/goal")
    assert.equal(calls[0].init?.method, "PATCH")
  })

  it("请求体只有 start_date 与 baseline —— target / duration 根本传不出去", async () => {
    const calls = stubFetch(updated)
    await api.updateMineGoal({
      start_date: "2026-11-01",
      baseline_qualified_impressions: 12000,
    })
    assert.deepEqual(Object.keys(bodyOf(calls[0])).sort(), [
      "baseline_qualified_impressions",
      "start_date",
    ])
    assert.deepEqual(bodyOf(calls[0]), {
      start_date: "2026-11-01",
      baseline_qualified_impressions: 12000,
    })
  })

  it("返回的就是服务端按新起点重算后的状态（页面直接用它刷新）", async () => {
    stubFetch(updated)
    const goal = await api.updateMineGoal({
      start_date: "2026-10-07",
      baseline_qualified_impressions: 4700,
    })
    // 不是「返回一个 200 就完事」—— 必须是完整状态，否则图表刷新不了。
    assert.equal(goal.exists, true)
    assert.equal(goal.goal?.start_date, "2026-10-07")
    assert.equal(goal.baseline, 4700)
    assert.equal(goal.target, 500000)
    assert.equal(goal.duration_days, 90)
    assert.equal(goal.remaining_days, 90)
    assert.ok(goal.chart, "图表数据必须一起回来")
  })

  it("对照：createMineGoal 仍是 POST（编辑没有被错接到建立）", async () => {
    const calls = stubFetch(updated)
    await api.createMineGoal({
      start_date: "2026-10-07",
      baseline_qualified_impressions: 4700,
    })
    assert.equal(calls[0].init?.method, "POST")
  })

  it("目标不存在（404）→ 抛错，UI 不会假装保存成功", async () => {
    stubFetch({ detail: "目标不存在，无法修改" }, 404)
    await assert.rejects(
      () =>
        api.updateMineGoal({
          start_date: "2026-10-07",
          baseline_qualified_impressions: 4700,
        }),
      /目标不存在，无法修改/
    )
  })
})
