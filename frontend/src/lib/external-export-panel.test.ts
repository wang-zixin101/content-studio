import { describe, it } from "node:test"
import assert from "node:assert"
import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"

import { ExternalExportPanel } from "../components/external-export-panel.ts"
import { exportPanelFlags, exportableStates } from "./external-panel-mode.ts"

/**
 * Stage 12.1 前端 handoff 分支：**真实组件渲染**测试。
 *
 * 用 react-dom/server 渲染真正的组件树（不是 helper 副本），
 * 因此断言的是最终输出的 HTML 文案 —— 这正是之前出错的那一层。
 * 不需要 jsdom，也不引入新的测试框架。
 */
function render(states: string[], over: Record<string, unknown> = {}) {
  const props: Record<string, unknown> = {
    states,
    counting: false,
    exportCount: null,
    authorCount: 0,
    noAuthorCount: 0,
    estimatedBytes: 0,
    handoffCount: 0,
    processingCount: 0,
    handing: false,
    handoffNote: null,
    onCopyPrompt: () => {},
    onDownload: () => {},
    onHandoff: () => {},
  }
  Object.assign(props, over)
  return renderToStaticMarkup(
    createElement(ExternalExportPanel, props as never)
  )
}

describe("exportPanelFlags 分支判定", () => {
  it("queued 不是可导出状态", () => {
    assert.deepEqual(exportableStates(["queued"]), [])
    assert.deepEqual(exportableStates(["processing"]), [])
    assert.deepEqual(exportableStates(["unprocessed", "failed"]), [
      "unprocessed",
      "failed",
    ])
  })

  it("选中排队中 → 只显示 handoff，不显示待导出", () => {
    const f = exportPanelFlags(["queued"])
    assert.equal(f.showExportRow, false)
    assert.equal(f.showHandoff, true)
    assert.equal(f.showProcessing, false)
  })

  it("选中正在分析 → 只显示 processing", () => {
    const f = exportPanelFlags(["processing"])
    assert.equal(f.showExportRow, false)
    assert.equal(f.showHandoff, false)
    assert.equal(f.showProcessing, true)
  })

  it("默认（未分析 + 失败）→ 仍是导出模式", () => {
    const f = exportPanelFlags(["unprocessed", "failed"])
    assert.equal(f.showExportRow, true)
    assert.equal(f.showHandoff, false)
  })

  it("未分析 + 排队中 → 两块都显示（各自语义独立）", () => {
    const f = exportPanelFlags(["unprocessed", "queued"])
    assert.equal(f.showExportRow, true)
    assert.equal(f.showHandoff, true)
  })
})

describe("ExternalExportPanel 真实渲染", () => {
  it("queued selected + candidates count=1 → 待转出 1 条 + 转为外部分析，且没有「待导出」", () => {
    const html = render(["queued"], {
      handoffCount: 1,
      exportCount: 0,          // 即使父组件给了 0，也不该出现「待导出」
      processingCount: 0,
    })
    assert.match(html, /待转出/)
    assert.match(html, />\s*1\s*</)
    // 断言的是**按钮**，不是说明文案里出现的词
    assert.match(html, />转为外部分析<\/button>/)
    assert.match(html, /排队中的内容还不是可导出的分析任务/)
    assert.doesNotMatch(html, /待导出/)
    assert.doesNotMatch(html, /下载任务 JSONL/)
    assert.doesNotMatch(html, /正在分析/)
  })

  it("V2 任务 02：排队中 0 条 → 不显示待转出区块（没有东西可转）", () => {
    const html = render(["queued"], { handoffCount: 0 })
    assert.doesNotMatch(html, /待转出/)
    assert.doesNotMatch(html, />转为外部分析<\/button>/)
  })

  it("V2 任务 02：排队中 > 0 → 按钮可点（不再被 limit 禁用）", () => {
    const html = render(["unprocessed", "failed"], { handoffCount: 3, exportCount: 7 })
    assert.match(html, />转为外部分析<\/button>/)
    assert.doesNotMatch(html, /disabled=""/)
  })

  it("processing selected → 出现「正在分析」，不出现 handoff 按钮 / 下载", () => {
    const html = render(["processing"], { processingCount: 3 })
    assert.match(html, /正在分析/)
    assert.match(html, /已经开始内部分析的内容暂时不能转为外部分析/)
    // 不能有可点击的 handoff 按钮（说明文案里的「转为外部分析」不算）
    assert.doesNotMatch(html, />转为外部分析<\/button>/)
    assert.doesNotMatch(html, /待导出/)
    assert.doesNotMatch(html, /下载任务 JSONL/)
    assert.doesNotMatch(html, /待转出/)
  })

  it("默认导出模式 → 待导出 + 下载，不出现待转出 / 正在分析", () => {
    const html = render(["unprocessed", "failed"], { exportCount: 42 })
    assert.match(html, /待导出/)
    assert.match(html, /42/)
    assert.match(html, /下载 42 条任务 JSONL/)
    assert.doesNotMatch(html, /待转出/)
    assert.doesNotMatch(html, />转为外部分析<\/button>/)
  })

  it("handoff 成功后的自动刷新：状态切到未分析、数量变成可导出", () => {
    const after = render(["unprocessed"], {
      exportCount: 1,
      handoffCount: 0,
      handoffNote: "已转为外部分析 1 条",
    })
    assert.match(after, /待导出/)
    assert.match(after, /下载 1 条任务 JSONL/)
    assert.doesNotMatch(after, /待转出/)
    assert.doesNotMatch(after, />转为外部分析<\/button>/)
  })

  it("handoffNote 会渲染（部分成功文案）", () => {
    const html = render(["queued"], {
      handoffCount: 2,
      handoffNote: "已转出 18 条；2 条已经开始分析，未转出。",
    })
    assert.match(html, /已转出 18 条；2 条已经开始分析，未转出。/)
  })

  it("loading 时数量显示省略号，不显示 0", () => {
    const html = render(["queued"], { counting: true, handoffCount: 1 })
    assert.match(html, /…/)
  })

  it("V2 任务 02：取消上限 —— 待转出 137 条就显示 137，不再被截到 50", () => {
    const html = render(["queued"], { handoffCount: 137 })
    assert.match(html, /137/)
  })

  it("V2 任务 02：下载按钮显示真实条数（12345 条不被截断）", () => {
    const html = render(["unprocessed", "failed"], { exportCount: 12345 })
    assert.match(html, /下载 12345 条任务 JSONL/)
  })

  it("V2 任务 02：显示作者数 / 无作者数 / 预计体积", () => {
    const html = render(["unprocessed", "failed"], {
      exportCount: 5000,
      authorCount: 3,
      noAuthorCount: 12,
      estimatedBytes: 2 * 1024 * 1024,
    })
    assert.match(html, /作者 3 个/)
    assert.match(html, /含无作者 12 条/)
    assert.match(html, /2\.0 MB/)
  })

  it("V2 任务 02：queued 计数驱动 handoff（不再依赖勾选状态）", () => {
    // 内容范围里没有 queued，但库里确实有 5 条排队中 → 必须仍能看到并转出
    const html = render(["unprocessed", "failed"], { handoffCount: 5, exportCount: 9 })
    assert.match(html, /待转出/)
    assert.match(html, />转为外部分析<\/button>/)
    assert.match(html, /待导出/)
  })
})
