/**
 * 外部分析 · 导出区的**分支判定**（Stage 12.1 前端修复的核心）。
 *
 * ------------------------------------------------------------------
 * 为什么必须单独抽出来
 * ------------------------------------------------------------------
 * 之前的实现把「待导出」和「待转出」混在同一块里，而且两个数字来自
 * **两个不同来源**：
 *
 *   待导出  ← /tasks/preview 的 count（按仍选中的 states 过滤）
 *   待转出  ← 同一个响应里的 state_counts.queued
 *
 * 只要两者口径稍有差异（或者用户在 queued 之上还勾着别的状态），
 * 页面就会同时显示一个语义错误的「待导出」和一个正确的「待转出」，
 * 甚至只剩一个 0。
 *
 * 正确语义（queued 本身不是可导出状态，必须先 handoff → unprocessed）：
 *
 *   勾了未分析 / 失败 / 已分析 / 已取消  → 显示「待导出」+ 下载
 *   勾了排队中                          → 显示「待转出」+ 转为外部分析
 *                                          （数量取自 handoff candidates，与按钮同源）
 *   勾了正在分析                        → 只显示「正在分析」+ 说明，无按钮
 */

/** 真正可以被导出成分析任务的状态（queued / processing 不在其中）。 */
export const EXPORTABLE_STATES = [
  "unprocessed",
  "failed",
  "analyzed",
  "cancelled",
] as const

export function exportableStates(states: string[]): string[] {
  return states.filter((s) =>
    (EXPORTABLE_STATES as readonly string[]).includes(s)
  )
}

export interface ExportPanelFlags {
  /** 显示「待导出 N 条」+ 下载按钮 */
  showExportRow: boolean
  /** 显示「待转出 N 条」+ 转为外部分析 */
  showHandoff: boolean
  /** 显示「正在分析 N 条」+ 不可转说明 */
  showProcessing: boolean
}

export function exportPanelFlags(states: string[]): ExportPanelFlags {
  return {
    showExportRow: exportableStates(states).length > 0,
    showHandoff: states.includes("queued"),
    showProcessing: states.includes("processing"),
  }
}

/**
 * V2 任务 02 的面板分支。
 *
 * 与旧 `exportPanelFlags` 的区别：**待转出 / 正在分析不再依赖用户勾选了哪个状态**。
 * 现在内容范围只有「仅待分析 / 全部可导出」两档，queued 与 processing
 * 根本不在任何一档里，但用户仍然需要看到它们并转出 ——
 * 所以这两个分支改由**真实计数**驱动（state_counts.queued / processing）。
 */
export function exportPanelMode(input: {
  states: string[]
  queued: number
  processing: number
}): ExportPanelFlags {
  return {
    showExportRow: exportableStates(input.states).length > 0,
    showHandoff: input.queued > 0,
    showProcessing: input.processing > 0,
  }
}
