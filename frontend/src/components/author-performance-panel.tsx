import { useEffect, useState } from "react"
import { ChevronRight, Loader2 } from "lucide-react"
import { toast } from "sonner"

import { EmptyState, ListSkeleton } from "@/components/common"
import { Badge } from "@/components/ui/badge"
import { api } from "@/lib/api"
import type {
  AuthorPerformanceProfile,
  PerformanceCombo,
  PerformanceDimension,
  PerformanceEvidenceItem,
  PerformanceGoalSample,
  PerformanceValue,
} from "@/lib/types"
import { formatCompact } from "@/lib/format"
import { filterHigh, filterLow } from "@/lib/performance-buckets"
import { cn } from "@/lib/utils"

const EVIDENCE_PREVIEW = 5

/**
 * 一次证据请求。
 *
 * combo 走结构化 leftValue / rightValue，绝不解析「A × B」展示字符串。
 * order 决定先看最好的还是最差的：低表现榜必须先看真正最差的样本。
 */
interface EvidenceTarget {
  key: string
  value: string
  dimension?: string
  combo?: string
  leftValue?: string
  rightValue?: string
  order: "high" | "low"
}

/** 四象限标签文案。只描述「常用 × 历史表现」，不给建议。 */
const QUADRANT_LABEL: Record<string, string> = {
  core: "常用·高表现",
  opportunity: "少用·高表现",
  high_freq_low: "常用·低表现",
  low_priority: "少用·低表现",
  neutral: "表现居中",
}

/** 统一数字格式（B3 §35）：不再各自实现 toLocaleString。 */
function fmtCount(n: number | null | undefined): string {
  return formatCompact(n)
}

/**
 * 比率格式：收藏率这类量级很小（常见 0.4% 甚至 0.04%），
 * 直接显示小数会全是 0.00 —— 统一按百分比给 2 位小数。
 * null 是「不可算」（分母为 0 或计数未知），不是 0，必须区别显示。
 */
function fmtRate(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—"
  return `${(v * 100).toFixed(2)}%`
}

/**
 * 单账号流量画像（阶段 8）。
 *
 * 全部数字来自已有数据的确定性统计：0 AI、不补分析、不预测。
 * 两个样本池严格分开显示：
 *   - 表现有效样本 → 账号曝光基线
 *   - 其中已分析   → 主题 / 写法 / 组合的特征统计
 */
export default function AuthorPerformancePanel({
  authorId,
  onOpenContent,
}: {
  authorId: number
  onOpenContent: (id: number) => void
}) {
  const [data, setData] = useState<AuthorPerformanceProfile | null>(null)
  const [loading, setLoading] = useState(true)

  const [dimIndex, setDimIndex] = useState(0)
  const [showAllValues, setShowAllValues] = useState(false)

  const [openEvidence, setOpenEvidence] = useState<EvidenceTarget | null>(null)
  const [evidence, setEvidence] = useState<PerformanceEvidenceItem[]>([])
  // API 只返回条目、不返回总数；用「当前是不是全量视图」判断要不要显示查看全部
  const [evidenceAll, setEvidenceAll] = useState(false)
  const [evidenceLoading, setEvidenceLoading] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    void api
      .getAuthorPerformanceProfile(authorId)
      .then((d) => !cancelled && setData(d))
      .catch((err) => !cancelled && toast.error((err as Error).message))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [authorId])

  const loadEvidence = async (target: EvidenceTarget, limit: number) => {
    setEvidenceLoading(true)
    try {
      const items = await api.getAuthorPerformanceEvidence(authorId, {
        value: target.value,
        dimension: target.dimension,
        combo: target.combo,
        leftValue: target.leftValue,
        rightValue: target.rightValue,
        order: target.order,
        limit,
      })
      setEvidence(items)
      setEvidenceAll(limit > EVIDENCE_PREVIEW)
    } catch (err) {
      toast.error((err as Error).message)
      setEvidence([])
      setEvidenceAll(false)
    } finally {
      setEvidenceLoading(false)
    }
  }

  const toggleEvidence = (target: EvidenceTarget) => {
    if (openEvidence?.key === target.key) {
      setOpenEvidence(null)
      setEvidence([])
      return
    }
    setOpenEvidence(target)
    setEvidence([])
    void loadEvidence(target, EVIDENCE_PREVIEW)
  }

  const summary = data?.sample_summary
  const eligible = summary?.eligible_count ?? 0
  const analyzed = summary?.analyzed_eligible_count ?? 0

  if (loading || !data) {
    return <ListSkeleton rows={4} />
  }

  if (eligible === 0) {
    return (
      <EmptyState
        title="还没有可用的表现样本"
        description={
          data.sample_summary.performance_count === 0
            ? "这个账号的帖子还没有导入曝光数据。导入 Xporter 导出文件（含 view_count）后，且帖子发布满 48 小时，这里会自动出现统计。"
            : `已导入 ${data.sample_summary.performance_count} 条表现数据，但没有一条满足「有曝光数 + 非置顶 + 发布满 ${data.sample_summary.min_age_hours} 小时」。`
        }
      />
    )
  }

  return (
    <div className="max-w-3xl space-y-7">
      {/* 样本口径：两个池必须说清楚 */}
      {/*
        B3 最终复查：样本口径压成三行（数字全部真实动态），不再常驻方法论长文。
        算法 / API / 48h / 60 / 40 一律没动。
      */}
      <div className="rounded-lg border border-border px-4 py-3">
        <p className="text-[12px] text-text-secondary">
          有效表现样本{" "}
          <span className="tabular-nums text-foreground">{eligible}</span>
          {" / "}
          <span className="tabular-nums">{summary?.total_content_count}</span>
          {" · 已分析 "}
          <span className="tabular-nums text-foreground">{analyzed}</span>
        </p>
        {analyzed === 0 ? (
          <p className="mt-1 text-[12px] text-text-tertiary">
            目前还没有已分析的有效样本，下面的特征统计为空。
          </p>
        ) : (
          <p className="mt-1 text-[12px] text-text-tertiary">
            内容与写法规律仅统计已分析样本。
          </p>
        )}
        {summary && <ExcludedLine summary={summary} />}
      </div>

      {/* 账号曝光基线 */}
      <section>
        <h2 className="mb-3 text-[15px] font-semibold leading-none text-foreground">
          账号曝光基线
        </h2>
        <div className="rounded-lg border border-border">
          <div className="grid grid-cols-2 divide-x divide-border/40 border-b border-divider sm:grid-cols-4">
            <Cell label="中位曝光" value={fmtCount(data.baseline.median_views)} strong />
            <Cell label="平均曝光" value={fmtCount(data.baseline.average_views)} />
            <Cell label="Top 10% 门槛" value={fmtCount(data.baseline.top10_view_threshold)} />
            <Cell label="Bottom 40% 门槛" value={fmtCount(data.baseline.bottom40_view_threshold)} />
          </div>
          <div className="flex flex-wrap gap-x-5 gap-y-1 px-4 py-2.5 text-[11px] text-text-secondary">
            <span>
              最低 <span className="tabular-nums">{fmtCount(data.baseline.min_views)}</span>
            </span>
            <span>
              最高 <span className="tabular-nums">{fmtCount(data.baseline.max_views)}</span>
            </span>
            <span>统计口径：非置顶 · 已发布 ≥ {data.sample_summary.min_age_hours}h</span>
          </div>
        </div>
      </section>

      {/* 内容维度 */}
      <section>
        <div className="mb-3 flex items-center gap-2">
          <h2 className="text-[15px] font-semibold leading-none text-foreground">
            内容维度
          </h2>
          <div className="ml-1 inline-flex h-7 items-center rounded border border-border bg-hover p-0.5">
            {data.content_dimensions.map((d, i) => (
              <button
                key={d.key}
                onClick={() => {
                  setDimIndex(i)
                  setShowAllValues(false)
                  setOpenEvidence(null)
                }}
                className={cn(
                  "h-6 rounded px-2.5 text-[11px] font-medium transition-colors",
                  dimIndex === i
                    ? "bg-surface text-foreground"
                    : "text-text-secondary hover:text-foreground"
                )}
              >
                {d.label}
              </button>
            ))}
          </div>
        </div>
        <DimensionBlock
          dim={data.content_dimensions[dimIndex]}
          showAll={showAllValues}
          onToggleAll={() => setShowAllValues((v) => !v)}
          openKey={openEvidence?.key ?? null}
          evidence={evidence}
          evidenceAll={evidenceAll}
          evidenceLoading={evidenceLoading}
          onToggle={toggleEvidence}
          onViewAll={() =>
            openEvidence && void loadEvidence(openEvidence, 100)
          }
          onOpenContent={onOpenContent}
        />
      </section>

      {/* 写法维度 */}
      {data.writing_sections.some((s) =>
        s.metrics.some((m) => m.values.length > 0)
      ) && (
        <section>
          <h2 className="mb-1 text-[15px] font-semibold leading-none text-foreground">
            写法维度
          </h2>
          <p className="mb-3 text-[12px] leading-relaxed text-text-tertiary">
            这里回答「哪些写法历史表现更好」，不是「更常用」。常用程度另列为使用率。
            内容绑定、个人口头禅一类高维长尾字段不进统计，避免碎片值伪装成流量密码。
          </p>
          <div className="space-y-6">
            {data.writing_sections.map((s) => {
              const metrics = s.metrics.filter((m) => m.values.length > 0)
              if (metrics.length === 0) return null
              return (
                <div key={s.key}>
                  <p className="mb-2 text-xs font-medium">{s.title}</p>
                  <div className="space-y-3">
                    {metrics.map((m) => (
                      <MetricRow
                        key={m.field}
                        metric={m}
                        openKey={openEvidence?.key ?? null}
                        evidence={evidence}
                        evidenceAll={evidenceAll}
                        evidenceLoading={evidenceLoading}
                        onToggle={toggleEvidence}
                        onViewAll={() =>
                          openEvidence && void loadEvidence(openEvidence, 100)
                        }
                        onOpenContent={onOpenContent}
                      />
                    ))}
                  </div>
                </div>
              )
            })}
          </div>
        </section>
      )}

      {/* 二元组合 */}
      <section>
        <h2 className="mb-1 text-[15px] font-semibold leading-none text-foreground">
          二元组合
        </h2>
        <p className="mb-3 text-[12px] leading-relaxed text-text-tertiary">
          只看 4 组固定组合，不做三维交叉、不做全量笛卡尔展开。
        </p>
        <div className="space-y-3">
          {data.combinations.map((c) => (
            <ComboBlock
              key={c.key}
              combo={c}
              openKey={openEvidence?.key ?? null}
              evidence={evidence}
              evidenceAll={evidenceAll}
              evidenceLoading={evidenceLoading}
              onToggle={toggleEvidence}
              onViewAll={() => openEvidence && void loadEvidence(openEvidence, 100)}
              onOpenContent={onOpenContent}
            />
          ))}
        </div>
      </section>

      {/* 互动指标基线（点赞 / 评论 / 转发 / 引用 / 收藏）*/}
      {data.metric_baselines?.length > 0 && (
        <section>
          <h2 className="mb-1 text-[15px] font-semibold leading-none text-foreground">
            互动指标
          </h2>
          <p className="mb-3 text-[12px] leading-relaxed text-text-tertiary">
            计数 ÷ 浏览量的比率，只在该帖浏览量 {">"} 0 且计数已知时计算。
            未知 ≠ 0；不做加权合成，没有「爆款总分」。
          </p>
          <div className="overflow-hidden rounded-lg border border-border">
            <div className="grid grid-cols-[1.2fr_repeat(4,1fr)] divide-x divide-border/40 border-b border-divider bg-hover/40 text-[11px] text-text-secondary">
              <div className="px-3 py-2">指标</div>
              <div className="px-3 py-2 text-right">样本</div>
              <div className="px-3 py-2 text-right">中位</div>
              <div className="px-3 py-2 text-right">Top 10%</div>
              <div className="px-3 py-2 text-right">未知</div>
            </div>
            {data.metric_baselines.map((m) => (
              <div
                key={m.metric}
                className="grid grid-cols-[1.2fr_repeat(4,1fr)] divide-x divide-border/40 border-b border-divider last:border-b-0 text-[12px]"
              >
                <div className="px-3 py-2 text-foreground/90">{m.label}</div>
                <div className="px-3 py-2 text-right tabular-nums text-text-secondary">
                  {m.sample_count}
                </div>
                <div className="px-3 py-2 text-right tabular-nums text-foreground">
                  {m.kind === "absolute"
                    ? fmtCount(m.median_value)
                    : fmtRate(m.median_value)}
                </div>
                <div className="px-3 py-2 text-right tabular-nums text-text-secondary">
                  {m.kind === "absolute"
                    ? fmtCount(m.top10_threshold)
                    : fmtRate(m.top10_threshold)}
                </div>
                <div className="px-3 py-2 text-right tabular-nums text-text-tertiary">
                  {m.unknown_count}
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* 书写目标：不同目标下值得看的案例 */}
      {data.goal_samples && Object.keys(data.goal_samples).length > 0 && (
        <section>
          <h2 className="mb-1 text-[15px] font-semibold leading-none text-foreground">
            按书写目标看案例
          </h2>
          <p className="mb-3 text-[12px] leading-relaxed text-text-tertiary">
            同一批帖子按不同目标各自排序，用来找参照个案 —— 不合成一个总排名。
            默认目标仍是曝光。
          </p>
          <div className="space-y-3">
            {Object.values(data.goal_samples).map((goal) => (
              <div key={goal.goal} className="rounded-lg border border-border p-3">
                <div className="mb-2 flex items-baseline justify-between">
                  <p className="text-[13px] font-medium text-foreground">
                    {goal.label}
                    {goal.goal === data.default_goal && (
                      <span className="ml-2 text-[11px] font-normal text-text-tertiary">
                        默认
                      </span>
                    )}
                  </p>
                  <p className="text-[11px] text-text-tertiary">
                    可算样本 <span className="tabular-nums">{goal.sample_count}</span>
                  </p>
                </div>
                {goal.insufficient ? (
                  <p className="text-[12px] text-text-tertiary">
                    样本不足（{"<"} 5 条），暂不列案例。
                  </p>
                ) : (
                  <div className="space-y-2">
                    <GoalCaseRow label="高" items={goal.high} kind={goal.kind} />
                    <GoalCaseRow label="低" items={goal.low} kind={goal.kind} />
                  </div>
                )}
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}

// ---------------------------------------------------------------- 目标案例

function GoalCaseRow({
  label,
  items,
  kind,
}: {
  label: string
  items: PerformanceGoalSample[]
  kind: "absolute" | "rate"
}) {
  if (items.length === 0) return null
  const shown = items.slice(0, 3)
  return (
    <div className="flex gap-2">
      <span className="mt-1 shrink-0 text-[11px] text-text-tertiary">{label}</span>
      <div className="flex flex-1 flex-wrap gap-1.5">
        {shown.map((s) => {
          const features = Object.entries(s.features ?? {})
            .slice(0, 3)
            .map(([k, v]) => `${k} ${Array.isArray(v) ? v.join("/") : v}`)
          return (
            <span
              key={s.content_id}
              title={s.excerpt}
              className="rounded border border-border px-2 py-1 text-[11px] text-text-secondary"
            >
              <span className="tabular-nums text-foreground">
                {kind === "absolute" ? fmtCount(s.metric_value) : fmtRate(s.metric_value)}
              </span>
              {features.length > 0 && (
                <span className="ml-1.5 text-text-tertiary">
                  {features.join(" · ")}
                </span>
              )}
            </span>
          )
        })}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- 基线

function Cell({
  label,
  value,
  strong,
}: {
  label: string
  value: string
  strong?: boolean
}) {
  return (
    <div className="px-4 py-3">
      <p className="text-[11px] text-text-secondary">{label}</p>
      <p
        className={cn(
          "mt-0.5 tabular-nums",
          strong ? "text-lg font-medium" : "text-sm text-foreground/80"
        )}
      >
        {value}
      </p>
    </div>
  )
}

function ExcludedLine({
  summary,
}: {
  summary: AuthorPerformanceProfile["sample_summary"]
}) {
  const ex = summary.excluded ?? {}
  const parts: string[] = []
  if (ex.no_performance) parts.push(`${ex.no_performance} 篇无曝光数据`)
  if (ex.no_view_count) parts.push(`${ex.no_view_count} 篇曝光为空`)
  if (ex.pinned) parts.push(`${ex.pinned} 篇置顶`)
  if (ex.too_fresh) parts.push(`${ex.too_fresh} 篇发布未满 ${summary.min_age_hours}h`)
  if (ex.invalid_time) parts.push(`${ex.invalid_time} 篇时间无效`)
  if (parts.length === 0) return null
  return (
    <p className="mt-1.5 text-[11px] text-text-tertiary">
      已排除：{parts.join(" · ")}
    </p>
  )
}

// ---------------------------------------------------------------- 维度

function DimensionBlock({
  dim,
  showAll,
  onToggleAll,
  openKey,
  evidence,
  evidenceAll,
  evidenceLoading,
  onToggle,
  onViewAll,
  onOpenContent,
}: {
  dim: PerformanceDimension
  showAll: boolean
  onToggleAll: () => void
  openKey: string | null
  evidence: PerformanceEvidenceItem[]
  evidenceAll: boolean
  evidenceLoading: boolean
  onToggle: (t: EvidenceTarget) => void
  onViewAll: () => void
  onOpenContent: (id: number) => void
}) {
  if (dim.values.length === 0) {
    return (
      <p className="text-xs text-text-tertiary">
        还没有已分析的有效样本，{dim.label}维度的表现统计暂不可用。
      </p>
    )
  }

  const rankable = dim.values.filter((v) => v.sample_count >= 5)

  return (
    <div className="space-y-4">
      <RankList
        title="历史高表现"
        hint="中位流量分 ≥ 60 · 样本 ≥ 5"
        items={filterHigh(dim.high)}
        empty={`还没有中位分 ≥ 60 且样本 ≥ 5 的${dim.label}取值`}
        dimension={dim.key}
        order="high"
        openKey={openKey}
        evidence={evidence}
        evidenceAll={evidenceAll}
        evidenceLoading={evidenceLoading}
        onToggle={onToggle}
        onViewAll={onViewAll}
        onOpenContent={onOpenContent}
      />
      <RankList
        title="历史低表现"
        hint="中位流量分 < 40 · 样本 ≥ 5"
        items={filterLow(dim.low)}
        empty={`还没有中位分 < 40 且样本 ≥ 5 的${dim.label}取值`}
        dimension={dim.key}
        order="low"
        openKey={openKey}
        evidence={evidence}
        evidenceAll={evidenceAll}
        evidenceLoading={evidenceLoading}
        onToggle={onToggle}
        onViewAll={onViewAll}
        onOpenContent={onOpenContent}
      />

      <div>
        <button
          onClick={onToggleAll}
          className="inline-flex items-center gap-1 text-[11px] text-text-secondary hover:text-foreground"
        >
          <ChevronRight
            className={cn("h-3 w-3 transition-transform", showAll && "rotate-90")}
          />
          {showAll ? "收起全部取值" : `展开全部取值（${dim.values.length}）`}
        </button>
        {showAll && (
          <div className="mt-2 overflow-hidden rounded-lg border border-border">
            <ValueTable
              values={dim.values}
              dimension={dim.key}
              openKey={openKey}
              evidence={evidence}
              evidenceAll={evidenceAll}
              evidenceLoading={evidenceLoading}
              onToggle={onToggle}
              onViewAll={onViewAll}
              onOpenContent={onOpenContent}
            />
          </div>
        )}
        {rankable.length === 0 && (
          <p className="mt-1 text-[11px] text-text-tertiary">
            所有取值样本量都不足 5，因此不进高 / 低表现榜。
          </p>
        )}
      </div>
    </div>
  )
}

function MetricRow({
  metric,
  openKey,
  evidence,
  evidenceAll,
  evidenceLoading,
  onToggle,
  onViewAll,
  onOpenContent,
}: {
  metric: PerformanceDimension
  openKey: string | null
  evidence: PerformanceEvidenceItem[]
  evidenceAll: boolean
  evidenceLoading: boolean
  onToggle: (t: EvidenceTarget) => void
  onViewAll: () => void
  onOpenContent: (id: number) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const ranked = metric.values.filter((v) => v.sample_count >= 5)
  const top = ranked.length ? ranked.slice(0, 3) : metric.values.slice(0, 3)

  return (
    <div>
      <div className="mb-1.5 flex items-baseline gap-2">
        <p className="text-xs font-medium">{metric.label}</p>
        <span className="text-[11px] text-text-tertiary">
          {ranked.length === 0 ? "样本量均 < 5" : `${ranked.length} 个取值可比较`}
          {` · 有值样本 ${metric.present_count}`}
        </span>
        {metric.values.length > 3 && (
          <button
            onClick={() => setExpanded((v) => !v)}
            className="text-[11px] text-text-secondary hover:text-foreground"
          >
            {expanded ? "收起" : `展开 ${metric.values.length} 个取值`}
          </button>
        )}
      </div>
      <div className="overflow-hidden rounded-lg border border-border">
        <ValueTable
          values={expanded ? metric.values : top}
          dimension={metric.field}
          openKey={openKey}
          evidence={evidence}
          evidenceAll={evidenceAll}
          evidenceLoading={evidenceLoading}
          onToggle={onToggle}
          onViewAll={onViewAll}
          onOpenContent={onOpenContent}
        />
      </div>
    </div>
  )
}

function ComboBlock({
  combo,
  openKey,
  evidence,
  evidenceAll,
  evidenceLoading,
  onToggle,
  onViewAll,
  onOpenContent,
}: {
  combo: PerformanceCombo
  openKey: string | null
  evidence: PerformanceEvidenceItem[]
  evidenceAll: boolean
  evidenceLoading: boolean
  onToggle: (t: EvidenceTarget) => void
  onViewAll: () => void
  onOpenContent: (id: number) => void
}) {
  const [showLow, setShowLow] = useState(false)
  const has = combo.high.length > 0 || combo.low.length > 0

  return (
    <div>
      <p className="mb-1.5 text-xs font-medium">{combo.label}</p>
      {!has ? (
        <p className="text-[11px] text-text-tertiary">
          还没有样本量足够（≥5）的组合
        </p>
      ) : (
        <div className="space-y-2">
          <ValueTable
            values={combo.high}
            combo={combo.key}
            order="high"
            openKey={openKey}
            evidence={evidence}
            evidenceAll={evidenceAll}
            evidenceLoading={evidenceLoading}
            onToggle={onToggle}
            onViewAll={onViewAll}
            onOpenContent={onOpenContent}
          />
          {combo.low.length > 0 && (
            <>
              <button
                onClick={() => setShowLow((v) => !v)}
                className="inline-flex items-center gap-1 text-[11px] text-text-secondary hover:text-foreground"
              >
                <ChevronRight
                  className={cn("h-3 w-3 transition-transform", showLow && "rotate-90")}
                />
                低表现组合（{combo.low.length}）
              </button>
              {showLow && (
                <ValueTable
                  values={combo.low}
                  combo={combo.key}
                  order="low"
                  openKey={openKey}
                  evidence={evidence}
                  evidenceAll={evidenceAll}
                  evidenceLoading={evidenceLoading}
                  onToggle={onToggle}
                  onViewAll={onViewAll}
                  onOpenContent={onOpenContent}
                />
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}

function RankList({
  title,
  hint,
  items,
  empty,
  dimension,
  order = "high",
  openKey,
  evidence,
  evidenceAll,
  evidenceLoading,
  onToggle,
  onViewAll,
  onOpenContent,
}: {
  title: string
  hint: string
  items: PerformanceValue[]
  empty: string
  dimension: string
  order?: "high" | "low"
  openKey: string | null
  evidence: PerformanceEvidenceItem[]
  evidenceAll: boolean
  evidenceLoading: boolean
  onToggle: (t: EvidenceTarget) => void
  onViewAll: () => void
  onOpenContent: (id: number) => void
}) {
  return (
    <div>
      <div className="mb-1.5 flex items-baseline gap-2">
        <p className="text-xs font-medium">{title}</p>
        <span className="text-[11px] text-text-tertiary">{hint}</span>
      </div>
      {items.length === 0 ? (
        <p className="text-[11px] text-text-tertiary">{empty}</p>
      ) : (
        <div className="overflow-hidden rounded-lg border border-border">
          <ValueTable
            values={items}
            dimension={dimension}
            order={order}
            openKey={openKey}
            evidence={evidence}
            evidenceAll={evidenceAll}
            evidenceLoading={evidenceLoading}
            onToggle={onToggle}
            onViewAll={onViewAll}
            onOpenContent={onOpenContent}
          />
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------- 取值表

function ValueTable({
  values,
  dimension,
  combo,
  order = "high",
  openKey,
  evidence,
  evidenceAll,
  evidenceLoading,
  onToggle,
  onViewAll,
  onOpenContent,
}: {
  values: PerformanceValue[]
  dimension?: string
  combo?: string
  /** high = 先看最好的；low = 先看真正最差的（低表现榜必须用 low） */
  order?: "high" | "low"
  openKey: string | null
  evidence: PerformanceEvidenceItem[]
  evidenceAll: boolean
  evidenceLoading: boolean
  onToggle: (t: EvidenceTarget) => void
  onViewAll: () => void
  onOpenContent: (id: number) => void
}) {
  return (
    <div>
      {values.map((v) => {
        const key = `${combo ?? dimension}::${v.value}`
        const open = openKey === key
        return (
          <div key={v.value} className="border-b border-divider last:border-0">
            <button
              onClick={() =>
                onToggle({
                  key,
                  value: v.value,
                  dimension,
                  combo,
                  // 组合 identity 用结构化左右值，不传展示字符串
                  leftValue: combo ? (v.left_value ?? undefined) : undefined,
                  rightValue: combo ? (v.right_value ?? undefined) : undefined,
                  order,
                })
              }
              className="flex w-full items-center gap-3 px-3 py-2 text-left transition-colors hover:bg-hover"
            >
              <span className="w-36 shrink-0 truncate text-xs sm:w-44">
                {v.value}
              </span>

              <div className="hidden h-1.5 w-20 shrink-0 rounded-full bg-hover sm:block">
                <div
                  className={cn(
                    "h-1.5 rounded-full",
                    v.median_performance_score >= 60
                      ? "bg-success/70"
                      : v.median_performance_score < 40
                        ? "bg-warning/70"
                        : "bg-foreground/45"
                  )}
                  style={{
                    width: `${Math.max(2, Math.min(100, v.median_performance_score))}%`,
                  }}
                />
              </div>

              <span className="w-10 shrink-0 text-right text-[11px] tabular-nums">
                {v.median_performance_score}
              </span>
              <span className="w-16 shrink-0 text-right text-[11px] tabular-nums text-text-secondary">
                {fmtCount(v.median_view_count)}
              </span>
              <span className="hidden w-12 shrink-0 text-right text-[11px] tabular-nums text-text-tertiary sm:inline">
                n={v.sample_count}
              </span>
              {v.low_sample && (
                <Badge variant="outline" className="shrink-0 px-1 py-0">
                  样本较少
                </Badge>
              )}
              {v.quadrant && v.quadrant !== "neutral" && (
                <span className="hidden shrink-0 text-[11px] text-text-tertiary lg:inline">
                  {QUADRANT_LABEL[v.quadrant] ?? v.quadrant}
                </span>
              )}
              <span className="ml-auto shrink-0 text-[11px] tabular-nums text-text-tertiary">
                常用 {v.usage_rate}%
              </span>
              <ChevronRight
                className={cn(
                  "h-3.5 w-3.5 shrink-0 text-text-tertiary transition-transform",
                  open && "rotate-90"
                )}
              />
            </button>

            {open && (
              <EvidenceList
                value={v.value}
                items={evidence}
                showAll={evidenceAll}
                order={order}
                loading={evidenceLoading}
                onViewAll={onViewAll}
                onOpenContent={onOpenContent}
              />
            )}
          </div>
        )
      })}
    </div>
  )
}

function EvidenceList({
  value,
  items,
  showAll,
  order,
  loading,
  onViewAll,
  onOpenContent,
}: {
  value: string
  items: PerformanceEvidenceItem[]
  showAll: boolean
  order: "high" | "low"
  loading: boolean
  onViewAll: () => void
  onOpenContent: (id: number) => void
}) {
  return (
    <div className="border-t border-divider bg-canvas px-3 py-2">
      <p className="mb-1.5 text-[11px] text-text-secondary">
        证据帖子（精确匹配「{value}」，
        {order === "low" ? "先看表现最差的" : "先看表现最好的"}，已显示{" "}
        {items.length} 条{showAll ? " · 全部" : " · 预览"})
      </p>
      {loading && items.length === 0 ? (
        <div className="flex items-center gap-1.5 py-1 text-[11px] text-text-secondary">
          <Loader2 className="h-3 w-3 animate-spin" />
          加载中…
        </div>
      ) : items.length === 0 ? (
        <p className="text-[11px] text-text-tertiary">没有匹配的帖子。</p>
      ) : (
        <div className="space-y-1">
          {items.map((item) => (
            <button
              key={item.content_id}
              onClick={() => onOpenContent(item.content_id)}
              className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left transition-colors hover:bg-surface"
            >
              <span className="w-14 shrink-0 text-[11px] tabular-nums text-text-secondary">
                {fmtCount(item.view_count)}
              </span>
              <Badge
                variant={item.source === "mine" ? "mine" : "benchmark"}
                className="shrink-0"
              >
                {item.band_label}
              </Badge>
              <span className="min-w-0 flex-1 truncate text-[11px]">
                {item.excerpt || "（无正文预览）"}
              </span>
            </button>
          ))}
        </div>
      )}
      {!showAll && items.length >= EVIDENCE_PREVIEW && (
        <button
          onClick={onViewAll}
          disabled={loading}
          className="mt-1.5 inline-flex items-center gap-1 text-[11px] text-text-secondary hover:text-foreground"
        >
          {loading && <Loader2 className="h-3 w-3 animate-spin" />}
          查看全部
        </button>
      )}
    </div>
  )
}
