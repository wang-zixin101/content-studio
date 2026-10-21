import { useCallback, useEffect, useRef, useState } from "react"
import {
  AlertTriangle,
  CheckCircle2,
  FileText,
  Loader2,
  Play,
  StopCircle,
  Undo2,
  Upload,
  X,
  Zap,
} from "lucide-react"
import { toast } from "sonner"

import { HelpText, PageHeader } from "@/components/common"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api } from "@/lib/api"
import {
  assignAuthor,
  batchIds,
  committableFiles,
  removeItemAt,
  summarizeDrafts,
  summarizeResults,
  toDraft,
  type AuthorScope,
  type ImportFileDraft,
} from "@/lib/import-batch"
import type { AnalysisRun, ImportFileResult, Source } from "@/lib/types"
import { cn } from "@/lib/utils"

const ACTIVE_RUN = new Set(["running", "cancelling"])
const FINISHED_RUN = new Set(["completed", "cancelled", "failed"])

export default function ImportPage() {
  const inputRef = useRef<HTMLInputElement>(null)

  // 导入来源：必须由用户明确选择，**没有默认值**。
  // 这是为了杜绝「500 条 benchmark 误进 mine」再次发生。
  const [source, setSource] = useState<Source | null>(null)
  const [drafts, setDrafts] = useState<ImportFileDraft[]>([])
  const [parsing, setParsing] = useState(false)
  const [importing, setImporting] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [batchAvailable, setBatchAvailable] = useState(true)

  // 每个文件一个作者输入框（key = draft id）
  const [authorInputs, setAuthorInputs] = useState<Record<string, string>>({})

  // 导入结果：入库后停在这里，是否分析完全由用户决定
  const [results, setResults] = useState<ImportFileResult[] | null>(null)
  const [importedSource, setImportedSource] = useState<Source | null>(null)
  const [run, setRun] = useState<AnalysisRun | null>(null)
  const [starting, setStarting] = useState(false)
  const [runError, setRunError] = useState<string | null>(null)

  const handleFiles = async (files: File[]) => {
    setParsing(true)
    try {
      const result = await api.parseImportBatch(files)
      setDrafts(result.files.map(toDraft))
      setAuthorInputs({})
      setBatchAvailable(result.batch_available)
      const t = result.totals
      if (t.ok_files === 0) {
        toast.error("所有文件都解析失败，请检查文件格式")
      } else if (t.failed_files > 0) {
        toast.warning(
          `${t.ok_files} 个文件解析成功（${t.count} 条），${t.failed_files} 个失败`
        )
      } else {
        toast.success(
          t.files === 1
            ? `解析出 ${t.count} 条内容`
            : `已解析 ${t.files} 个文件，共 ${t.count} 条`
        )
      }
    } catch (err) {
      toast.error((err as Error).message)
      setDrafts([])
    } finally {
      setParsing(false)
    }
  }

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const files = Array.from(e.dataTransfer.files ?? [])
    if (files.length) void handleFiles(files)
  }

  const clearAll = () => {
    setDrafts([])
    setAuthorInputs({})
    if (inputRef.current) inputRef.current.value = ""
  }

  const applyAuthor = (file: ImportFileDraft, scope: AuthorScope) => {
    const raw = authorInputs[file.id] ?? ""
    if (!raw.trim()) {
      toast.error("请先输入作者用户名")
      return
    }
    setDrafts((prev) =>
      prev.map((f) => (f.id === file.id ? assignAuthor(f, raw, scope) : f))
    )
  }

  // 入库：只写数据库，绝不调用 AI。
  // 每个文件**单独提交**，因此各自形成一条导入记录，部分失败也能精确报出。
  const commit = async () => {
    const files = committableFiles(drafts)
    if (files.length === 0) return
    if (!source) {
      toast.error("请选择导入来源：我的 / 对标")
      return
    }
    setImporting(true)
    const outcome: ImportFileResult[] = []
    try {
      for (const file of files) {
        try {
          const r = await api.commitImport(
            source,
            file.items.map((text, i) => ({ text, metadata: file.metadatas[i] ?? {} })),
            file.filename
          )
          outcome.push({
            filename: file.filename,
            ok: true,
            created: r.created,
            duplicates: r.duplicates,
            skipped: r.skipped,
            error: null,
            import_batch_id: r.import_batch_id ?? null,
          })
        } catch (err) {
          // 单个文件失败不中断其余文件；错误如实记录，不伪装成成功
          outcome.push({
            filename: file.filename,
            ok: false,
            created: 0,
            duplicates: 0,
            skipped: 0,
            error: (err as Error).message,
            import_batch_id: null,
          })
        }
      }

      setResults(outcome)
      setImportedSource(source)
      setRun(null)
      setRunError(null)
      clearAll()

      const summary = summarizeResults(outcome)
      if (summary.allOk) {
        toast.success(`已导入 ${summary.created} 条，尚未开始分析`)
      } else {
        toast.warning(
          `${summary.okFiles} 个文件导入成功、${summary.failedFiles} 个失败，共新增 ${summary.created} 条`
        )
      }
    } finally {
      setImporting(false)
    }
  }

  // 只有「恰好一个成功批次」时才内联显示分析操作；
  // 多批次请到「导入记录」页逐批处理，那里有完整的暂停/继续/删除。
  const succeededBatchIds = batchIds(results ?? [])
  const onlyBatchId = succeededBatchIds.length === 1 ? succeededBatchIds[0] : null

  const startAnalysis = async (mode: "realtime" | "batch") => {
    if (!onlyBatchId) return
    setStarting(true)
    setRunError(null)
    try {
      const created = await api.startAnalysis(onlyBatchId, mode)
      setRun(created)
      toast.success(mode === "batch" ? "已提交 Batch 分析" : "已开始实时分析")
    } catch (err) {
      // 明确展示失败原因（例如 Batch 提交失败），不静默改用其它通道
      const message = (err as Error).message
      setRunError(message)
      toast.error(message)
    } finally {
      setStarting(false)
    }
  }

  const stopAnalysis = async () => {
    if (!run) return
    try {
      const result = await api.cancelRun(run.id)
      toast.success(
        result.reverted > 0
          ? `已停止，${result.reverted} 条退回未分析`
          : "已停止分析"
      )
      if (onlyBatchId) {
        const runs = await api.listRuns(onlyBatchId)
        if (runs[0]) setRun(runs[0])
      }
    } catch (err) {
      toast.error((err as Error).message)
    }
  }

  const undo = async () => {
    const ids = batchIds(results ?? [])
    if (ids.length === 0) return
    const total = (results ?? []).reduce((n, r) => n + r.created, 0)
    const ok = window.confirm(
      ids.length === 1
        ? `确定撤销本次导入？将删除这 ${total} 条内容，且不可恢复。`
        : `确定撤销本次导入？将删除 ${ids.length} 个批次共 ${total} 条内容，且不可恢复。`
    )
    if (!ok) return

    let deleted = 0
    let failed = 0
    for (const id of ids) {
      try {
        deleted += (await api.undoImport(id)).deleted
      } catch {
        failed += 1
      }
    }
    if (failed > 0) {
      toast.warning(`已删除 ${deleted} 条，${failed} 个批次撤销失败`)
    } else {
      toast.success(`已撤销本次导入，删除 ${deleted} 条`)
    }
    setResults(null)
    setRun(null)
    setRunError(null)
  }

  const refreshRun = useCallback(async () => {
    if (!onlyBatchId) return
    try {
      const runs = await api.listRuns(onlyBatchId)
      if (runs[0]) setRun(runs[0])
    } catch {
      /* 轮询失败静默，不打扰用户 */
    }
  }, [onlyBatchId])

  // 分析进行中时轮询进度
  useEffect(() => {
    if (!run || FINISHED_RUN.has(run.status)) return
    const timer = setInterval(() => void refreshRun(), 2000)
    return () => clearInterval(timer)
  }, [run?.id, run?.status, refreshRun])

  const unfinished =
    (run?.breakdown?.unprocessed ?? 0) +
    (run?.breakdown?.queued ?? 0) +
    (run?.breakdown?.processing ?? 0)

  const summary = summarizeDrafts(drafts)
  const outcome = results ? summarizeResults(results) : null

  return (
    <div className="mx-auto w-full max-w-4xl px-6 py-8">
      {/*
        任务 07 S3 第二批：页头走公共 PageHeader。
        说明文字里有内联强调（「不会自动调用 AI」），因此以 ReactNode 传入；
        PageHeader 的 description 类型为此从 string 放宽到 ReactNode，
        既有传 string 的页面（我的 / 跨作者洞察 / 外部分析）行为不变。
      */}
      <PageHeader
        title="批量导入"
        description={
          <>
            支持 JSON / TXT / Markdown / CSV，可一次选择多个文件。导入只入库，
            <span className="text-foreground">不会自动调用 AI</span>，是否分析由你决定。
          </>
        }
      />

      {/* ---------- 导入结果面板（优先展示） ---------- */}
      {results && outcome && (
        <section className="mt-7 rounded-lg border border-border bg-canvas p-5">
          <div className="flex items-start gap-2.5">
            {outcome.allOk ? (
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success" />
            ) : (
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
            )}
            <div className="flex-1">
              <p className="text-sm font-medium">
                {outcome.allOk
                  ? `已导入 ${outcome.created} 条`
                  : `${outcome.okFiles} 个文件成功、${outcome.failedFiles} 个失败`}
                {outcome.duplicates > 0 && (
                  <span className="ml-2 text-xs font-normal text-warning">
                    重复跳过 {outcome.duplicates} 条
                  </span>
                )}
                {outcome.skipped > 0 && (
                  <span className="ml-2 text-xs font-normal text-text-secondary">
                    无有效正文 {outcome.skipped} 条
                  </span>
                )}
              </p>
              <p className="mt-0.5 text-xs text-text-secondary">
                来源：
                <span className="font-medium text-foreground">
                  {importedSource === "benchmark" ? "对标" : "我的"}
                </span>
                {" · "}
                {results.length} 个文件 · 状态：{run ? runLabel(run) : "未分析"}
              </p>
            </div>
          </div>

          {/* 逐文件实际结果：局部失败必须如实显示 */}
          <div className="mt-4 space-y-1.5">
            {results.map((r, i) => (
              <div
                key={`${r.filename}-${r.import_batch_id ?? `fail-${i}`}`}
                className={cn(
                  "flex flex-wrap items-center gap-x-3 gap-y-1 rounded border px-3 py-2 text-xs",
                  r.ok ? "border-border" : "border-danger/25 bg-danger/5"
                )}
              >
                <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-text-secondary">
                  {r.filename}
                </span>
                {r.ok ? (
                  <>
                    <span className="tabular-nums text-foreground">新增 {r.created}</span>
                    {r.duplicates > 0 && (
                      <span className="tabular-nums text-warning">重复 {r.duplicates}</span>
                    )}
                    {r.skipped > 0 && (
                      <span className="tabular-nums text-text-secondary">跳过 {r.skipped}</span>
                    )}
                    {r.import_batch_id && (
                      <span className="font-mono text-[11px] opacity-50">
                        #{r.import_batch_id.slice(0, 8)}
                      </span>
                    )}
                  </>
                ) : (
                  <span className="text-danger">{r.error}</span>
                )}
              </div>
            ))}
          </div>

          {/* 进度（仅单批次内联分析时显示） */}
          {run && (
            <div className="mt-4 space-y-2">
              <div className="flex items-center justify-between text-xs">
                <span className="text-text-secondary">
                  {run.kind === "batch" ? "Batch 分析" : "实时分析"}
                  {ACTIVE_RUN.has(run.status) && "中"}
                </span>
                <span className="tabular-nums text-text-secondary">
                  已完成 {run.done} / {run.total}
                  {run.failed > 0 && (
                    <span className="ml-2 text-danger">失败 {run.failed}</span>
                  )}
                  {unfinished > 0 && ACTIVE_RUN.has(run.status) && (
                    <span className="ml-2">待处理 {unfinished}</span>
                  )}
                </span>
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-border">
                <div
                  className="h-full rounded-full bg-foreground transition-all"
                  style={{
                    width: `${run.total ? Math.min(100, (run.done / run.total) * 100) : 0}%`,
                  }}
                />
              </div>
            </div>
          )}

          {/* 失败原因：明确展示，绝不静默 */}
          {runError && (
            <div className="mt-4 flex items-start gap-2 rounded border border-danger/25 bg-danger/5 p-3">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-danger" />
              <p className="text-xs leading-relaxed text-danger">{runError}</p>
            </div>
          )}

          {run?.error && !runError && (
            <div className="mt-4 flex items-start gap-2 rounded border border-danger/25 bg-danger/5 p-3">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-danger" />
              <p className="text-xs leading-relaxed text-danger">{run.error}</p>
            </div>
          )}

          {/* 已取消的计费提示 */}
          {run?.status === "cancelled" && (
            <p className="mt-3 text-[11px] leading-relaxed text-text-secondary">
              已请求取消。不会再启动未执行的请求；但已经完成的请求仍可能产生费用。
            </p>
          )}

          <div className="mt-5 flex flex-wrap items-center gap-2">
            {onlyBatchId && (!run || FINISHED_RUN.has(run.status)) && (
              <>
                {batchAvailable && (
                  <Button
                    size="sm"
                    disabled={starting}
                    onClick={() => void startAnalysis("batch")}
                  >
                    {starting ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Zap className="h-4 w-4" />
                    )}
                    Batch Pro 分析
                  </Button>
                )}
                <Button
                  size="sm"
                  variant={batchAvailable ? "outline" : "default"}
                  disabled={starting}
                  onClick={() => void startAnalysis("realtime")}
                >
                  {starting ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Play className="h-4 w-4" />
                  )}
                  实时 Pro 分析
                </Button>
              </>
            )}

            {run && ACTIVE_RUN.has(run.status) && (
              <Button size="sm" variant="outline" onClick={() => void stopAnalysis()}>
                <StopCircle className="h-4 w-4" />
                {run.kind === "batch" ? "取消分析" : "停止分析"}
              </Button>
            )}

            <Button size="sm" variant="ghost" onClick={() => setResults(null)}>
              暂不分析
            </Button>
            {succeededBatchIds.length > 0 && (
              <Button
                size="sm"
                variant="ghost"
                className="text-text-secondary"
                onClick={() => void undo()}
              >
                <Undo2 className="h-4 w-4" />
                撤销本次导入
              </Button>
            )}
          </div>

          {onlyBatchId && !run && (
            <p className="mt-3 text-[11px] text-text-secondary">
              {batchAvailable
                ? "内容已入库但未分析，不会产生任何费用。"
                : "Batch 不可用（未配置 AI_BATCH_BASE_URL），可先用实时分析。"}
            </p>
          )}
          {succeededBatchIds.length > 1 && (
            <p className="mt-3 text-[11px] text-text-secondary">
              本次涉及 {succeededBatchIds.length} 个批次（每个文件一条记录）。
              逐批分析、暂停或删除请到「导入记录」页操作。
            </p>
          )}
        </section>
      )}

      {/* ---------- 文件选择 ---------- */}
      <div
        onDragOver={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={cn(
          "mt-7 rounded-lg border border-dashed border-border p-10 text-center transition-colors",
          dragging && "border-foreground/40 bg-hover/60"
        )}
      >
        <input
          ref={inputRef}
          type="file"
          multiple
          className="hidden"
          accept=".json,.txt,.md,.markdown,.csv,application/json,text/plain,text/csv"
          onChange={(e) => {
            const files = Array.from(e.target.files ?? [])
            e.target.value = "" // 允许再次选择同一批文件
            if (files.length) void handleFiles(files)
          }}
        />
        <div className="flex flex-col items-center gap-3">
          {parsing ? (
            <Loader2 className="h-5 w-5 animate-spin text-text-secondary" />
          ) : (
            <Upload className="h-5 w-5 text-text-secondary" />
          )}
          <div className="space-y-1">
            <p className="text-sm">
              {parsing ? "正在解析…" : "拖入文件，或"}
              {!parsing && (
                <button
                  type="button"
                  className="ml-1 underline underline-offset-4 hover:text-foreground"
                  onClick={() => inputRef.current?.click()}
                >
                  选择文件
                </button>
              )}
            </p>
            <p className="text-xs text-text-secondary">
              JSON（Xporter）/ TXT / Markdown / CSV · 可多选
            </p>
          </div>
        </div>
      </div>

      {/* ---------- 导入来源 ---------- */}
      {/* 放在上传区下方、预览之前：用户在解析前就能明确选好，且没有默认值 */}
      <section className="mt-6">
        <div className="flex items-center gap-3">
          <h2 className="text-[13px] font-medium">导入来源</h2>
          {!source && <span className="text-[11px] text-warning">必选</span>}
        </div>

        <div className="mt-2.5 flex items-start gap-2">
          {(
            [
              {
                key: "mine" as Source,
                label: "我的",
                hint: "你自己的历史内容，可作为个人内容语料。",
              },
              {
                key: "benchmark" as Source,
                label: "对标",
                hint: "其他作者内容，用于研究与参考，不参与个人风格学习。",
              },
            ] as const
          ).map((opt) => {
            const active = source === opt.key
            return (
              <button
                key={opt.key}
                type="button"
                onClick={() => setSource(opt.key)}
                aria-pressed={active}
                className={cn(
                  "flex-1 rounded border px-3 py-2 text-left transition-colors duration-150",
                  active
                    ? "border-brand/40 bg-selected"
                    : "border-border bg-surface hover:bg-hover"
                )}
              >
                <span
                  className={cn(
                    "block text-[13px]",
                    active ? "font-medium text-foreground" : "text-text-secondary"
                  )}
                >
                  {opt.label}
                </span>
                <span className="mt-0.5 block text-[11px] leading-relaxed text-text-secondary">
                  {opt.hint}
                </span>
              </button>
            )
          })}
        </div>

        {!source && (
          <HelpText className="mt-2">
            系统无法从作者或文件名推断来源，必须由你明确选择。
            未选择时可以先上传和预览，但不会写入数据库。
          </HelpText>
        )}
      </section>

      {/* ---------- 预览（按文件分组） ---------- */}
      {drafts.length > 0 && (
        <div className="mt-6">
          <div className="flex items-center justify-between">
            <p className="text-sm">
              <span className="font-medium">
                {summary.files === 1 ? "1 个文件" : `${summary.files} 个文件`}
              </span>
              <span className="ml-2 text-text-secondary">
                · {summary.count} 条 · 预计新增 {summary.newCount}
                {summary.duplicateCount > 0 && ` · 重复 ${summary.duplicateCount}`}
              </span>
            </p>
            <Button variant="ghost" size="sm" className="text-xs" onClick={clearAll}>
              清空
            </Button>
          </div>

          {summary.failedFiles > 0 && (
            <div className="mt-3 flex items-start gap-2 rounded border border-danger/25 bg-danger/5 p-3">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-danger" />
              <p className="text-xs leading-relaxed text-danger">
                {summary.failedFiles} 个文件解析失败，不会被提交。其余文件不受影响。
              </p>
            </div>
          )}

          {summary.missingAuthorCount > 0 && (
            <div className="mt-3 flex items-start gap-2 rounded border border-warning/25 bg-warning/5 p-3">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" />
              <p className="text-xs leading-relaxed text-warning">
                有 {summary.missingAuthorCount} 条没有作者标识。它们不会归入任何账号，
                请用下面的输入框为对应文件指定作者。
              </p>
            </div>
          )}

          <div className="mt-3 space-y-3">
            {drafts.map((file, idx) => (
              <div
                key={file.id}
                className={cn(
                  "rounded-lg border p-3",
                  file.ok ? "border-border" : "border-danger/25 bg-danger/5"
                )}
              >
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <span className="w-5 shrink-0 text-right text-[11px] tabular-nums text-text-secondary">
                    {idx + 1}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-xs font-medium">
                    {file.filename}
                  </span>
                  {file.ok ? (
                    <span className="text-[11px] tabular-nums text-text-secondary">
                      {file.items.length} 条 · 预计新增{" "}
                      {Math.min(file.newCount, file.items.length)}
                      {file.duplicateCount > 0 && ` · 重复 ${file.duplicateCount}`}
                    </span>
                  ) : (
                    <span className="text-[11px] text-danger">{file.error}</span>
                  )}
                </div>

                {file.ok && (
                  <>
                    <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 pl-8">
                      <span className="text-[11px] text-text-secondary">作者：</span>
                      {file.authors.length > 0 ? (
                        <span className="text-[11px] font-medium text-foreground">
                          {file.authors.join("、")}
                        </span>
                      ) : (
                        <span className="text-[11px] text-warning">未识别</span>
                      )}
                      {file.missingAuthorCount > 0 && (
                        <span className="text-[11px] text-warning">
                          · 缺作者 {file.missingAuthorCount} 条
                        </span>
                      )}
                    </div>

                    <div className="mt-2 flex flex-wrap items-center gap-2 pl-8">
                      <Input
                        value={authorInputs[file.id] ?? ""}
                        onChange={(e) =>
                          setAuthorInputs((prev) => ({
                            ...prev,
                            [file.id]: e.target.value,
                          }))
                        }
                        placeholder="指定作者用户名，如 @someone"
                        className="h-7 w-52 text-xs"
                      />
                      <Button
                        size="sm"
                        variant="outline"
                        className="h-7 text-[11px]"
                        onClick={() => applyAuthor(file, "all")}
                      >
                        应用到全部 {file.items.length} 条
                      </Button>
                      {file.missingAuthorCount > 0 && (
                        <Button
                          size="sm"
                          variant="outline"
                          className="h-7 text-[11px]"
                          onClick={() => applyAuthor(file, "missing")}
                        >
                          仅填补无作者 {file.missingAuthorCount} 条
                        </Button>
                      )}
                    </div>

                    {file.items.length > 0 && (
                      <div className="mt-2 max-h-56 space-y-1 overflow-y-auto rounded border border-border p-2">
                        {file.items.map((text, i) => (
                          <div
                            key={i}
                            className="group flex items-start gap-2 rounded px-2 py-1 hover:bg-hover"
                          >
                            <span className="mt-0.5 w-6 shrink-0 text-right text-[11px] tabular-nums text-text-secondary">
                              {i + 1}
                            </span>
                            <p className="flex-1 text-xs leading-relaxed text-text-secondary">
                              {text.length > 120 ? `${text.slice(0, 120)}…` : text}
                            </p>
                            <button
                              type="button"
                              onClick={() =>
                                setDrafts((prev) =>
                                  prev.map((f) =>
                                    f.id === file.id ? removeItemAt(f, i) : f
                                  )
                                )
                              }
                              className="mt-0.5 opacity-0 transition-opacity group-hover:opacity-100"
                              aria-label="移除"
                            >
                              <X className="h-3.5 w-3.5 text-text-secondary hover:text-foreground" />
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                  </>
                )}
              </div>
            ))}
          </div>

          <div className="mt-4 flex items-center gap-3">
            <span className="text-xs text-text-secondary">
              来源：
              {source ? (
                <span className="ml-1 font-medium text-foreground">
                  {source === "mine" ? "我的" : "对标"}
                </span>
              ) : (
                <span className="ml-1 text-warning">尚未选择</span>
              )}
            </span>
            <div className="ml-auto flex items-center gap-3">
              <span className="text-xs text-text-secondary">
                将提交 {committableFiles(drafts).length} 个文件 · {summary.count} 条
              </span>
              <Button
                onClick={() => void commit()}
                disabled={importing || !source || summary.okFiles === 0}
                title={!source ? "请先选择导入来源" : undefined}
              >
                {importing && <Loader2 className="h-4 w-4 animate-spin" />}
                确认导入
              </Button>
            </div>
          </div>
        </div>
      )}

      <p className="mt-6 flex items-start gap-1.5 text-[11px] leading-relaxed text-text-secondary">
        <FileText className="mt-0.5 h-3 w-3 shrink-0" />
        多个 TXT / Markdown 请用单独一行 ===POST=== 分隔 · CSV 每行一条 ·
        JSON 每条帖子一条 · 每个文件单独形成一条导入记录
      </p>
    </div>
  )
}

function runLabel(run: AnalysisRun): string {
  switch (run.status) {
    case "running":
      return run.kind === "batch" ? "Batch 分析中" : "实时分析中"
    case "cancelling":
      return "正在取消…"
    case "cancelled":
      return "已取消"
    case "completed":
      return "分析完成"
    case "failed":
      return "分析失败"
    default:
      return run.status
  }
}
