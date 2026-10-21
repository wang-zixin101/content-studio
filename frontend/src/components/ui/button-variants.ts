import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "../../lib/utils.ts"

/**
 * 按钮样式变体（从 button.tsx 抽出的 .ts 模块）。
 *
 * 抽出的原因：纯展示组件如果写成 .ts（无 JSX），`node --test` 就能直接
 * import 并真实渲染它做断言；但 .ts 不能 import .tsx。
 * 样式定义放在这里后，button.tsx 与 .ts 组件共用**同一份**类名，
 * 不存在第二份按钮样式。
 *
 * 高度只有两档：h-8（dense 工具栏）/ h-9（常规）。
 * 动效只保留 150ms 颜色过渡 —— 不要 scale / spring / bounce。
 */
export const buttonVariants = cva(
  "inline-flex shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded text-[13px] font-medium transition-colors duration-150 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-3.5 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground hover:bg-primary/90",
        destructive: "bg-danger text-white hover:bg-danger/90",
        outline: "border border-border bg-surface hover:bg-hover",
        secondary: "bg-hover text-text-secondary hover:bg-hover/70",
        ghost: "text-text-secondary hover:bg-hover hover:text-foreground",
        link: "text-brand underline-offset-4 hover:underline",
      },
      size: {
        default: "h-9 px-4",
        sm: "h-8 px-3 text-[12px]",
        icon: "h-8 w-8",
        lg: "h-9 px-5",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  }
)

export type ButtonVariantProps = VariantProps<typeof buttonVariants>

/** 便捷函数：直接拿到某个变体的完整类名（供无 JSX 的纯组件使用）。 */
export function buttonClass(
  opts: { variant?: ButtonVariantProps["variant"]; size?: ButtonVariantProps["size"] },
  className?: string
): string {
  return cn(buttonVariants({ variant: opts.variant, size: opts.size }), className)
}
