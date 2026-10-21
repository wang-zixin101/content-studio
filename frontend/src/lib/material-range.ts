/**
 * 随机素材抽取范围（任务 05）—— **纯逻辑**，无 React / 无网络，便于单元测试。
 *
 * ------------------------------------------------------------------
 * 三条被这轮修掉的行为
 * ------------------------------------------------------------------
 * 1. 切换作者 / 素材池后**不自动抽取**（旧实现用 useEffect 立即 draw）；
 * 2. 指定作者后抽取结果必须只属于该作者，抽空了要**明确提示**，
 *    绝不自动切到别的作者；
 * 3. 展示的是帖子**完整 raw_text**，不是摘要。
 */

export type MaterialPoolKey = "all" | "blank" | "author"

/** 结构类型：与 lib/types 的 MaterialPools 结构兼容，但不依赖它（便于纯测试）。 */
export interface MaterialPoolsLike {
  all: number
  blank: number
  authors: {
    author_id: number
    username: string | null
    display_name: string | null
    count: number
  }[]
}

export interface MaterialRangeOption {
  key: MaterialPoolKey
  label: string
  count: number
  authorId: number | null
}

/** 「全部素材」一行 + 「空白作者」一行 + 每个**有素材的**正式作者一行。 */
export const ALL_RANGE_LABEL = "全部素材"
export const BLANK_RANGE_LABEL = "空白作者"

/**
 * 素材范围选项。
 *
 * 只列出 count > 0 的作者：0 条的作者点进去必然是死路，
 * 与其让用户撞一次错误，不如不出现在列表里。
 */
export function materialRangeOptions(
  pools: MaterialPoolsLike | null | undefined,
  labelOf: (displayName: string | null, username: string) => string
): MaterialRangeOption[] {
  if (!pools) return []
  const rows: MaterialRangeOption[] = [
    { key: "all", label: ALL_RANGE_LABEL, count: pools.all, authorId: null },
    { key: "blank", label: BLANK_RANGE_LABEL, count: pools.blank, authorId: null },
  ]
  for (const a of pools.authors) {
    if (a.count <= 0) continue
    rows.push({
      key: "author",
      label: labelOf(a.display_name ?? null, a.username ?? ""),
      count: a.count,
      authorId: a.author_id,
    })
  }
  return rows
}

/** 当前抽取范围的可读名字，用于「已抽完」「正在抽」这类提示。 */
export function rangeTitle(
  options: MaterialRangeOption[],
  pool: MaterialPoolKey,
  authorId: number | null
): string {
  const hit = options.find(
    (o) => o.key === pool && (o.key !== "author" || o.authorId === authorId)
  )
  if (hit) return hit.label
  if (pool === "all") return ALL_RANGE_LABEL
  if (pool === "blank") return BLANK_RANGE_LABEL
  return "指定作者"
}

/** 当前范围的素材总数（用于「本会话已看过 N/M 条」）。 */
export function rangeCount(
  options: MaterialRangeOption[],
  pool: MaterialPoolKey,
  authorId: number | null
): number {
  const hit = options.find(
    (o) => o.key === pool && (o.key !== "author" || o.authorId === authorId)
  )
  return hit?.count ?? 0
}

/**
 * 抽空时的提示。**必须点名范围**，否则用户不知道是「这条作者抽完了」
 * 还是「整个库都抽完了」——那正是「不自动切换到其他作者」的前提。
 */
export function rangeEmptyNotice(args: {
  title: string
  count: number
  seenCount: number
}): string {
  const { title, count, seenCount } = args
  if (count <= 0) {
    return `${title} 里没有可抽取的帖子。换一个范围试试，不会自动切换。`
  }
  return (
    `${title} 的 ${count} 条帖子已经全部抽过一遍` +
    `（本会话已看 ${seenCount} 条）。` +
    `可以换一个作者范围，或点「重来」清空已看记录后继续抽。`
  )
}

/** author 池必须带 author_id —— 与后端 `pool=author 必须提供 author_id` 同一条规则。 */
export function drawScopeError(
  pool: MaterialPoolKey,
  authorId: number | null
): string | null {
  if (pool === "author" && authorId === null) {
    return "请先选择一位作者，再抽取素材。"
  }
  return null
}

/** 抽取按钮文案：还没抽到时是「随机抽取」，抽到之后是「换一条」。 */
export function drawButtonLabel(hasMaterial: boolean): string {
  return hasMaterial ? "换一条" : "随机抽取"
}

/** 是否展示「重来」（清空已看记录）。只在抽失败且确实有已看记录时才有意义。 */
export function shouldOfferRestart(args: {
  failed: boolean
  seenCount: number
}): boolean {
  return args.failed && args.seenCount > 0
}

// ---------------------------------------------------------------- 在途请求守卫
//
// 第二轮验收：抽取请求未完成时用户仍能切换作者 —— 旧请求返回后会无条件
// setMaterial()，于是 A 作者的帖子显示在 B 作者的范围下（seen 也会串）。
//
// 做法是给每次抽取发一个**递增 token**，只有最新 token 的结果允许落地；
// 切换范围同样推进 token，从而**作废**所有在途请求。
// 界面层再叠一道「抽取期间禁用范围切换」，两道一起保证不串。

export interface DrawSession {
  /** 最新一次「发起抽取」或「切换范围」的序号 */
  latest: number
}

export function newDrawSession(): DrawSession {
  return { latest: 0 }
}

/**
 * 发起一次抽取：返回本次的 token 与推进后的会话。
 * 调用方必须在响应回来时用 `canApplyDraw` 校验，**不能**直接 setState。
 */
export function beginDraw(session: DrawSession): {
  session: DrawSession
  token: number
} {
  const token = session.latest + 1
  return { session: { latest: token }, token }
}

/**
 * 切换抽取范围：作废所有在途请求。
 * 返回新会话，之后任何旧 token 的响应都会被 `canApplyDraw` 判为过期。
 */
export function invalidateDraws(session: DrawSession): DrawSession {
  return { latest: session.latest + 1 }
}

/**
 * 这次响应是否仍然有效 —— 唯一允许落地结果的条件。
 * 返回 false 时必须**整段丢弃**：不 setMaterial、不 setSeen、不碰 loading。
 */
export function canApplyDraw(session: DrawSession, token: number): boolean {
  return session.latest === token
}

/** 抽取进行中：此时禁用范围切换，避免用户点了个「暂时不会有反应」的按钮。 */
export function rangeSwitchDisabled(loading: boolean): boolean {
  return loading
}
