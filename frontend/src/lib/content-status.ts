/**
 * 分析状态展示口径（Stage 12.1 P0 修复的核心）。
 *
 * ------------------------------------------------------------------
 * 背景
 * ------------------------------------------------------------------
 * contents 表有两个状态字段：
 *
 *   status          旧字段，兼容保留，取值 pending/analyzing/analyzed/failed
 *   analysis_state  分析流程的**唯一控制状态**
 *                   unprocessed / queued / processing / analyzed / failed / cancelled
 *
 * `status='pending'` 是**有歧义**的：它既可能是「从未分析」，也可能是「已排队」。
 * 内容库以前读 status，于是把 744 条 unprocessed 也显示成「排队中」；
 * 而外部分析页按 analysis_state='queued' 筛选 —— 两个口径对不上，
 * 用户就会看到「内容库显示排队中，但外部分析待转出 0 条」。
 *
 * 结论：**展示一律以 analysis_state 为准**，status 只在 analysis_state 缺失时兜底。
 */

export type AnalysisState =
  | "unprocessed"
  | "queued"
  | "processing"
  | "analyzed"
  | "failed"
  | "cancelled"

const ANALYSIS_LABEL: Record<string, string> = {
  unprocessed: "未分析",
  queued: "排队中",
  processing: "分析中",
  analyzed: "已分析",
  failed: "分析失败",
  cancelled: "已取消",
}

const LEGACY_LABEL: Record<string, string> = {
  pending: "排队中",
  analyzing: "分析中",
  analyzed: "已分析",
  failed: "分析失败",
}

/** 取一条内容真实的分析状态：analysis_state 优先，缺失时回退 legacy status。 */
export function effectiveAnalysisState(
  analysisState: string | null | undefined,
  legacyStatus?: string | null
): string {
  return (analysisState || legacyStatus || "unprocessed") as string
}

export function analysisStateLabel(state: string): string {
  return ANALYSIS_LABEL[state] ?? LEGACY_LABEL[state] ?? state
}

/** 是否处于「排队中」—— 与外部分析 handoff 的 queued 口径完全一致。 */
export function isQueued(state: string | null | undefined): boolean {
  return state === "queued"
}

/** 是否正在分析中（不可交接）。 */
export function isProcessing(state: string | null | undefined): boolean {
  return state === "processing"
}

/** 是否已分析。 */
export function isAnalyzed(state: string | null | undefined): boolean {
  return state === "analyzed"
}

/** 是否失败。 */
export function isFailed(state: string | null | undefined): boolean {
  return state === "failed"
}
