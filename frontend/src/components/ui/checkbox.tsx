import { Check } from "lucide-react"

import { cn } from "@/lib/utils"

/**
 * 统一复选框（任务 07 S6）。
 *
 * 之前 4 处直接用原生 `input[type=checkbox]`（浏览器蓝、圆角与全站控件不一致）。
 * 这里用 `button[role=checkbox]` 实现：
 *   · 选中态走前景色（与深墨主按钮同一语言），不用浏览器蓝
 *   · 4px 圆角（`rounded-sm`），与 6px 控件 / 8px 面板的圆角阶梯一致
 *   · label 文本一起作为按钮内容 —— 点文字也能切换，保留原 `<label>` 的行为
 *
 * 用法：
 *   <Checkbox checked={v} onChange={setV}>说明文字</Checkbox>
 */
export function Checkbox({
  checked,
  onChange,
  children,
  className,
  disabled,
  "aria-label": ariaLabel,
}: {
  checked: boolean
  onChange: (checked: boolean) => void
  children?: React.ReactNode
  className?: string
  disabled?: boolean
  "aria-label"?: string
}) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={checked}
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "flex items-center gap-2 text-left transition-colors",
        disabled ? "cursor-not-allowed opacity-50" : "cursor-pointer",
        className
      )}
    >
      <span
        aria-hidden
        className={cn(
          "flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-sm border transition-colors",
          checked
            ? "border-foreground bg-foreground text-primary-foreground"
            : "border-border bg-surface group-hover/row:border-text-secondary"
        )}
      >
        {checked && <Check className="h-2.5 w-2.5" strokeWidth={3} />}
      </span>
      {children}
    </button>
  )
}
