import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

/**
 * B3 统一状态色板（spec §26）。
 *
 * 语义色一律**低饱和**，且只在真正需要表达状态时出现：
 *   neutral  未分析 / 普通标签
 *   info     进行中
 *   success  已分析 / 完成
 *   warning  暂停 / 需注意（**不是错误**）
 *   danger   失败 / 删除
 *   mine / benchmark  业务来源标识（保留）
 *
 * 各页面不得再写 bg-red-500/5、text-warning 之类硬编码。
 */
const badgeVariants = cva(
  "inline-flex items-center rounded-sm px-1.5 py-[3px] text-[11px] font-medium leading-none transition-colors focus:outline-none",
  {
    variants: {
      variant: {
        neutral: "bg-hover text-text-secondary",
        default: "bg-hover text-text-secondary",
        secondary: "bg-hover text-text-secondary",
        outline: "bg-transparent text-text-tertiary ring-1 ring-inset ring-divider",
        info: "bg-info/10 text-info",
        success: "bg-success/10 text-success",
        warning: "bg-warning/10 text-warning",
        danger: "bg-danger/10 text-danger",
        destructive: "bg-danger/10 text-danger",
        /* 来源标识：非常浅、低饱和，不与状态色抢注意力（B3 #19） */
        mine: "bg-hover text-text-secondary",
        benchmark: "bg-hover text-text-secondary",
      },
    },
    defaultVariants: { variant: "default" },
  }
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />
}

export { Badge, badgeVariants }
