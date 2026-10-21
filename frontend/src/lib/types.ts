export type Source = "mine" | "benchmark"
export type Status = "pending" | "analyzing" | "analyzed" | "failed"
export type Provenance = "human_original" | "imported_self" | "ai_assisted"
export type PersonalizationLevel = "COLD_START" | "LEARNING" | "PERSONALIZED"

export interface PersonalizationState {
  state: PersonalizationLevel
  confidence: string
  reason: string
  sample_count: number
  topic_count: number
  content_type_count: number
  human_original_count: number
  imported_self_count: number
  ai_assisted_count: number
  thresholds: Record<string, number>
}

export interface AnalysisClaim {
  kind: string
  text: string
  evidence: string
}

export interface AnalysisMove {
  type: string
  evidence: string
  mechanism: string
}

export interface AnalysisMarker {
  type: string
  evidence: string
}

export interface AnalysisPattern {
  method: string
  evidence: string
  how_to_apply: string
}

/** 内容层：这篇实际说了什么、改写必须保住什么、当前看不到什么。 */
export interface AnalysisContentBlock {
  claims: AnalysisClaim[]
  must_preserve: string[]
  context_limits: string[]
}

/** 写法层：开头 / 结构 / 结尾 / 动作。 */
export interface AnalysisWritingBlock {
  opening_type: string | null
  structure_type: string | null
  ending_type: string | null
  moves: AnalysisMove[]
}

/** 语言层：只描述这一篇，不是作者的长期风格。 */
export interface AnalysisStyleBlock {
  tone: string | null
  register: string | null
  directness: string | null
  certainty: string | null
  rhythm: string | null
  markers: AnalysisMarker[]
  distinctive_phrases: string[]
}

/** 复用层：可迁移的方法与不宜迁移的边界。 */
export interface AnalysisReuseBlock {
  patterns: AnalysisPattern[]
  boundaries: string[]
}

export interface Analysis {
  // 基础字段
  topic: string | null
  subtopic: string | null
  content_type: string | null
  core_message: string | null
  tags: string[]
  keywords: string[]
  // 四个模块
  content: AnalysisContentBlock | null
  writing: AnalysisWritingBlock | null
  style: AnalysisStyleBlock | null
  reuse: AnalysisReuseBlock | null
  // 本地计算 / 版本指纹
  length_chars: number | null
  model: string | null
  prompt_version: string | null
  schema_version: string | null
  schema_hash: string | null
  created_at: string | null
}

/** Xporter 扩展来源字段（只读，用于查看与过滤）。 */
export interface SourceMeta {
  conversation_id?: string
  language?: string
  reply_to_username?: string
  hashtags?: string[]
  urls?: string[]
  media_urls?: string[]
}

export interface SimilarItem {
  id: number
  source: Source
  excerpt: string
  topic: string | null
  content_type: string | null
  score: number
  shared_tags: string[]
  provenance?: Provenance | null
  edit_ratio?: number | null
}

export type AnalysisState =
  | "unprocessed"
  | "queued"
  | "processing"
  | "analyzed"
  | "failed"
  | "cancelled"

export interface ImportResult {
  created: number
  content_ids: number[]
  skipped: number
  duplicates: number
  input_count: number
  mode: string
  import_batch_id: string | null
}

export interface AnalysisRun {
  id: number
  kind: "realtime" | "batch"
  import_batch_id: string | null
  status: "running" | "cancelling" | "cancelled" | "completed" | "failed"
  total: number
  done: number
  failed: number
  batch_job_id: number | null
  model: string | null
  error: string | null
  created_at: string
  updated_at: string
  breakdown: Record<string, number>
}

export interface Author {
  id: number
  platform: string
  username: string
  display_name: string | null
  notes: string | null
  content_count: number
  analyzed_count: number
  created_at: string
  updated_at: string
}

/** 作者概览：只描述「这个作者通常写什么」，不含任何表现/爆款结论。 */
export interface AuthorOverview {
  author_id: number
  content_count: number
  analyzed_count: number
  unanalyzed_count: number
  mine_count: number
  benchmark_count: number
  earliest_content_at: string | null
  latest_content_at: string | null
  avg_chars: number | null
  median_chars: number | null
  topics: { name: string; count: number }[]
  content_types: { name: string; count: number }[]
}

/** 画像证据帖（轻量，不含完整正文） */
export interface ProfileEvidenceItem {
  content_id: number
  source: string | null
  created_at: string | null
  excerpt: string
  value: string
}

export interface ProfileMetricValue {
  value: string
  count: number
  pct: number
  evidence_total: number
  evidence: ProfileEvidenceItem[]
}

/** kind: categorical = 取值互斥的占比；list = 取值的出现率 */
export interface ProfileMetric {
  field: string
  label: string
  kind: "categorical" | "list"
  present_count: number
  distinct_value_count: number
  other_values_count: number
  values: ProfileMetricValue[]
}

export interface ProfileSection {
  key: string
  title: string
  metrics: ProfileMetric[]
}

/** 作者写作画像：现有 ANALYSIS V2 的确定性聚合，不含 AI 生成总结 */
export interface AuthorWritingProfile {
  author_id: number
  total_content_count: number
  analyzed_sample_count: number
  coverage: number
  sections: ProfileSection[]
}

// ------------------------------------------------------------------
// 阶段 8：单账号流量画像（确定性统计，0 AI）
// ------------------------------------------------------------------

/** 四象限标签：只描述「常用 × 表现」，不代表建议 */
export type PerfQuadrant =
  | "core"
  | "opportunity"
  | "high_freq_low"
  | "low_priority"
  | "neutral"

export interface PerformanceValue {
  value: string
  sample_count: number
  median_performance_score: number
  median_view_count: number
  top10_count: number
  top10_rate: number
  bottom40_count: number
  bottom40_rate: number
  /** 使用率：回答「常用」，与「有效」是两件事 */
  usage_rate: number
  low_sample: boolean
  quadrant: PerfQuadrant | null
  left_value?: string | null
  right_value?: string | null
}

export interface PerformanceDimension {
  key: string
  label: string
  field: string
  kind: "categorical" | "list"
  /** 该字段在这批样本中真正取到值的 content 数 = usage_rate 的分母 */
  present_count: number
  values: PerformanceValue[]
  high: PerformanceValue[]
  low: PerformanceValue[]
}

export interface PerformanceWritingSection {
  key: string
  title: string
  metrics: PerformanceDimension[]
}

export interface PerformanceCombo {
  key: string
  label: string
  left: { field: string; label: string }
  right: { field: string; label: string }
  /** 两侧都取到值的样本数 = 该组合 usage_rate 的分母 */
  present_count: number
  high: PerformanceValue[]
  low: PerformanceValue[]
}

export interface PerformanceSampleSummary {
  total_content_count: number
  performance_count: number
  eligible_count: number
  analyzed_eligible_count: number
  min_age_hours: number
  excluded: {
    no_performance?: number
    no_view_count?: number
    pinned?: number
    too_fresh?: number
    invalid_time?: number
  }
}

/** 账号内部曝光基线。中位优先，平均值只作参考。 */
export interface PerformanceBaseline {
  median_views: number | null
  average_views: number | null
  top10_view_threshold: number | null
  bottom40_view_threshold: number | null
  min_views: number | null
  max_views: number | null
}

/** 单个表现指标的账号内基线（曝光绝对值 / 次级比率的比率值）。 */
export interface PerformanceMetricBaseline {
  metric: string
  label: string
  kind: "absolute" | "rate"
  sample_count: number
  median_value: number | null
  min_value: number | null
  max_value: number | null
  top10_threshold: number | null
  bottom40_threshold: number | null
  /** 该指标无法计算（浏览量为 0 或计数未知）的帖子数 */
  unknown_count: number
}

/** 某个书写目标下的案例帖（只带客观指标与结构化写法特征）。 */
export interface PerformanceGoalSample {
  content_id: number
  source: string | null
  excerpt: string
  posted_at: string | null
  view_count: number | null
  metric: string
  metric_value: number | null
  metric_score: number | null
  performance_score: number | null
  band: string | null
  features: Record<string, string | string[]>
}

export interface PerformanceGoal extends PerformanceMetricBaseline {
  goal: string
  insufficient: boolean
  high: PerformanceGoalSample[]
  low: PerformanceGoalSample[]
}

export interface AuthorPerformanceProfile {
  author_id: number
  sample_summary: PerformanceSampleSummary
  baseline: PerformanceBaseline
  content_dimensions: PerformanceDimension[]
  writing_sections: PerformanceWritingSection[]
  combinations: PerformanceCombo[]
  thresholds: {
    min_age_hours?: number
    min_ranking_sample?: number
    low_sample_threshold?: number
    top10_score?: number
    bottom40_score?: number
  }
  /** D9：五个次级互动指标的账号内基线 */
  metric_baselines: PerformanceMetricBaseline[]
  /** D9：按书写目标（曝光 / 讨论 / 传播 / 收藏）分组的高/低案例 */
  goal_samples: Record<string, PerformanceGoal>
  goals: Record<string, { label: string; metric: string }>
  default_goal: string
}

export interface PerformanceEvidenceItem {
  content_id: number
  source: string | null
  excerpt: string
  posted_at: string | null
  view_count: number
  performance_score: number
  band: string
  band_label: string
  matched_value: string
}

// ------------------------------------------------------------------
// 阶段 9：跨账号流量画像（作者等权，0 AI）
// ------------------------------------------------------------------

export interface CrossAccountAuthorObservation {
  author_id: number
  username: string | null
  display_name: string | null
  sample_count: number
  median_performance_score: number
  median_view_count: number | null
  top10_count: number
  top10_rate: number
  bottom40_count: number
  bottom40_rate: number
  direction: "positive" | "neutral" | "negative"
  /** Stage 8 evidence 必须用原始取值查询，不能用 normalized identity */
  evidence_values: string[]
}

export interface CrossAccountValue {
  /** identity（已规范化），不用于展示 */
  value: string
  /** 一个真实存在的原始拼写，用于展示 */
  display_value: string
  author_count: number
  /** 仅供展示，**不做权重** */
  total_sample_count: number
  median_author_score: number
  positive_author_count: number
  neutral_author_count: number
  negative_author_count: number
  positive_rate: number
  negative_rate: number
  min_author_score: number
  max_author_score: number
  spread: number
  /** 三个 flag 互相独立，可重叠 */
  is_common_high: boolean
  is_common_low: boolean
  is_account_dependent: boolean
  is_mixed: boolean
  authors: CrossAccountAuthorObservation[]
}

export interface CrossAccountDimension {
  key: string
  label: string
  field: string
  kind: "categorical" | "list"
  value_count: number
  common_high: CrossAccountValue[]
  common_low: CrossAccountValue[]
  account_dependent: CrossAccountValue[]
  all_values: CrossAccountValue[]
}

/** 一个作者在某个互动指标上的观测值。 */
export interface CrossAccountMetricObservation {
  author_id: number
  username: string | null
  display_name: string | null
  sample_count: number
  median_value: number | null
}

/** 跨账号互动指标比较（作者等权：先作者内中位，再对作者取中位）。 */
export interface CrossAccountEngagementDimension {
  metric: string
  label: string
  kind: "absolute" | "rate"
  author_count: number
  insufficient: boolean
  cross_author_median: number | null
  author_observations: CrossAccountMetricObservation[]
  insufficient_authors: CrossAccountMetricObservation[]
}

export interface CrossAccountInsights {
  author_summary: {
    author_count: number
    eligible_author_count: number
    analyzed_author_count: number
  }
  authors: {
    author_id: number
    username: string | null
    display_name: string | null
    eligible_count: number
    analyzed_eligible_count: number
    median_views: number | null
    metric_medians: Record<string, number | null>
  }[]
  content_dimensions: CrossAccountDimension[]
  writing_dimensions: CrossAccountDimension[]
  /** D9：次级互动指标的跨账号比较 */
  engagement_dimensions: CrossAccountEngagementDimension[]
  thresholds: {
    min_author_sample?: number
    common_pattern_min_authors?: number
    account_dependent_min_authors?: number
    positive_score?: number
    negative_score?: number
  }
  method: Record<string, unknown>
}

export interface Facets {
  tags: { name: string; count: number }[]
  topics: { name: string; count: number }[]
  content_types: { name: string; count: number }[]
  authors: { name: string; count: number }[]
}

export interface ImportBatch {
  import_batch_id: string
  filename: string | null
  source: string
  author_username: string | null
  created_at: string
  input_count: number
  inserted_count: number
  duplicate_count: number
  skipped_count: number
  remaining: number
  analyzed: number
  unanalyzed: number
  breakdown: Record<string, number>
  is_running: boolean
  run_kind: string | null
  run_status: string | null
}

export interface ContentItem {
  id: number
  source: Source
  provenance: Provenance | null
  /** 素材作者（客观 metadata：这条内容原本是谁发的）。与「参考作者」完全独立。 */
  author_id: number | null
  /**
   * 分析流程的**唯一控制状态** —— 展示必须读它。
   * legacy status 的 pending 无法区分「未分析」与「排队中」（Stage 12.1 P0）。
   */
  analysis_state: AnalysisState | null
  import_batch_id: string | null
  external_id: string | null
  source_url: string | null
  author_username: string | null
  posted_at: string | null
  post_type: string | null
  /** 是否允许作为个人风格学习样本。与 source 完全独立。 */
  style_eligible: boolean
  summary: string | null
  excerpt: string
  status: Status
  analysis_error: string | null
  created_at: string
  updated_at: string
  tags: string[]
  topic: string | null
  content_type: string | null
  length_chars: number | null
}

/**
 * 帖子表现数据（Performance V1）：Xporter 导入时附带的公开计数。
 * null = 未知（缺失 / 无法解析 / 负数）；0 = 已知为 0。两者严格区分。
 */
export interface ContentPerformance {
  view_count: number | null
  favorite_count: number | null
  reply_count: number | null
  retweet_count: number | null
  quote_count: number | null
  bookmark_count: number | null
  is_pinned: boolean
  media_type: string | null
  captured_at: string
}

export interface ContentDetail extends ContentItem {
  raw_text: string
  analysis: Analysis | null
  similar: SimilarItem[]
  performance: ContentPerformance | null
  /** Xporter 扩展来源字段；没有时为空对象（不是 null） */
  source_meta: SourceMeta
}

export interface ContentList {
  items: ContentItem[]
  total: number
  limit: number
  offset: number
}

export interface Rewrite {
  id: number
  benchmark_content_id: number | null
  ai_draft: string
  final_text: string | null
  is_saved_as_mine: boolean
  saved_content_id: number | null
  examples_used: SimilarItem[]
  style_profile_version: number | null
  edit_distance: number | null
  edit_ratio: number | null
  prompt_version: string | null
  personalization_state: string | null
  model: string | null
  created_at: string
  updated_at: string
  personalization?: PersonalizationState | null
}

// ---------------------------------------------------------------- 阶段 10

export type MaterialPoolKey = "all" | "blank" | "author"

export interface MaterialPools {
  all: number
  blank: number
  authors: {
    author_id: number
    username: string | null
    display_name: string | null
    count: number
  }[]
}

/** 随机素材卡：只给「这条值不值得改」所需的最少信息。 */
export interface Material {
  content_id: number
  raw_text: string
  author_id: number | null
  author_username: string | null
  author_display_name: string | null
  posted_at: string | null
  view_count: number | null
  media_type: string | null
  fallback_used: boolean
}

export interface RewriteCandidate {
  key: string
  label: string
  text: string
  /** 由 Rewrite Context Selector 生成，不是模型自报 */
  strategy_tags: string[]
}

export interface RewriteCandidatesResult {
  source_content_id: number | null
  reference_author_id: number | null
  candidates: RewriteCandidate[]
  context: {
    source_analysis: Record<string, unknown> | null
    writing_count: number
    performance_high: Record<string, unknown>[]
    performance_low: Record<string, unknown>[]
    cross_high: Record<string, unknown>[]
    cross_low: Record<string, unknown>[]
    account_dependent: Record<string, unknown>[]
    skipped: string[]
    budget: Record<string, number> | null
  }
  context_empty: boolean
  prompt_version: string | null
  model: string | null
  personalization?: PersonalizationState | null
}

export interface AdoptCandidateResult {
  rewrite_id: number
  ai_draft: string
  benchmark_content_id: number | null
}

export interface StyleProfile {
  id: number
  version: number
  based_on_count: number
  profile_json: Record<string, unknown>
  summary_text: string | null
  created_at: string
}

export interface StyleStatus {
  has_profile: boolean
  mine_count: number
  based_on_count: number
  new_since: number
  should_recompute: boolean
  min_required: number
  recommended: number
}

export interface AppConfig {
  ai_configured: boolean
  ai_provider: string
  ai_base_url: string
  models: { analysis: string; rewrite: string; style: string; batch?: string }
  queue_size: number
  batch_available: boolean
  batch_base_url: string | null
}

export interface ImportParseResult {
  filename: string
  count: number
  items: string[]
  metadatas?: Record<string, unknown>[]
  preview: string[]
  suggest_batch: boolean
  batch_available: boolean
  batch_threshold: number
}

// ---------------------------------------------------------------- 多文件导入

/** 多文件解析里，单个文件的结果（含只读重复预检的预计值）。 */
export interface ImportBatchFile {
  filename: string
  ok: boolean
  error: string | null
  count: number
  items: string[]
  metadatas: Record<string, unknown>[]
  /** 该文件出现过的作者（已规范化、去重、排序） */
  authors: string[]
  /** 缺作者标识的条数 —— 必须让用户看见，不得静默归属 */
  missing_author_count: number
  /** 预计重复：库里已有 + 本批次前面文件已占用 */
  duplicate_count: number
  /** 预计新增 */
  new_count: number
}

export interface ImportBatchTotals {
  files: number
  ok_files: number
  failed_files: number
  count: number
  duplicate_count: number
  new_count: number
  missing_author_count: number
}

export interface ImportBatchParseResult {
  files: ImportBatchFile[]
  totals: ImportBatchTotals
  batch_available: boolean
  batch_threshold: number
}

/** 多文件提交后，单个文件的**实际**入库结果。 */
export interface ImportFileResult {
  filename: string
  ok: boolean
  created: number
  duplicates: number
  skipped: number
  error: string | null
  import_batch_id: string | null
}


// ---------------------------------------------------------------- 阶段 12：外部分析

export interface ExternalPreviewItem {
  line: number
  content_id: number | null
  classification:
    | "valid"
    | "invalid_json"
    | "schema_error"
    | "content_not_found"
    | "content_hash_mismatch"
    | "already_analyzed"
    | "busy"
    | "duplicate_result"
  field_path: string | null
  message: string
}

/** 导出范围预检（V2 任务 02：按作者、不限量）。 */
export interface ExternalTaskPreview {
  /** 本次将导出的条数 —— 与下载得到的条数**恒等** */
  count: number
  limit: number
  states: string[]
  state_counts: Record<string, number>
  author_ids: number[]
  include_no_author: boolean
  scope: string | null
  matching_count: number
  author_count: number
  no_author_count: number
  estimated_bytes: number
}

export interface ExternalPreview {
  meta: Record<string, unknown>
  warnings: string[]
  counts: Record<string, number>
  total: number
  valid_count: number
  items: ExternalPreviewItem[]
  overwrite: boolean
  /** 文件级错误时非空 → 整份文件不允许 Confirm */
  file_error: string | null
  /** 无状态一致性指纹，Confirm 原样带回 */
  confirm_token: string | null
}

export interface ExternalConfirm {
  imported: number
  skipped: number
  model: string | null
  overwrite: boolean
  counts: Record<string, number>
  warnings: string[]
  reason: string | null
}


export interface ExternalHandoff {
  transferred: number[]
  not_queued: number[]
  processing: number[]
  not_found: number[]
  transferred_count: number
}

// ================================================================
// 「我的」V1：目标 / 合格浏览量快照 / 账号日指标
// ================================================================
// 全部 0 AI。只服务 /mine。

/** 目标状态：达标优先于过期。 */
export type GoalStatus = "active" | "achieved" | "expired"

export interface GoalChartPoint {
  day: number
  date: string
  value: number
  id?: number | null
}

export interface GoalChart {
  /** 90 天线性基准线（91 个点）。无插值、无预测。 */
  line: GoalChartPoint[]
  /** 用户真实录入的快照点。 */
  snapshots: GoalChartPoint[]
}

export interface CreatorGoal {
  id: number
  start_date: string
  baseline_qualified_impressions: number
  created_at: string
}

export interface GoalState {
  exists: boolean
  goal: CreatorGoal | null
  target: number
  duration_days: number
  baseline: number | null
  current: number | null
  gained: number | null
  remaining: number | null
  /** 0 ~ 1。目标为绝对值口径：current / target。 */
  progress: number | null
  status: GoalStatus | null
  elapsed_days: number | null
  remaining_days: number | null
  /** 剩余天数为 0（今天截止）时为 null —— 不制造没有意义的数字。 */
  current_daily_required: number | null
  /** 建目标时按 90 天匀速摊派的日均要求；baseline >= target 时为 0。 */
  original_daily_required: number | null
  /** 目标线在今天这一点上的计划值；baseline >= target 时恒为 target。 */
  planned_today: number | null
  /** 领先/落后于计划线：current - planned_today。 */
  ahead_behind: number | null
  snapshots: QualifiedSnapshot[]
  chart: GoalChart
}

export interface QualifiedSnapshot {
  id: number
  recorded_at: string
  qualified_impressions: number
  created_at: string
  updated_at: string
}

export interface AccountMetricsImportResult {
  input_rows: number
  inserted: number
  updated: number
  date_min: string | null
  date_max: string | null
}

export interface AccountMetricPoint {
  metric_date: string
  value: number
}

export interface AccountMetric {
  metric: string
  items: AccountMetricPoint[]
  latest: AccountMetricPoint | null
  previous: AccountMetricPoint | null
  difference: number | null
}

/** Account Overview CSV 的 13 个指标（与后端白名单一致）。 */
export const MINE_METRICS = [
  { key: "impressions", label: "Impressions" },
  { key: "likes", label: "Likes" },
  { key: "engagements", label: "Engagements" },
  { key: "bookmarks", label: "Bookmarks" },
  { key: "shares", label: "Shares" },
  { key: "new_follows", label: "New follows" },
  { key: "unfollows", label: "Unfollows" },
  { key: "replies", label: "Replies" },
  { key: "reposts", label: "Reposts" },
  { key: "profile_visits", label: "Profile visits" },
  { key: "create_post", label: "Create Post" },
  { key: "video_views", label: "Video views" },
  { key: "media_views", label: "Media views" },
] as const

export type MineMetricKey = (typeof MINE_METRICS)[number]["key"]
