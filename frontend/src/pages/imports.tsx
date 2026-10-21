import { useCallback, useEffect, useState } from "react"
import { useNavigate } from "react-router-dom"
import { FileJson, Loader2, Pause, Play, RefreshCw, Trash2 } from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { PageHeader } from "@/components/common"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { api } from "@/lib/api"
import type { ImportBatch } from "@/lib/types"
import { cn, formatTime } from "@/lib/utils"

export default function ImportsPage() {
  const navigate = useNavigate()
  const [items, setItems] = useState<ImportBatch[]>([])
  const [loading, setLoading] = useState(true)
  // 正在执行的操作：用于禁用重复点击
  const [busy, setBusy] = useState<string | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<ImportBatch | null>(null)

  const load = useCallback(async () => {
    try {
      const data = await api.listImportBatches()
      setItems(data.items)
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  // 有运行中的任务时轻量轮询，让进度和按钮状态保持同步
  useEffect(() => {
    if (!items.some((b) => b.is_running)) return
    const timer = setInterval(() => void load(), 3000)
    return () => clearInterval(timer)
  }, [items, load])

  const runAction = async (key: string, fn: () => Promise<unknown>, ok: string) => {
    if (busy) return                    // 防重复点击
    setBusy(key)
    try {
      await fn()
      toast.success(ok)
      await load()
    } catch (err) {
      toast.error((err as Error).message, { duration: 6000 })
    } finally {
      setBusy(null)
    }
  }

  const pause = (batch: ImportBatch) =>
    runAction(
      `pause-${batch.import_batch_id}`,
      () => api.pauseAnalysis(batch.import_batch_id),
      "已暂停分析，未完成的内容已回到待分析"
    )

  const resume = (batch: ImportBatch) =>
    runAction(
      `resume-${batch.import_batch_id}`,
      () =>
        api.continueAnalysis(
          batch.import_batch_id,
          batch.run_kind === "batch" ? "batch" : "realtime"
        ),
      `已继续分析剩余 ${batch.unanalyzed} 条`
    )

  const confirmDelete = (mode: "keep_analyzed" | "all") =>
    runAction(
      `delete-${deleteTarget?.import_batch_id}`,
      async () => {
        if (!deleteTarget) return
        const r = await api.deleteImportBatch(deleteTarget.import_batch_id, mode)
        setDeleteTarget(null)
        return r
      },
      mode === "keep_analyzed" ? "已删除未分析内容" : "已删除本批次全部内容"
    )

  return (
    <div className="mx-auto w-full max-w-4xl px-6 py-8">
      {/*
        任务 07 S3 第二批：页头走公共 PageHeader。
        「刷新」入口（variant=ghost / size=sm / onClick）原样保留。
      */}
      <PageHeader
        title="导入记录"
        description="每次批量导入的批次、去重结果与当前占用情况。"
        actions={
          <Button variant="ghost" size="sm" onClick={() => void load()}>
            <RefreshCw className="h-4 w-4" />
            刷新
          </Button>
        }
      />

      {loading ? (
        <div className="flex justify-center py-16">
          <Loader2 className="h-5 w-5 animate-spin text-text-secondary" />
        </div>
      ) : items.length === 0 ? (
        <div className="mt-10 rounded-lg border border-dashed border-border py-16 text-center">
          <FileJson className="mx-auto h-5 w-5 text-text-secondary" />
          <p className="mt-3 text-sm text-text-secondary">
            还没有导入记录。到「批量导入」上传一份文件试试。
          </p>
        </div>
      ) : (
        <div className="mt-7 space-y-3">
          {items.map((batch) => (
            <div
              key={batch.import_batch_id}
              className="rounded-lg border border-border bg-surface p-4"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">
                    {batch.filename || "（未命名文件）"}
                  </p>
                  <p className="mt-0.5 text-xs text-text-secondary">
                    {formatTime(batch.created_at)}
                    <span className="ml-2 font-mono text-[11px] opacity-60">
                      #{batch.import_batch_id.slice(0, 8)}
                    </span>
                  </p>
                </div>
                <Badge variant={batch.source === "mine" ? "mine" : "benchmark"}>
                  {batch.source === "mine" ? "我的" : "对标"}
                </Badge>
              </div>

              {batch.author_username && (
                <p className="mt-2 text-xs text-text-secondary">
                  作者：<span className="text-foreground/80">{batch.author_username}</span>
                </p>
              )}

              <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs sm:grid-cols-3">
                <Stat label="文件记录" value={batch.input_count} />
                <Stat label="新增" value={batch.inserted_count} accent="text-success" />
                <Stat
                  label="重复跳过"
                  value={batch.duplicate_count}
                  accent={batch.duplicate_count > 0 ? "text-warning" : undefined}
                />
                <Stat label="当前内容" value={batch.remaining} />
                <Stat label="已分析" value={batch.analyzed} />
                <Stat label="未分析" value={batch.unanalyzed} />
              </div>

              <div className="mt-4 flex flex-wrap items-center gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  className=""
                  onClick={() =>
                    navigate(`/library?import_batch_id=${batch.import_batch_id}`)
                  }
                >
                  查看内容
                </Button>

                {/* 运行中 → 暂停；已暂停且仍有剩余 → 继续；全部完成 → 都不显示 */}
                {batch.is_running ? (
                  <Button
                    size="sm"
                    variant="outline"
                    className=""
                    disabled={busy !== null}
                    onClick={() => void pause(batch)}
                  >
                    {busy?.startsWith("pause") ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Pause className="h-3.5 w-3.5" />
                    )}
                    {busy?.startsWith("pause") ? "暂停中…" : "暂停分析"}
                  </Button>
                ) : batch.unanalyzed > 0 ? (
                  <Button
                    size="sm"
                    variant="outline"
                    className=""
                    disabled={busy !== null}
                    onClick={() => void resume(batch)}
                  >
                    {busy?.startsWith("resume") ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Play className="h-3.5 w-3.5" />
                    )}
                    继续分析（{batch.unanalyzed}）
                  </Button>
                ) : null}

                <Button
                  size="sm"
                  variant="ghost"
                  className=" text-text-secondary"
                  disabled={busy !== null}
                  onClick={() => setDeleteTarget(batch)}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  删除本批次
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      <p className="mt-6 text-[11px] leading-relaxed text-text-secondary">
        说明：删除批次只会删除<strong>本次真正新增</strong>的内容；
        因重复被跳过的内容属于更早的批次，不会受影响。
        去重依据是 X 帖子的原始 ID（external_id）。
      </p>

      {/* 删除方式选择 */}
      <Dialog open={deleteTarget !== null} onOpenChange={(o) => !o && setDeleteTarget(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>删除本批次内容</DialogTitle>
            <DialogDescription>
              {deleteTarget && (
                <>
                  当前：总计 {deleteTarget.remaining} · 已分析{" "}
                  {deleteTarget.analyzed} · 未分析 {deleteTarget.unanalyzed}
                  {deleteTarget.is_running && (
                    <span className="mt-1 block text-warning">
                      该批次分析正在运行，删除前会先暂停并等待结算。
                    </span>
                  )}
                </>
              )}
            </DialogDescription>
          </DialogHeader>

          <div className="mt-2 space-y-2">
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => void confirmDelete("keep_analyzed")}
              className={cn(
                "w-full rounded border border-border bg-surface px-3 py-2.5 text-left",
                "transition-colors hover:bg-hover disabled:opacity-50"
              )}
            >
              <span className="block text-[13px] font-medium">保留已分析，仅删除未分析</span>
              <span className="mt-0.5 block text-[11px] leading-relaxed text-text-secondary">
                保留已完成分析并落库的 {deleteTarget?.analyzed ?? 0} 条，
                删除尚未完成的 {deleteTarget?.unanalyzed ?? 0} 条。批次记录会保留。
              </span>
            </button>

            <button
              type="button"
              disabled={busy !== null}
              onClick={() => void confirmDelete("all")}
              className={cn(
                "w-full rounded border border-border bg-surface px-3 py-2.5 text-left",
                "transition-colors hover:bg-hover disabled:opacity-50"
              )}
            >
              <span className="block text-[13px] font-medium text-danger">
                删除本批次全部内容
              </span>
              <span className="mt-0.5 block text-[11px] leading-relaxed text-text-secondary">
                删除该批次仍存在的全部 {deleteTarget?.remaining ?? 0} 条内容，不可恢复。
              </span>
            </button>
          </div>

          <div className="mt-3 flex justify-end">
            <Button
              variant="ghost"
              size="sm"
              disabled={busy !== null}
              onClick={() => setDeleteTarget(null)}
            >
              取消
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function Stat({
  label,
  value,
  accent,
}: {
  label: string
  value: number
  accent?: string
}) {
  return (
    <p className="flex items-baseline gap-1.5">
      <span className="text-text-secondary">{label}</span>
      <span className={`tabular-nums ${accent ?? "text-foreground/90"}`}>{value}</span>
    </p>
  )
}
