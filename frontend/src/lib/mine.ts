import type { AccountMetricPoint, GoalChart, GoalState, GoalStatus } from "./types"

/**
 * 「我的」页面的纯计算层。
 *
 * 抽出来是为了能直接用 `node --test` 跑**真实生产代码** ——
 * 图表数据一旦算错，页面上是看不出来的（线照样画得出来）。
 */

// ---------------------------------------------------------------- 数字格式

/**
 * 紧凑中文计数：1.2 万 / 3.4 亿。
 * 小于 1 万时保留千分位，避免「8000」被写成「0.8 万」这种别扭表达。
 */
export function formatCount(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—"
  const n = Math.round(value)
  const abs = Math.abs(n)
  if (abs >= 100_000_000) return `${trimZero(n / 100_000_000)} 亿`
  if (abs >= 10_000) return `${trimZero(n / 10_000)} 万`
  return n.toLocaleString("zh-CN")
}

function trimZero(v: number): string {
  const s = v.toFixed(1)
  return s.endsWith(".0") ? s.slice(0, -2) : s
}

/** 百分比：0.0834 → "8.3%" */
export function formatPercent(ratio: number | null | undefined): string {
  if (ratio == null || Number.isNaN(ratio)) return "—"
  return `${(ratio * 100).toFixed(1)}%`
}

// ---------------------------------------------------------------- 目标文案

export const GOAL_STATUS_LABEL: Record<GoalStatus, string> = {
  active: "进行中",
  achieved: "已达成",
  expired: "已过期",
}

/**
 * 期限提示。
 *
 * 刻意区分两种「剩 0 天」：
 *   - remaining_days = 0 且仍 active → 今天截止（还没过期）
 *   - expired        → 已过期
 * 这两种状态在界面上必须说不同的话，否则用户会以为已经失败。
 */
export function deadlineText(
  status: GoalStatus | null,
  remainingDays: number | null
): string {
  if (status === "achieved") return "已达成"
  if (status === "expired") return "已过期"
  if (remainingDays == null) return ""
  if (remainingDays === 0) return "今天截止"
  return `还剩 ${remainingDays} 天`
}

/**
 * 日均要求。
 *
 * 剩余天数为 0 时后端返回 null —— 这里绝不自行补一个"今天要补 50 万"
 * 这种没有意义、只会制造焦虑的数字。
 */
export function dailyRequiredText(required: number | null): string | null {
  if (required == null) return null
  return `接下来日均需 ${formatCount(required)}`
}

// ---------------------------------------------------------------- 目标对话框

/** 固定规则：改不了。后端也把它们放在 config 常量里，不在 creator_goal 表。 */
export const GOAL_FIXED_TARGET = 500_000
export const GOAL_FIXED_DURATION_DAYS = 90

export type GoalDialogMode = "create" | "edit"

/** 已经建过目标 → 入口从「建立目标」变成「编辑目标」。 */
export function goalDialogMode(goal: GoalState | null | undefined): GoalDialogMode {
  return goal?.exists ? "edit" : "create"
}

export function goalDialogCopy(mode: GoalDialogMode): {
  title: string
  description: string
  submit: string
  success: string
} {
  return mode === "edit"
    ? {
        title: "编辑目标",
        description:
          "开始日期与起始有效曝光是你自己的计划参数，可以随时修正；改完立刻按新起点重算。",
        submit: "保存",
        success: "目标已更新",
      }
    : {
        title: "建立 90 天目标",
        description:
          "目标是在 90 天内让合格浏览量总量达到 500,000。基线是你此刻的数值，只作为图表起点。",
        submit: "建立",
        success: "目标已建立",
      }
}

/**
 * 打开对话框时回填的表单值。
 * 没有目标时退回「今天 / 0」，而不是空白 —— 少一次手动输入。
 */
export function goalFormValues(
  goal: GoalState | null | undefined,
  today: string
): { startDate: string; baseline: string } {
  if (!goal?.exists) return { startDate: today, baseline: "0" }
  return {
    startDate: goal.goal?.start_date?.slice(0, 10) ?? today,
    baseline: String(goal.baseline ?? 0),
  }
}

/**
 * 对话框里展示的固定信息。刻意是**只读文本**，不是输入框 ——
 * 500,000 和 90 天不允许用户编辑。
 *
 * 这里用完整数字 500,000 而不是「50 万」：同一个对话框的说明文字里
 * 已经写了「达到 500,000」，两行之隔换个写法会显得像两个不同的数。
 * （页面头部的区块 hint 仍然走 formatCount 的紧凑写法。）
 */
export function goalFixedInfo(): { label: string; value: string }[] {
  return [
    { label: "目标", value: GOAL_FIXED_TARGET.toLocaleString("en-US") },
    { label: "周期", value: `${GOAL_FIXED_DURATION_DAYS} 天` },
  ]
}

// ---------------------------------------------------------------- 图表数据

export interface GoalChartRow {
  day: number
  date: string
  ideal: number | null
  actual: number | null
}

/**
 * 把后端的「基准线 + 快照点」合并成 Recharts 需要的**单一行数组**。
 *
 * 关键点：没有快照的日子，actual 为 null。
 * Recharts 配 connectNulls=false 会**断开**而不是连线 ——
 * 这正是"不做插值"：不存在的日子不会被补出来。
 */
export function toGoalChartRows(chart: GoalChart | null | undefined): GoalChartRow[] {
  if (!chart) return []

  const ideal = new Map<number, number>()
  for (const p of chart.line ?? []) ideal.set(p.day, p.value)

  const actual = new Map<number, number>()
  const dateOf = new Map<number, string>()
  for (const p of chart.snapshots ?? []) {
    actual.set(p.day, p.value)
    dateOf.set(p.day, p.date)
  }

  const days = Array.from(new Set([...ideal.keys(), ...actual.keys()])).sort(
    (a, b) => a - b
  )

  return days.map((day) => ({
    day,
    date: dateOf.get(day) ?? "",
    ideal: ideal.get(day) ?? null,
    actual: actual.get(day) ?? null,
  }))
}

export interface AccountChartRow {
  date: string
  value: number
}

/** 账号指标 → 图表行。NULL 在后端就被排除了，这里不需要再过滤。 */
export function toAccountChartRows(
  items: AccountMetricPoint[] | null | undefined
): AccountChartRow[] {
  return (items ?? []).map((i) => ({ date: i.metric_date, value: i.value }))
}

/** 差值文案：带正负号，方便一眼看出涨跌。 */
export function differenceText(diff: number | null | undefined): string | null {
  if (diff == null) return null
  const sign = diff > 0 ? "+" : diff < 0 ? "−" : ""
  return `${sign}${formatCount(Math.abs(diff))}`
}
