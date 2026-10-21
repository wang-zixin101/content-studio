import { describe, it } from "node:test"
import assert from "node:assert"

import {
  EXPORT_SCOPES,
  buildExportQuery,
  exportButtonLabel,
  formatBytes,
  isAllAuthors,
  scopeSummaryText,
} from "./export-scope.ts"

/**
 * V2 任务 02：导出范围的纯逻辑。
 *
 * 最关键的一条断言：**预览与下载必须拼出完全相同的查询串** ——
 * 之前两边各拼一套，还各自剔除状态，才出现「显示 3 条、下载 5 条」。
 */
describe("buildExportQuery", () => {
  it("预览与下载共用同一个 builder → 查询串完全一致", () => {
    const params = {
      source: "benchmark" as const,
      authorIds: [3, 7],
      includeNoAuthor: false,
      scope: "pending" as const,
    }
    const a = buildExportQuery(params).toString()
    const b = buildExportQuery(params).toString()
    assert.equal(a, b)
    assert.equal(a, "source=benchmark&author_ids=3%2C7&scope=pending")
  })

  it("不传作者 = 全部作者：不出现 author_ids 参数", () => {
    const q = buildExportQuery({ scope: "all" })
    assert.equal(q.get("author_ids"), null)
    assert.equal(q.get("scope"), "all")
    assert.equal(q.toString(), "scope=all")
  })

  it("绝不出现 limit —— 后端默认不限量", () => {
    const q = buildExportQuery({ authorIds: [1], scope: "pending" })
    assert.equal(q.get("limit"), null)
    assert.equal(q.toString().includes("limit"), false)
  })

  it("多个作者用逗号分隔，与后端 author_ids 范式一致", () => {
    const q = buildExportQuery({ authorIds: [1, 2, 3], scope: "all" })
    assert.equal(q.get("author_ids"), "1,2,3")
  })

  it("include_no_author 只在指定了作者时才传", () => {
    assert.equal(
      buildExportQuery({ includeNoAuthor: true, scope: "all" }).get("include_no_author"),
      null
    )
    assert.equal(
      buildExportQuery({ authorIds: [5], includeNoAuthor: true, scope: "all" }).get(
        "include_no_author"
      ),
      "true"
    )
  })

  it("过滤非法作者 id（NaN / Infinity 不能进 URL）", () => {
    const q = buildExportQuery({ authorIds: [2, Number.NaN], scope: "all" })
    assert.equal(q.get("author_ids"), "2")
  })
})

describe("isAllAuthors", () => {
  it("空数组 / undefined = 全部作者", () => {
    assert.equal(isAllAuthors({ scope: "all" }), true)
    assert.equal(isAllAuthors({ authorIds: [], scope: "all" }), true)
    assert.equal(isAllAuthors({ authorIds: [1], scope: "all" }), false)
  })
})

describe("formatBytes", () => {
  it("0 → 0 KB；小于 1 KB → 字节", () => {
    assert.equal(formatBytes(0), "0 KB")
    assert.equal(formatBytes(null), "0 KB")
    assert.equal(formatBytes(512), "512 B")
  })
  it("KB / MB 分档", () => {
    assert.equal(formatBytes(2048), "2 KB")
    assert.equal(formatBytes(2 * 1024 * 1024), "2.0 MB")
  })
})

describe("exportButtonLabel", () => {
  it("带真实条数，绝不出现「下载」这种看不出规模的字", () => {
    assert.equal(exportButtonLabel(12345, false), "下载 12345 条任务 JSONL")
    assert.equal(exportButtonLabel(1, false), "下载 1 条任务 JSONL")
    assert.equal(exportButtonLabel(0, false), "没有可导出内容")
    assert.equal(exportButtonLabel(null, true), "统计中…")
  })
})

describe("scopeSummaryText", () => {
  it("全部作者 → 显示有内容作者数", () => {
    const text = scopeSummaryText({
      scope: "all",
      authorCount: 4,
      matchingCount: 8200,
      noAuthorCount: 30,
      estimatedBytes: 3 * 1024 * 1024,
      allAuthors: true,
    })
    assert.match(text, /全部作者 · 4 个有内容作者/)
    assert.match(text, /符合条件 8200 条/)
    assert.match(text, /3\.0 MB/)
  })

  it("指定作者且含无作者 → 明确写出无作者条数", () => {
    const text = scopeSummaryText({
      scope: "pending",
      authorCount: 2,
      matchingCount: 120,
      noAuthorCount: 15,
      estimatedBytes: 45000,
      allAuthors: false,
    })
    assert.match(text, /指定 2 个作者/)
    assert.match(text, /含无作者 15 条/)
  })
})

describe("EXPORT_SCOPES", () => {
  it("只有两档：仅待分析 / 全部可导出", () => {
    assert.deepEqual(
      EXPORT_SCOPES.map((s) => s.key),
      ["pending", "all"]
    )
  })
})
