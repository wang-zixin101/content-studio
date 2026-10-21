import { useEffect, useMemo, useState } from "react"
import { ChevronRight, Loader2 } from "lucide-react"
import { toast } from "sonner"

import { EmptyState, HelpText, ListSkeleton, PageHeader as SharedPageHeader, SectionHeader, Segmented } from "@/components/common"
import { ContentDetailSheet } from "@/components/content-detail-sheet"
import { api } from "@/lib/api"
import { authorLabel } from "@/lib/author-display"
import type {
  CrossAccountDimension,
  CrossAccountInsights,
  CrossAccountValue,
  PerformanceEvidenceItem,
} from "@/lib/types"
import { cn, formatTime } from "@/lib/utils"

type Group = "content" | "writing"

/** 三类标签可重叠，所以是「组合标签」而不是单一枚举。 */
function classLabel(v: CrossAccountValue): string {
  const parts: string[] = []
  if (v.is_common_high) parts.push("跨账号高表现")
  if (v.is_common_low) parts.push("跨账号低表现")
  if (v.is_account_dependent) parts.push("账号依赖明显")
  return parts.length ? parts.join(" + ") : "混合"
}

/**
 * 跨作者洞察页（阶段 9）。
 *
 * 与作者主页的「流量画像」是两件事：那一页是单账号内部的历史统计，
 * 这一页是**多个账号之间**的横向比较——所以必须是独立页面，
 * 不能塞进某个作者主页。
 *
 * 全部数字来自各作者 Stage 8 画像的确定性聚合：0 AI、不补分析、不预测。
 * 比较基准是各作者**内部**已标准化的表现分，绝不直接比较绝对曝光；
 * 一个作者 = 一票，帖子数只作证据量展示、不做权重。
 */
export default function InsightsPage() {
  // B3 #46：全部取值默认折叠 —— 它与三榜内容重复，展开才看原始结果
  const [showAllValues, setShowAllValues] = useState(false)

  const [data, setData] = useState<CrossAccountInsights | null>(null)
  const [loading, setLoading] = useState(true)

  const [group, setGroup] = useState<Group>("content")
  const [dimKey, setDimKey] = useState<string>("topic")

  const [openValue, setOpenValue] = useState<string | null>(null)
  const [openAuthor, setOpenAuthor] = useState<number | null>(null)
  const [evidence, setEvidence] = useState<PerformanceEvidenceItem[]>([])
  const [evidenceLoading, setEvidenceLoading] = useState(false)

  const [openId, setOpenId] = useState<number | null>(null)
  const [sheetOpen, setSheetOpen] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    void api
      .getCrossAccountInsights()
      .then((d) => !cancelled && setData(d))
      .catch((err) => !cancelled && toast.error((err as Error).message))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [reloadKey])

  const dimensions: CrossAccountDimension[] = useMemo(() => {
    if (!data) return []
    return group === "content" ? data.content_dimensions : data.writing_dimensions
  }, [data, group])

  // 切换分组时把维度重置到该组第一个
  useEffect(() => {
    if (!dimensions.length) return
    if (!dimensions.some((d) => d.key === dimKey)) {
      setDimKey(dimensions[0].key)
      setOpenValue(null)
      setOpenAuthor(null)
      setEvidence([])
    }
  }, [dimensions, dimKey])

  const dim = dimensions.find((d) => d.key === dimKey) ?? null

  /**
   * 加载某个作者在某取值下的证据帖。
   *
   * 关键点：用 observation 里的 **evidence_values（原始取值）** 查询，
   * 绝不用 normalized identity 去打精确匹配 —— 否则 "gpt" 命中不了真实帖子 "GPT"。
   * 同一作者若存在多个被规范化合并的 raw 变体，逐个查并按 content_id 去重合并。
   */
  const loadEvidence = async (
    authorId: number,
    field: string,
    evidenceValues: string[],
    order: "high" | "low"
  ) => {
    setEvidenceLoading(true)
    try {
      const merged = new Map<number, PerformanceEvidenceItem>()
      for (const rawValue of evidenceValues.length ? evidenceValues : [""]) {
        const items = await api.getAuthorPerformanceEvidence(authorId, {
          dimension: field,
          value: rawValue,
          order,
          limit: 20,
        })
        for (const item of items) {
          if (!merged.has(item.content_id)) merged.set(item.content_id, item)
        }
      }
      const list = [...merged.values()]
      list.sort((a, b) =>
        order === "low"
          ? a.performance_score - b.performance_score || a.content_id - b.content_id
          : b.performance_score - a.performance_score || b.content_id - a.content_id
      )
      setEvidence(list.slice(0, 5))
    } catch (err) {
      toast.error((err as Error).message)
      setEvidence([])
    } finally {
      setEvidenceLoading(false)
    }
  }

  if (loading || !data) {
    return (
      <div className="px-6 py-6">
        <ListSkeleton rows={4} />
      </div>
    )
  }

  const summary = data.author_summary

  if (summary.eligible_author_count === 0) {
    return (
      <div className="mx-auto max-w-3xl px-6 py-8">
        <PageHeader />
        <EmptyState
          title="还没有可用的跨账号数据"
          description="跨账号比较需要至少一位作者有「表现有效样本」（有真实曝光数、非置顶、发布满 48 小时）。先导入 Xporter 导出文件，再回来查看。"
        />
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border px-6 py-4">
        <PageHeader />
        <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-text-secondary">
          <span>
            参与账号 <span className="tabular-nums text-foreground">{summary.author_count}</span>
          </span>
          <span>
            有有效表现数据{" "}
            <span className="tabular-nums text-foreground">
              {summary.eligible_author_count}
            </span>
          </span>
          <span>
            其中已分析{" "}
            <span className="tabular-nums text-foreground">
              {summary.analyzed_author_count}
            </span>
          </span>
        </div>
        <p className="mt-2 text-[12px] text-text-tertiary">
          每个账号等权比较，结果仅表示历史表现。
        </p>

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Segmented
            options={[
              { key: "content", label: "内容" },
              { key: "writing", label: "写法" },
            ]}
            value={group}
            onChange={setGroup}
          />
          <div className="inline-flex flex-wrap items-center gap-1">
            {dimensions.map((d) => (
              <button
                key={d.key}
                onClick={() => {
                  setDimKey(d.key)
                  setOpenValue(null)
                  setOpenAuthor(null)
                }}
                className={cn(
                  "rounded border px-2 py-0.5 text-[11px] transition-colors",
                  dimKey === d.key
                    ? "border-foreground/30 bg-accent text-foreground"
                    : "border-border text-text-secondary hover:border-muted-foreground/40 hover:text-foreground"
                )}
              >
                {d.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
        <div className="max-w-3xl space-y-7">
          {dim && dim.value_count === 0 ? (
            <EmptyState
              title="这个维度暂时没有可比较的取值"
              description={
                summary.analyzed_author_count === 0
                  ? "还没有作者做过分析。跨账号比较必须知道每条帖子「是什么写法」，所以只统计已分析的部分。"
                  : "还没有任何取值在某个作者内部达到 5 条样本，因此不形成有效判断。"
              }
            />
          ) : (
            dim && (
              <>
                <Section
                  title="跨账号高表现"
                  hint={`≥ ${data.thresholds.common_pattern_min_authors ?? 3} 个账号 · 跨账号中位分 ≥ ${data.thresholds.positive_score ?? 60} · 正向账号 ≥ 2/3`}
                  items={dim.common_high}
                  tone="high"
                  field={dim.field}
                  openValue={openValue}
                  openAuthor={openAuthor}
                  evidence={evidence}
                  evidenceLoading={evidenceLoading}
                  onToggleValue={(v) => {
                    setOpenValue(openValue === v ? null : v)
                    setOpenAuthor(null)
                    setEvidence([])
                  }}
                  onToggleAuthor={(_key, authorId, evidenceValues, order) => {
                    if (openAuthor === authorId) {
                      setOpenAuthor(null)
                      setEvidence([])
                      return
                    }
                    setOpenAuthor(authorId)
                    setEvidence([])
                    void loadEvidence(authorId, dim.field, evidenceValues, order)
                  }}
                  onOpenContent={(cid) => {
                    setOpenId(cid)
                    setSheetOpen(true)
                  }}
                />
                <Section
                  title="跨账号低表现"
                  hint={`≥ ${data.thresholds.common_pattern_min_authors ?? 3} 个账号 · 跨账号中位分 < ${data.thresholds.negative_score ?? 40} · 负向账号 ≥ 2/3`}
                  items={dim.common_low}
                  tone="low"
                  field={dim.field}
                  openValue={openValue}
                  openAuthor={openAuthor}
                  evidence={evidence}
                  evidenceLoading={evidenceLoading}
                  onToggleValue={(v) => {
                    setOpenValue(openValue === v ? null : v)
                    setOpenAuthor(null)
                    setEvidence([])
                  }}
                  onToggleAuthor={(_key, authorId, evidenceValues, order) => {
                    if (openAuthor === authorId) {
                      setOpenAuthor(null)
                      setEvidence([])
                      return
                    }
                    setOpenAuthor(authorId)
                    setEvidence([])
                    void loadEvidence(authorId, dim.field, evidenceValues, order)
                  }}
                  onOpenContent={(cid) => {
                    setOpenId(cid)
                    setSheetOpen(true)
                  }}
                />
                <Section
                  title="账号依赖明显"
                  hint="同一个模式在不同账号方向相反（至少一个账号 ≥60，至少一个 <40）"
                  items={dim.account_dependent}
                  tone="dependent"
                  field={dim.field}
                  openValue={openValue}
                  openAuthor={openAuthor}
                  evidence={evidence}
                  evidenceLoading={evidenceLoading}
                  onToggleValue={(v) => {
                    setOpenValue(openValue === v ? null : v)
                    setOpenAuthor(null)
                    setEvidence([])
                  }}
                  onToggleAuthor={(_key, authorId, evidenceValues, order) => {
                    if (openAuthor === authorId) {
                      setOpenAuthor(null)
                      setEvidence([])
                      return
                    }
                    setOpenAuthor(authorId)
                    setEvidence([])
                    void loadEvidence(authorId, dim.field, evidenceValues, order)
                  }}
                  onOpenContent={(cid) => {
                    setOpenId(cid)
                    setSheetOpen(true)
                  }}
                />
              </>
            )
          )}

          {dim && dim.all_values.length > 0 && (
            <div>
              <button
                onClick={() => setShowAllValues((v) => !v)}
                className="mb-2 inline-flex items-center gap-1 text-[13px] font-semibold leading-none text-text-secondary hover:text-foreground"
              >
                <ChevronRight
                  className={cn("h-3.5 w-3.5 transition-transform", showAllValues && "rotate-90")}
                />
                全部取值（{dim.all_values.length}）
              </button>
              {showAllValues && (
                <>
              <p className="mb-2 text-[12px] leading-relaxed text-text-tertiary">
                这里包含只出现在 1–2 个账号的取值，不是跨账号共同规律，只是原始结果。
              </p>
              <div className="overflow-hidden rounded-lg border border-border">
                {dim.all_values.map((v) => (
                  <div
                    key={v.display_value || v.value}
                    className="flex items-center gap-3 border-b border-divider px-3 py-2 last:border-0"
                  >
                    <span className="w-36 shrink-0 truncate text-xs sm:w-44">
                      {v.display_value || v.value}
                    </span>
                    <span className="w-12 shrink-0 text-right text-[11px] tabular-nums">
                      {v.median_author_score}
                    </span>
                    <span className="w-14 shrink-0 text-right text-[11px] tabular-nums text-text-secondary">
                      {v.author_count} 账号
                    </span>
                    <span className="ml-auto shrink-0 text-[11px] tabular-nums text-text-tertiary">
                      帖子 {v.total_sample_count}
                    </span>
                    <span className="w-20 shrink-0 text-right text-[11px] text-text-tertiary">
                      {classLabel(v)}
                    </span>
                  </div>
                ))}
              </div>
                </>
              )}
            </div>
          )}
        </div>
      </div>

      <ContentDetailSheet
        contentId={openId}
        open={sheetOpen}
        onOpenChange={setSheetOpen}
        onSelectContent={(cid) => setOpenId(cid)}
        onChanged={() => setReloadKey((k) => k + 1)}
      />
    </div>
  )
}

/** 复用公共 PageHeader（B3 §42），不再各页面自己写一份。 */
function PageHeader() {
  return (
    <SharedPageHeader
      title="跨作者洞察"
      description="横向比较多个账号的历史表现模式"
    />
  )
}

function Section({
  title,
  hint,
  items,
  tone,
  field,
  openValue,
  openAuthor,
  evidence,
  evidenceLoading,
  onToggleValue,
  onToggleAuthor,
  onOpenContent,
}: {
  title: string
  hint: string
  items: CrossAccountValue[]
  tone: "high" | "low" | "dependent"
  field: string
  openValue: string | null
  openAuthor: number | null
  evidence: PerformanceEvidenceItem[]
  evidenceLoading: boolean
  onToggleValue: (value: string) => void
  onToggleAuthor: (
    value: string,
    authorId: number,
    evidenceValues: string[],
    order: "high" | "low"
  ) => void
  onOpenContent: (id: number) => void
}) {
  return (
    <section>
      {/*
        任务 07 S6：判定规则原来是 SectionHeader 的行内 hint
        （标题后跟一串括号规则，窄宽度下被截断、读起来像实现注释）。
        改为标题下方独立一行的 HelpText —— 三个分组（高表现 / 低表现 / 账号依赖）都走这里。
      */}
      <SectionHeader title={title} className="mb-1" />
      {hint && <HelpText className="mb-2">{hint}</HelpText>}
      {items.length === 0 ? (
        <p className="text-[11px] text-text-tertiary">
          当前维度还没有满足条件的取值
        </p>
      ) : (
        <div className="overflow-hidden rounded-lg border border-border">
          {items.map((v) => {
            const open = openValue === v.value
            return (
              <div key={v.value} className="border-b border-divider last:border-0">
                <button
                  onClick={() => onToggleValue(v.value)}
                  className="flex w-full items-center gap-3 px-3 py-2 text-left transition-colors hover:bg-hover"
                >
                  <span className="w-36 shrink-0 truncate text-xs sm:w-44">
                    {v.display_value || v.value}
                  </span>
                  <span
                    className="w-12 shrink-0 text-right text-[11px] tabular-nums"
                  >
                    {v.median_author_score}
                  </span>
                  <span className="w-24 shrink-0 text-right text-[11px] tabular-nums text-text-secondary">
                    {tone === "low"
                      ? `负向 ${v.negative_author_count}/${v.author_count}`
                      : `正向 ${v.positive_author_count}/${v.author_count}`}
                  </span>
                  <span className="hidden w-20 shrink-0 text-right text-[11px] tabular-nums text-text-tertiary sm:inline">
                    帖子 {v.total_sample_count}
                  </span>
                  {tone === "dependent" ? (
                    <span className="shrink-0 text-right text-[11px] tabular-nums text-text-secondary">
                      高 {v.positive_author_count} · 低 {v.negative_author_count}
                      <span className="ml-1.5 text-text-tertiary">
                        差异 {v.spread}
                      </span>
                    </span>
                  ) : (
                    <span className="w-24 shrink-0" />
                  )}
                  <ChevronRight
                    className={cn(
                      "ml-auto h-3.5 w-3.5 shrink-0 text-text-tertiary transition-transform",
                      open && "rotate-90"
                    )}
                  />
                </button>

                {open && (
                  <div className="border-t border-divider bg-canvas px-3 py-2">
                    <p className="mb-1.5 text-[11px] text-text-secondary">
                      各账号表现（一个账号一票，按表现分倒序）
                    </p>
                    <div className="space-y-1">
                      {v.authors.map((a) => {
                        const order: "high" | "low" =
                          a.median_performance_score < 40 ? "low" : "high"
                        const expanded = openAuthor === a.author_id
                        return (
                          <div key={a.author_id}>
                            <button
                              onClick={() =>
                                onToggleAuthor(
                                  v.value,
                                  a.author_id,
                                  a.evidence_values,
                                  order
                                )
                              }
                              className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left transition-colors hover:bg-surface"
                            >
                              <span className="w-28 shrink-0 truncate text-[11px]">
                                {authorLabel(a.display_name, a.username ?? "")}
                              </span>
                              <span className="w-10 shrink-0 text-right text-[11px] tabular-nums">
                                {a.median_performance_score}
                              </span>
                              <span className="w-12 shrink-0 text-right text-[11px] tabular-nums text-text-secondary">
                                n={a.sample_count}
                              </span>
                              <span className="w-16 shrink-0 text-right text-[11px] tabular-nums text-text-tertiary">
                                Top10% {a.top10_rate}%
                              </span>
                              <span className="hidden w-20 shrink-0 text-right text-[11px] tabular-nums text-text-tertiary sm:inline">
                                Bottom40% {a.bottom40_rate}%
                              </span>
                              <span className="ml-auto shrink-0 text-[11px] text-text-tertiary">
                                {expanded ? "收起帖子" : "看帖子"}
                              </span>
                            </button>
                            {expanded && (
                              <div className="mt-1 space-y-1 pl-2">
                                {evidenceLoading && evidence.length === 0 ? (
                                  <div className="flex items-center gap-1.5 py-1 text-[11px] text-text-secondary">
                                    <Loader2 className="h-3 w-3 animate-spin" />
                                    加载中…
                                  </div>
                                ) : evidence.length === 0 ? (
                                  <p className="py-1 text-[11px] text-text-tertiary">
                                    没有匹配的帖子。
                                  </p>
                                ) : (
                                  evidence.map((item) => (
                                    <button
                                      key={item.content_id}
                                      onClick={() => onOpenContent(item.content_id)}
                                      className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left transition-colors hover:bg-surface"
                                    >
                                      <span className="w-14 shrink-0 text-[11px] tabular-nums text-text-secondary">
                                        {item.view_count.toLocaleString("zh-CN")}
                                      </span>
                                      <span className="min-w-0 flex-1 truncate text-[11px]">
                                        {item.excerpt || "（无正文预览）"}
                                      </span>
                                      {item.posted_at && (
                                        <span className="shrink-0 text-[11px] text-text-tertiary">
                                          {formatTime(item.posted_at)}
                                        </span>
                                      )}
                                    </button>
                                  ))
                                )}
                              </div>
                            )}
                          </div>
                        )
                      })}
                    </div>
                    <p className="mt-1.5 text-[11px] text-text-tertiary">
                      字段：{field}
                    </p>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}
