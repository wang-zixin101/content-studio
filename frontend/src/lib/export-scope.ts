/**
 * 外部分析 · 导出范围的**纯逻辑**（V2 任务 02）。
 *
 * ------------------------------------------------------------------
 * 为什么必须有这个文件
 * ------------------------------------------------------------------
 * 之前「预览」和「下载」各拼一套查询参数，而且前端还要自己把
 * queued / processing 从 states 里剔掉 —— 两边口径一旦漂移，
 * 就会出现「显示 3 条、下载 5 条」这种永远查不出来原因的问题。
 *
 * 现在只有一个出口 `buildExportQuery()`：
 *   预览、统计、下载**共用同一个 URLSearchParams**，
 * 状态筛选交给后端的 `scope` 参数（pending / all），
 * 前端不再自己拼 states —— 口径不可能再不一致。
 *
 * 本文件刻意零依赖、无 React：`node --test` 可以直接 import 真实生产代码。
 */

export type ExportScope = "pending" | "all"

export const EXPORT_SCOPES: { key: ExportScope; label: string; hint: string }[] = [
  { key: "pending", label: "仅待分析", hint: "未分析 + 失败" },
  { key: "all", label: "全部可导出", hint: "再加上已分析 / 已取消" },
]

/** 「全部作者」= 不传作者参数；「指定作者」= 传 author_ids。 */
export type AuthorScopeMode = "all" | "selected"

export interface ExportScopeParams {
  source?: string | null
  /** 指定的作者 id；空数组 / undefined = 全部作者（含无作者内容） */
  authorIds?: number[]
  /** 仅在「指定作者」时有效：是否把无作者内容也算进来 */
  includeNoAuthor?: boolean
  scope: ExportScope
}

/** 预览 / 下载共用的查询串。改这里就同时改两边，不存在第二份。 */
export function buildExportQuery(params: ExportScopeParams): URLSearchParams {
  const q = new URLSearchParams()
  if (params.source) q.set("source", params.source)
  const ids = (params.authorIds ?? []).filter((id) => Number.isFinite(id))
  if (ids.length) q.set("author_ids", ids.join(","))
  if (ids.length && params.includeNoAuthor) q.set("include_no_author", "true")
  q.set("scope", params.scope)
  // 不传 limit —— 后端默认 0 = 不限量
  return q
}

export function isAllAuthors(params: ExportScopeParams): boolean {
  return (params.authorIds ?? []).length === 0
}

/** 人类可读的体积（1 MB = 1024 KB，与后端字节口径一致）。 */
export function formatBytes(bytes: number | null | undefined): string {
  const n = Number(bytes ?? 0)
  if (!Number.isFinite(n) || n <= 0) return "0 KB"
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

/** 下载按钮文案：始终带真实条数，绝不显示「下载」这种看不出规模的字。 */
export function exportButtonLabel(
  count: number | null,
  counting: boolean
): string {
  if (counting && count == null) return "统计中…"
  const n = count ?? 0
  if (n === 0) return "没有可导出内容"
  return `下载 ${n} 条任务 JSONL`
}

/** 「作者 N 个 · 符合条件 M 条 · 预计 X」这一行的文案。 */
export function scopeSummaryText(input: {
  scope: ExportScope
  authorCount: number | null
  matchingCount: number | null
  noAuthorCount: number | null
  estimatedBytes: number | null
  allAuthors: boolean
}): string {
  const authors = input.authorCount ?? 0
  const matching = input.matchingCount ?? 0
  const parts: string[] = [
    input.allAuthors ? `全部作者 · ${authors} 个有内容作者` : `指定 ${authors} 个作者`,
    `符合条件 ${matching} 条`,
  ]
  if (!input.allAuthors && (input.noAuthorCount ?? 0) > 0) {
    parts.push(`含无作者 ${input.noAuthorCount} 条`)
  }
  if (matching > 0) parts.push(`预计 ${formatBytes(input.estimatedBytes)}`)
  return parts.join(" · ")
}
