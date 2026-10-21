// 注意：node --test 直接跑 .ts 时 ESM 解析需要显式扩展名
import { buildExportQuery, type ExportScopeParams } from "./export-scope.ts"

/** 导出查询参数（预览 / 下载共用同一份，口径不可能不一致）。 */
export type ExportScopeQuery = ExportScopeParams

import type {
  AnalysisRun,
  AppConfig,
  ContentDetail,
  ContentItem,
  ContentList,
  Facets,
  ImportBatch,
  ImportBatchParseResult,
  ImportParseResult,
  ImportResult,
  Material,
  MaterialPoolKey,
  MaterialPools,
  PersonalizationState,
  Rewrite,
  RewriteCandidatesResult,
  AdoptCandidateResult,
  Source,
  StyleProfile,
  StyleStatus,
} from "./types"

const BASE = "/api"

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // FormData 必须让浏览器自己带 multipart boundary，不能手动设 Content-Type
  const isForm = typeof FormData !== "undefined" && init?.body instanceof FormData
  const res = await fetch(BASE + path, {
    ...init,
    headers: {
      ...(isForm ? {} : { "Content-Type": "application/json" }),
      ...((init?.headers as Record<string, string>) ?? {}),
    },
  })

  if (!res.ok) {
    let detail = `请求失败（${res.status}）`
    try {
      const body = await res.json()
      if (body?.detail) {
        detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail)
      }
    } catch {
      /* ignore */
    }
    throw new Error(detail)
  }

  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

export const api = {
  // ---- 系统 ----
  getConfig: () => request<AppConfig>("/config"),

  // ---- 内容 ----
  // 单条统一入口（阶段 4）：三模式共用，analyze 默认 false —— 收录不等于分析
  createContent: (payload: {
    raw_text: string
    source: Source
    analyze?: boolean
    /** 显式指定风格资格；不传则走后端默认规则（mine 可学 / benchmark 不可学） */
    style_eligible?: boolean
    /** 对标内容的正式作者 id（AuthorSelector 选出） */
    author_id?: number | null
    /** 对标内容原链接（复用 source_url） */
    source_url?: string
  }) =>
    request<ContentDetail>("/contents", {
      method: "POST",
      body: JSON.stringify({ analyze: false, ...payload }),
    }),

  listContents: (params: {
    source?: Source | null
    q?: string
    tags?: string[]
    topic?: string
    content_type?: string
    author_username?: string
    /** 作者主页的身份依据：用 author_id，不用 username */
    author_id?: number | null
    import_batch_id?: string
    style_eligible?: boolean
    limit?: number
    offset?: number
  }) => {
    const sp = new URLSearchParams()
    if (params.source) sp.set("source", params.source)
    if (params.q) sp.set("q", params.q)
    params.tags?.forEach((t) => sp.append("tags", t))
    if (params.topic) sp.set("topic", params.topic)
    if (params.content_type) sp.set("content_type", params.content_type)
    if (params.author_username) sp.set("author_username", params.author_username)
    if (params.author_id != null) sp.set("author_id", String(params.author_id))
    if (params.import_batch_id) sp.set("import_batch_id", params.import_batch_id)
    if (params.style_eligible !== undefined) {
      sp.set("style_eligible", String(params.style_eligible))
    }
    sp.set("limit", String(params.limit ?? 30))
    sp.set("offset", String(params.offset ?? 0))
    return request<ContentList>(`/contents?${sp.toString()}`)
  },

  getContent: (id: number) => request<ContentDetail>(`/contents/${id}`),

  deleteContent: (id: number) =>
    request<{ deleted: number }>(`/contents/${id}`, { method: "DELETE" }),

  reanalyze: (id: number) =>
    request<ContentDetail>(`/contents/${id}/analyze`, { method: "POST" }),

  getFacets: (params?: {
    source?: Source | null
    q?: string
    tags?: string[]
    topic?: string
    content_type?: string
    author_username?: string
    author_id?: number | null
    import_batch_id?: string
    style_eligible?: boolean
  }) => {
    const sp = new URLSearchParams()
    if (params?.source) sp.set("source", params.source)
    if (params?.q) sp.set("q", params.q)
    params?.tags?.forEach((t) => sp.append("tags", t))
    if (params?.topic) sp.set("topic", params.topic)
    if (params?.content_type) sp.set("content_type", params.content_type)
    if (params?.author_username) sp.set("author_username", params.author_username)
    if (params?.author_id != null) sp.set("author_id", String(params.author_id))
    if (params?.import_batch_id) sp.set("import_batch_id", params.import_batch_id)
    if (params?.style_eligible !== undefined) {
      sp.set("style_eligible", String(params.style_eligible))
    }
    const qs = sp.toString()
    return request<Facets>(`/facets${qs ? `?${qs}` : ""}`)
  },

  bulkEditContents: (payload: {
    ids: number[]
    source?: Source
    author_id?: number | null
    author_username?: string
    /** 用户显式请求清除作者（author_id 与 author_username 同时置空） */
    clear_author?: boolean
    style_eligible?: boolean
  }) =>
    request<{
      updated: number
      source_updated: number
      author_updated: number
      style_eligible_updated: number
    }>("/contents/bulk-edit", { method: "POST", body: JSON.stringify(payload) }),

  setStyleEligible: (contentId: number, eligible: boolean) =>
    request<{ content_id: number; style_eligible: boolean; updated: number }>(
      `/contents/${contentId}/style-eligible`,
      { method: "POST", body: JSON.stringify({ eligible }) }
    ),

  getStyleEvidence: () =>
    request<{
      eligible_count: number
      human_original: number
      imported_self: number
      ai_assisted: number
      excluded_count: number
      profile_version: number | null
      profile_sample_count: number
      profile_invalidated_samples: number
      sample_ids: number[]
    }>("/style/evidence"),

  // ---- 导入 ----
  parseImport: async (file: File): Promise<ImportParseResult> => {
    const form = new FormData()
    form.append("file", file)
    const res = await fetch(`${BASE}/imports/parse`, { method: "POST", body: form })
    if (!res.ok) throw new Error(`解析失败（${res.status}）`)
    return res.json()
  },

  /** 多文件解析：单文件失败不影响其它文件；附带只读的重复预检。 */
  parseImportBatch: async (files: File[]): Promise<ImportBatchParseResult> => {
    const form = new FormData()
    files.forEach((f) => form.append("files", f))
    const res = await fetch(`${BASE}/imports/parse-batch`, { method: "POST", body: form })
    if (!res.ok) throw new Error(`解析失败（${res.status}）`)
    return res.json()
  },

  bulkDeleteContents: (ids: number[]) =>
    request<{ deleted: number }>("/contents/bulk-delete", {
      method: "POST",
      body: JSON.stringify({ ids }),
    }),

  // ---- 导入（只入库，不分析）----
  commitImport: (
    source: Source,
    items: { text: string; source?: Source; metadata?: Record<string, unknown> }[],
    filename?: string
  ) =>
    request<ImportResult>("/imports/commit", {
      method: "POST",
      body: JSON.stringify({ source, items, filename, mode: "auto" }),
    }),

  listImportBatches: () =>
    request<{ items: ImportBatch[]; total: number }>("/imports"),

  pauseAnalysis: (batchId: string) =>
    request<{
      paused: boolean
      kind: string | null
      breakdown: Record<string, number>
    }>(`/imports/${batchId}/pause`, { method: "POST" }),

  continueAnalysis: (batchId: string, mode: "realtime" | "batch") =>
    request<AnalysisRun>(`/imports/${batchId}/continue`, {
      method: "POST",
      body: JSON.stringify({ mode }),
    }),

  deleteImportBatch: (batchId: string, mode: "keep_analyzed" | "all") =>
    request<{
      import_batch_id: string
      mode: string
      deleted: number
      kept_analyzed: number
      settled_breakdown: Record<string, number>
      remaining: Record<string, number>
      batch_record_kept: boolean
    }>(`/imports/${batchId}/delete`, {
      method: "POST",
      body: JSON.stringify({ mode }),
    }),

  undoImport: (batchId: string) =>
    request<{ import_batch_id: string; deleted: number; cancelled_remote_batch: boolean }>(
      `/imports/${batchId}`,
      { method: "DELETE" }
    ),

  // ---- 分析控制（必须由用户显式触发）----
  startAnalysis: (batchId: string, mode: "realtime" | "batch") =>
    request<AnalysisRun>(`/imports/${batchId}/analyze`, {
      method: "POST",
      body: JSON.stringify({ mode }),
    }),

  listRuns: (batchId?: string) =>
    request<AnalysisRun[]>(
      batchId ? `/analysis/runs?import_batch_id=${encodeURIComponent(batchId)}` : "/analysis/runs"
    ),

  cancelRun: (runId: number) =>
    request<{
      run_id: number
      status: string
      reverted: number
      remote: boolean
      batch_id: string | null
      already_finished: boolean
    }>(`/analysis/runs/${runId}/cancel`, { method: "POST" }),

  // ---- 改写 / 写作 ----
  generateDraft: (params: {
    source_text?: string
    source_content_id?: number | null
    instruction?: string
    base_text?: string
    /** 阶段 7：只传作者 id + 开关，画像内容由服务端读取，前端不传画像 JSON。 */
    reference_author_id?: number | null
    use_reference_profile?: boolean
    /** 04-C：创作输入路径。author_auto 下 source_text 可以为空。 */
    creation_path?: string | null
  }) =>
    request<Rewrite>("/rewrites", {
      method: "POST",
      body: JSON.stringify({ ...params, examples_k: 5 }),
    }),

  getRewrite: (id: number) => request<Rewrite>(`/rewrites/${id}`),

  saveRewrite: (id: number, final_text: string, save_as_mine: boolean) =>
    request<Rewrite>(`/rewrites/${id}`, {
      method: "PUT",
      body: JSON.stringify({ final_text, save_as_mine }),
    }),

  listRewrites: (contentId: number) =>
    request<{ items: Rewrite[]; total: number }>(`/contents/${contentId}/rewrites`),

  // ---- 阶段 10：素材池 + 三候选 ----
  getMaterialPools: () => request<MaterialPools>("/materials/pools"),

  getRandomMaterial: (params: {
    pool?: MaterialPoolKey
    author_id?: number | null
    exclude_ids?: number[]
    unused_first?: boolean
  }) => {
    const search = new URLSearchParams()
    search.set("pool", params.pool ?? "all")
    if (params.author_id) search.set("author_id", String(params.author_id))
    search.set("unused_first", String(params.unused_first ?? true))
    for (const id of params.exclude_ids ?? []) {
      search.append("exclude_ids", String(id))
    }
    return request<Material>(`/materials/random?${search.toString()}`)
  },

  generateCandidates: (params: {
    source_text?: string
    source_content_id?: number | null
    instruction?: string
    reference_author_id?: number | null
    use_reference_profile?: boolean
    /** 04-C：创作输入路径。author_auto 下 source_text 可以为空。 */
    creation_path?: string | null
  }) =>
    request<RewriteCandidatesResult>("/rewrite-candidates", {
      method: "POST",
      body: JSON.stringify(params),
    }),

  /** 采用某个候选 → 建一条正式 rewrite 记录（0 AI），之后复用现有保存链路。 */
  adoptCandidate: (params: {
    text: string
    source_content_id?: number | null
    instruction?: string
    model?: string | null
  }) =>
    request<AdoptCandidateResult>("/rewrites/adopt", {
      method: "POST",
      body: JSON.stringify(params),
    }),

  // ---- Personalization ----
  getPersonalization: () => request<PersonalizationState>("/personalization"),

  // ---- 风格档案 ----
  getStyleProfile: () => request<StyleProfile | null>("/style/profile"),
  recomputeStyle: () =>
    request<StyleProfile>("/style/recompute", { method: "POST" }),
  getStyleStatus: () => request<StyleStatus>("/style/status"),

  // ---- 作者 ----
  /**
   * @param params.withContent true = 只返回至少有一条内容的作者（内容库作者模式口径）
   *                           不传 / false = 全部正式作者（作者管理页口径，含 0 内容作者）
   */
  listAuthors: (params?: { q?: string; withContent?: boolean }) => {
    const sp = new URLSearchParams()
    if (params?.q) sp.set("q", params.q)
    if (params?.withContent) sp.set("with_content", "true")
    const qs = sp.toString()
    return request<{ items: import("./types").Author[]; total: number }>(
      `/authors${qs ? `?${qs}` : ""}`
    )
  },

  getAuthor: (id: number) => request<import("./types").Author>(`/authors/${id}`),

  /** 作者概览：纯聚合，后端不调用任何 AI，也不会触发分析。 */
  getAuthorOverview: (id: number) =>
    request<import("./types").AuthorOverview>(`/authors/${id}/overview`),

  /** 作者写作画像：现有 ANALYSIS V2 的聚合，0 AI。 */
  getAuthorWritingProfile: (id: number) =>
    request<import("./types").AuthorWritingProfile>(
      `/authors/${id}/writing-profile`
    ),

  /** 某个画像取值的全部证据帖（确定性匹配）。 */
  getAuthorProfileEvidence: (
    id: number,
    params: { field: string; value: string; limit?: number; offset?: number }
  ) => {
    const sp = new URLSearchParams()
    sp.set("field", params.field)
    sp.set("value", params.value)
    sp.set("limit", String(params.limit ?? 20))
    sp.set("offset", String(params.offset ?? 0))
    return request<import("./types").ProfileEvidenceItem[]>(
      `/authors/${id}/writing-profile/evidence?${sp.toString()}`
    )
  },

  /** 单账号流量画像：确定性历史统计，0 AI，现算不缓存。 */
  getAuthorPerformanceProfile: (id: number) =>
    request<import("./types").AuthorPerformanceProfile>(
      `/authors/${id}/performance-profile`
    ),

  /** 某个取值 / 组合的真实证据帖（精确匹配）。
   *
   * combo 必须走 left_value + right_value 结构化传参，
   * 绝不解析「A × B」展示字符串——取值本身可能含 ×。
   * order: high = 表现分倒序（默认） / low = 表现分升序（低表现榜用）。
   */
  getAuthorPerformanceEvidence: (
    id: number,
    params: {
      value?: string
      dimension?: string
      combo?: string
      leftValue?: string
      rightValue?: string
      order?: "high" | "low"
      limit?: number
      offset?: number
    }
  ) => {
    const sp = new URLSearchParams()
    if (params.value != null) sp.set("value", params.value)
    if (params.dimension) sp.set("dimension", params.dimension)
    if (params.combo) sp.set("combo", params.combo)
    if (params.leftValue != null) sp.set("left_value", params.leftValue)
    if (params.rightValue != null) sp.set("right_value", params.rightValue)
    sp.set("order", params.order ?? "high")
    sp.set("limit", String(params.limit ?? 5))
    sp.set("offset", String(params.offset ?? 0))
    return request<import("./types").PerformanceEvidenceItem[]>(
      `/authors/${id}/performance-profile/evidence?${sp.toString()}`
    )
  },

  /** 跨账号流量画像：作者等权的确定性聚合，0 AI，现算不缓存。 */
  getCrossAccountInsights: (authorIds?: number[]) => {
    const sp = new URLSearchParams()
    if (authorIds && authorIds.length) sp.set("author_ids", authorIds.join(","))
    const qs = sp.toString()
    return request<import("./types").CrossAccountInsights>(
      `/performance-insights/cross-account${qs ? `?${qs}` : ""}`
    )
  },

  createAuthor: (payload: {
    username: string
    display_name?: string
    notes?: string
  }) => request<import("./types").Author>("/authors", { method: "POST", body: JSON.stringify(payload) }),

  updateAuthor: (id: number, payload: {
    username?: string
    display_name?: string | null
    notes?: string | null
  }) => request<import("./types").Author>(`/authors/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),

  deleteAuthor: (id: number) =>
    request<{ deleted: number; id: number }>(`/authors/${id}`, { method: "DELETE" }),

  // ---- 阶段 12：外部分析 ----
  // 全部 0 AI：导出是纯查询，preview 是纯校验，confirm 是纯写入。
  getExternalPrompt: (count = 0) =>
    request<{
      prompt: string
      analysis_schema_version: string
      schema_hash: string
      prompt_version: string
      format_version: number
    }>(`/external-analysis/prompt?count=${count}`),

  /**
   * 导出范围预检（0 写入 / 0 AI）。
   *
   * 与 `externalTasksExportUrl` **共用** `buildExportQuery`：
   * 预览显示多少条，下载就是多少条，不存在第二套口径。
   * 不再传 limit —— 后端默认不限量。
   */
  previewExternalTasks: (params: ExportScopeQuery) =>
    request<import("./types").ExternalTaskPreview>(
      `/external-analysis/tasks/preview?${buildExportQuery(params).toString()}`
    ),

  /** 任务 JSONL 下载地址（后端生成，前端不拼数据）。 */
  externalTasksExportUrl: (params: ExportScopeQuery) =>
    `/api/external-analysis/tasks/export?${buildExportQuery(params).toString()}`,

  /** 当前筛选下待转出的 queued 内容 id（不限量，0 写入）。 */
  queuedHandoffCandidates: (params: {
    source?: Source | null
    authorIds?: number[]
    includeNoAuthor?: boolean
  }) => {
    const q = new URLSearchParams()
    if (params.source) q.set("source", params.source)
    const ids = (params.authorIds ?? []).filter((id) => Number.isFinite(id))
    if (ids.length) q.set("author_ids", ids.join(","))
    if (ids.length && params.includeNoAuthor) q.set("include_no_author", "true")
    return request<{ count: number; ids: number[] }>(
      `/external-analysis/handoff/candidates?${q.toString()}`
    )
  },

  /** 队列 → 外部分析交接：只转 queued（0 AI）。 */
  handoffToExternal: (contentIds: number[]) =>
    request<import("./types").ExternalHandoff>("/external-analysis/handoff", {
      method: "POST",
      body: JSON.stringify({ content_ids: contentIds }),
    }),

  previewExternalResults: (file: File, overwrite: boolean) => {
    const form = new FormData()
    form.append("file", file)
    form.append("overwrite", String(overwrite))
    return request<import("./types").ExternalPreview>("/external-analysis/results/preview", {
      method: "POST",
      body: form,
    })
  },

  confirmExternalResults: (file: File, overwrite: boolean, token: string | null) => {
    const form = new FormData()
    form.append("file", file)
    form.append("overwrite", String(overwrite))
    if (token) form.append("token", token)
    return request<import("./types").ExternalConfirm>("/external-analysis/results/confirm", {
      method: "POST",
      body: form,
    })
  },

  // ---- 「我的」V1：目标 / 合格浏览量 / 账号日指标 ----
  // 全部 0 AI：目标计算是纯日期运算，CSV 导入是纯解析 + 写入。

  /** 没有目标时 exists=false（这是常态，不是 404）。 */
  getMineGoal: () => request<import("./types").GoalState>("/mine/goal"),

  createMineGoal: (payload: { start_date: string; baseline_qualified_impressions: number }) =>
    request<import("./types").GoalState>("/mine/goal", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  /**
   * 就地修改目标的两个计划参数（单例，不新建、不留历史）。
   * TARGET / DURATION 是固定规则，请求体里没有它们。
   */
  updateMineGoal: (payload: { start_date: string; baseline_qualified_impressions: number }) =>
    request<import("./types").GoalState>("/mine/goal", {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  listMineSnapshots: () =>
    request<import("./types").QualifiedSnapshot[]>("/mine/snapshots"),

  addMineSnapshot: (payload: { recorded_at: string; qualified_impressions: number }) =>
    request<import("./types").QualifiedSnapshot>("/mine/snapshots", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  /** 只更新显式传入的字段（undefined = 不改）。 */
  editMineSnapshot: (
    id: number,
    payload: { recorded_at?: string; qualified_impressions?: number }
  ) =>
    request<import("./types").QualifiedSnapshot>(`/mine/snapshots/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  deleteMineSnapshot: (id: number) =>
    request<{ deleted: number; id: number }>(`/mine/snapshots/${id}`, { method: "DELETE" }),

  /** 整文件一个事务：任一行出错 → 全部回滚，detail 带 row / column。 */
  importAccountMetrics: (file: File) => {
    const form = new FormData()
    form.append("file", file)
    return request<import("./types").AccountMetricsImportResult>(
      "/mine/account-metrics/import",
      { method: "POST", body: form }
    )
  },

  /** 某指标完整序列（不分页）+ 最新 / 上一次 / 差值。 */
  getAccountMetric: (metric: string) =>
    request<import("./types").AccountMetric>(
      `/mine/account-metrics?metric=${encodeURIComponent(metric)}`
    ),
}

export type { ContentItem, ContentDetail }
