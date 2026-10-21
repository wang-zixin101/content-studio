/**
 * Stage 8 UI 的「高表现 / 低表现」分档过滤。
 *
 * ------------------------------------------------------------------
 * 为什么必须过滤
 * ------------------------------------------------------------------
 * 后端 Performance Profile 的 `high` / `low` 是
 * **「该维度里中位最高 / 最低的前 N 个取值」**，本质是排序结果，
 * 不是绝对的高 / 低。当某个维度只有少数取值时，同一个取值会同时出现在两榜；
 * 中位 50 的取值也会出现在「低表现」里 —— 用户读到的语义就是错的。
 *
 * Stage 10 已经锁定过同一套口径：
 *   high   : median_performance_score >= 60
 *   low    : median_performance_score < 40
 *   neutral: 40 <= score < 60  → 只存在于「全部取值」
 *
 * 这里在**前端展示层**复用同一口径，不改后端算法、不改 API。
 */

/**
 * 只声明过滤所需的两个字段 —— 具体取值类型由调用方决定，
 * 这样 filterHigh / filterLow 能直接保持原数组的元素类型。
 */
export interface BucketValue {
  median_performance_score?: number | null
  sample_count?: number | null
}

export const HIGH_PERF_SCORE = 60
export const LOW_PERF_SCORE = 40
export const MIN_RANKING_SAMPLE = 5

export function isHighPerformance(v: BucketValue | null | undefined): boolean {
  if (!v) return false
  const score = v.median_performance_score
  const n = v.sample_count
  if (typeof score !== "number" || !Number.isFinite(score)) return false
  if (typeof n !== "number" || n < MIN_RANKING_SAMPLE) return false
  return score >= HIGH_PERF_SCORE
}

export function isLowPerformance(v: BucketValue | null | undefined): boolean {
  if (!v) return false
  const score = v.median_performance_score
  const n = v.sample_count
  if (typeof score !== "number" || !Number.isFinite(score)) return false
  if (typeof n !== "number" || n < MIN_RANKING_SAMPLE) return false
  return score < LOW_PERF_SCORE
}

/** 中性（40–60）：既不进高榜也不进低榜，完整数据由「全部取值」承担。 */
export function isNeutralPerformance(v: BucketValue | null | undefined): boolean {
  return !isHighPerformance(v) && !isLowPerformance(v)
}

export function filterHigh<T extends BucketValue>(items: T[] | null | undefined): T[] {
  return (items ?? []).filter(isHighPerformance)
}

export function filterLow<T extends BucketValue>(items: T[] | null | undefined): T[] {
  return (items ?? []).filter(isLowPerformance)
}
