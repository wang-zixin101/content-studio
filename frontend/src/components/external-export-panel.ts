import { createElement, type ReactNode } from "react"
import { ArrowRightLeft, Copy, Download, Loader2 } from "lucide-react"

import { buttonClass } from "./ui/button-variants.ts"
import { exportPanelMode } from "../lib/external-panel-mode.ts"
import { exportButtonLabel, formatBytes } from "../lib/export-scope.ts"

/**
 * 外部分析 · 导出区（纯展示组件，Stage 12.1 + V2 任务 02）。
 *
 * ------------------------------------------------------------------
 * 为什么是 .ts 而不是 .tsx
 * ------------------------------------------------------------------
 * 刻意不用 JSX：这样 `node --test` 可以直接 import **同一份生产组件**
 * 并用 react-dom/server 真实渲染断言输出文案。JSX 需要构建步骤，
 * 而本项目的前端测试刻意保持零额外依赖（node 内置 --test + 类型剥离）。
 *
 * ------------------------------------------------------------------
 * V2 任务 02 的三处变化
 * ------------------------------------------------------------------
 * 1) **取消 20/50/100 数量上限**：下载按钮直接显示真实条数，
 *    「待转出」也不再被 `Math.min(count, limit)` 二次截断。
 * 2) 显示「作者数 · 无作者条数 · 预计体积」，
 *    让用户在点下载**之前**就知道规模，而不是下载完才发现少了。
 * 3) 待转出 / 正在分析两个分支改由**真实计数**驱动 ——
 *    后端已明确拒绝 queued / processing 进入普通导出，
 *    UI 不能再靠「用户勾选了哪个状态」判断。
 */
export interface ExternalExportPanelProps {
  states: string[]
  counting: boolean
  /** 本次将导出的真实条数（后端不带上限的计数） */
  exportCount: number | null
  /** 命中的作者数 */
  authorCount: number
  /** 其中无作者内容的条数 */
  noAuthorCount: number
  /** 预计任务文件字节数 */
  estimatedBytes: number
  /** 排队中（可转出）条数 */
  handoffCount: number
  /** 正在分析条数 */
  processingCount: number
  handing: boolean
  handoffNote: string | null
  onCopyPrompt: () => void
  onDownload: () => void
  onHandoff: () => void
  children?: ReactNode
}

const h = createElement

/**
 * 与 `<Button>` 完全相同的类名（共用 button-variants.ts），
 * 但渲染成原生 <button>：这样组件本身不依赖任何 .tsx，
 * `node --test` 可以直接 import 它做真实渲染断言。
 */
function Btn(props: {
  variant?: "default" | "outline" | "ghost"
  size?: "default" | "sm"
  onClick: () => void
  disabled?: boolean
  children?: ReactNode
}) {
  return h(
    "button",
    {
      type: "button",
      onClick: props.onClick,
      disabled: props.disabled,
      className: buttonClass({ variant: props.variant ?? "default", size: props.size ?? "sm" }),
    },
    props.children
  )
}

export function ExternalExportPanel(props: ExternalExportPanelProps) {
  const { showExportRow, showHandoff, showProcessing } = exportPanelMode({
    states: props.states,
    queued: props.handoffCount,
    processing: props.processingCount,
  })
  const count = props.exportCount ?? 0

  const exportRow = showExportRow
    ? h(
        "div",
        { className: "flex flex-wrap items-center justify-between gap-3" },
        h(
          "div",
          { className: "space-y-0.5" },
          h(
            "p",
            { className: "text-[13px] text-text-secondary" },
            "待导出 ",
            h(
              "span",
              { className: "tabular-nums font-medium text-foreground" },
              props.counting ? "…" : String(count)
            ),
            " 条"
          ),
          h(
            "p",
            { className: "text-[12px] text-text-tertiary" },
            `作者 ${props.authorCount} 个`,
            props.noAuthorCount > 0 ? ` · 含无作者 ${props.noAuthorCount} 条` : "",
            count > 0 ? ` · 预计 ${formatBytes(props.estimatedBytes)}` : ""
          )
        ),
        h(
          "div",
          { className: "flex items-center gap-2" },
          h(
            Btn,
            { variant: "outline", size: "sm", onClick: props.onCopyPrompt },
            h(Copy, { className: "h-3.5 w-3.5" }),
            "复制分析提示词"
          ),
          h(
            Btn,
            { size: "sm", onClick: props.onDownload, disabled: !count },
            h(Download, { className: "h-3.5 w-3.5" }),
            exportButtonLabel(props.exportCount, props.counting)
          )
        )
      )
    : null

  const exportEmpty =
    showExportRow && props.exportCount === 0 && !props.counting
      ? h(
          "p",
          { className: "text-[12px] text-text-tertiary" },
          "当前筛选条件下没有待导出的内容。可以对失败的内容重新导出，或先调整作者 / 范围。"
        )
      : null

  const handoff = showHandoff
    ? h(
        "div",
        { className: "space-y-2 border-t border-divider pt-3" },
        h(
          "div",
          { className: "flex flex-wrap items-center justify-between gap-3" },
          h(
            "p",
            { className: "text-[13px] text-text-secondary" },
            "待转出 ",
            h(
              "span",
              { className: "tabular-nums font-medium text-foreground" },
              props.counting ? "…" : String(props.handoffCount)
            ),
            " 条",
            h(
              "span",
              { className: "ml-2 text-[12px] text-text-tertiary" },
              "排队中 · 尚未开始分析"
            )
          ),
          h(
            Btn,
            {
              size: "sm",
              onClick: props.onHandoff,
              disabled: props.handing || props.handoffCount === 0,
            },
            props.handing
              ? h(Loader2, { className: "h-3.5 w-3.5 animate-spin" })
              : h(ArrowRightLeft, { className: "h-3.5 w-3.5" }),
            "转为外部分析"
          )
        ),
        h(
          "p",
          { className: "text-[12px] leading-relaxed text-text-tertiary" },
          "排队中的内容还不是可导出的分析任务：转出后它们回到「未分析」，即可导出给外部模型。"
        )
      )
    : null

  const processing = showProcessing
    ? h(
        "div",
        { className: "border-t border-divider pt-3" },
        h(
          "p",
          { className: "text-[13px] text-text-secondary" },
          "正在分析 ",
          h(
            "span",
            { className: "tabular-nums font-medium text-foreground" },
            props.counting ? "…" : String(props.processingCount)
          ),
          " 条"
        ),
        h(
          "p",
          { className: "mt-1 text-[12px] leading-relaxed text-text-tertiary" },
          "已经开始内部分析的内容暂时不能转为外部分析。"
        )
      )
    : null

  return h(
    "div",
    { className: "space-y-4" },
    exportRow,
    exportEmpty,
    handoff,
    processing,
    props.handoffNote
      ? h("p", { className: "text-[12px] text-text-secondary" }, props.handoffNote)
      : null,
    props.children
  )
}
