/**
 * 分页收集器：把「当前筛选结果」的全部 ID 取回来。
 *
 * 为什么需要它：内容库的「全选全部」必须跨页拉取，而后端列表接口
 * 规定 `limit <= 100`。因此这里强制分页，每次请求不超过 MAX_PAGE_SIZE。
 *
 * 设计要点：
 * - 只负责「按页取 ID」，不关心筛选条件 —— 筛选由调用方在 fetchPage 里绑定，
 *   因此不可能全选到当前筛选之外的内容；
 * - 失败时直接抛出，**不返回半成品**，由调用方决定如何提示；
 * - 纯函数、无 React 依赖，便于单测。
 */

/** 必须与后端列表接口的 limit 上限保持一致（backend 端为 100）。 */
export const MAX_PAGE_SIZE = 100

export interface PageResult {
  items: { id: number }[]
  total: number
}

export type PageFetcher = (params: {
  limit: number
  offset: number
}) => Promise<PageResult>

export interface CollectOptions {
  /** 期望收集的总条数（来自列表接口的 total）。 */
  total: number
  /** 单页大小，会被钳制到 MAX_PAGE_SIZE 以内。 */
  pageSize?: number
  /** 每成功取完一页的回调，用于展示进度。 */
  onProgress?: (fetched: number, total: number) => void
}

/**
 * 反复请求分页接口，直到收集到 total 条（或服务端不再返回数据）。
 * 任一页失败都会抛出，不会返回已收集的部分。
 */
export async function collectAllIds(
  fetchPage: PageFetcher,
  options: CollectOptions
): Promise<number[]> {
  const { total, onProgress } = options
  // 关键：永远不超过后端上限
  const size = Math.max(1, Math.min(options.pageSize ?? MAX_PAGE_SIZE, MAX_PAGE_SIZE))

  const ids: number[] = []
  if (total <= 0) return ids

  while (ids.length < total) {
    const page = await fetchPage({ limit: size, offset: ids.length })

    if (page.items.length === 0) break
    ids.push(...page.items.map((item) => item.id))
    onProgress?.(ids.length, total)

    // 服务端提前给完（例如期间数据被删），避免死循环
    if (page.items.length < size) break
  }

  return ids
}
