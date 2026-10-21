import { describe, it } from "node:test"
import assert from "node:assert"

import {
  analysisStateLabel,
  effectiveAnalysisState,
  isAnalyzed,
  isFailed,
  isProcessing,
  isQueued,
} from "./content-status.ts"

/**
 * Stage 12.1 P0 回归：展示口径必须与筛选口径一致。
 *
 * 内容库以前读 legacy `status`（pending → 「排队中」），而外部分析页按
 * `analysis_state='queued'` 筛选 —— 于是 744 条 status=pending 但
 * analysis_state=unprocessed 的内容被显示成「排队中」，却筛不出来。
 */
describe("分析状态展示口径", () => {
  it("analysis_state 优先于 legacy status", () => {
    // status=pending 但实际未分析 —— 这是之前错位的根源场景
    assert.equal(effectiveAnalysisState("unprocessed", "pending"), "unprocessed")
    assert.equal(analysisStateLabel(effectiveAnalysisState("unprocessed", "pending")), "未分析")

    // 真正的排队
    assert.equal(effectiveAnalysisState("queued", "pending"), "queued")
    assert.equal(analysisStateLabel(effectiveAnalysisState("queued", "pending")), "排队中")
  })

  it("analysis_state 缺失时回退 legacy status", () => {
    assert.equal(effectiveAnalysisState(null, "pending"), "pending")
    assert.equal(effectiveAnalysisState(undefined, "analyzed"), "analyzed")
    assert.equal(effectiveAnalysisState(null, null), "unprocessed")
  })

  it("六种 analysis_state 都有中文标签", () => {
    const map: Record<string, string> = {
      unprocessed: "未分析",
      queued: "排队中",
      processing: "分析中",
      analyzed: "已分析",
      failed: "分析失败",
      cancelled: "已取消",
    }
    for (const [state, label] of Object.entries(map)) {
      assert.equal(analysisStateLabel(state), label, state)
    }
  })

  it("queued 判定只认 analysis_state='queued'", () => {
    assert.equal(isQueued("queued"), true)
    assert.equal(isQueued("unprocessed"), false)
    // legacy pending 不等于 queued（这正是错位来源）
    assert.equal(isQueued("pending"), false)
    assert.equal(isQueued(null), false)
  })

  it("processing / analyzed / failed 判定", () => {
    assert.equal(isProcessing("processing"), true)
    assert.equal(isProcessing("queued"), false)
    assert.equal(isAnalyzed("analyzed"), true)
    assert.equal(isAnalyzed("processing"), false)
    assert.equal(isFailed("failed"), true)
    assert.equal(isFailed("cancelled"), false)
  })

  it("未知取值不炸，原样回显", () => {
    assert.equal(analysisStateLabel("weird_state"), "weird_state")
  })
})
