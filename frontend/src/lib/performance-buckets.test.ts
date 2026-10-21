import { describe, it } from "node:test"
import assert from "node:assert"

import {
  filterHigh,
  filterLow,
  isHighPerformance,
  isLowPerformance,
  isNeutralPerformance,
} from "./performance-buckets.ts"

const v = (score: number, n = 10) => ({
  value: `v${score}`,
  median_performance_score: score,
  sample_count: n,
})

describe("Stage 8 UI 高/低表现分档", () => {
  it("score >= 60 才进高表现榜", () => {
    assert.equal(isHighPerformance(v(60)), true)
    assert.equal(isHighPerformance(v(77)), true)
    assert.equal(isHighPerformance(v(97)), true)
    assert.equal(isHighPerformance(v(59.9)), false)
    assert.equal(isHighPerformance(v(50)), false)
    assert.equal(isHighPerformance(v(16.8)), false)
  })

  it("score < 40 才进低表现榜", () => {
    assert.equal(isLowPerformance(v(39.9)), true)
    assert.equal(isLowPerformance(v(16.8)), true)
    assert.equal(isLowPerformance(v(36.8)), true)
    assert.equal(isLowPerformance(v(40)), false)
    assert.equal(isLowPerformance(v(50)), false)
    assert.equal(isLowPerformance(v(77)), false)
  })

  it("score 50 中性：高榜低榜都不进", () => {
    const neutral = v(50)
    assert.equal(isHighPerformance(neutral), false)
    assert.equal(isLowPerformance(neutral), false)
    assert.equal(isNeutralPerformance(neutral), true)
  })

  it("样本量 < 5 不进榜", () => {
    assert.equal(isHighPerformance(v(90, 4)), false)
    assert.equal(isLowPerformance(v(10, 4)), false)
  })

  it("score 50 同时出现在 raw high/low 排名时，两榜都过滤掉", () => {
    // 后端 high / low 是排序结果，同一个取值可能同时出现
    const rawHigh = [v(97), v(77), v(50)]
    const rawLow = [v(16.8), v(36.8), v(50)]
    const high = filterHigh(rawHigh)
    const low = filterLow(rawLow)

    assert.deepEqual(
      high.map((x) => x.median_performance_score),
      [97, 77]
    )
    assert.deepEqual(
      low.map((x) => x.median_performance_score),
      [16.8, 36.8]
    )
    for (const item of high) assert.equal(isNeutralPerformance(item), false)
    for (const item of low) assert.equal(isNeutralPerformance(item), false)
  })
})
