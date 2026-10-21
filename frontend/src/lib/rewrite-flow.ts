/**
 * 阶段 10 双入口改写的**纯逻辑**（无 React、无网络），便于单元测试。
 *
 * 三件事必须彻底分开：
 *   素材作者  = 这条 benchmark content 原本是谁发的（客观 metadata）
 *   参考作者  = 本次生成想参考谁的数据（用户选择，与素材作者独立）
 *   素材池    = 随机入口从哪个子集抽素材（blank 不是 Author）
 */

export type MaterialPool = "all" | "blank" | "author"

/** 参考作者策略。 */
export type ReferenceMode = "follow_source" | "none" | "fixed"

/**
 * 04-C：五种创作输入路径。
 *
 *   rewrite        原帖改写（有素材，无参考作者）
 *   rewrite_author 原帖 + 参考作者
 *   topic          仅主题创作
 *   topic_author   主题 + 作者
 *   author_auto    仅选择参考作者，由 AI 自主选题
 *
 * 前端是**唯一知道**「这段文本是原帖还是主题」的一方（靠 mode），
 * 因此路径由前端显式声明，后端只做校验与兜底推导。
 */
export type CreationPath =
  | "rewrite"
  | "rewrite_author"
  | "topic"
  | "topic_author"
  | "author_auto"

export const CREATION_PATHS: CreationPath[] = [
  "rewrite",
  "rewrite_author",
  "topic",
  "topic_author",
  "author_auto",
]

/** 生成型模式（成稿收录不调用生成服务，没有创作路径）。 */
export type GenerateMode = "idea" | "benchmark" | "author"

export interface CreationPathInput {
  mode: GenerateMode
  /** 是否有素材 / 主题文本（trim 后非空）。 */
  hasSource: boolean
  referenceAuthorId: number | null
  useProfile: boolean
}

/**
 * 04-C 第二轮验收：素材态切片。
 *
 * 「切换创作模式」必须把这**整片**重置。漏掉 `sourceContentId` 会出现两类串味：
 *   ① 切到「作者起稿」（无素材）时仍带着旧素材 id → 服务端按 id 取回原文 →
 *      与 author_auto「不接收素材」冲突，直接报错；
 *   ② 切到「灵感起稿」写新主题时仍带着旧对标素材 id → 新主题被错误关联到
 *      旧素材（并注入旧素材的 ANALYSIS）。
 * `sourceEntry` 同理：留在 "random" 会让下一次进入对标模式时停在抽素材阶段之外。
 */
export type SourceEntry = "specified" | "random"

export interface SourceSlice {
  sourceText: string
  sourceContentId: number | null
  sourceEntry: SourceEntry
  sourceAuthorId: number | null
  sourceAuthorLabel: string | null
}

/** 干净的初始素材态 —— 进入任何模式时的正确起点。 */
export function initialSourceSlice(): SourceSlice {
  return {
    sourceText: "",
    sourceContentId: null,
    sourceEntry: "specified",
    sourceAuthorId: null,
    sourceAuthorLabel: null,
  }
}

/**
 * 切换创作模式时的**唯一**素材态重置出口。
 *
 * 做成纯函数是为了可测：把「换模式后不得残留旧素材」变成断言，
 * 而不是靠人眼检查 switchMode 里那十几行 setState。
 */
export function resetSourceOnModeSwitch(): SourceSlice {
  return initialSourceSlice()
}

/**
 * 本次是否真的有「素材 / 主题」输入。
 *
 * **必须把 content_id 也算进去**：只给 content_id 不给文本时，
 * 服务端同样会取回原文当作素材 —— 只看文本会漏判这一种。
 */
export function hasSourceInput(input: {
  sourceText: string
  sourceContentId: number | null
}): boolean {
  return Boolean(input.sourceText.trim()) || input.sourceContentId !== null
}

/**
 * 生成请求里的**素材字段**，单稿与三候选共用的唯一出口。
 *
 * 单独拎出来是为了可以直接断言「切换模式后的请求不会携带旧素材 id」。
 */
export function sourceFieldsFor(input: {
  sourceText: string
  sourceContentId: number | null
}): { source_text?: string; source_content_id: number | null } {
  const text = input.sourceText.trim()
  return {
    ...(text ? { source_text: text } : {}),
    source_content_id: input.sourceContentId,
  }
}

/**
 * 由当前 UI 状态推导创作路径。
 *
 * - author 模式：没有素材，只有参考作者 → author_auto；
 *   还没选作者 → null（还不满足生成条件，由调用方提示）。
 * - idea  模式：文本是主题 / 灵感 → topic / topic_author。
 * - benchmark 模式：文本是待改写的原帖 → rewrite / rewrite_author。
 * - 有素材但 mode=author → null（冲突，不该发生；返回 null 让调用方拦住，
 *   绝不猜一个路径出来 —— 静默改判会把「自主选题」变成「拿空文本生成」）。
 */
export function resolveCreationPath(
  input: CreationPathInput
): CreationPath | null {
  // 路径依据是「有没有选参考作者」，不是「有没有勾选画像开关」：
  // 作者一旦被选中，其流量画像与跨账号数据就已进入本次生成。
  // useProfile 只决定要不要再注入写作画像（阶段 7 的更细开关）。
  const withAuthor = input.referenceAuthorId !== null

  if (input.mode === "author") {
    if (input.hasSource) return null
    return withAuthor ? "author_auto" : null
  }
  if (!input.hasSource) return null
  if (input.mode === "idea") return withAuthor ? "topic_author" : "topic"
  return withAuthor ? "rewrite_author" : "rewrite"
}

/** 是否可以在当前状态下点生成。 */
export function canGenerate(path: CreationPath | null): boolean {
  return path !== null
}

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

export interface RewriteCandidateItem {
  key: string
  label: string
  text: string
  strategy_tags: string[]
}

/**
 * 解析参考作者 id。
 *
 * - follow_source → 跟随素材作者；素材没有作者时**退化为 none**（绝不报错、绝不猜）
 * - none          → 不指定
 * - fixed         → 固定作者，换素材也不变
 */
export function resolveReferenceAuthor(
  mode: ReferenceMode,
  sourceAuthorId: number | null,
  fixedAuthorId: number | null
): number | null {
  if (mode === "none") return null
  if (mode === "fixed") return fixedAuthorId ?? null
  // follow_source
  return sourceAuthorId ?? null
}

/** 素材有正式作者 → 默认 follow_source；没有 → 默认 none。 */
export function defaultReferenceMode(sourceAuthorId: number | null): ReferenceMode {
  return sourceAuthorId ? "follow_source" : "none"
}

/**
 * 换素材后保持参考作者策略（阶段 10 §37）。
 *
 * - fixed     → 仍是 fixed + 同一个作者
 * - none      → 仍是 none
 * - follow_source → 重新指向新素材的作者（新素材无作者则退化为 none）
 */
export function referenceModeOnSourceChange(
  mode: ReferenceMode,
  nextSourceAuthorId: number | null,
  currentFixedAuthorId: number | null = null
): { mode: ReferenceMode; fixedAuthorId: number | null } {
  if (mode === "fixed") {
    return { mode: "fixed", fixedAuthorId: currentFixedAuthorId }
  }
  if (mode === "none") return { mode: "none", fixedAuthorId: null }
  return {
    mode: nextSourceAuthorId ? "follow_source" : "none",
    fixedAuthorId: null,
  }
}

/**
 * 选择参考作者的**唯一**入口。
 *
 * ------------------------------------------------------------------
 * 硬约束（Stage 11 最终验收 P0 回归）
 * ------------------------------------------------------------------
 * 切换参考作者只允许改变：
 *     referenceMode（none / follow_source / fixed）
 *     referenceAuthorId
 *
 * 绝不允许触碰任何素材状态：
 *     素材入口（specified / random）
 *     source_content_id / benchmark_content_id
 *     sourceText
 *     sourceAuthorId / sourceAuthorLabel
 *     当前已选素材 / 素材池 / seen
 *
 * 之所以做成纯函数：把「只改参考作者」变成**结构上的唯一可能** ——
 * 入参只有参考作者相关字段，返回值也只有这两个字段，
 * 调用方拿不到也传不进任何素材字段，从根上杜绝误重置。
 */
export function selectReferenceAuthor(
  _current: { mode: ReferenceMode; fixedAuthorId: number | null },
  choice: { kind: "none" } | { kind: "follow_source" } | { kind: "fixed"; authorId: number }
): { mode: ReferenceMode; fixedAuthorId: number | null } {
  switch (choice.kind) {
    case "none":
      return { mode: "none", fixedAuthorId: null }
    case "follow_source":
      return { mode: "follow_source", fixedAuthorId: null }
    case "fixed":
      return { mode: "fixed", fixedAuthorId: choice.authorId }
  }
}

/** 参考作者**正式列表**：blank / null 绝不能被当成正式作者。 */
export function isFormalAuthor(authorId: number | null | undefined): boolean {
  return typeof authorId === "number" && Number.isFinite(authorId) && authorId > 0
}

/**
 * 当前 session 已看过的素材 id。
 *
 * 只存在组件 state 里：刷新后重置没有问题，
 * **不建 seen 表 / used 表 / 素材状态机**。
 */
export function addSeen(seen: number[], contentId: number): number[] {
  if (seen.includes(contentId)) return seen
  return [...seen, contentId]
}

/** 切换素材池 → seen 清空（不同池的 id 空间混在一起没有意义）。 */
export function resetSeen(): number[] {
  return []
}
