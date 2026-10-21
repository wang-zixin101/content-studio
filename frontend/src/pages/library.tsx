import { useEffect, useState } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { ChevronRight, Loader2, Search, SlidersHorizontal, X } from "lucide-react"
import { toast } from "sonner"

import { AuthorSelector } from "@/components/author-selector"
import { ContentDetailSheet } from "@/components/content-detail-sheet"
import { ContentRow } from "@/components/content-row"
import { EmptyState, ListSkeleton, Segmented } from "@/components/common"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api } from "@/lib/api"
import { authorLabel } from "@/lib/author-display"
import { collectAllIds } from "@/lib/collect-all-ids"
import type { Author, ContentItem, Facets, Source } from "@/lib/types"
import { cn } from "@/lib/utils"

type Tab = "all" | Source
/** 内容库的两种浏览对象：内容列表 / 正式作者列表。默认「内容」。 */
type View = "content" | "author"
const PAGE = 30

const TABS: { key: Tab; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "mine", label: "我的" },
  { key: "benchmark", label: "对标" },
]

const VIEWS: { key: View; label: string }[] = [
  { key: "content", label: "内容" },
  { key: "author", label: "作者" },
]

export default function LibraryPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()

  const [view, setView] = useState<View>("content")
  const [tab, setTab] = useState<Tab>("all")
  const [q, setQ] = useState("")
  const [debouncedQ, setDebouncedQ] = useState("")
  const [tags, setTags] = useState<string[]>([])
  const [topic, setTopic] = useState<string | null>(null)
  const [type, setType] = useState<string | null>(null)
  const [author, setAuthor] = useState<string | null>(null)
  // 风格学习资格筛选：undefined=全部 / true=参与 / false=不参与
  const [eligibleFilter, setEligibleFilter] = useState<boolean | undefined>(undefined)
  const [showFilters, setShowFilters] = useState(false)

  const [facets, setFacets] = useState<Facets | null>(null)
  const [items, setItems] = useState<ContentItem[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)

  const [openId, setOpenId] = useState<number | null>(null)
  const [sheetOpen, setSheetOpen] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)

  // 多选与批量删除
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [deleting, setDeleting] = useState(false)
  const [editing, setEditing] = useState(false)

  // 批量改作者
  const [authorSelectorOpen, setAuthorSelectorOpen] = useState(false)

  // 作者模式：只展示至少有一条内容的正式作者（0 内容作者归作者管理页管）
  const [authors, setAuthors] = useState<Author[]>([])
  // 内容模式下作者筛选 chip 显示 display_name（原来是裸 username）。
  // 纯读取既有列表接口做映射，selection 仍传 username，不改 API。
  const [authorNames, setAuthorNames] = useState<Record<string, string>>({})
  const [authorsLoading, setAuthorsLoading] = useState(false)

  // 来自「导入记录」的批次筛选
  const [batchFilter, setBatchFilter] = useState<string | null>(null)
  useEffect(() => {
    const bid = searchParams.get("import_batch_id")
    if (bid) {
      setBatchFilter(bid)
      setShowFilters(true)
    }
  }, [searchParams])

  const toggleSelect = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const selectAllLoaded = () => setSelected(new Set(items.map((i) => i.id)))
  const clearSelection = () => setSelected(new Set())

  // 全选「当前筛选结果」的全部内容（跨页）。
  // 后端 limit 上限为 100，因此必须分页拉取 —— 见 lib/collect-all-ids。
  const [selectingAll, setSelectingAll] = useState(false)
  const [selectProgress, setSelectProgress] = useState<number | null>(null)

  const selectAllMatching = async () => {
    if (selectingAll) return // 获取中禁止重复点击
    setSelectingAll(true)
    setSelectProgress(0)
    // 先清空：失败后状态明确是「未选择」，不会留下半成品
    setSelected(new Set())
    try {
      const ids = await collectAllIds(
        ({ limit, offset }) =>
          api.listContents({
            // 完整保留当前筛选条件，绝不越界选到筛选之外的内容
            source: tab === "all" ? null : tab,
            q: debouncedQ || undefined,
            tags,
            topic: topic ?? undefined,
            content_type: type ?? undefined,
            author_username: author ?? undefined,
            import_batch_id: batchFilter ?? undefined,
            style_eligible: eligibleFilter,
            limit,
            offset,
          }),
        {
          total,
          onProgress: (fetched) => setSelectProgress(fetched),
        }
      )
      setSelected(new Set(ids))
      toast.success(`已选中全部 ${ids.length} 条`)
    } catch (err) {
      setSelected(new Set())
      toast.error(`选择全部失败：${(err as Error).message}`, { duration: 6000 })
    } finally {
      setSelectingAll(false)
      setSelectProgress(null)
    }
  }

  const deleteSelected = async () => {
    if (selected.size === 0) return
    const ok = window.confirm(`确定删除选中的 ${selected.size} 条内容？不可恢复。`)
    if (!ok) return
    setDeleting(true)
    try {
      const result = await api.bulkDeleteContents([...selected])
      toast.success(`已删除 ${result.deleted} 条`)
      clearSelection()
      setReloadKey((k) => k + 1)
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setDeleting(false)
    }
  }

  // 批量修改来源
  const changeSource = async (next: Source) => {
    if (selected.size === 0) return
    const ok = window.confirm(
      `将修改 ${selected.size} 条内容的来源为「${next === "mine" ? "我的" : "对标"}」？`
    )
    if (!ok) return
    setEditing(true)
    try {
      const r = await api.bulkEditContents({ ids: [...selected], source: next })
      toast.success(`已修改 ${r.source_updated} 条来源`)
      clearSelection()
      setReloadKey((k) => k + 1)
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setEditing(false)
    }
  }

  // 批量设置是否参与风格学习（只对「我的内容」生效）
  const changeEligibility = async (eligible: boolean) => {
    if (selected.size === 0) return
    const mineCount = items.filter(
      (i) => selected.has(i.id) && i.source === "mine"
    ).length
    const total = selected.size
    const benchCount = total - mineCount

    const msg =
      benchCount > 0
        ? `选中 ${total} 条\n其中：\n  mine ${mineCount}\n  benchmark ${benchCount}\n\n` +
          `将修改 ${mineCount} 条 mine 内容。\n` +
          `${benchCount} 条 benchmark 不参与风格学习。`
        : `将 ${mineCount} 条内容设为「${eligible ? "参与" : "不参与"}风格学习」？`
    if (!window.confirm(msg)) return

    setEditing(true)
    try {
      const r = await api.bulkEditContents({
        ids: [...selected],
        style_eligible: eligible,
      })
      toast.success(
        `已更新 ${r.style_eligible_updated} 条（benchmark 不受影响）`
      )
      clearSelection()
      setReloadKey((k) => k + 1)
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setEditing(false)
    }
  }

  // 批量修改作者：使用 AuthorSelector 选择正式作者；也支持清除作者
  const changeAuthor = () => {
    if (selected.size === 0) return
    setAuthorSelectorOpen(true)
  }

  const applyAuthor = async (author: Author | null) => {
    setEditing(true)
    try {
      const r = await api.bulkEditContents(
        author
          ? { ids: [...selected], author_id: author.id }
          : // 清除作者是独立语义，不能用 author_id=null 表示（那会被当成「未传」）
            { ids: [...selected], clear_author: true }
      )
      toast.success(
        author
          ? `已修改 ${r.author_updated} 条作者`
          : `已清除 ${r.author_updated} 条内容的作者`
      )
      clearSelection()
      setReloadKey((k) => k + 1)
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setEditing(false)
    }
  }

  const sourceFilter = tab === "all" ? null : (tab as Source)

  // 搜索防抖
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQ(q.trim()), 300)
    return () => clearTimeout(timer)
  }, [q])

  // 作者显示名映射（任务 07 S6）：筛选 chip 用 display_name，过滤仍用 username
  useEffect(() => {
    let cancelled = false
    void api
      .listAuthors({})
      .then((data) => {
        if (cancelled) return
        const m: Record<string, string> = {}
        for (const a of data.items || []) if (a.display_name) m[a.username] = a.display_name
        setAuthorNames(m)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [reloadKey])

  // 作者模式：数据来自正式 authors，with_content=true 过滤掉 0 内容作者。
  // 纯读取，不触发任何分析。
  useEffect(() => {
    if (view !== "author") return
    let cancelled = false
    setAuthorsLoading(true)
    void api
      .listAuthors({ q: debouncedQ || undefined, withContent: true })
      .then((data) => {
        if (!cancelled) setAuthors(data.items || [])
      })
      .catch((err) => !cancelled && toast.error((err as Error).message))
      .finally(() => !cancelled && setAuthorsLoading(false))
    return () => {
      cancelled = true
    }
  }, [view, debouncedQ, reloadKey])

  // 加载筛选项。
  // 必须与列表使用**完全相同的筛选条件**，否则会出现
  // 「右侧 0 条、左侧仍显示一堆主题/标签」的幽灵统计。
  // 依赖里带上 reloadKey：删除 / 恢复 / 撤销导入后立即同步刷新。
  const tagsKey = tags.join("\u0001")
  useEffect(() => {
    if (view !== "content") return
    let cancelled = false
    void api
      .getFacets({
        source: sourceFilter,
        q: debouncedQ || undefined,
        tags: tags.length ? tags : undefined,
        topic: topic || undefined,
        content_type: type || undefined,
        author_username: author || undefined,
        import_batch_id: batchFilter || undefined,
        style_eligible: eligibleFilter,
      })
      .then((data) => {
        if (!cancelled) setFacets(data)
      })
      .catch(() => {
        if (!cancelled) setFacets(null)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, sourceFilter, debouncedQ, tagsKey, topic, type, author, batchFilter,
      eligibleFilter, reloadKey])

  // 加载列表
  useEffect(() => {
    if (view !== "content") return
    let cancelled = false
    setLoading(true)
    void api
      .listContents({
        source: sourceFilter,
        q: debouncedQ || undefined,
        tags: tags.length ? tags : undefined,
        topic: topic || undefined,
        content_type: type || undefined,
        author_username: author || undefined,
        import_batch_id: batchFilter || undefined,
        style_eligible: eligibleFilter,
        limit: PAGE,
        offset: 0,
      })
      .then((data) => {
        if (cancelled) return
        setItems(data.items)
        setTotal(data.total)
      })
      .catch((err) => toast.error((err as Error).message))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
    // 任务 07 S6：补上 author / batchFilter —— 之前漏在依赖里，
    // 点作者或批次筛选后只有 facets 会重查、列表永远不重查（筛选等于失效）。
    // 最终验收补充：eligibleFilter 也一样漏了 —— 「参与 / 不参与」切换后列表不重查。
  }, [view, sourceFilter, debouncedQ, tags, topic, type, author, batchFilter,
      eligibleFilter, reloadKey])

  // 打开详情（URL 携带 focus）
  const focusParam = searchParams.get("focus")
  useEffect(() => {
    if (focusParam) {
      setOpenId(Number(focusParam))
      setSheetOpen(true)
    }
  }, [focusParam])

  const openDetail = (id: number) => {
    setOpenId(id)
    setSheetOpen(true)
  }

  const closeSheet = (open: boolean) => {
    setSheetOpen(open)
    if (!open && searchParams.has("focus")) {
      searchParams.delete("focus")
      setSearchParams(searchParams, { replace: true })
    }
  }

  const loadMore = async () => {
    setLoadingMore(true)
    try {
      const data = await api.listContents({
        source: sourceFilter,
        q: debouncedQ || undefined,
        tags: tags.length ? tags : undefined,
        topic: topic || undefined,
        content_type: type || undefined,
        author_username: author || undefined,
        import_batch_id: batchFilter || undefined,
        style_eligible: eligibleFilter,
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

  const toggleTag = (name: string) =>
    setTags((prev) =>
      prev.includes(name) ? prev.filter((t) => t !== name) : [...prev, name]
    )

  // 最终验收补充：作者 / 批次 / 风格学习也是实际生效的筛选，
  // 之前漏算 —— 只选这些条件时已选筛选条和「清空」都不出现。
  const activeFilterCount =
    tags.length +
    (topic ? 1 : 0) +
    (type ? 1 : 0) +
    (author ? 1 : 0) +
    (batchFilter ? 1 : 0) +
    (eligibleFilter !== undefined ? 1 : 0)

  const clearFilters = () => {
    setTags([])
    setTopic(null)
    setType(null)
    setAuthor(null)
    setBatchFilter(null)
    setEligibleFilter(undefined)
  }

  return (
    <div className="flex h-full">
      {/* 筛选栏：只在内容模式出现（作者模式的筛选对象是作者本身，不是 contents） */}
      {view === "content" && (
      <aside
        className={cn(
          "w-[216px] shrink-0 overflow-y-auto border-r border-border px-4 py-5",
          showFilters ? "block" : "hidden lg:block"
        )}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-[12px] font-semibold text-text-secondary">筛选</h2>
          {activeFilterCount > 0 && (
            <button
              onClick={clearFilters}
              className="text-[12px] text-text-tertiary hover:text-foreground"
            >
              清空
            </button>
          )}
        </div>

        {!facets ? (
          <p className="text-[12px] text-text-tertiary">加载中…</p>
        ) : (
          <div className="space-y-7 pt-1">
            <div className="space-y-2">
              <p className="text-[12px] font-semibold text-text-secondary">风格学习</p>
              <div className="flex flex-wrap gap-1.5">
                {(
                  [
                    { label: "全部", value: undefined },
                    { label: "参与", value: true },
                    { label: "不参与", value: false },
                  ] as const
                ).map((opt) => (
                  <button
                    key={opt.label}
                    type="button"
                    onClick={() => setEligibleFilter(opt.value)}
                    className={cn(
                      "rounded border px-2 py-1 text-[12px] transition-colors",
                      eligibleFilter === opt.value
                        ? "border-border bg-surface text-foreground"
                        : "border-border text-text-secondary hover:text-foreground"
                    )}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
              <p className="text-[12px] leading-relaxed text-text-tertiary">
                只影响「我的内容」。对标内容永远不参与风格学习。
              </p>
            </div>
            <FilterGroup
              title="作者"
              items={facets.authors.map((f) => ({
                ...f,
                label: authorNames[f.name] ?? f.name,
              }))}
              selected={author ? [author] : []}
              single
              onToggle={(name) => setAuthor(author === name ? null : name)}
            />
            <FilterGroup
              title="主题"
              items={facets.topics}
              selected={topic ? [topic] : []}
              single
              onToggle={(name) => setTopic(topic === name ? null : name)}
            />
            <FilterGroup
              title="内容类型"
              items={facets.content_types}
              selected={type ? [type] : []}
              single
              onToggle={(name) => setType(type === name ? null : name)}
            />
            <FilterGroup
              title="标签"
              items={facets.tags.slice(0, 40)}
              selected={tags}
              onToggle={toggleTag}
            />
          </div>
        )}
      </aside>
      )}

      {/* 主区 */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* 工具条 */}
        <div className="flex flex-col gap-3 border-b border-border px-6 py-4">
          <div className="flex items-center gap-3">
            <Segmented
              options={VIEWS}
              value={view}
              onChange={setView}
            />

            {view === "content" && (
              <Segmented options={TABS} value={tab} onChange={setTab} size="sm" />
            )}

            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-secondary" />
              <Input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder={
                  view === "author"
                    ? "搜索作者名称或 @username…"
                    : "搜索原文、摘要或标签…"
                }
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

            {view === "content" && (
              <Button
                variant="outline"
                size="sm"
                className="lg:hidden"
                onClick={() => setShowFilters((v) => !v)}
              >
                <SlidersHorizontal className="h-4 w-4" />
                {activeFilterCount > 0 && (
                  <span className="ml-1 text-xs">{activeFilterCount}</span>
                )}
              </Button>
            )}
          </div>

          {view === "content" && activeFilterCount > 0 && (
            <div className="flex flex-wrap items-center gap-1.5">
              {topic && (
                <FilterChip label={topic} onRemove={() => setTopic(null)} />
              )}
              {author && (
                <FilterChip
                  label={`作者：${authorNames[author] ?? author}`}
                  onRemove={() => setAuthor(null)}
                />
              )}
              {batchFilter && (
                <FilterChip
                  label={`批次：${batchFilter.slice(0, 8)}`}
                  onRemove={() => setBatchFilter(null)}
                />
              )}
              {eligibleFilter !== undefined && (
                <FilterChip
                  label={eligibleFilter ? "风格学习：参与" : "风格学习：不参与"}
                  onRemove={() => setEligibleFilter(undefined)}
                />
              )}
              {type && <FilterChip label={type} onRemove={() => setType(null)} />}
              {tags.map((t) => (
                <FilterChip key={t} label={t} onRemove={() => toggleTag(t)} />
              ))}
            </div>
          )}
        </div>

        {/* 列表 */}
        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
          {view === "author" ? (
            <>
              <div className="mb-3">
                <p className="text-[12px] text-text-tertiary">
                  {authors.length} 位作者
                  {debouncedQ ? ` · 匹配「${debouncedQ}」` : ""}
                  {" · 只看已有内容的作者"}
                </p>
              </div>

              {authorsLoading ? (
                <ListSkeleton />
              ) : authors.length === 0 ? (
                <EmptyState
                  title="没有找到作者"
                  description={
                    debouncedQ
                      ? "换个关键词试试。"
                      : "还没有任何带作者的内容。批量导入对标内容后，作者会自动出现在这里。"
                  }
                  action={
                    debouncedQ ? undefined : (
                      <Button size="sm" variant="outline" onClick={() => navigate("/import")}>
                        去导入素材
                      </Button>
                    )
                  }
                />
              ) : (
                <div className="overflow-hidden rounded-lg border border-border">
                  {authors.map((author) => (
                    <button
                      key={author.id}
                      onClick={() => navigate(`/authors/${author.id}`)}
                      className="flex w-full items-center gap-3 border-b border-divider px-4 py-3 text-left transition-colors last:border-0 hover:bg-hover"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="truncate text-sm font-medium">
                            {authorLabel(author.display_name, author.username)}
                          </span>
                          {author.display_name && (
                            <span className="truncate text-xs text-text-tertiary">
                              @{author.username}
                            </span>
                          )}
                        </div>
                        <p className="mt-0.5 text-xs text-text-secondary">
                          <span className="tabular-nums">
                            {author.content_count} 篇
                          </span>
                          {" · 已分析 "}
                          <span className="tabular-nums">
                            {author.analyzed_count}
                          </span>
                        </p>
                      </div>
                      <ChevronRight className="h-4 w-4 shrink-0 text-text-tertiary" />
                    </button>
                  ))}
                </div>
              )}
            </>
          ) : (
          <>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <p className="text-[12px] text-text-tertiary">
              共 <span className="tabular-nums">{total}</span> 条{debouncedQ ? ` · 匹配「${debouncedQ}」` : ""}
              {selected.size > 0 && ` · 已选 ${selected.size} 条`}
            </p>

            <div className="ml-auto flex items-center gap-2">
              {selected.size > 0 ? (
                <>
                  <Button variant="ghost" size="sm" className="" onClick={clearSelection}>
                    取消选择
                  </Button>
                  <Button variant="ghost" size="sm" onClick={selectAllLoaded}>
                    全选本页（{items.length}）
                  </Button>
                  {total > items.length && (
                    <Button
                      variant="outline"
                      size="sm"
                      className=""
                      disabled={selectingAll}
                      onClick={() => void selectAllMatching()}
                    >
                      {selectingAll && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                      {selectingAll
                        ? `正在选择全部 ${total} 条${selectProgress ? `（${selectProgress}/${total}）` : "…"}`
                        : `全选全部（${total}）`}
                    </Button>
                  )}
                  <Button
                    variant="destructive"
                    size="sm"
                    disabled={deleting || editing}
                    onClick={() => void deleteSelected()}
                  >
                    {deleting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                    删除已选（{selected.size}）
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={editing || deleting}
                    onClick={() => void changeSource("benchmark")}
                  >
                    改为对标
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={editing || deleting}
                    onClick={() => void changeSource("mine")}
                  >
                    改为我的
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={editing || deleting}
                    onClick={() => void changeAuthor()}
                  >
                    改作者
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={editing || deleting}
                    onClick={() => void changeEligibility(true)}
                  >
                    参与风格学习
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-text-tertiary"
                    disabled={editing || deleting}
                    onClick={() => void changeEligibility(false)}
                  >
                    不参与风格学习
                  </Button>
                </>
              ) : (
                items.length > 0 && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className=" text-text-secondary"
                    onClick={() => setSelected(new Set())}
                  >
                    多选
                  </Button>
                )
              )}
            </div>
          </div>

          {loading ? (
            <ListSkeleton />
          ) : items.length === 0 ? (
            <EmptyState
              title="没有找到内容"
              description="换个关键词，或到「写作」添加一条内容试试。"
              action={
                <Button size="sm" variant="outline" onClick={() => navigate("/write")}>
                  去写作
                </Button>
              }
            />
          ) : (
            <div className="-mx-6 divide-y divide-divider border-t border-divider">
              {items.map((item) => (
                <ContentRow
                  key={item.id}
                  item={item}
                  onOpen={openDetail}
                  selected={selected.has(item.id)}
                  onToggleSelect={toggleSelect}
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
          </>
          )}
        </div>
      </div>

      <ContentDetailSheet
        contentId={openId}
        open={sheetOpen}
        onOpenChange={closeSheet}
        onSelectContent={(id) => setOpenId(id)}
        onChanged={() => setReloadKey((k) => k + 1)}
      />

      <AuthorSelector
        open={authorSelectorOpen}
        onOpenChange={setAuthorSelectorOpen}
        onSelect={(author) => void applyAuthor(author)}
        allowClear
        onClear={() => void applyAuthor(null)}
      />
    </div>
  )
}

function FilterGroup({
  title,
  items,
  selected,
  onToggle,
  single,
}: {
  title: string
  items: { name: string; count: number; label?: string }[]
  selected: string[]
  onToggle: (name: string) => void
  single?: boolean
}) {
  if (items.length === 0) return null

  return (
    <div>
      <p className="mb-2 text-xs font-medium text-text-secondary">{title}</p>
      <div className="flex flex-wrap gap-1.5">
        {items.map((f) => {
          const active = selected.includes(f.name)
          return (
            <button
              key={f.name}
              onClick={() => onToggle(f.name)}
              className={cn(
                "rounded border px-2 py-0.5 text-[11px] transition-colors",
                active
                  ? "border-foreground/30 bg-accent text-foreground"
                  : "border-border text-text-secondary hover:border-muted-foreground/40 hover:text-foreground"
              )}
              title={single ? undefined : `${f.count} 条`}
            >
              {f.label ?? f.name}
              <span className="ml-1 text-text-tertiary">{f.count}</span>
            </button>
          )
        })}
      </div>
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
