import type { ReactNode } from "react"

import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import type { Status } from "@/lib/types"
import {
  analysisStateLabel,
  effectiveAnalysisState,
  type AnalysisState,
} from "@/lib/content-status"
import { cn } from "@/lib/utils"

/**
 * B3 收口的少量公共组件。
 *
 * 原则（spec §42）：只抽象**真实被 3 个以上页面复用**的模式，
 * 不为了「设计系统」一次新建几十个组件。
 */

// ---------------------------------------------------------------- 状态

/**
 * 统一状态词汇（spec §26）：
 *   未分析 → neutral / 排队中·分析中 → info / 已分析 → success /
 *   已取消 → warning / 失败 → danger
 * 各页面不得再自行写 bg-red-100 / text-warning 之类。
 */
export function StatusBadge({
  status,
  analysisState,
  error,
}: {
  status: Status
  /** 有值时优先使用 —— 它是唯一控制状态 */
  analysisState?: AnalysisState | string | null
  error?: string | null
}) {
  const state = analysisState || null
  const label = analysisStateLabel(effectiveAnalysisState(state, status))

  if (state === "analyzed" || (!state && status === "analyzed")) {
    return (
      <Badge variant="success" className="border-0 bg-transparent px-0">
        已分析
      </Badge>
    )
  }
  if (state === "failed" || (!state && status === "failed")) {
    return (
      <Badge variant="danger" title={error ?? undefined}>
        分析失败
      </Badge>
    )
  }
  if (state === "queued" || state === "processing") {
    return (
      <Badge variant="info">
        <span className="mr-1 h-1.5 w-1.5 animate-pulse rounded-full bg-current" />
        {label}
      </Badge>
    )
  }
  if (state) {
    const variant: "success" | "danger" | "warning" | "neutral" =
      state === "cancelled" ? "warning" : "neutral"
    return (
      <Badge variant={variant} title={error ?? undefined}>
        {label}
      </Badge>
    )
  }
  return (
    <Badge variant="info">
      <span className="mr-1 h-1.5 w-1.5 animate-pulse rounded-full bg-current" />
      {label}
    </Badge>
  )
}
// ---------------------------------------------------------------- 骨架屏

/** 列表骨架：dense 行形态，不再用「一张张卡片」。 */
export function ListSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div className="divide-y divide-divider">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex items-start gap-3 px-4 py-3">
          <Skeleton className="mt-0.5 h-4 w-10 rounded-sm" />
          <div className="min-w-0 flex-1 space-y-2">
            <Skeleton className="h-3.5 w-full" />
            <Skeleton className="h-3 w-2/5" />
          </div>
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------- 空态

/**
 * Empty State 必须**可行动**（spec §27）：
 * 标题 + 一句解释 + 一个 CTA。不做插画。
 */
export function EmptyState({
  title,
  description,
  action,
  className,
  icon,
  bordered = true,
}: {
  title: string
  description?: string
  action?: ReactNode
  className?: string
  /**
   * 任务 07 S6：可选图标（渲染在标题上方）。
   * 写作页两个右栏空状态用它统一「居中 + 字级」，候选空态按 B3 #10 不传图标。
   */
  icon?: ReactNode
  /** 任务 07 S6：嵌入实线面板内的空态可关掉自带虚线框，避免双层框。 */
  bordered?: boolean
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center rounded-lg px-6 py-14 text-center",
        bordered && "border border-dashed border-border",
        className
      )}
    >
      {icon && <div className="mb-3">{icon}</div>}
      <p className="text-sm text-foreground">{title}</p>
      {description && (
        <p className="mt-1.5 max-w-sm text-xs leading-relaxed text-text-secondary">
          {description}
        </p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}

/**
 * 解释性文案的统一形态（任务 07 S6）。
 *
 * 盘点发现解释性长文案散落在各页，有的 12px secondary、有的甚至与正文同权重，
 * 读起来像说明书。统一降到 11px tertiary —— 这是全站最小可读字号的下限。
 */
export function HelpText({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <p className={cn("text-[11px] leading-relaxed text-text-tertiary", className)}>
      {children}
    </p>
  )
}

// ---------------------------------------------------------------- 页头 / 区块头

/** 页面标题统一 22px semibold（工具不是官网，不做 32px+）。 */
export function PageHeader({
  title,
  description,
  actions,
  className,
}: {
  title: string
  /**
   * 页面说明。
   *
   * 任务 07 S3 第二批：从 `string` 放宽到 `ReactNode` ——
   * 批量导入页的说明里有内联强调（「不会自动调用 AI」），纯 string 装不下。
   * 既有调用方（我的 / 跨作者洞察 / 外部分析）传的都是 string，行为不变。
   */
  description?: ReactNode
  actions?: ReactNode
  className?: string
}) {
  return (
    <div className={cn("flex items-start justify-between gap-4", className)}>
      <div className="min-w-0">
        <h1 className="truncate text-[22px] font-semibold leading-tight tracking-tight">
          {title}
        </h1>
        {description && (
          <p
            className={cn(
              "mt-1 text-[13px] text-text-secondary",
              // string 说明沿用原来的单行截断；node 说明（含内联标记）允许折行
              typeof description === "string" && "truncate"
            )}
          >
            {description}
          </p>
        )}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  )
}

/**
 * 区块标题统一 15px semibold。
 * **不用 uppercase / tracking-wide** —— 那是英文小标题的做法，中文会显得松散。
 */
export function SectionHeader({
  title,
  hint,
  actions,
  className,
}: {
  title: string
  hint?: string
  actions?: ReactNode
  className?: string
}) {
  return (
    <div className={cn("flex items-baseline justify-between gap-3", className)}>
      <div className="flex min-w-0 items-baseline gap-2">
        <h2 className="text-[15px] font-semibold leading-none">{title}</h2>
        {hint && (
          <span className="truncate text-[12px] text-text-tertiary">{hint}</span>
        )}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-1.5">{actions}</div>}
    </div>
  )
}

// ---------------------------------------------------------------- 紧凑工具条

export function Toolbar({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border px-6 py-3",
        className
      )}
    >
      {children}
    </div>
  )
}

// ---------------------------------------------------------------- 分段控件

/**
 * 分段控件（segmented）。
 *
 * B3 明确：**不用阴影**、不用大蓝块，只靠背景明度 + 字重表达选中。
 * size="sm" 用于页面内的二级切换（如 Write 的「指定素材 / 随机素材」）。
 */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  size = "md",
  className,
}: {
  options: { key: T; label: string }[]
  value: T
  onChange: (key: T) => void
  size?: "sm" | "md"
  className?: string
}) {
  return (
    <div
      className={cn(
        "inline-flex items-center gap-0.5 rounded bg-hover p-0.5",
        size === "sm" ? "h-7" : "h-8",
        className
      )}
    >
      {options.map((opt) => (
        <button
          key={opt.key}
          type="button"
          onClick={() => onChange(opt.key)}
          aria-pressed={value === opt.key}
          className={cn(
            "h-full rounded-sm px-3 font-medium transition-colors duration-150",
            size === "sm" ? "text-[12px]" : "text-[13px]",
            value === opt.key
              ? "bg-surface text-foreground"
              : "text-text-secondary hover:text-foreground"
          )}
        >
          {opt.label}
        </button>
      ))}
    </div>
  )
}
