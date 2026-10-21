/**
 * B3：全站统一的数字 / 时间格式。
 *
 * 目的：杜绝各页面自己写 toLocaleString / toFixed / slice(0,10)，
 * 避免同一页面同时出现「12.5K」和「12,521」这种混用。
 *
 * 约定：
 *   列表    → 紧凑（formatCompact）
 *   详情    → 精确（formatNumber）
 *   百分比  → 最多 1 位小数（formatPercent）
 */

/** 精确数字：37371 → 37,371 */
export function formatNumber(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—"
  return value.toLocaleString("zh-CN")
}

/** 紧凑数字：37371 → 37.4K；小于 10000 原样千分位。用于空间紧张的列表。 */
export function formatCompact(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—"
  const abs = Math.abs(value)
  if (abs < 10_000) return formatNumber(value)
  if (abs < 1_000_000) return `${(value / 1000).toFixed(1)}K`
  return `${(value / 1_000_000).toFixed(1)}M`
}

/** 百分比：最多 1 位小数，去掉多余的 .0 */
export function formatPercent(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—"
  const rounded = Math.round(value * 10) / 10
  return `${Number.isInteger(rounded) ? rounded : rounded.toFixed(1)}%`
}

/** 分位分数：60.0 → 60；60.25 → 60.3 */
export function formatScore(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—"
  const rounded = Math.round(value * 10) / 10
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1)
}

/**
 * X 原始 posted_at（RFC2822 或 ISO）→ 可读时间。
 *
 * 原始值绝不裸展示给 UI：解析失败时返回空串，由调用方决定省略。
 * 数据库的原值保持不动。
 */
function parseDate(value: string): Date | null {
  const raw = (value || "").trim()
  if (!raw) return null
  const date = new Date(raw)
  if (!Number.isNaN(date.getTime())) return date
  // 兼容 RFC2822（Xporter 导出格式）
  const legacy = Date.parse(raw)
  return Number.isNaN(legacy) ? null : new Date(legacy)
}

/** 列表：10月5日（跨年补年份） */
export function formatDateShort(value: string | null | undefined): string {
  if (!value) return ""
  const date = parseDate(value)
  if (!date) return ""
  const now = new Date()
  const sameYear = date.getFullYear() === now.getFullYear()
  return date.toLocaleDateString("zh-CN", {
    year: sameYear ? undefined : "numeric",
    month: "long",
    day: "numeric",
  })
}

/** 详情：2026-10-05 18:31 */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return ""
  const date = parseDate(value)
  if (!date) return ""
  const pad = (n: number) => String(n).padStart(2, "0")
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    ` ${pad(date.getHours())}:${pad(date.getMinutes())}`
  )
}

/** 相对时间：刚刚 / 12 分钟前 / 3 小时前 / 2 天前，超过 30 天退回日期 */
export function formatRelative(value: string | null | undefined): string {
  if (!value) return ""
  const date = parseDate(value)
  if (!date) return ""
  const diff = Date.now() - date.getTime()
  const min = Math.floor(diff / 60000)
  if (min < 1) return "刚刚"
  if (min < 60) return `${min} 分钟前`
  const hour = Math.floor(min / 60)
  if (hour < 24) return `${hour} 小时前`
  const day = Math.floor(hour / 24)
  if (day < 30) return `${day} 天前`
  return formatDateShort(value)
}
