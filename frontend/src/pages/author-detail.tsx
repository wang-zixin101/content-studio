import { useEffect, useMemo, useState } from "react"
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom"
import { ArrowLeft, ChevronRight, Loader2, Search, X } from "lucide-react"
import { toast } from "sonner"

import AuthorPerformancePanel from "@/components/author-performance-panel"
import { ContentDetailSheet } from "@/components/content-detail-sheet"
import { ContentRow } from "@/components/content-row"
import {
  EmptyState,
  ListSkeleton,
  PageHeader,
  SectionHeader,
  Segmented,
} from "@/components/common"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api } from "@/lib/api"
import { authorLabel } from "@/lib/author-display"
import type {
  Author,
  AuthorOverview,
  AuthorWritingProfile,
  ContentItem,
  Facets,
  ProfileEvidenceItem,
  ProfileMetric,
  Source,
} from "@/lib/types"
import { cn, formatTime } from "@/lib/utils"

const PAGE = 30
type Panel = "overview" | "posts" | "profile" | "performance"
type Tab = "all" | Source

const PANELS: { key: Panel; label: string }[] = [
  { key: "overview", label: "概览" },
  { key: "posts", label: "帖子" },
  { key: "profile", label: "写作画像" },
  { key: "performance", label: "流量画像" },
]

const TABS: { key: Tab; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "mine", label: "我的" },
  { key: "benchmark", label: "对标" },
]

/**
 * 作者主页。
 *
 * 身份依据始终是 **author_id**：作者改 username 后，本页链接与帖子归属都不失效。
 * 帖子区直接复用内容库的 /contents 查询（外加 author_id），
 * 不复制第二套列表、第二套详情、第二套 Facet API。
 *
 * 本页 0 AI 调用：进入页面 / 切换概览 / 翻帖子都不会触发分析。
 */
export default function AuthorDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const authorId = id ? Number(id) : null

  const [author, setAuthor] = useState<Author | null>(null)
  const [overview, setOverview] = useState<AuthorOverview | null>(null)
  const [loading, setLoading] = useState(true)
  const [notFound, setNotFound] = useState(false)

  const [panel, setPanel] = useState<Panel>("overview")

  // 帖子区筛选：与内容库同源，只是额外固定 author_id
  const [tab, setTab] = useState<Tab>("all")
  const [q, setQ] = useState("")
  const [debouncedQ, setDebouncedQ] = useState("")
  const [topic, setTopic] = useState<string | null>(null)
  const [type, setType] = useState<string | null>(null)
  const [tags, setTags] = useState<string[]>([])
  // 与内容库一致：批次筛选从 URL 带入（导入记录跳转过来）
  const [searchParams] = useSearchParams()
  const batchFilter = searchParams.get("import_batch_id")

  const toggleTag = (name: string) =>
    setTags((prev) =>
      prev.includes(name) ? prev.filter((t) => t !== name) : [...prev, name]
    )

  const [items, setItems] = useState<ContentItem[]>([])
  const [total, setTotal] = useState(0)
  const [facets, setFacets] = useState<Facets | null>(null)
  const [listLoading, setListLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)

  const [openId, setOpenId] = useState<number | null>(null)
  const [sheetOpen, setSheetOpen] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)

  // 写作画像：现有 ANALYSIS V2 的聚合，进入页面不触发任何 AI
  const [profile, setProfile] = useState<AuthorWritingProfile | null>(null)
  const [profileLoading, setProfileLoading] = useState(false)
  const [openEvidence, setOpenEvidence] = useState<{
    field: string
    value: string
  } | null>(null)
  const [fullEvidence, setFullEvidence] = useState<ProfileEvidenceItem[] | null>(null)
  const [evidenceLoading, setEvidenceLoading] = useState(false)

  useEffect(() => {
    if (authorId == null || panel !== "profile") return
    let cancelled = false
    setProfileLoading(true)
    void api
      .getAuthorWritingProfile(authorId)
      .then((data) => !cancelled && setProfile(data))
      .catch((err) => !cancelled && toast.error((err as Error).message))
      .finally(() => !cancelled && setProfileLoading(false))
    return () => {
      cancelled = true
    }
  }, [authorId, panel, reloadKey])

  const toggleEvidence = (field: string, value: string) => {
    if (openEvidence?.field === field && openEvidence.value === value) {
      setOpenEvidence(null)
      setFullEvidence(null)
      return
    }
    setOpenEvidence({ field, value })
    setFullEvidence(null)
  }

  const viewAllEvidence = async () => {
    if (authorId == null || !openEvidence) return
    setEvidenceLoading(true)
    try {
      const items = await api.getAuthorProfileEvidence(authorId, {
        field: openEvidence.field,
        value: openEvidence.value,
        limit: 100,
      })
      setFullEvidence(items)
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setEvidenceLoading(false)
    }
  }

  useEffect(() => {
    if (authorId == null) return
    let cancelled = false
    setLoading(true)
    setNotFound(false)
    void Promise.all([
      api.getAuthor(authorId),
      api.getAuthorOverview(authorId),
    ])
      .then(([a, o]) => {
        if (cancelled) return
        setAuthor(a)
        setOverview(o)
      })
      .catch(() => {
        if (!cancelled) setNotFound(true)
      })
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [authorId, reloadKey])

  // 搜索防抖
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQ(q.trim()), 300)
    return () => clearTimeout(timer)
  }, [q])

  const sourceFilter = tab === "all" ? null : (tab as Source)

  useEffect(() => {
    if (authorId == null || panel !== "posts") return
    let cancelled = false
    setListLoading(true)
    void api
      .listContents({
        source: sourceFilter,
        q: debouncedQ || undefined,
        tags: tags.length ? tags : undefined,
        topic: topic ?? undefined,
        content_type: type ?? undefined,
        author_id: authorId,
        import_batch_id: batchFilter ?? undefined,
        limit: PAGE,
        offset: 0,
      })
      .then((data) => {
        if (cancelled) return
        setItems(data.items)
        setTotal(data.total)
      })
      .catch((err) => !cancelled && toast.error((err as Error).message))
      .finally(() => !cancelled && setListLoading(false))
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authorId, panel, sourceFilter, debouncedQ, topic, type, reloadKey,
      batchFilter, tags.join("\u0001")])

  // 筛选项复用同一个 /facets（口径与列表完全一致）
  useEffect(() => {
    if (authorId == null || panel !== "posts") return
    let cancelled = false
    void api
      .getFacets({
        source: sourceFilter,
        q: debouncedQ || undefined,
        tags: tags.length ? tags : undefined,
        topic: topic ?? undefined,
        content_type: type ?? undefined,
        author_id: authorId,
        import_batch_id: batchFilter ?? undefined,
      })
      .then((data) => !cancelled && setFacets(data))
      .catch(() => !cancelled && setFacets(null))
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authorId, panel, sourceFilter, debouncedQ, topic, type, reloadKey,
      batchFilter, tags.join("\u0001")])

  const loadMore = async () => {
    if (authorId == null) return
    setLoadingMore(true)
    try {
      const data = await api.listContents({
        source: sourceFilter,
        q: debouncedQ || undefined,
        tags: tags.length ? tags : undefined,
        topic: topic ?? undefined,
        content_type: type ?? undefined,
        author_id: authorId,
        import_batch_id: batchFilter ?? undefined,
        limit: PAGE,
        offset: items.length,
      })
      setItems((prev) => [...prev, ...data.items])
      setTotal(data.total)
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setLoadingMore(false)
    }
  }

  const activeFilterCount =
    (topic ? 1 : 0) + (type ? 1 : 0) + tags.length + (batchFilter ? 1 : 0)
  const clearFilters = () => {
    setTopic(null)
    setType(null)
    setTags([])
  }

  const displayedName = useMemo(
    () => (author ? authorLabel(author.display_name, author.username) : ""),
    [author]
  )

  /*
    B3 #29：display_name 与格式化 username 文本相同时不再显示 @handle，
    避免「@王长富 / @王长富」重复两行。
  */
  const showHandle = author
    ? authorLabel(author.display_name, author.username) !== `@${author.username}`
    : false

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin text-text-secondary" />
      </div>
    )
  }

  if (notFound || !author || !overview) {
    return (
      <div className="mx-auto max-w-3xl p-6 md:p-8">
        <EmptyState
          title="找不到这个作者"
          description="作者可能已被删除。可以回到内容库「作者」模式重新选择。"
        />
        <div className="mt-4 text-center">
          <Button variant="outline" size="sm" asChild>
            <Link to="/library">回到内容库</Link>
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      {/* 头部：轻量，不做 Hero。
          任务 07 S3 第三批：标题与账号元信息走公共 PageHeader
          （description 已支持 ReactNode，承载多行元信息）。
          返回按钮 PageHeader 装不下（它没有前导插槽），按验收要求
          在外侧用轻量 flex 容器包住；「作者管理」走 PageHeader 的 actions。
          顶部 16px 间距保留 —— 本页是数据工作台，不强改成普通页的 32px。 */}
      <div className="border-b border-border px-6 py-4">
        <div className="flex items-start gap-3">
          <Button variant="ghost" size="icon" className="h-8 w-8 shrink-0" asChild>
            <Link to="/library">
              <ArrowLeft className="h-4 w-4" />
            </Link>
          </Button>
          <PageHeader
            title={displayedName}
            className="min-w-0 flex-1"
            description={
              showHandle || overview.latest_content_at || author.notes ? (
                <>
                  {showHandle && (
                    <span className="block truncate text-[12px] text-text-secondary">
                      @{author.username}
                    </span>
                  )}
                  {/* 顶部不再重复「篇数 / 已分析」—— 那组数字在「规模」区完整展示（B3 #30） */}
                  {overview.latest_content_at && (
                    <span className="mt-1.5 block text-[12px] text-text-tertiary">
                      最近更新 {formatTime(overview.latest_content_at)}
                    </span>
                  )}
                  {author.notes && (
                    <span className="mt-2 block max-w-2xl text-xs leading-relaxed text-text-tertiary">
                      {author.notes}
                    </span>
                  )}
                </>
              ) : undefined
            }
            actions={
              /* 与「作者管理页」互通，但两页职责不合并 */
              <Button variant="ghost" size="sm" className="shrink-0" asChild>
                <Link to="/authors">作者管理</Link>
              </Button>
            }
          />
        </div>

        <div className="mt-4">
          <Segmented options={PANELS} value={panel} onChange={setPanel} />
        </div>
      </div>

      {/* 概览 */}
      {panel === "overview" && (
        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
          <div className="max-w-3xl space-y-6">
            <Section title="规模">
              <div className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
                <Stat label="帖子总数" value={overview.content_count} />
                <Stat label="已分析" value={overview.analyzed_count} />
                <Stat label="未分析" value={overview.unanalyzed_count} />
                <Stat label="平均长度" value={overview.avg_chars} unit="字" />
              </div>
              <p className="mt-3 text-[11px] leading-relaxed text-text-tertiary">
                这里只统计「这个作者写了多少、分析到什么程度、通常写多长」。
                真实曝光 / 互动数据请看「流量画像」——那里同样只做历史统计，
                不提供「什么最容易爆」一类结论。
              </p>
            </Section>

            <Section title="内容归属">
              <div className="flex flex-wrap gap-x-6 gap-y-2 text-xs text-text-secondary">
                <span>
                  我的 <span className="tabular-nums">{overview.mine_count}</span>
                </span>
                <span>
                  对标 <span className="tabular-nums">{overview.benchmark_count}</span>
                </span>
                <span>
                  中位长度{" "}
                  <span className="tabular-nums">{overview.median_chars ?? "—"}</span> 字
                </span>
                {overview.earliest_content_at && (
                  <span>最早 {formatTime(overview.earliest_content_at)}</span>
                )}
                {overview.latest_content_at && (
                  <span>最近 {formatTime(overview.latest_content_at)}</span>
                )}
              </div>
              <p className="mt-2 text-[11px] leading-relaxed text-text-tertiary">
                「作者」回答谁写的，「来源」回答这条内容在系统里的语义，两者互不推导。
              </p>
            </Section>

            <Section title="常写主题">
              {overview.topics.length === 0 ? (
                <Hint text="还没有已分析内容，主题分布暂不可用。" />
              ) : (
                <BarList items={overview.topics} max={overview.analyzed_count} />
              )}
            </Section>

            <Section title="内容类型">
              {overview.content_types.length === 0 ? (
                <Hint text="还没有已分析内容，类型分布暂不可用。" />
              ) : (
                <BarList items={overview.content_types} max={overview.analyzed_count} />
              )}
            </Section>
          </div>
        </div>
      )}

      {/* 写作画像 */}
      {panel === "profile" && (
        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
          <div className="max-w-3xl">
            {profileLoading || !profile ? (
              <ListSkeleton />
            ) : profile.analyzed_sample_count === 0 ? (
              <EmptyState
                title="还没有可聚合的分析样本"
                description="写作画像来自已有内容分析结果。先对这位作者的帖子做分析，画像会自动出现——本页不会自动补分析。"
              />
            ) : (
              <>
                <div className="mb-6 rounded-lg border border-border px-4 py-3">
                  <p className="text-xs text-text-secondary">
                    已分析样本{" "}
                    <span className="tabular-nums text-foreground">
                      {profile.analyzed_sample_count}
                    </span>
                    {" / "}
                    <span className="tabular-nums">
                      {profile.total_content_count}
                    </span>
                    {" · 覆盖率 "}
                    <span className="tabular-nums text-foreground">
                      {Math.round(profile.coverage * 100)}%
                    </span>
                  </p>
                  {/* B3 #32：覆盖率说明压成两行，不出现技术字段名 */}
                  <p className="mt-1.5 text-[12px] text-text-tertiary">
                    样本覆盖 {profile.analyzed_sample_count} / {profile.total_content_count} ·{" "}
                    {Math.round(profile.coverage * 100)}%
                  </p>
                  <p className="mt-0.5 text-[12px] text-text-tertiary">
                    未分析帖子不计入本页统计。
                  </p>
                </div>

                <div className="space-y-7">
                  {profile.sections.map((section) => (
                    <section key={section.key}>
                      <h2 className="mb-3 text-[15px] font-semibold leading-none text-foreground">
                        {section.title}
                      </h2>
                      <div className="space-y-3">
                        {section.metrics.map((metric) => (
                          <MetricBlock
                            key={metric.field}
                            metric={metric}
                            sampleCount={profile.analyzed_sample_count}
                            openValue={
                              openEvidence?.field === metric.field
                                ? openEvidence.value
                                : null
                            }
                            fullEvidence={fullEvidence}
                            evidenceLoading={evidenceLoading}
                            onToggle={toggleEvidence}
                            onViewAll={() => void viewAllEvidence()}
                            onOpenContent={(cid) => {
                              setOpenId(cid)
                              setSheetOpen(true)
                            }}
                          />
                        ))}
                      </div>
                    </section>
                  ))}
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {/* 流量画像：确定性历史统计，0 AI */}
      {panel === "performance" && (
        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
          {authorId != null && (
            <AuthorPerformancePanel
              authorId={authorId}
              onOpenContent={(cid) => {
                setOpenId(cid)
                setSheetOpen(true)
              }}
            />
          )}
        </div>
      )}

      {/* 帖子 */}
      {panel === "posts" && (
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex flex-col gap-3 border-b border-border px-6 py-3">
            <div className="flex items-center gap-3">
              <div className="inline-flex h-9 items-center rounded-lg border border-border bg-hover p-0.5">
                {TABS.map((t) => (
                  <button
                    key={t.key}
                    onClick={() => setTab(t.key)}
                    className={cn(
                      "h-8 rounded px-3 text-sm font-medium transition-colors",
                      tab === t.key
                        ? "bg-surface text-foreground"
                        : "text-text-secondary hover:text-foreground"
                    )}
                  >
                    {t.label}
                  </button>
                ))}
              </div>

              <div className="relative flex-1">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-secondary" />
                <Input
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  placeholder="在该作者的帖子中搜索…"
                  className="pl-9"
                />
                {q && (
                  <button
                    onClick={() => setQ("")}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-text-secondary hover:text-foreground"
                  >
                    <X className="h-4 w-4" />
                  </button>
                )}
              </div>
            </div>

            {/* 筛选项来自同一个 /facets（外加 author_id），口径与列表一致 */}
            {facets && (facets.topics.length > 0 || facets.content_types.length > 0) && (
              <div className="flex flex-wrap items-center gap-1.5">
                {facets.topics.slice(0, 12).map((f) => (
                  <FacetButton
                    key={`topic-${f.name}`}
                    label={f.name}
                    count={f.count}
                    active={topic === f.name}
                    onClick={() => setTopic(topic === f.name ? null : f.name)}
                  />
                ))}
                {facets.content_types.slice(0, 8).map((f) => (
                  <FacetButton
                    key={`type-${f.name}`}
                    label={f.name}
                    count={f.count}
                    active={type === f.name}
                    onClick={() => setType(type === f.name ? null : f.name)}
                  />
                ))}
              </div>
            )}

            {/* 标签：同样来自同一个 facets，不新建第二套 Facet API */}
            {facets && facets.tags.length > 0 && (
              <div className="flex flex-wrap items-center gap-1.5">
                {facets.tags.slice(0, 20).map((f) => (
                  <FacetButton
                    key={`tag-${f.name}`}
                    label={`#${f.name}`}
                    count={f.count}
                    active={tags.includes(f.name)}
                    onClick={() => toggleTag(f.name)}
                  />
                ))}
              </div>
            )}

            {(activeFilterCount > 0 || debouncedQ) && (
              <div className="flex flex-wrap items-center gap-1.5">
                {batchFilter && (
                  <FilterChip
                    label={`批次：${batchFilter.slice(0, 8)}`}
                    onRemove={() => navigate(`/authors/${authorId}`)}
                  />
                )}
                {activeFilterCount > 0 && (
                  <button
                    onClick={clearFilters}
                    className="text-[11px] text-text-secondary hover:text-foreground"
                  >
                    清空筛选
                  </button>
                )}
              </div>
            )}
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
            <div className="mb-3">
              <p className="text-xs text-text-secondary">
                共 {total} 条{debouncedQ ? ` · 匹配「${debouncedQ}」` : ""}
                {" · 仅该作者"}
              </p>
            </div>

            {listLoading ? (
              <ListSkeleton />
            ) : items.length === 0 ? (
              <EmptyState
                title="没有找到帖子"
                description="换个关键词，或清空筛选条件试试。"
              />
            ) : (
              <div className="space-y-2">
                {items.map((item) => (
                  <ContentRow
                    key={item.id}
                    item={item}
                    onOpen={(cid) => {
                      setOpenId(cid)
                      setSheetOpen(true)
                    }}
                  />
                ))}

                {items.length < total && (
                  <div className="pt-3">
                    <Button
                      variant="outline"
                      size="sm"
                      className="w-full"
                      onClick={() => void loadMore()}
                      disabled={loadingMore}
                    >
                      {loadingMore && <Loader2 className="h-4 w-4 animate-spin" />}
                      加载更多（剩余 {total - items.length} 条）
                    </Button>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

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

/**
 * 单个写作维度的聚合结果。
 *
 * 每行可展开证据：证据帖是该字段取值**真实匹配**的帖子，
 * 点击后复用内容库同一套详情面板打开。
 */
function MetricBlock({
  metric,
  sampleCount,
  openValue,
  fullEvidence,
  evidenceLoading,
  onToggle,
  onViewAll,
  onOpenContent,
}: {
  metric: ProfileMetric
  sampleCount: number
  openValue: string | null
  fullEvidence: ProfileEvidenceItem[] | null
  evidenceLoading: boolean
  onToggle: (field: string, value: string) => void
  onViewAll: () => void
  onOpenContent: (id: number) => void
}) {
  if (metric.values.length === 0) {
    return (
      <div>
        <p className="text-xs text-text-tertiary">
          {metric.label}
          <span className="ml-1.5 text-text-tertiary">
            本批样本暂无该字段
          </span>
        </p>
      </div>
    )
  }

  const missing = sampleCount - metric.present_count
  const max = metric.values[0]?.count ?? 1

  return (
    <div>
      <div className="mb-1.5 flex items-baseline gap-2">
        <p className="text-xs font-medium">{metric.label}</p>
        <span className="text-[11px] text-text-tertiary">
          {metric.kind === "list" ? "出现率" : "占比"}
          {missing > 0 && ` · ${missing} 篇未给出该字段`}
        </span>
      </div>

      {/* B3 #33：不把每个 metric 做成 mini-card —— dense rows + divide-y */}
      <div className="divide-y divide-divider border-y border-divider">
        {metric.values.map((v) => {
          const open = openValue === v.value
          const evidence =
            open && fullEvidence ? fullEvidence : (v.evidence ?? [])
          return (
            <div key={v.value} className="border-b border-divider last:border-0">
              <button
                onClick={() => onToggle(metric.field, v.value)}
                className="flex w-full items-center gap-3 px-3 py-2 text-left transition-colors hover:bg-hover"
              >
                <span className="w-32 shrink-0 truncate text-xs sm:w-40">
                  {v.value}
                </span>
                <div className="h-1.5 min-w-0 flex-1 rounded-full bg-hover">
                  <div
                    className="h-1.5 rounded-full bg-foreground/45"
                    style={{ width: `${Math.round((v.count / max) * 100)}%` }}
                  />
                </div>
                <span className="w-8 shrink-0 text-right text-[11px] tabular-nums">
                  {v.count}
                </span>
                <span className="w-12 shrink-0 text-right text-[11px] tabular-nums text-text-secondary">
                  {v.pct}%
                </span>
                <ChevronRight
                  className={cn(
                    "h-3.5 w-3.5 shrink-0 text-text-tertiary transition-transform",
                    open && "rotate-90"
                  )}
                />
              </button>

              {open && (
                <div className="border-t border-divider bg-canvas px-3 py-2">
                  <p className="mb-1.5 text-[11px] text-text-secondary">
                    证据帖子（该字段取值 = {v.value}，共 {v.evidence_total} 条）
                  </p>
                  <div className="space-y-1">
                    {evidence.map((item) => (
                      <button
                        key={item.content_id}
                        onClick={() => onOpenContent(item.content_id)}
                        className="flex w-full items-start gap-2 rounded px-2 py-1.5 text-left transition-colors hover:bg-surface"
                      >
                        <Badge
                          variant={item.source === "mine" ? "mine" : "benchmark"}
                          className="mt-0.5 shrink-0"
                        >
                          {item.source === "mine" ? "我的" : "对标"}
                        </Badge>
                        <span className="min-w-0 flex-1 truncate text-[11px]">
                          {item.excerpt || "（无正文预览）"}
                        </span>
                        <span className="shrink-0 text-[11px] text-text-tertiary">
                          {item.created_at ? formatTime(item.created_at) : ""}
                        </span>
                      </button>
                    ))}
                  </div>
                  {v.evidence_total > evidence.length && (
                    <button
                      onClick={onViewAll}
                      disabled={evidenceLoading}
                      className="mt-1.5 inline-flex items-center gap-1 text-[11px] text-text-secondary hover:text-foreground"
                    >
                      {evidenceLoading && (
                        <Loader2 className="h-3 w-3 animate-spin" />
                      )}
                      查看全部 {v.evidence_total} 条
                    </button>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {metric.other_values_count > 0 && (
        <p className="mt-1 text-[11px] text-text-tertiary">
          另有 {metric.other_values_count} 个低频取值未展示
        </p>
      )}
    </div>
  )
}

/** 复用公共 SectionHeader（B3 §42）：四个 tab 共用同一套区块标题。 */
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <SectionHeader title={title} className="mb-3" />
      {children}
    </section>
  )
}

function Stat({
  label,
  value,
  unit,
}: {
  label: string
  value: number | null
  unit?: string
}) {
  return (
    <div>
      <p className="text-[11px] text-text-secondary">{label}</p>
      <p className="mt-0.5 text-[18px] font-medium leading-none tabular-nums">
        {value ?? "—"}
        {unit && value != null && (
          <span className="ml-0.5 text-xs font-normal text-text-secondary">
            {unit}
          </span>
        )}
      </p>
    </div>
  )
}

function Hint({ text }: { text: string }) {
  return <p className="text-xs text-text-tertiary">{text}</p>
}

function BarList({
  items,
  max,
}: {
  items: { name: string; count: number }[]
  max: number
}) {
  return (
    <div className="space-y-1.5">
      {items.map((it) => (
        <div key={it.name} className="flex items-center gap-3">
          <span className="w-40 shrink-0 truncate text-xs">{it.name}</span>
          <div className="h-1.5 min-w-0 flex-1 rounded-full bg-hover">
            <div
              className="h-1.5 rounded-full bg-foreground/45"
              style={{
                width: `${max > 0 ? Math.round((it.count / max) * 100) : 0}%`,
              }}
            />
          </div>
          <span className="w-8 shrink-0 text-right text-[11px] tabular-nums text-text-secondary">
            {it.count}
          </span>
        </div>
      ))}
    </div>
  )
}

function FilterChip({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <span className="inline-flex items-center gap-1 rounded border border-border bg-accent/60 px-2 py-0.5 text-[11px] text-foreground">
      {label}
      <button onClick={onRemove} className="text-text-secondary hover:text-foreground">
        <X className="h-3 w-3" />
      </button>
    </span>
  )
}

function FacetButton({
  label,
  count,
  active,
  onClick,
}: {
  label: string
  count: number
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "rounded border px-2 py-0.5 text-[11px] transition-colors",
        active
          ? "border-foreground/30 bg-accent text-foreground"
          : "border-border text-text-secondary hover:border-muted-foreground/40 hover:text-foreground"
      )}
    >
      {label}
      <span className="ml-1 text-text-tertiary">{count}</span>
    </button>
  )
}
