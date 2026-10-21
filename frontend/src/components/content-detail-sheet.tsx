import { useEffect, useState } from "react"
import { useNavigate } from "react-router-dom"
import { Loader2, RefreshCw, Trash2 } from "lucide-react"
import { toast } from "sonner"

import { StatusBadge } from "@/components/common"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
} from "@/components/ui/sheet"
import { api } from "@/lib/api"
import type { ContentDetail } from "@/lib/types"
import { formatNumber } from "@/lib/format"
import { formatTime } from "@/lib/utils"
import { newMaterialOpenTicket } from "@/lib/write-session"

interface Props {
  contentId: number | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onSelectContent: (id: number) => void
  onChanged: () => void
}

export function ContentDetailSheet({
  contentId,
  open,
  onOpenChange,
  onSelectContent,
  onChanged,
}: Props) {
  const navigate = useNavigate()
  const [detail, setDetail] = useState<ContentDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [togglingEligible, setTogglingEligible] = useState(false)

  const load = async (id: number, silent = false) => {
    if (!silent) setLoading(true)
    try {
      setDetail(await api.getContent(id))
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      if (!silent) setLoading(false)
    }
  }

  useEffect(() => {
    if (open && contentId != null) void load(contentId)
  }, [open, contentId])

  // 未分析完成时轮询
  useEffect(() => {
    if (!open || !detail) return
    if (detail.status !== "pending" && detail.status !== "analyzing") return
    const timer = setInterval(() => void load(detail.id, true), 2500)
    return () => clearInterval(timer)
  }, [open, detail])

  const reanalyze = async () => {
    if (!detail) return
    setBusy(true)
    try {
      await api.reanalyze(detail.id)
      toast.success("已重新加入分析队列")
      await load(detail.id, true)
      onChanged()
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  // 切换「参与风格学习」；只改这一个字段，不触发 AI 分析
  const toggleEligible = async () => {
    if (!detail) return
    setTogglingEligible(true)
    try {
      const next = !detail.style_eligible
      await api.setStyleEligible(detail.id, next)
      toast.success(next ? "已设为参与风格学习" : "已设为不参与风格学习")
      await load(detail.id, true)
      onChanged()
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setTogglingEligible(false)
    }
  }

  const remove = async () => {
    if (!detail) return
    if (!confirm("确定删除这条内容？此操作不可撤销。")) return    setBusy(true)
    try {
      await api.deleteContent(detail.id)
      toast.success("已删除")
      onOpenChange(false)
      onChanged()
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const analysis = detail?.analysis
  // 没有表现数据（TXT / MD / 手工输入）时整区不显示
  const perf = detail?.performance ?? null

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="sm:max-w-2xl">
        {loading || !detail ? (
          <div className="flex flex-1 items-center justify-center">
            <Loader2 className="h-5 w-5 animate-spin text-text-secondary" />
          </div>
        ) : (
          <>
            <SheetHeader>
              <div className="flex items-center gap-2">
                <Badge variant={detail.source === "mine" ? "mine" : "benchmark"}>
                  {detail.source === "mine" ? "我的" : "对标"}
                </Badge>
                <StatusBadge
                  status={detail.status}
                  analysisState={detail.analysis_state}
                  error={detail.analysis_error}
                />
                <span className="text-xs text-text-tertiary">
                  {formatTime(detail.created_at)}
                </span>
              </div>
              {/*
                顶部**只放 metadata**（B3 #22 / #23）：
                正文只在「原文」出现一次；topic / content_type 全部归到「内容分析」。
              */}
              <SheetDescription className="pt-1.5 text-[12px] text-text-tertiary">
                {detail.author_username ? `@${detail.author_username}` : "无作者"}
                {" · "}
                <span className="tabular-nums">{detail.raw_text.length}</span> 字
              </SheetDescription>
            </SheetHeader>

            <div className="min-h-0 flex-1 overflow-y-auto p-5">
              {/* 风格学习：轻量属性行，不再是一块灰卡（B3 #24） */}
              <div className="mb-4 flex items-center justify-between gap-3 text-[12px]">
                <span className="text-text-tertiary">风格学习</span>
                {detail.source === "mine" ? (
                  <button
                    type="button"
                    disabled={togglingEligible}
                    onClick={() => void toggleEligible()}
                    className="text-text-secondary hover:text-foreground"
                  >
                    {detail.style_eligible ? "已参与" : "未参与"}
                  </button>
                ) : (
                  <span className="text-text-secondary">不参与</span>
                )}
              </div>

              {/* 我的版本入口 */}
              {detail.source === "benchmark" && (
                <div className="mb-5 flex items-center justify-between gap-3">
                  <span className="text-[12px] text-text-tertiary">
                    想基于这条素材生成自己的版本？
                  </span>
                  {/*
                    任务 06 第二轮验收：这里传一张**一次性票据**。
                    「改写这条」是一次明确的打开意图 —— 即使之前打开并清空过
                    同一个素材，也要重新载入；而普通的切页返回必须原样恢复现场。
                    两者路由相同，只能靠 nav state 区分。
                  */}
                  <Button
                    size="sm"
                    onClick={() =>
                      navigate(`/write/${detail.id}`, {
                        state: {
                          openMaterial: {
                            id: detail.id,
                            ticket: newMaterialOpenTicket(),
                          },
                        },
                      })
                    }
                  >
                    改写这条
                  </Button>
                </div>
              )}

              {/* 原文 */}
              <Section title="原文">
                <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-foreground/90">
                  {detail.raw_text}
                </p>
              </Section>

              {/* 表现数据（只有 Xporter 导入带数据时才出现） */}
              {perf && (
                <>
                  <Separator className="my-5" />
                  {/* B3 #26：表现数据做成一条紧凑数字行，不拉成属性表 */}
                  <Section title="表现数据">
                    <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1 text-[13px]">
                      {(
                        [
                          ["曝光", perf.view_count],
                          ["点赞", perf.favorite_count],
                          ["回复", perf.reply_count],
                          ["转发", perf.retweet_count],
                          ["引用", perf.quote_count],
                          ["收藏", perf.bookmark_count],
                        ] as const
                      ).map(([label, value]) => (
                        <span key={label} className="text-text-tertiary">
                          {label}{" "}
                          <span className="tabular-nums text-foreground">
                            {fmtCount(value)}
                          </span>
                        </span>
                      ))}
                    </div>
                    <p className="mt-2 text-[12px] text-text-tertiary">
                      置顶 {perf.is_pinned ? "是" : "否"} · 采集于{" "}
                      {formatTime(perf.captured_at)}
                    </p>
                  </Section>
                </>
              )}

              <Separator className="my-5" />

              {/* AI 分析：四组结果，自然语言优先 + 原文证据 */}
              <Section title="内容分析">
                {analysis ? (
                  <div className="space-y-4">
                    <FieldGrid
                      items={[
                        ["主题", analysis.topic],
                        ["子主题", analysis.subtopic],
                        ["内容类型", analysis.content_type],
                        ["字数", analysis.length_chars],
                      ]}
                    />
                    <DetailRow label="核心含义" value={analysis.core_message} />

                    <ChipRow label="标签" items={analysis.tags} variant="badge" />
                    <ChipRow label="关键词" items={analysis.keywords} variant="outline" />

                    {analysis.writing && (
                      <AnalysisGroup title="写法">
                        <FieldGrid
                          items={[
                            ["开头方式", analysis.writing.opening_type],
                            ["整体结构", analysis.writing.structure_type],
                            ["结尾方式", analysis.writing.ending_type],
                          ]}
                        />
                        <EvidenceList
                          items={analysis.writing.moves.map((m) => ({
                            type: m.type,
                            detail: m.mechanism,
                            evidence: m.evidence,
                          }))}
                          emptyHint="本文没有识别到明显的修辞 / 互动动作"
                        />
                      </AnalysisGroup>
                    )}

                    {analysis.style && (
                      <AnalysisGroup title="语言（仅描述这一篇）">
                        <FieldGrid
                          items={[
                            ["语气", analysis.style.tone],
                            ["语体", analysis.style.register],
                            ["直接程度", analysis.style.directness],
                            ["确定性", analysis.style.certainty],
                            ["句式节奏", analysis.style.rhythm],
                          ]}
                        />
                        <EvidenceList
                          items={analysis.style.markers.map((m) => ({
                            type: m.type,
                            evidence: m.evidence,
                          }))}
                          emptyHint="本文没有识别到显著的形式标记"
                        />
                        <ChipRow
                          label="显眼用语"
                          items={analysis.style.distinctive_phrases}
                          variant="outline"
                        />
                      </AnalysisGroup>
                    )}

                    {analysis.content && (
                      <AnalysisGroup title="内容约束">
                        <PlainList
                          label="改写必须保留"
                          items={analysis.content.must_preserve}
                        />
                        <PlainList
                          label="看不到的信息（不得凭空补写）"
                          items={analysis.content.context_limits}
                        />
                        {analysis.content.claims.length > 0 && (
                          <div>
                            <p className="mb-1.5 text-xs text-text-secondary">
                              主张与归属
                            </p>
                            <ul className="space-y-1.5">
                              {analysis.content.claims.map((c, i) => (
                                <li
                                  key={`${c.kind}-${i}`}
                                  className="rounded border border-border p-2"
                                >
                                  <div className="flex items-center gap-2">
                                    <Badge variant="secondary">{c.kind}</Badge>
                                    <span className="text-sm text-foreground/90">
                                      {c.text}
                                    </span>
                                  </div>
                                  <p className="mt-1 text-[11px] italic text-text-tertiary">
                                    「{c.evidence}」
                                  </p>
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}
                      </AnalysisGroup>
                    )}

                    {analysis.reuse && (
                      <AnalysisGroup title="可复用方法">
                        <EvidenceList
                          items={analysis.reuse.patterns.map((p) => ({
                            type: p.method,
                            detail: p.how_to_apply,
                            evidence: p.evidence,
                          }))}
                          emptyHint="本文没有可迁移的写法"
                        />
                        <PlainList
                          label="不宜直接迁移"
                          items={analysis.reuse.boundaries}
                        />
                      </AnalysisGroup>
                    )}

                    <p className="text-[11px] text-text-tertiary">
                      {analysis.model || "未知模型"}
                      {analysis.prompt_version
                        ? ` · ${analysis.prompt_version}`
                        : ""}
                      {analysis.schema_version
                        ? ` · ${analysis.schema_version}`
                        : ""}
                    </p>
                  </div>
                ) : (
                  <p className="text-sm text-text-secondary">
                    {detail.status === "failed"
                      ? detail.analysis_error || "分析失败"
                      : "分析中…"}
                  </p>
                )}
              </Section>

              {/* 来源元数据（Xporter 扩展字段，只读） */}
              {(detail.source_meta?.language ||
                detail.source_meta?.reply_to_username ||
                detail.source_meta?.conversation_id ||
                (detail.source_meta?.urls?.length ?? 0) > 0 ||
                (detail.source_meta?.hashtags?.length ?? 0) > 0 ||
                (detail.source_meta?.media_urls?.length ?? 0) > 0) && (
                <>
                  <Separator className="my-5" />
                  <Section title="来源信息">
                    <FieldGrid
                      items={[
                        // 设计 §5：未知语言照常保留，但必须**标记**出来 ——
                        // 空白会让人误以为「没有这个字段」而不是「来源没标注」。
                        [
                          "语言",
                          detail.source_meta?.language
                            ? detail.source_meta.language === "zh"
                              ? "zh（中文）"
                              : detail.source_meta.language
                            : "未标注",
                        ],
                        [
                          "回复",
                          detail.source_meta?.reply_to_username
                            ? `@${detail.source_meta.reply_to_username}`
                            : null,
                        ],
                        ["会话 id", detail.source_meta?.conversation_id ?? null],
                      ]}
                    />
                    <ChipRow
                      label="话题标签"
                      items={detail.source_meta?.hashtags ?? []}
                      variant="outline"
                    />
                    <LinkList
                      label="引用链接"
                      items={detail.source_meta?.urls ?? []}
                    />
                    <LinkList
                      label="媒体地址"
                      items={detail.source_meta?.media_urls ?? []}
                    />
                  </Section>
                </>
              )}

              {/* 相似内容 */}
              {detail.similar.length > 0 && (
                <>
                  <Separator className="my-5" />
                  <Section title="相似内容（我的）">
                    <div className="space-y-2">
                      {detail.similar.map((s) => (
                        <button
                          key={s.id}
                          onClick={() => onSelectContent(s.id)}
                          className="w-full rounded-lg border border-border p-3 text-left transition-colors hover:bg-hover"
                        >
                          <p className="line-clamp-2 text-sm text-foreground/90">
                            {s.excerpt}
                          </p>
                          <div className="mt-1.5 flex items-center gap-2 text-[11px] text-text-tertiary">
                            <span>相似度 {(s.score * 100).toFixed(0)}%</span>
                            {s.topic && <span>· {s.topic}</span>}
                            {s.shared_tags.slice(0, 3).map((t) => (
                              <span key={t}>#{t}</span>
                            ))}
                          </div>
                        </button>
                      ))}
                    </div>
                  </Section>
                </>
              )}
            </div>

            {/* 底部操作 */}
            <div className="flex items-center justify-between gap-2 border-t border-border p-4">
              <Button
                variant="ghost"
                size="sm"
                className="text-destructive hover:text-destructive"
                onClick={() => void remove()}
                disabled={busy}
              >
                <Trash2 className="h-4 w-4" />
                删除
              </Button>
              {/* B3 #28：排队中 / 分析中不给可点击的重新分析 */}
              {detail.status !== "pending" && detail.status !== "analyzing" && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void reanalyze()}
                  disabled={busy}
                >
                  <RefreshCw className={busy ? "h-3.5 w-3.5 animate-spin" : "h-3.5 w-3.5"} />
                  重新分析
                </Button>
              )}
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  )
}

/** 计数格式化：未知（null）保持不显示，0 正常显示为 0。 */
/** 详情里给**精确**数字（B3 §35：列表紧凑、详情精确）。 */
function fmtCount(value: number | null): string | null {
  if (value === null) return null
  return formatNumber(value)
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="mb-2.5 text-[13px] font-semibold leading-none text-text-secondary">
        {title}
      </h3>
      {children}
    </div>
  )
}

function FieldGrid({ items }: { items: [string, string | number | null][] }) {
  const valid = items.filter(([, v]) => v !== null && v !== "")
  if (valid.length === 0) return null
  return (
    <div className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3">
      {valid.map(([label, value]) => (
        <div key={label}>
          <p className="text-[11px] text-text-secondary">{label}</p>
          <p className="mt-0.5 text-sm text-foreground/90">{value}</p>
        </div>
      ))}
    </div>
  )
}

function DetailRow({ label, value }: { label: string; value: string | null }) {
  if (!value) return null
  return (
    <div>
      <p className="text-[11px] text-text-secondary">{label}</p>
      <p className="mt-0.5 text-sm leading-relaxed text-foreground/90">{value}</p>
    </div>
  )
}

function AnalysisGroup({
  title,
  children,
}: {
  title: string
  children: React.ReactNode
}) {
  return (
    <div className="rounded-lg border border-border p-3">
      <p className="mb-2 text-xs font-medium text-text-secondary">{title}</p>
      <div className="space-y-3">{children}</div>
    </div>
  )
}

function ChipRow({
  label,
  items,
  variant,
}: {
  label: string
  items: string[]
  variant: "badge" | "outline"
}) {
  if (items.length === 0) return null
  return (
    <div>
      <p className="mb-1.5 text-xs text-text-secondary">{label}</p>
      <div className="flex flex-wrap gap-1.5">
        {items.map((item) =>
          variant === "badge" ? (
            <Badge key={item} variant="secondary">
              {item}
            </Badge>
          ) : (
            <span
              key={item}
              className="rounded border border-border px-1.5 py-0.5 text-[11px] text-text-secondary"
            >
              {item}
            </span>
          )
        )}
      </div>
    </div>
  )
}

function PlainList({ label, items }: { label: string; items: string[] }) {
  if (items.length === 0) return null
  return (
    <div>
      <p className="mb-1.5 text-xs text-text-secondary">{label}</p>
      <ul className="list-inside list-disc space-y-0.5">
        {items.map((item, i) => (
          <li key={`${item}-${i}`} className="text-sm text-foreground/90">
            {item}
          </li>
        ))}
      </ul>
    </div>
  )
}

/** 证据型条目：把「说了什么」与「原文哪一句」并排放在一起。 */
function EvidenceList({
  items,
  emptyHint,
}: {
  items: { type: string; detail?: string; evidence: string }[]
  emptyHint: string
}) {
  if (items.length === 0) {
    return <p className="text-[11px] text-text-tertiary">{emptyHint}</p>
  }
  return (
    <ul className="space-y-1.5">
      {items.map((item, i) => (
        <li key={`${item.type}-${i}`} className="rounded border border-border p-2">
          <Badge variant="secondary">{item.type}</Badge>
          {item.detail && (
            <p className="mt-1 text-sm leading-relaxed text-foreground/90">
              {item.detail}
            </p>
          )}
          <p className="mt-1 text-[11px] italic text-text-tertiary">
            「{item.evidence}」
          </p>
        </li>
      ))}
    </ul>
  )
}

function LinkList({ label, items }: { label: string; items: string[] }) {
  if (items.length === 0) return null
  return (
    <div>
      <p className="mb-1.5 text-xs text-text-secondary">{label}</p>
      <ul className="space-y-0.5">
        {items.map((url) => (
          <li key={url}>
            <a
              href={url}
              target="_blank"
              rel="noreferrer"
              className="break-all text-[11px] text-text-secondary underline-offset-2 hover:text-brand hover:underline"
            >
              {url}
            </a>
          </li>
        ))}
      </ul>
    </div>
  )
}
