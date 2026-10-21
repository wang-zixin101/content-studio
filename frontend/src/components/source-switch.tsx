import { cn } from "@/lib/utils"
import type { Source } from "@/lib/types"

interface SourceSwitchProps {
  value: Source
  onChange: (value: Source) => void
  size?: "sm" | "default"
}

/** 「我的 / 对标」切换，纯受控组件。 */
export function SourceSwitch({ value, onChange, size = "default" }: SourceSwitchProps) {
  const options: { key: Source; label: string; hint: string }[] = [
    { key: "mine", label: "我的", hint: "Ctrl 1" },
    { key: "benchmark", label: "对标", hint: "Ctrl 2" },
  ]

  return (
    <div
      className={cn(
        "inline-flex items-center rounded-lg border border-border bg-hover p-0.5",
        size === "sm" ? "h-8" : "h-9"
      )}
    >
      {options.map((opt) => {
        const active = value === opt.key
        return (
          <button
            key={opt.key}
            type="button"
            onClick={() => onChange(opt.key)}
            className={cn(
              "flex items-center gap-2 rounded font-medium transition-colors",
              size === "sm" ? "h-7 px-3 text-xs" : "h-8 px-4 text-sm",
              active
                ? opt.key === "mine"
                  ? "bg-[hsl(var(--mine)/0.16)] text-[hsl(var(--mine))]"
                  : "bg-[hsl(var(--benchmark)/0.16)] text-[hsl(var(--benchmark))]"
                : "text-text-secondary hover:text-foreground"
            )}
          >
            {opt.label}
            <kbd
              className={cn(
                "rounded border border-border/70 px-1 text-[11px] font-normal text-text-tertiary",
                active ? "opacity-80" : "opacity-50"
              )}
            >
              {opt.hint}
            </kbd>
          </button>
        )
      })}
    </div>
  )
}
