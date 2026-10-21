import { Check } from "lucide-react"

import { StatusBadge } from "@/components/common"
import { Badge } from "@/components/ui/badge"
import { contentRowMainText } from "@/lib/content-row-text"
import { formatRelative } from "@/lib/format"
import type { ContentItem } from "@/lib/types"
import { cn } from "@/lib/utils"

/**
 * 内容列表行。**内容库与作者主页共用同一套行组件。**
 *
 * B3 §17：像 Linear issue list / Things list —— 一行就是一条，**不是一张卡片**。
 * 行内层级：摘要（主）→ 来源 → 状态 → 时间/主题（tertiary）。
 * 靠 hover 背景与左侧细条表达选中，不靠整块描边。
 *
 * 多选是可选的：作者主页不做批量操作，不传 onToggleSelect 即隐藏复选框。
 */
export function ContentRow({
  item,
  onOpen,
  selected,
  onToggleSelect,
}: {
  item: ContentItem
  onOpen: (id: number) => void
  selected?: boolean
  onToggleSelect?: (id: number) => void
}) {
  const selectable = Boolean(onToggleSelect)

  return (
    <div
      className={cn(
        "group relative flex w-full items-start gap-3 px-4 py-3 transition-colors duration-150",
        selected ? "bg-selected" : "hover:bg-hover"
      )}
    >
      {/* 选中：左侧细条，不只靠颜色 */}
      {selected && (
        <span className="absolute inset-y-0 left-0 w-0.5 bg-brand" aria-hidden />
      )}

      {selectable && (
        <button
          type="button"
          aria-label={selected ? "取消选择" : "选择"}
          onClick={() => onToggleSelect?.(item.id)}
          className={cn(
            "mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-sm border transition-colors",
            selected
              ? "border-primary bg-primary text-primary-foreground"
              : "border-border opacity-0 group-hover:opacity-100"
          )}
        >
          {selected && <Check className="h-3 w-3" />}
        </button>
      )}

      <button
        onClick={() => onOpen(item.id)}
        className="flex min-w-0 flex-1 items-start gap-3 text-left"
      >
        <Badge
          variant={item.source === "mine" ? "mine" : "benchmark"}
          className="mt-0.5 shrink-0 font-normal"
        >
          {item.source === "mine" ? "我的" : "对标"}
        </Badge>

        <div className="min-w-0 flex-1">
          <p
            className={cn(
              // B3 #18：正文提到 primary，作者/状态 secondary，时间 tertiary
              // Stage 12 走查：主文本统一为原文（excerpt 派生自 raw_text），
              // 因此颜色不再按 summary 有无分叉
              "line-clamp-2 text-[13px] leading-relaxed text-foreground/90"
            )}
          >
            {contentRowMainText(item)}
          </p>

          <div className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[12px] text-text-tertiary">
            <StatusBadge
              status={item.status}
              analysisState={item.analysis_state}
              error={item.analysis_error}
            />
            {item.source === "mine" && item.style_eligible && (
              <span className="text-success">参与风格学习</span>
            )}
            <span>{formatRelative(item.created_at)}</span>
            {item.topic && <span>· {item.topic}</span>}
            {item.content_type && <span>· {item.content_type}</span>}
          </div>
        </div>
      </button>
    </div>
  )
}
