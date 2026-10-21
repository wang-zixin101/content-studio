import { describe, it } from "node:test"
import assert from "node:assert"
import fs from "node:fs"
import path from "node:path"

import { contentRowMainText } from "./content-row-text.ts"

/**
 * Stage 12 走查修复：内容库列表主文本必须**永远显示原文**。
 *
 * 以前是 `summary || excerpt`，导致同一列表里
 * 「AI 总结句」与「原帖正文」混排，用户无法快速识别原帖。
 */
describe("内容库列表主文本", () => {
  it("raw_text + summary 同时存在 → 显示原文，不显示 summary", () => {
    const item = {
      raw_text: "原帖正文：今天把这件事拆开看了一下，发现问题不在执行。",
      excerpt: "原帖正文：今天把这件事拆开看了一下，发现问题不在执行。",
      summary: "AI 总结：作者认为问题不在执行。",
    }
    const text = contentRowMainText(item)
    assert.equal(text, item.excerpt)
    assert.ok(!text.includes("AI 总结"))
    assert.notEqual(text, item.summary)
  })

  it("只有 raw_text → 正常显示", () => {
    const item = { raw_text: "只有原文的一条内容。", excerpt: "只有原文的一条内容。" }
    assert.equal(contentRowMainText(item), "只有原文的一条内容。")
  })

  it("excerpt 缺失时回退 raw_text", () => {
    assert.equal(contentRowMainText({ raw_text: "回退原文。" }), "回退原文。")
    assert.equal(contentRowMainText({ excerpt: "", raw_text: "回退原文。" }), "回退原文。")
    assert.equal(contentRowMainText({ excerpt: "   ", raw_text: "回退原文。" }), "回退原文。")
  })

  it("excerpt 与 raw_text 都为空 → 返回空串，绝不回落到 summary", () => {
    const item = { excerpt: "", raw_text: "", summary: "这段摘要绝不能被当成正文" }
    assert.equal(contentRowMainText(item), "")
  })

  it("签名不接受 summary —— 摘要从结构上无法混进主文本", () => {
    // contentRowMainText 的入参只有 excerpt / raw_text；
    // 多传的 summary 会被忽略，不会影响结果。
    const text = contentRowMainText({
      excerpt: "原文。",
      summary: "摘要。",
    } as never)
    assert.equal(text, "原文。")
  })
})

describe("ContentRow 无回归（源码级守卫）", () => {
  const src = fs.readFileSync(
    path.join(process.cwd(), "src/components/content-row.tsx"),
    "utf8"
  )

  it("已改用 contentRowMainText，且不再有 summary 作为主文本 fallback", () => {
    assert.match(src, /contentRowMainText\(item\)/)
    assert.doesNotMatch(
      src,
      /item\.summary\s*\|\|/,
      "不得重新引入 `summary || excerpt` 这种 fallback"
    )
  })

  it("其它列（来源 badge / 状态 / 时间 / 主题）保持不变", () => {
    // 来源 badge
    assert.match(src, /item\.source === "mine" \? "mine" : "benchmark"/)
    // 状态徽章仍以 analysis_state 为准
    assert.match(src, /analysisState=\{item\.analysis_state\}/)
    // 时间 / 主题 / 内容类型
    assert.match(src, /formatRelative\(item\.created_at\)/)
    assert.match(src, /item\.topic/)
    assert.match(src, /item\.content_type/)
    // 多选能力保留
    assert.match(src, /onToggleSelect/)
  })
})
