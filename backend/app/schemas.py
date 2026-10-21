"""数据模型：Pydantic 请求/响应模型。

与数据库行结构解耦，避免上层直接依赖 sqlite3.Row。
"""

from __future__ import annotations

import warnings
from typing import Any, Literal

from pydantic import BaseModel, Field

# ANALYSIS 合同的 style 模块有一个字段就叫 `register`（语体）。
# Pydantic 会为「与元类属性同名」发出 UserWarning —— 那是误报：
# 字段名必须与合同逐字一致，不能为了消警告改叫 register_type 之类。
warnings.filterwarnings(
    "ignore",
    message=r'Field name "register" .* shadows an attribute',
    category=UserWarning,
)

Source = Literal["mine", "benchmark"]
Status = Literal["pending", "analyzing", "analyzed", "failed"]
Provenance = Literal["human_original", "imported_self", "ai_assisted"]
ImportMode = Literal["auto", "realtime", "batch"]


# ---------------------------------------------------------------- 内容

class ContentCreate(BaseModel):
    raw_text: str = Field(min_length=1)
    source: Source
    # 仅 source='mine' 时有意义；不传则按来源推断（手动输入=human_original）
    provenance: Provenance | None = None
    analyze: bool = True  # 保存后是否立即进入分析队列

    # ---- 单条统一入口（阶段 4）----
    # 以下三个字段都只是把**已有列**透传给 repository，不新增任何表或列。

    # 显式指定是否允许参与风格学习。
    # None = 沿用既有默认规则（mine → True，benchmark → False）。
    # 例如「我的灵感」是本人所写但不成文，必须显式传 False。
    style_eligible: bool | None = None
    # 对标内容的作者（AuthorSelector 选出的正式作者）。
    # 传 id 时 repository 会同时维护 author_username，绝不单字段写入。
    author_id: int | None = None
    # 对标内容的原链接（复用已有 source_url 列，不新增字段）。
    source_url: str | None = None


class ContentUpdate(BaseModel):
    raw_text: str | None = None
    summary: str | None = None


class AnalysisClaim(BaseModel):
    """一条主张：文中说了什么 + 归类 + 逐字原文依据。"""

    kind: str
    text: str
    evidence: str


class AnalysisMove(BaseModel):
    """一个真实存在的修辞 / 互动动作。"""

    type: str
    evidence: str
    mechanism: str


class AnalysisMarker(BaseModel):
    """一个可观察的形式标记（短句 / 换行 / 标点强调 …）。"""

    type: str
    evidence: str


class AnalysisPattern(BaseModel):
    """一条可迁移的写法，以及换题后怎么用。"""

    method: str
    evidence: str
    how_to_apply: str


class AnalysisContentBlock(BaseModel):
    """内容层：这篇实际说了什么、改写必须保住什么、看不到什么。"""

    claims: list[AnalysisClaim] = Field(default_factory=list)
    must_preserve: list[str] = Field(default_factory=list)
    context_limits: list[str] = Field(default_factory=list)


class AnalysisWritingBlock(BaseModel):
    """写法层：开头 / 结构 / 结尾 / 动作。"""

    opening_type: str | None = None
    structure_type: str | None = None
    ending_type: str | None = None
    moves: list[AnalysisMove] = Field(default_factory=list)


class AnalysisStyleBlock(BaseModel):
    """语言层：**只描述这一篇**，不是作者的长期风格。"""

    tone: str | None = None
    register: str | None = None
    directness: str | None = None
    certainty: str | None = None
    rhythm: str | None = None
    markers: list[AnalysisMarker] = Field(default_factory=list)
    distinctive_phrases: list[str] = Field(default_factory=list)


class AnalysisReuseBlock(BaseModel):
    """复用层：可迁移的方法与不宜迁移的边界。"""

    patterns: list[AnalysisPattern] = Field(default_factory=list)
    boundaries: list[str] = Field(default_factory=list)


class AnalysisOut(BaseModel):
    """一份完整的 ANALYSIS 结果。

    **必须声明全部字段**：这些模型是 API 的唯一出口，
    没声明的字段会被 Pydantic 静静丢掉 —— 那正是「分析成功但下游读不到数据」
    最常见的一种成因（旧版 AnalysisOut 只声明 13 个扁平字段，
    content_invariants 这类嵌套结构在响应里根本不存在）。
    """

    # ---- 基础字段 ----
    topic: str | None = None
    subtopic: str | None = None
    content_type: str | None = None
    core_message: str | None = None
    tags: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)

    # ---- 四个模块 ----
    content: AnalysisContentBlock | None = None
    writing: AnalysisWritingBlock | None = None
    style: AnalysisStyleBlock | None = None
    reuse: AnalysisReuseBlock | None = None

    # ---- 本地计算 / 版本指纹 ----
    length_chars: int | None = None
    model: str | None = None
    prompt_version: str | None = None
    schema_version: str | None = None
    schema_hash: str | None = None
    created_at: str | None = None


class ContentOut(BaseModel):
    """列表展示用的轻量结构。

    **每一个 repository._row_to_content 会产出的键都必须在这里声明** ——
    Pydantic 会静默丢弃未声明的键，UI 就会一直读到 undefined。
    本类曾漏掉 author_username / style_eligible，导致内容详情页
    永远显示「无作者 / 未参与风格学习」。回归测试
    TestTask04AResponseCompleteness 会用递归比对守住这条线。
    """

    id: int
    source: Source
    provenance: Provenance | None = None
    # 阶段 10：素材作者（客观 metadata，content.author_id）。
    # 与「参考作者」完全独立 —— 它只回答「这篇原本是谁发的」。
    author_id: int | None = None
    # 兼容/便捷列：与 author_id 由 repo 层同步维护
    author_username: str | None = None
    summary: str | None = None
    excerpt: str
    # status 是兼容用的旧字段（pending 既可能是未分析也可能是排队中，无法区分）。
    # analysis_state 才是分析流程的唯一控制状态 —— 前端展示必须读这个，
    # 否则会出现「内容库显示排队中，但外部分析按 queued 筛不到」的错位。
    status: Status
    analysis_state: str | None = None
    analysis_error: str | None = None
    created_at: str
    updated_at: str
    tags: list[str] = Field(default_factory=list)
    topic: str | None = None
    content_type: str | None = None
    length_chars: int | None = None
    # ---- 来源元数据（治理维度）----
    external_id: str | None = None
    source_url: str | None = None
    posted_at: str | None = None
    post_type: str | None = None
    import_batch_id: str | None = None
    # ---- 风格学习资格 ----
    style_eligible: bool = False


class ContentPerformanceOut(BaseModel):
    """帖子表现数据（Performance V1）：Xporter 导入时附带的公开计数。

    NULL = 未知（缺失 / 无法解析 / 负数），0 = 已知为 0。两者严格区分。
    captured_at 是工作台拿到这批数据的时间代理，不是帖子发布时间。
    """

    view_count: int | None = None
    favorite_count: int | None = None
    reply_count: int | None = None
    retweet_count: int | None = None
    quote_count: int | None = None
    bookmark_count: int | None = None
    is_pinned: bool = False
    media_type: str | None = None
    captured_at: str


class ContentDetail(ContentOut):
    raw_text: str
    analysis: AnalysisOut | None = None
    similar: list["SimilarItem"] = Field(default_factory=list)
    performance: ContentPerformanceOut | None = None
    #: Xporter 扩展来源字段：conversation_id / language / reply_to_username /
    #: hashtags / urls / media_urls。没有则空 dict（不返回 None，前端少一层判空）。
    source_meta: dict[str, Any] = Field(default_factory=dict)


class SimilarItem(BaseModel):
    id: int
    source: Source
    excerpt: str
    topic: str | None = None
    content_type: str | None = None
    score: float
    shared_tags: list[str] = Field(default_factory=list)
    provenance: Provenance | None = None
    edit_ratio: float | None = None


class ContentListOut(BaseModel):
    items: list[ContentOut]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------- 筛选

class FacetsOut(BaseModel):
    tags: list[dict[str, Any]] = Field(default_factory=list)
    topics: list[dict[str, Any]] = Field(default_factory=list)
    content_types: list[dict[str, Any]] = Field(default_factory=list)
    authors: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------- 导入

class ImportItem(BaseModel):
    text: str
    source: Source | None = None
    # 来自 JSON（如 Xporter）的来源元数据；不含正文，仅用于溯源
    metadata: dict[str, Any] = Field(default_factory=dict)  # 单条可覆盖默认来源


class ImportPreviewIn(BaseModel):
    source: Source


class ImportCommitIn(BaseModel):
    source: Source
    items: list[ImportItem]
    filename: str | None = None
    # 兼容字段：导入不再自动分析，mode 仅保留以兼容旧调用
    mode: ImportMode = "auto"


class ImportResult(BaseModel):
    created: int
    content_ids: list[int]
    skipped: int = 0
    # 去重统计：让用户明白"为什么少了 N 条"
    duplicates: int = 0
    #: 重复项里「表现数据被按采集时间刷新」的条数（内容未重复导入）
    performance_refreshed: int = 0
    input_count: int = 0
    # 导入本身不分析：mode 恒为 "none"
    mode: str = "none"
    import_batch_id: str | None = None
    batch_job_id: int | None = None
    batch_id: str | None = None


class BulkEditIn(BaseModel):
    """批量修改 source / author / style_eligible。三者互相独立。

    作者修改有三态，必须严格区分（不能把 author_id=null 当成「未传」）：

    1. 不修改作者  —— author_id / author_username / clear_author 全为默认
    2. 设置正式作者 —— 传 author_id（前端 AuthorSelector 选择），
       或传 author_username（旧调用，后端会 resolve 到正式作者）
    3. 清除作者    —— clear_author=True → author_id 与 author_username 同时置 NULL
    """

    ids: list[int] = Field(default_factory=list)
    source: Source | None = None
    author_id: int | None = None
    author_username: str | None = None
    clear_author: bool = False
    style_eligible: bool | None = None


class StyleEligibleIn(BaseModel):
    eligible: bool


class BatchDeleteRequest(BaseModel):
    """删除导入批次的方式。

    keep_analyzed：保留已成功分析的内容，只删除未完成的
    all：删除该批次当前仍存在的全部内容
    """

    mode: Literal["keep_analyzed", "all"] = "keep_analyzed"


class AuthorCreate(BaseModel):
    """手动新增作者。没有任何帖子也可以创建。"""

    username: str = Field(min_length=1)      # X 用户名（必填）
    display_name: str | None = None          # 显示名称（可选）
    notes: str | None = None                 # 备注（可选）


class AuthorUpdate(BaseModel):
    username: str | None = None
    display_name: str | None = None
    notes: str | None = None


class AuthorOut(BaseModel):
    id: int
    platform: str = "x"
    username: str
    display_name: str | None = None
    notes: str | None = None
    content_count: int = 0
    analyzed_count: int = 0
    created_at: str
    updated_at: str


class AuthorOverview(BaseModel):
    """作者概览：只描述「这个作者通常写什么」。

    系统没有 impressions / likes / reposts 等真实表现数据，
    因此这里**不产出**任何「什么最容易爆 / 最佳写法 / 高转化」类结论。
    """

    author_id: int
    content_count: int = 0
    analyzed_count: int = 0
    unanalyzed_count: int = 0
    # source 与 author 相互独立：同一作者名下可以同时有 mine 与 benchmark
    mine_count: int = 0
    benchmark_count: int = 0
    earliest_content_at: str | None = None
    latest_content_at: str | None = None
    avg_chars: int | None = None
    median_chars: int | None = None
    topics: list[dict[str, Any]] = Field(default_factory=list)
    content_types: list[dict[str, Any]] = Field(default_factory=list)


class ProfileEvidenceItem(BaseModel):
    """画像证据帖的轻量信息（不含几百篇完整 raw_text）。"""

    content_id: int
    source: str | None = None
    created_at: str | None = None
    excerpt: str = ""
    value: str


# ---------------------------------------------------------------- 流量画像（阶段 8）
#
# 全部是账号**内部**历史统计：
#   - performance_score = 该帖曝光在此作者历史有效样本中的百分位（0–100）
#   - 不是爆款概率、不是质量评级、不跨作者、不含因果结论

class PerformanceValue(BaseModel):
    """某个取值（主题 / 写法 / 组合）的历史表现统计。"""

    value: str
    sample_count: int
    median_performance_score: float
    median_view_count: float
    top10_count: int = 0
    top10_rate: float = 0.0
    bottom40_count: int = 0
    bottom40_rate: float = 0.0
    # 使用率：回答「常用」，与「有效」是两件事，必须分开看
    usage_rate: float = 0.0
    low_sample: bool = False        # 5 <= N < 10：可显示但样本较少
    quadrant: str | None = None    # core / opportunity / high_freq_low / low_priority / neutral
    left_value: str | None = None  # 仅二元组合
    right_value: str | None = None


class PerformanceDimension(BaseModel):
    key: str
    label: str
    field: str
    kind: str = "categorical"
    # 该字段在这批样本中真正取到值的 content 数
    # = usage_rate 的分母。字段缺失不能解释成「作者没这么写」
    present_count: int = 0
    values: list[PerformanceValue] = Field(default_factory=list)
    high: list[PerformanceValue] = Field(default_factory=list)
    low: list[PerformanceValue] = Field(default_factory=list)


class PerformanceWritingSection(BaseModel):
    key: str
    title: str
    metrics: list[PerformanceDimension] = Field(default_factory=list)


class PerformanceCombo(BaseModel):
    key: str
    label: str
    left: dict[str, str] = Field(default_factory=dict)
    right: dict[str, str] = Field(default_factory=dict)
    # 两侧都取到值的样本数 = 该组合 usage_rate 的分母
    present_count: int = 0
    high: list[PerformanceValue] = Field(default_factory=list)
    low: list[PerformanceValue] = Field(default_factory=list)


class PerformanceSampleSummary(BaseModel):
    total_content_count: int = 0
    performance_count: int = 0
    eligible_count: int = 0
    analyzed_eligible_count: int = 0
    min_age_hours: int = 48
    excluded: dict[str, int] = Field(default_factory=dict)


class PerformanceBaseline(BaseModel):
    """账号内部曝光基线。中位优先，平均值只作参考。"""

    median_views: float | None = None
    average_views: float | None = None
    top10_view_threshold: int | None = None
    bottom40_view_threshold: int | None = None
    min_views: int | None = None
    max_views: int | None = None


class PerformanceMetricBaseline(BaseModel):
    """单个表现指标的账号内基线（曝光绝对值 / 次级比率的比率值）。"""

    metric: str
    label: str
    kind: Literal["absolute", "rate"] = "rate"
    sample_count: int = 0
    median_value: float | None = None
    min_value: float | None = None
    max_value: float | None = None
    top10_threshold: float | None = None
    bottom40_threshold: float | None = None
    #: 该指标无法计算（浏览量为 0 或计数未知）的帖子数
    unknown_count: int = 0


class PerformanceGoalSample(BaseModel):
    """某个书写目标下的案例帖（只带客观指标与结构化写法特征）。"""

    content_id: int
    source: str | None = None
    excerpt: str = ""
    posted_at: str | None = None
    view_count: int | None = None
    metric: str
    metric_value: float | None = None
    metric_score: float | None = None
    performance_score: float | None = None
    band: str | None = None
    features: dict[str, Any] = Field(default_factory=dict)


class PerformanceGoal(PerformanceMetricBaseline):
    """书写目标 → 指标基线 + 高/低案例。

    沿用 PerformanceMetricBaseline 的基线字段，另加案例列表。
    """

    goal: str
    insufficient: bool = False
    high: list[PerformanceGoalSample] = Field(default_factory=list)
    low: list[PerformanceGoalSample] = Field(default_factory=list)


class AuthorPerformanceProfile(BaseModel):
    author_id: int
    sample_summary: PerformanceSampleSummary
    baseline: PerformanceBaseline
    content_dimensions: list[PerformanceDimension] = Field(default_factory=list)
    writing_sections: list[PerformanceWritingSection] = Field(default_factory=list)
    combinations: list[PerformanceCombo] = Field(default_factory=list)
    thresholds: dict[str, Any] = Field(default_factory=dict)
    # ---- D9：五个次级互动指标真正进入画像 ----
    metric_baselines: list[PerformanceMetricBaseline] = Field(default_factory=list)
    goal_samples: dict[str, PerformanceGoal] = Field(default_factory=dict)
    goals: dict[str, Any] = Field(default_factory=dict)
    default_goal: str = "exposure"


class PerformanceEvidenceItem(BaseModel):
    """流量画像证据帖：精确匹配该取值 / 组合的真实帖子。"""

    content_id: int
    source: str | None = None
    excerpt: str = ""
    posted_at: str | None = None
    view_count: int
    performance_score: float
    band: str
    band_label: str
    matched_value: str


class ProfileMetricValue(BaseModel):
    value: str
    count: int = 0
    pct: float = 0.0
    evidence_total: int = 0
    evidence: list[ProfileEvidenceItem] = Field(default_factory=list)


class ProfileMetric(BaseModel):
    """单个写作维度的聚合结果。

    kind:
      categorical —— 取值互斥（如 opening_type=直接判断），百分比 = 占比
      list        —— 取值可多选（如 rhetorical.moves），百分比 = 出现率
    """

    field: str
    label: str
    kind: Literal["categorical", "list"]
    # 样本中真正给出了这个字段的帖子数（模型可能在某些帖子上没输出）
    present_count: int = 0
    distinct_value_count: int = 0
    other_values_count: int = 0
    values: list[ProfileMetricValue] = Field(default_factory=list)


class ProfileSection(BaseModel):
    key: str
    title: str
    metrics: list[ProfileMetric] = Field(default_factory=list)


class AuthorWritingProfile(BaseModel):
    """作者写作画像：现有 ANALYSIS 的确定性聚合，**0 AI**。

    只回答「这个作者怎么写」；「写了什么」属于阶段 5 的概览，这里不重复。

    注意：这里的每个字段都必须**显式声明** —— Pydantic 会把未声明的键静静丢掉，
    那正是「后端算出来了、前端永远看不到」这一类缺陷的成因。
    """

    author_id: int
    total_content_count: int = 0
    analyzed_sample_count: int = 0
    coverage: float = 0.0
    #: 因「明确标注为非中文」被排除在画像之外的条数（0 = 没有或全部合格）
    excluded_non_chinese: int = 0
    sections: list[ProfileSection] = Field(default_factory=list)


class AnalyzeRequest(BaseModel):
    """用户显式点击「分析」时才调用。"""

    mode: Literal["realtime", "batch"]


class BulkDeleteIn(BaseModel):
    ids: list[int] = Field(default_factory=list)


class AnalysisRunOut(BaseModel):
    id: int
    kind: str
    import_batch_id: str | None = None
    status: str
    total: int = 0
    done: int = 0
    failed: int = 0
    batch_job_id: int | None = None
    model: str | None = None
    error: str | None = None
    created_at: str
    updated_at: str
    # 该批次当前各分析状态的分布（便于前端显示进度）
    breakdown: dict[str, int] = Field(default_factory=dict)


class RunCancelResult(BaseModel):
    run_id: int
    status: str
    reverted: int = 0
    remote: bool = False
    batch_id: str | None = None
    already_finished: bool = False


# ---------------------------------------------------------------- 改写

#: 04-C：五种创作输入路径。
#:   rewrite        原帖改写
#:   rewrite_author 原帖 + 参考作者
#:   topic          仅主题创作
#:   topic_author   主题 + 作者
#:   author_auto    仅选择参考作者，由 AI 自主选题
#: 后端无法从文本本身区分「原帖」与「主题」，因此主题路径必须由调用方
#: 显式声明；未声明时按 rewrite 处理（与 04-C 之前的行为一致）。
CreationPath = Literal[
    "rewrite", "rewrite_author", "topic", "topic_author", "author_auto"
]


class RewriteIn(BaseModel):
    """生成初稿请求。

    素材可以是直接粘贴的 source_text，也可以引用一条已有内容；
    引用对标内容时传 source_content_id（或兼容字段 benchmark_content_id）。

    任务 04-C：
      - `author_auto` 下 source_text / source_content_id 都可以为空，
        由 AI 依据参考作者画像自主选题；
      - 未分析的素材（新粘贴、或尚未跑过 ANALYSIS）照常生成，
        不阻塞、不自动补分析。
    """

    source_text: str | None = None
    source_content_id: int | None = None
    benchmark_content_id: int | None = None   # 兼容旧调用
    instruction: str | None = None            # 附加指令，如「更短」「更直接」
    base_text: str | None = None              # 在已有初稿上迭代时传入
    examples_k: int = 5
    # 阶段 7：参考作者（仅对标模式）。**只传 id + 开关，绝不接收前端画像 JSON** ——
    # 画像内容一律由服务端按 author_id 从库里读正式聚合结果。
    reference_author_id: int | None = None
    # 04-C 验收修复 ②：**三态**。
    #   None（默认）→ 选了参考作者就用其可用写作画像，缺则跳过并如实记录；
    #   True        → 显式要求必须用，缺画像时明确报错；
    #   False       → 明确不用（仅记录来源）。
    # 产品要求：选了参考作者就默认启用，不需要额外操作。
    use_reference_profile: bool | None = None
    # 任务 04-A：当次书写目标。决定「历史表现参考」用哪个指标
    # （exposure 曝光 / discussion 讨论 / spread 传播 / saves 收藏）。
    # 默认曝光 —— 不传时行为与旧版完全一致。
    goal: Literal["exposure", "discussion", "spread", "saves"] | None = None
    # 任务 04-C：创作输入路径。不传时由后端按输入形态推导。
    creation_path: CreationPath | None = None


class RewriteOut(BaseModel):
    id: int
    benchmark_content_id: int | None = None
    ai_draft: str
    final_text: str | None = None
    is_saved_as_mine: bool = False
    saved_content_id: int | None = None
    examples_used: list[SimilarItem] = Field(default_factory=list)
    style_profile_version: int | None = None
    edit_distance: int | None = None
    edit_ratio: float | None = None
    prompt_version: str | None = None
    personalization_state: str | None = None
    model: str | None = None
    created_at: str
    updated_at: str
    personalization: dict[str, Any] | None = None
    #: 04-C：本次实际生效的创作输入路径
    creation_path: str | None = None
    #: 04-C 验收修复 ②：参考作者写作画像是否真的进入了本次 WRITE 上下文
    reference_profile_used: bool = False


class RewriteSaveIn(BaseModel):
    final_text: str
    save_as_mine: bool = True


# ---------------------------------------------------------------- 阶段 10：素材池 + 三候选

class MaterialPoolAuthor(BaseModel):
    author_id: int
    username: str | None = None
    display_name: str | None = None
    count: int = 0


class MaterialPoolsOut(BaseModel):
    """素材池计数。**只统计 source=benchmark 且正文非空**。

    blank = author_id IS NULL 的素材数量。它**不是**一个 Author，
    库里绝不存在「空白作者」这条作者记录。
    """

    all: int = 0
    blank: int = 0
    authors: list[MaterialPoolAuthor] = Field(default_factory=list)


class MaterialOut(BaseModel):
    """随机素材卡：轻量信息，只帮助用户快速判断「这条值不值得改」。

    刻意不返回 ANALYSIS / Writing Profile / Performance Profile。
    """

    content_id: int
    raw_text: str
    author_id: int | None = None
    author_username: str | None = None
    author_display_name: str | None = None
    posted_at: str | None = None
    view_count: int | None = None
    media_type: str | None = None
    # True = 该池的未改写素材已用完，本次允许了历史素材
    fallback_used: bool = False


class RewriteCandidateIn(BaseModel):
    """生成 3 个候选。

    素材可以是粘贴文本，也可以是已有 content（source_content_id）。
    reference_author_id = None 表示「不指定参考作者」——
    仍然会用跨账号共性 + 用户自身风格，只是不用某个作者的私有画像。

    任务 04-C：author_auto 下 source_text / source_content_id 都可为空。
    """

    source_text: str | None = None
    source_content_id: int | None = None
    instruction: str | None = None
    reference_author_id: int | None = None
    # 与单稿保持同一套三态语义：不传 = 选了作者就默认用其可用画像
    use_reference_profile: bool | None = None
    # 与单稿保持同一套书写目标
    goal: Literal["exposure", "discussion", "spread", "saves"] | None = None
    examples_k: int = 5
    # 任务 04-C：创作输入路径。不传时由后端按输入形态推导。
    creation_path: CreationPath | None = None


class RewriteCandidate(BaseModel):
    """一个候选稿。strategy_tags **由代码生成**，不是模型自报。"""

    key: str                      # A / B / C
    label: str                    # 稳妥版 / 强钩子版 / 重构版
    text: str
    strategy_tags: list[str] = Field(default_factory=list)


class RewriteCandidatesOut(BaseModel):
    source_content_id: int | None = None
    reference_author_id: int | None = None
    #: 04-C：本次实际生效的创作输入路径
    creation_path: str | None = None
    #: 04-C 验收修复 ②：参考作者写作画像是否真的进入了本次 WRITE 上下文
    reference_profile_used: bool = False
    candidates: list[RewriteCandidate] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    context_empty: bool = False
    prompt_version: str | None = None
    model: str | None = None
    personalization: dict[str, Any] | None = None


class AdoptCandidateIn(BaseModel):
    """采用某个候选：把候选正文建成一条正式 rewrite 记录（0 AI）。

    这样后续「编辑 → 保存为我的」完全复用既有 /rewrites/{id} 保存链路，
    初稿→终稿的编辑距离也照常计算。
    """

    text: str
    source_content_id: int | None = None
    instruction: str | None = None
    model: str | None = None


class AdoptCandidateOut(BaseModel):
    rewrite_id: int
    ai_draft: str
    benchmark_content_id: int | None = None


# ---------------------------------------------------------------- 风格档案

class StyleProfileOut(BaseModel):
    id: int
    version: int
    based_on_count: int
    profile_json: dict[str, Any]
    summary_text: str | None = None
    #: 产出本版档案的 STYLE 提示词版本（04-A D6 / 04-B v3）
    prompt_version: str | None = None
    created_at: str


ContentDetail.model_rebuild()


# ---------------------------------------------------------------------------
# 阶段 9：跨账号流量画像（0 AI，作者等权）
# ---------------------------------------------------------------------------


class CrossAccountAuthorObservation(BaseModel):
    """某个作者对某个 normalized 取值的一条 author-level observation。

    (author_id, dimension, normalized_value) 恒等于 1 条：作者等权，一个作者 = 一票。
    同一作者若存在多个被规范化合并的 raw 变体（GPT / gpt），
    统计量是对合并后**实际样本**的聚合，不是「中位分再取中位」。
    """

    author_id: int
    username: str | None = None
    display_name: str | None = None
    sample_count: int
    median_performance_score: float
    median_view_count: float | None = None
    top10_count: int = 0
    top10_rate: float = 0.0
    bottom40_count: int = 0
    bottom40_rate: float = 0.0
    direction: str                      # positive / neutral / negative
    # Stage 8 evidence 必须用**原始**取值查询，不能用 normalized identity
    evidence_values: list[str] = Field(default_factory=list)


class CrossAccountValue(BaseModel):
    """某个 normalized 取值跨账号的聚合结果。"""

    value: str                          # identity（已规范化），不用于展示
    display_value: str = ""             # 一个真实存在的原始拼写，用于展示
    author_count: int
    total_sample_count: int             # 仅供展示，**不做权重**
    median_author_score: float
    positive_author_count: int = 0
    neutral_author_count: int = 0
    negative_author_count: int = 0
    positive_rate: float = 0.0
    negative_rate: float = 0.0
    min_author_score: float = 0.0
    max_author_score: float = 0.0
    spread: float = 0.0
    # 三个 flag **互相独立**，可重叠：同一个 value 允许同时是
    # 跨账号高表现 + 账号依赖明显
    is_common_high: bool = False
    is_common_low: bool = False
    is_account_dependent: bool = False
    is_mixed: bool = True               # 三个 flag 全 false
    authors: list[CrossAccountAuthorObservation] = Field(default_factory=list)


class CrossAccountDimension(BaseModel):
    key: str
    label: str
    field: str
    kind: str = "categorical"
    value_count: int = 0
    common_high: list[CrossAccountValue] = Field(default_factory=list)
    common_low: list[CrossAccountValue] = Field(default_factory=list)
    account_dependent: list[CrossAccountValue] = Field(default_factory=list)
    all_values: list[CrossAccountValue] = Field(default_factory=list)


class CrossAccountAuthorSummary(BaseModel):
    author_count: int = 0
    eligible_author_count: int = 0
    analyzed_author_count: int = 0


class CrossAccountAuthorRow(BaseModel):
    author_id: int
    username: str | None = None
    display_name: str | None = None
    eligible_count: int = 0
    analyzed_eligible_count: int = 0
    median_views: float | None = None
    #: 该作者各指标的中位数（键 = 指标名，值可能为 None = 样本不足）
    metric_medians: dict[str, float | None] = Field(default_factory=dict)


class CrossAccountMetricObservation(BaseModel):
    """一个作者在某个指标上的观测值。"""

    author_id: int
    username: str | None = None
    display_name: str | None = None
    sample_count: int = 0
    median_value: float | None = None


class CrossAccountEngagementDimension(BaseModel):
    """跨账号互动指标比较（作者等权：先作者内中位，再对作者取中位）。"""

    metric: str
    label: str
    kind: Literal["absolute", "rate"] = "rate"
    author_count: int = 0
    insufficient: bool = False
    cross_author_median: float | None = None
    author_observations: list[CrossAccountMetricObservation] = Field(
        default_factory=list
    )
    insufficient_authors: list[CrossAccountMetricObservation] = Field(
        default_factory=list
    )


class CrossAccountInsights(BaseModel):
    author_summary: CrossAccountAuthorSummary
    authors: list[CrossAccountAuthorRow] = Field(default_factory=list)
    content_dimensions: list[CrossAccountDimension] = Field(default_factory=list)
    writing_dimensions: list[CrossAccountDimension] = Field(default_factory=list)
    #: D9：次级互动指标的跨账号比较
    engagement_dimensions: list[CrossAccountEngagementDimension] = Field(
        default_factory=list
    )
    thresholds: dict[str, Any] = Field(default_factory=dict)
    method: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------- 阶段 12：外部分析

class ExternalTaskPreviewOut(BaseModel):
    """导出前的「待导出 N 条」预检（0 AI、0 写入）。"""

    count: int
    limit: int
    states: list[str] = Field(default_factory=list)
    source: str | None = None
    author_id: int | None = None
    # 各 analysis_state 的计数（一次 GROUP BY）
    state_counts: dict[str, int] = Field(default_factory=dict)

    # ---- V2 任务 02：按作者不限量导出（全部新增，旧字段语义不变）----
    author_ids: list[int] = Field(
        default_factory=list, description="指定的作者 id；空 = 全部作者"
    )
    include_no_author: bool = False
    scope: str | None = None
    # 不带任何上限的真实条数 —— 预览显示多少条，下载就得到多少条
    matching_count: int = 0
    author_count: int = 0
    no_author_count: int = 0
    estimated_bytes: int = 0


class ExternalResultItem(BaseModel):
    """单条结果的分类结论。"""

    line: int
    content_id: int | None = None
    classification: str
    field_path: str | None = None
    message: str = ""


class ExternalPreviewOut(BaseModel):
    """结果文件预检。**0 DB 写入 / 0 AI**。"""

    meta: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    total: int = 0
    valid_count: int = 0
    items: list[ExternalResultItem] = Field(default_factory=list)
    overwrite: bool = False
    # 文件级错误时才非空；有值 → 整份文件不允许 Confirm
    file_error: str | None = None
    # 无状态一致性指纹：Confirm 时必须原样带回
    confirm_token: str | None = None


class ExternalConfirmOut(BaseModel):
    imported: int = 0
    skipped: int = 0
    model: str | None = None
    overwrite: bool = False
    counts: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    reason: str | None = None


# ---------------------------------------------------------------- 阶段 12.1：队列交接

class ExternalHandoffIn(BaseModel):
    """把**仅处于 queued** 的内容交接给外部分析。"""

    content_ids: list[int] = Field(min_length=1)


class ExternalHandoffOut(BaseModel):
    transferred: list[int] = Field(default_factory=list)
    not_queued: list[int] = Field(default_factory=list)
    processing: list[int] = Field(default_factory=list)
    not_found: list[int] = Field(default_factory=list)
    transferred_count: int = 0


class ExternalHandoffCandidatesOut(BaseModel):
    """当前筛选下待转出的 queued 内容。"""

    count: int = 0
    ids: list[int] = Field(default_factory=list)


# ================================================================
# 「我的」V1：目标 / 合格浏览量快照 / 账号日指标
# ================================================================
# 全部 0 AI。只服务 /mine，不与内容/分析/风格体系共用模型。

class GoalCreateIn(BaseModel):
    start_date: str = Field(min_length=10, max_length=10)
    baseline_qualified_impressions: int = Field(ge=0)


class GoalUpdateIn(BaseModel):
    """就地修改目标的两个**计划参数**。

    两个字段都是必填 —— 这是「修正计划」，不是「局部打补丁」，
    提交时应当带上你想要的完整值。
    TARGET / DURATION 不在这里：它们是固定规则，接口层面就无从修改。
    """

    start_date: str = Field(min_length=10, max_length=10)
    baseline_qualified_impressions: int = Field(ge=0)


class GoalChartPoint(BaseModel):
    day: int
    date: str
    value: int
    id: int | None = None


class GoalChartOut(BaseModel):
    """90 天线性基准线 + 真实快照点。**无插值、无预测**。"""

    line: list[GoalChartPoint] = Field(default_factory=list)
    snapshots: list[GoalChartPoint] = Field(default_factory=list)


class GoalOut(BaseModel):
    """目标 + 现算进度。未建目标时 exists=False，其余字段为 None。"""

    exists: bool = False
    goal: dict[str, Any] | None = None
    target: int
    duration_days: int
    baseline: int | None = None
    current: int | None = None
    gained: int | None = None
    remaining: int | None = None
    progress: float | None = None
    status: str | None = None
    elapsed_days: int | None = None
    remaining_days: int | None = None
    current_daily_required: int | None = None
    # 建目标时按 90 天匀速摊派的日均要求；baseline >= target 时为 0。
    original_daily_required: int | None = None
    # 目标线在今天这一点上的计划值；baseline >= target 时恒为 target。
    planned_today: int | None = None
    # 领先/落后于计划线：current - planned_today。
    ahead_behind: int | None = None
    snapshots: list[dict[str, Any]] = Field(default_factory=list)
    chart: GoalChartOut = Field(default_factory=GoalChartOut)


class SnapshotCreateIn(BaseModel):
    recorded_at: str = Field(min_length=10, max_length=10)
    qualified_impressions: int = Field(ge=0)


class SnapshotUpdateIn(BaseModel):
    """只更新显式传入的字段。"""

    recorded_at: str | None = Field(default=None, min_length=10, max_length=10)
    qualified_impressions: int | None = Field(default=None, ge=0)


class SnapshotOut(BaseModel):
    id: int
    recorded_at: str
    qualified_impressions: int
    created_at: str
    updated_at: str


class AccountMetricsImportOut(BaseModel):
    input_rows: int = 0
    inserted: int = 0
    updated: int = 0
    date_min: str | None = None
    date_max: str | None = None


class AccountMetricPoint(BaseModel):
    metric_date: str
    value: int


class AccountMetricOut(BaseModel):
    """某指标的完整序列（不分页）+ 最新 / 上一次 / 差值。"""

    metric: str
    items: list[AccountMetricPoint] = Field(default_factory=list)
    latest: AccountMetricPoint | None = None
    previous: AccountMetricPoint | None = None
    difference: int | None = None
