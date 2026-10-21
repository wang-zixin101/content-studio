import { useCallback, useEffect, useRef, useState } from "react"
import { Check, FileUp, Loader2, Plus, X } from "lucide-react"
import { toast } from "sonner"

import { HelpText, PageHeader, SectionHeader, Segmented } from "@/components/common"
import { AuthorSelector } from "@/components/author-selector"
import { ExternalExportPanel } from "@/components/external-export-panel"
import { Checkbox } from "@/components/ui/checkbox"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"
import { authorLabel } from "@/lib/author-display"
import { EXPORT_SCOPES, type ExportScope } from "@/lib/export-scope"
import { copyText } from "@/lib/utils"
import type {
  Author,
  ExternalPreview,
  ExternalPreviewItem,
  ExternalTaskPreview,
  Source,
} from "@/lib/types"

/**
 * 外部分析（Stage 12 + V2 任务 02）。
 *
 * 上半：导出分析任务（0 AI）
 * 下半：导入分析结果（0 AI，严格校验 + Preview 0 写入 + Confirm 整批事务）
 *
 * 不写 import_batches、不进「导入记录」、没有撤销语义。
 *
 * V2 任务 02：
 *   - 取消 20/50/100 数量上限，有多少符合条件的帖子就导出多少条
 *   - 作者范围：全部作者 / 指定单个或多个作者
 *   - 内容范围：仅待分析 / 全部可导出（queued、processing 不再进入导出）
 *   - 预览与下载**共用同一份查询串**，口径不可能不一致
 */

/** 各状态在统计行里的展示顺序与中文名。 */
const STATE_SUMMARY_OPTIONS = [
  { key: "unprocessed", label: "未分析" },
  { key: "failed", label: "失败" },
  { key: "analyzed", label: "已分析" },
  { key: "cancelled", label: "已取消" },
  { key: "queued", label: "排队中" },
  { key: "processing", label: "正在分析" },
]

const CLASS_LABEL: Record<ExternalPreviewItem["classification"], string> = {
  valid: "可导入",
  invalid_json: "JSON 损坏",
  schema_error: "Schema 错误",
  content_not_found: "找不到内容",
  content_hash_mismatch: "原文不匹配",
  already_analyzed: "已有分析",
  busy: "正在分析",
  duplicate_result: "重复结果",
}

const CLASS_ORDER: ExternalPreviewItem["classification"][] = [
  "valid",
  "invalid_json",
  "schema_error",
  "content_not_found",
  "content_hash_mismatch",
  "already_analyzed",
  "busy",
  "duplicate_result",
]

export default function ExternalAnalysisPage() {
  // ---- 导出侧 ----
  const [source, setSource] = useState<"all" | Source>("all")
  /** 内容范围：仅待分析 / 全部可导出。状态筛选整体交给后端 scope 参数。 */
  const [scope, setScope] = useState<ExportScope>("pending")
  /** 选中的作者；空数组 = 全部作者（含无作者内容） */
  const [selectedAuthors, setSelectedAuthors] = useState<Author[]>([])
  const [includeNoAuthor, setIncludeNoAuthor] = useState(false)
  const [pickerOpen, setPickerOpen] = useState(false)

  const [taskPreview, setTaskPreview] = useState<ExternalTaskPreview | null>(null)
  const [counting, setCounting] = useState(false)
  const [handing, setHanding] = useState(false)
  const [handoffNote, setHandoffNote] = useState<string | null>(null)

  // ---- 导入侧 ----
  const fileRef = useRef<File | null>(null)
  const [fileName, setFileName] = useState<string | null>(null)
  const [preview, setPreview] = useState<ExternalPreview | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [importing, setImporting] = useState(false)
  const [overwrite, setOverwrite] = useState(false)
  const [done, setDone] = useState<{ imported: number; model: string | null } | null>(null)
  const [expanded, setExpanded] = useState<ExternalPreviewItem["classification"] | null>(null)

  /** 预览 / 下载 / handoff 共用的同一份筛选参数 —— 口径不可能不一致。 */
  const scopeParams = {
    source: source === "all" ? null : source,
    authorIds: selectedAuthors.map((a) => a.id),
    includeNoAuthor,
    scope,
  }

  const refreshCount = useCallback(async () => {
    setCounting(true)
    try {
      const data = await api.previewExternalTasks(scopeParams)
      setTaskPreview(data)
    } catch (err) {
      setTaskPreview(null)
      toast.error((err as Error).message)
    } finally {
      setCounting(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source, scope, includeNoAuthor, selectedAuthors.map((a) => a.id).join(",")])

  useEffect(() => {
    void refreshCount()
  }, [refreshCount])

  /** 把当前筛选命中的 queued 内容交接给外部分析（不限量）。 */
  const runHandoff = async () => {
    setHanding(true)
    try {
      const candidates = await api.queuedHandoffCandidates({
        source: source === "all" ? null : source,
        authorIds: selectedAuthors.map((a) => a.id),
        includeNoAuthor,
      })
      const ids = candidates.ids
      if (ids.length === 0) {
        toast.info("当前没有排队中的内容")
        return
      }
      const res = await api.handoffToExternal(ids)
      const moved = res.transferred_count
      const blocked = res.processing.length
      setHandoffNote(
        blocked > 0
          ? `已转出 ${moved} 条；${blocked} 条已经开始分析，未转出。`
          : `已转为外部分析 ${moved} 条`
      )
      await refreshCount()
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setHanding(false)
    }
  }

  const downloadTasks = () => {
    // 与上面的预览**同一个 builder**：显示 N 条就下载 N 条
    const url = api.externalTasksExportUrl(scopeParams)
    window.location.href = url
  }

  const copyPrompt = async () => {
    try {
      const data = await api.getExternalPrompt(taskPreview?.matching_count ?? 0)
      const ok = await copyText(data.prompt)
      if (ok) toast.success("分析提示词已复制")
      else toast.error("复制失败，请手动选择复制")
    } catch (err) {
      toast.error((err as Error).message)
    }
  }

  const pickFile = (file: File | null) => {
    fileRef.current = file
    setFileName(file?.name ?? null)
    setPreview(null)
    setDone(null)
    setExpanded(null)
  }

  const runPreview = async (ow: boolean) => {
    const file = fileRef.current
    if (!file) {
      toast.error("请先选择结果文件")
      return
    }
    setPreviewing(true)
    try {
      const data = await api.previewExternalResults(file, ow)
      setPreview({ ...data, overwrite: ow })
      setDone(null)
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setPreviewing(false)
    }
  }

  const runConfirm = async () => {
    const file = fileRef.current
    if (!file || !preview || preview.file_error || preview.valid_count === 0) return
    setImporting(true)
    try {
      const res = await api.confirmExternalResults(file, overwrite, preview.confirm_token)
      setDone({ imported: res.imported, model: res.model })
      setPreview(null)
      toast.success(`已导入 ${res.imported} 条分析结果`)
    } catch (err) {
      toast.error((err as Error).message)
      // 冲突（409）后必须重新预检
      void runPreview(overwrite)
    } finally {
      setImporting(false)
    }
  }

  const counts = preview?.counts ?? {}
  const problemItems = (preview?.items ?? []).filter((i) => i.classification !== "valid")
  const expandedItems = expanded
    ? problemItems.filter((i) => i.classification === expanded)
    : []

  return (
    <div className="mx-auto w-full max-w-4xl px-6 py-8">
      <PageHeader
        title="外部分析"
        description="把工作台内容交给外部模型分析，再把结果导回来 —— 结果直接进现有分析体系"
      />

      {/* ================= A. 导出分析任务 ================= */}
      <section className="mt-8">
        <SectionHeader title="导出分析任务" hint="0 AI · 只导出原文与校验信息" />

        <div className="mt-4 space-y-4 rounded-lg border border-border px-4 py-4">
          <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
            <div className="flex items-center gap-2">
              <span className="text-[12px] text-text-tertiary">来源</span>
              <Segmented
                size="sm"
                options={[
                  { key: "all", label: "全部" },
                  { key: "mine", label: "我的" },
                  { key: "benchmark", label: "对标" },
                ]}
                value={source}
                onChange={setSource}
              />
            </div>

            <div className="flex items-center gap-2">
              <span className="text-[12px] text-text-tertiary">内容范围</span>
              <Segmented
                size="sm"
                options={EXPORT_SCOPES.map((s) => ({ key: s.key, label: s.label }))}
                value={scope}
                onChange={setScope}
              />
            </div>
          </div>

          {/* 作者范围：不指定 = 全部作者（含无作者内容） */}
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[12px] text-text-tertiary">作者</span>
              {selectedAuthors.length === 0 ? (
                <span className="text-[12px] text-text-secondary">全部作者</span>
              ) : (
                selectedAuthors.map((author) => (
                  <span
                    key={author.id}
                    className="inline-flex items-center gap-1 rounded-full border border-border bg-canvas px-2.5 py-1 text-[12px] text-foreground"
                  >
                    {authorLabel(author.display_name, author.username)}
                    <button
                      type="button"
                      aria-label={`移除作者 ${author.username}`}
                      className="text-text-tertiary hover:text-foreground"
                      onClick={() =>
                        setSelectedAuthors((prev) =>
                          prev.filter((a) => a.id !== author.id)
                        )
                      }
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </span>
                ))
              )}
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPickerOpen(true)}
              >
                <Plus className="h-3.5 w-3.5" />
                添加作者
              </Button>
              {selectedAuthors.length > 0 && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setSelectedAuthors([])
                    setIncludeNoAuthor(false)
                  }}
                >
                  恢复全部作者
                </Button>
              )}
            </div>

            {selectedAuthors.length > 0 && (
              <Checkbox
                checked={includeNoAuthor}
                onChange={setIncludeNoAuthor}
                className="text-[12px] text-text-secondary"
              >
                同时包含没有关联作者的内容
              </Checkbox>
            )}

            <HelpText className="text-[12px]">
              不指定作者 = 全部作者（包含无作者内容）；指定作者后默认只导出这些作者的帖子。
              导出不再有 20 / 50 / 100 的条数上限 —— 有多少符合条件的帖子就导出多少条。
            </HelpText>
          </div>

          {/* 各状态数量：让用户知道「为什么是这个数」 */}
          {taskPreview && (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[12px]">
              {STATE_SUMMARY_OPTIONS.filter(
                (opt) => (taskPreview.state_counts?.[opt.key] ?? 0) > 0
              ).map((opt) => (
                <span
                  key={opt.key}
                  className="rounded border border-border px-2 py-0.5 text-text-secondary"
                >
                  {opt.label}{" "}
                  <span className="tabular-nums text-foreground">
                    {taskPreview.state_counts[opt.key]}
                  </span>
                </span>
              ))}
              {scope === "pending" && (
                <span className="text-text-tertiary">
                  排队中 / 正在分析不会进入导出，需先转为外部分析
                </span>
              )}
            </div>
          )}

          {/*
            导出区：分支由 external-panel-mode 决定（可测的纯逻辑）
              · 有可导出状态      → 待导出 N 条 + 下载（N 为真实条数）
              · 存在排队中        → 待转出 + [转为外部分析]
              · 存在正在分析      → 只说明，无按钮
          */}
          <ExternalExportPanel
            states={taskPreview?.states ?? []}
            counting={counting}
            exportCount={taskPreview?.matching_count ?? null}
            authorCount={taskPreview?.author_count ?? 0}
            noAuthorCount={taskPreview?.no_author_count ?? 0}
            estimatedBytes={taskPreview?.estimated_bytes ?? 0}
            handoffCount={taskPreview?.state_counts?.queued ?? 0}
            processingCount={taskPreview?.state_counts?.processing ?? 0}
            handing={handing}
            handoffNote={handoffNote}
            onCopyPrompt={() => void copyPrompt()}
            onDownload={downloadTasks}
            onHandoff={() => void runHandoff()}
          />

          <AuthorSelector
            open={pickerOpen}
            onOpenChange={setPickerOpen}
            excludeIds={selectedAuthors.map((a) => a.id)}
            onSelect={(author) =>
              setSelectedAuthors((prev) =>
                prev.some((a) => a.id === author.id) ? prev : [...prev, author]
              )
            }
          />
        </div>
      </section>

      {/* ================= B. 导入分析结果 ================= */}
      <section className="mt-10">
        <SectionHeader title="导入分析结果" hint="0 AI · 严格校验后才写入" />

        <div className="mt-4 space-y-4 rounded-lg border border-border px-4 py-4">
          <div className="flex flex-wrap items-center gap-3">
            <input
              ref={(el) => {
                if (el) el.setAttribute("data-file-input", "external")
              }}
              type="file"
              accept=".jsonl,.json,application/json,application/x-ndjson"
              className="hidden"
              id="external-result-file"
              onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
            />
            <Button variant="outline" size="sm" asChild>
              <label htmlFor="external-result-file" className="cursor-pointer">
                <FileUp className="h-3.5 w-3.5" />
                选择结果文件
              </label>
            </Button>
            <span className="min-w-0 truncate text-[12px] text-text-tertiary">
              {fileName ?? "支持 .jsonl / .json"}
            </span>

            <Checkbox
              checked={overwrite}
              onChange={(next) => {
                setOverwrite(next)
                if (fileRef.current) void runPreview(next)
              }}
              className="ml-auto text-[12px] text-text-secondary"
            >
              覆盖已有分析
            </Checkbox>

            <Button
              size="sm"
              variant="outline"
              onClick={() => void runPreview(overwrite)}
              disabled={!fileName || previewing}
            >
              {previewing && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              预检
            </Button>
          </div>

          <HelpText className="text-[12px]">
            覆盖已有分析会替换当前分析结果，并影响写作画像、流量画像和跨作者洞察。
            当前系统没有历史版本或快照，覆盖后无法恢复。
          </HelpText>

          {/* 文件级错误：整份文件不允许 Confirm */}
          {preview?.file_error && (
            <div className="flex items-start gap-2 rounded border border-danger/25 bg-danger/5 px-3 py-2.5">
              <X className="mt-0.5 h-3.5 w-3.5 shrink-0 text-danger" />
              <div className="min-w-0">
                <p className="text-[13px] font-medium text-danger">文件级错误，无法导入</p>
                <p className="mt-0.5 text-[12px] leading-relaxed text-text-secondary">
                  {preview.file_error}
                </p>
              </div>
            </div>
          )}

          {preview?.warnings?.length ? (
            <ul className="space-y-1">
              {preview.warnings.map((w) => (
                <li key={w} className="text-[12px] text-warning">
                  {w}
                </li>
              ))}
            </ul>
          ) : null}

          {/* 分类计数（可点开看问题行） */}
          {preview && !preview.file_error && (
            <>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-[12px]">
                <span className="text-text-tertiary">
                  总结果{" "}
                  <span className="tabular-nums text-text-secondary">{preview.total}</span>
                </span>
                {CLASS_ORDER.filter((c) => counts[c]).map((c) => (
                  <button
                    key={c}
                    type="button"
                    onClick={() => setExpanded(expanded === c ? null : c)}
                    className={
                      expanded === c
                        ? "text-foreground"
                        : "text-text-secondary hover:text-foreground"
                    }
                  >
                    {CLASS_LABEL[c]}{" "}
                    <span className="tabular-nums font-medium">{counts[c]}</span>
                  </button>
                ))}
              </div>

              {expanded && (
                <div className="overflow-hidden rounded border border-divider">
                  {expandedItems.length === 0 ? (
                    <p className="px-3 py-2 text-[12px] text-text-tertiary">没有此类记录</p>
                  ) : (
                    expandedItems.slice(0, 200).map((item, idx) => (
                      <div
                        key={`${item.line}-${idx}`}
                        className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-divider px-3 py-2 text-[12px] last:border-0"
                      >
                        <span className="w-14 shrink-0 tabular-nums text-text-tertiary">
                          第 {item.line} 行
                        </span>
                        <span className="w-24 shrink-0 tabular-nums text-text-secondary">
                          {item.content_id ?? "—"}
                        </span>
                        {item.field_path && (
                          <span className="shrink-0 font-mono text-[11px] text-text-tertiary">
                            {item.field_path}
                          </span>
                        )}
                        <span className="min-w-0 flex-1 text-text-secondary">
                          {item.message}
                        </span>
                      </div>
                    ))
                  )}
                </div>
              )}

              <div className="flex flex-wrap items-center justify-between gap-3 border-t border-divider pt-3">
                <p className="text-[13px] text-text-secondary">
                  可导入{" "}
                  <span className="tabular-nums font-medium text-foreground">
                    {preview.valid_count}
                  </span>{" "}
                  条
                  {preview.valid_count === 0 && "（修正文件后重新预检）"}
                </p>
                <Button
                  size="sm"
                  onClick={() => void runConfirm()}
                  disabled={importing || preview.valid_count === 0}
                >
                  {importing ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Check className="h-3.5 w-3.5" />
                  )}
                  确认导入 {preview.valid_count} 条
                </Button>
              </div>
            </>
          )}

          {done && (
            <div className="flex items-center gap-2 rounded border border-border bg-canvas px-3 py-2.5 text-[13px] text-success">
              <Check className="h-3.5 w-3.5" />
              已导入 <span className="tabular-nums">{done.imported}</span> 条分析结果
              {done.model && (
                <Badge variant="neutral" className="ml-1">
                  {done.model}
                </Badge>
              )}
            </div>
          )}
        </div>

        <HelpText className="mt-3">
          导入结果会写入现有分析体系：写作画像、流量画像、跨作者洞察立即可见；
          风格档案按既有规则（每新增 5 条有效样本）自动更新。
          外部分析不进入「导入记录」，也没有批次撤销。
        </HelpText>
      </section>
    </div>
  )
}
