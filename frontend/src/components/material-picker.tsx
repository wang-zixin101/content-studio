import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Check, Loader2, RefreshCw, RotateCcw, Shuffle } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { api } from "@/lib/api"
import { authorLabel } from "@/lib/author-display"
import { formatCompact, formatDateShort } from "@/lib/format"
import {
  beginDraw,
  canApplyDraw,
  drawButtonLabel,
  drawScopeError,
  invalidateDraws,
  materialRangeOptions,
  newDrawSession,
  rangeCount,
  rangeEmptyNotice,
  rangeSwitchDisabled,
  rangeTitle,
  shouldOfferRestart,
  type MaterialPoolKey,
} from "@/lib/material-range"
import { addSeen, resetSeen } from "@/lib/rewrite-flow"
import { getWriteSession, usePickerField } from "@/lib/write-session"
import type { Material, MaterialPools } from "@/lib/types"

interface MaterialPickerProps {
  /** 用户确认「使用这条」 */
  onPick: (material: Material) => void
}

/**
 * 随机素材入口（阶段 10 / 任务 05 完善）。
 *
 * ------------------------------------------------------------------
 * 任务 05 的三处行为修正
 * ------------------------------------------------------------------
 * 1. **切换作者或素材池后不自动抽取**。旧实现用 useEffect 立即 draw，
 *    用户还没确认范围就先被塞了一条素材，也看不清自己抽的是哪个范围。
 *    现在必须显式点「随机抽取」才出结果。
 * 2. **抽空当前范围要明确提示，绝不自动切到别的作者**。
 *    顺带给出「重来」（清空已看记录），否则 seen 覆盖整池后就是死路。
 * 3. **展示帖子完整 raw_text**（不是 excerpt、不截断、不 line-clamp），
 *    长帖放在可滚动阅读区里。
 *
 * seen 只存在组件 state：刷新后重置没有问题，不建 seen 表。
 * 全程 0 AI：抽素材、换一条、使用这条都不调用模型。
 */
export function MaterialPicker({ onPick }: MaterialPickerProps) {
  // pools / loading / error 是「每次挂载重新取」的瞬时状态，不进会话；
  // 范围、已看记录、抽到的素材则**跨页面保留**（任务 06）。
  const [pools, setPools] = useState<MaterialPools | null>(null)
  const [pool, setPool] = usePickerField("pool")
  const [poolAuthor, setPoolAuthor] = usePickerField("poolAuthor")
  const [unusedFirst, setUnusedFirst] = usePickerField("unusedFirst")
  const [material, setMaterial] = usePickerField("material")
  const [seen, setSeen] = usePickerField("seen")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)

  /**
   * 在途抽取请求守卫（任务 05 第二轮验收）。
   *
   * 没有它就会出现：抽王长富的请求还在路上，用户已经切到老雷；
   * 旧响应回来无条件 setMaterial()，于是王长富的帖子显示在「老雷」范围下，
   * seen 里也混进了别的作者的 id。用 ref 存是因为它不该触发渲染。
   */
  const drawSession = useRef(newDrawSession())

  useEffect(() => {
    void api
      .getMaterialPools()
      .then(setPools)
      .catch((err) => setError((err as Error).message))
  }, [])

  const options = useMemo(
    () => materialRangeOptions(pools, (d, u) => authorLabel(d, u)),
    [pools]
  )
  const title = rangeTitle(options, pool, poolAuthor)
  const count = rangeCount(options, pool, poolAuthor)

  /** 抽一条。**只由用户点击触发** —— 不再有任何 effect 自动调用它。 */
  const draw = useCallback(
    async (nextSeen: number[], override?: { pool: MaterialPoolKey; authorId: number | null }) => {
      const usePool = override?.pool ?? pool
      const useAuthor = override?.authorId ?? poolAuthor
      const scopeError = drawScopeError(usePool, useAuthor)
      if (scopeError) {
        setError(scopeError)
        setMaterial(null)
        return
      }
      // 每次抽取领一个 token；只有最新 token 的结果允许落地
      const begun = beginDraw(drawSession.current)
      drawSession.current = begun.session
      const token = begun.token
      // 记下会话代次：响应回来前用户若点了「× 清空」，这次结果也要丢弃
      const epoch = getWriteSession().epoch
      const alive = () =>
        canApplyDraw(drawSession.current, token) &&
        getWriteSession().epoch === epoch

      setLoading(true)
      setError(null)
      setFailed(false)
      try {
        const data = await api.getRandomMaterial({
          pool: usePool,
          author_id: usePool === "author" ? useAuthor : null,
          exclude_ids: nextSeen,
          unused_first: unusedFirst,
        })
        // 过期响应整段丢弃：期间用户换了范围、又抽了一次、或按了「× 清空」。
        // 绝不能把 A 作者的帖子落到 B 的范围下，也不让 seen 串味。
        if (!alive()) return
        setMaterial(data)
        setSeen(addSeen(nextSeen, data.content_id))
      } catch (err) {
        // 抽空当前范围：明确提示，**不自动切换**到别的作者
        if (!alive()) return
        setError((err as Error).message)
        setFailed(true)
        setMaterial(null)
      } finally {
        // loading 也只归最新那次请求管 —— 否则旧请求的 finally
        // 会在新请求仍在途时把「正在抽素材…」提前收掉
        if (alive()) setLoading(false)
      }
    },
    [pool, poolAuthor, unusedFirst]
  )

  /**
   * 切换范围：清空当前素材与已看记录，**不自动抽取**。
   * seen 按范围隔离 —— 换作者后沿用旧的 exclude 列表会凭空少掉一批帖子。
   *
   * 同时**作废所有在途请求**：否则上一个范围的响应回来后会把旧作者的帖子
   * 落到新范围下。丢弃在途结果后 loading 也一起收掉 —— 那一次抽取
   * 已经不属于当前范围了。
   */
  const switchRange = (nextPool: MaterialPoolKey, nextAuthor: number | null) => {
    if (nextPool === pool && nextAuthor === poolAuthor) return
    drawSession.current = invalidateDraws(drawSession.current)
    setPool(nextPool)
    setPoolAuthor(nextAuthor)
    setMaterial(null)
    setSeen(resetSeen())
    setError(null)
    setFailed(false)
    setLoading(false)
  }

  /** 重来：清空已看记录，留在当前范围。同样作废在途请求。 */
  const restart = () => {
    drawSession.current = invalidateDraws(drawSession.current)
    setSeen(resetSeen())
    setError(null)
    setFailed(false)
    setMaterial(null)
    setLoading(false)
  }

  if (!pools) {
    return (
      <div className="flex items-center gap-2 px-1 py-6 text-xs text-text-secondary">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        正在读取素材池…
      </div>
    )
  }

  if (pools.all === 0) {
    return (
      <p className="px-1 py-6 text-xs leading-relaxed text-text-secondary">
        还没有任何对标素材。先到「导入」页导入 Xporter 导出文件，再回来随机抽素材。
      </p>
    )
  }

  return (
    <div className="space-y-3">
      {/* 抽取范围。抽取期间禁用切换 —— 与 token 守卫互为双保险：
          token 保证不会串数据，禁用保证用户不会点一个「暂时没反应」的按钮。 */}
      <div className="space-y-1.5">
        <span className="text-[11px] text-text-secondary">抽取范围</span>
        <div className="max-h-40 space-y-0.5 overflow-y-auto pr-1">
          {options.map((opt) => {
            const active =
              pool === opt.key &&
              (opt.key !== "author" || poolAuthor === opt.authorId)
            const locked = rangeSwitchDisabled(loading)
            return (
              <button
                key={`${opt.key}-${opt.authorId ?? "x"}-${opt.label}`}
                type="button"
                disabled={locked}
                title={locked ? "正在抽素材，请稍候" : undefined}
                onClick={() => switchRange(opt.key, opt.authorId)}
                className={
                  (active
                    ? "flex w-full items-center gap-2 rounded bg-selected px-2 py-1.5 text-left text-xs font-medium"
                    : "flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-text-secondary hover:bg-hover") +
                  (locked ? " cursor-not-allowed opacity-50" : "")
                }
              >
                <span className="w-2 shrink-0 text-center">
                  {active ? "●" : "○"}
                </span>
                <span className="min-w-0 flex-1 truncate">{opt.label}</span>
                <span className="shrink-0 tabular-nums text-[11px] text-text-secondary">
                  {opt.count} 条
                </span>
              </button>
            )
          })}
        </div>
      </div>

      {/* 未改写优先。「未改写」= 从未采用过改写候选，不是「尚未保存最终稿」。 */}
      <Checkbox
        checked={unusedFirst}
        onChange={setUnusedFirst}
        className="text-[11px] text-text-secondary"
      >
        未改写优先
      </Checkbox>
      <p className="text-[11px] leading-relaxed text-text-tertiary">
        「未改写」= 从未采用过改写候选。只生成候选、没点采用，素材仍算未改写；
        一旦采用即视为用过，不要求已经保存最终稿。
      </p>

      {error && (
        <div className="space-y-1.5 rounded border border-border bg-canvas px-2.5 py-2">
          <p className="text-[11px] leading-relaxed text-destructive">
            {failed
              ? rangeEmptyNotice({ title, count, seenCount: seen.length })
              : error}
          </p>
          {shouldOfferRestart({ failed, seenCount: seen.length }) && (
            <Button size="sm" variant="outline" onClick={restart}>
              <RotateCcw className="h-3.5 w-3.5" />
              重来（清空已看 {seen.length} 条）
            </Button>
          )}
        </div>
      )}

      {material?.fallback_used && (
        <p className="text-[11px] leading-relaxed text-text-secondary">
          该素材池中的未改写内容已经用完，已允许抽取历史素材。
        </p>
      )}

      {/* 素材卡 */}
      {loading ? (
        <div className="flex items-center gap-2 py-8 text-xs text-text-secondary">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          正在抽素材…
        </div>
      ) : material ? (
        /*
         * 素材卡：正文是视觉主体，**完整展示 raw_text**（不截断、不省略），
         * 长帖放进可滚动阅读区，开头 / 中间 / 结尾都能读到。
         */
        <div className="rounded-lg border border-border bg-surface">
          {/* 抽取范围回显：卡片出现后「当前范围」那行会让位给正文，
              没有它用户就不知道自己正在哪个范围里换条目。 */}
          <div className="border-b border-divider px-4 py-1.5">
            <span className="text-[11px] text-text-tertiary">
              抽取范围 · <span className="text-text-secondary">{title}</span>
            </span>
          </div>
          <div className="max-h-[26rem] overflow-y-auto px-4 py-3">
            <p className="whitespace-pre-wrap break-words text-[14px] leading-relaxed text-foreground">
              {material.raw_text}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-t border-divider px-4 py-2.5">
            {material.author_id ? (
              <span className="text-[12px] text-text-secondary">
                {authorLabel(
                  material.author_display_name ?? null,
                  material.author_username ?? ""
                )}
              </span>
            ) : (
              <Badge variant="outline">空白作者</Badge>
            )}
            {material.view_count != null && (
              <span className="text-[12px] tabular-nums text-text-tertiary">
                曝光 {formatCompact(material.view_count)}
              </span>
            )}
            {material.posted_at && (
              <span className="text-[12px] text-text-tertiary">
                {formatDateShort(material.posted_at)}
              </span>
            )}
            <span className="text-[12px] tabular-nums text-text-tertiary">
              {material.raw_text.length} 字
            </span>
            <div className="ml-auto flex items-center gap-2">
              <Button size="sm" variant="outline" onClick={() => void draw(seen)}>
                <RefreshCw className="h-3.5 w-3.5" />
                换一条
              </Button>
              <Button size="sm" onClick={() => onPick(material)}>
                <Check className="h-3.5 w-3.5" />
                使用这条
              </Button>
            </div>
          </div>
        </div>
      ) : (
        /* 未抽取：只给一个显式的抽取入口，绝不自动出结果 */
        <div className="space-y-2 rounded-lg border border-dashed border-border px-4 py-5">
          <p className="text-[12px] leading-relaxed text-text-secondary">
            当前范围：<span className="text-foreground">{title}</span>
            <span className="tabular-nums text-text-tertiary"> · {count} 条</span>
          </p>
          <p className="text-[11px] leading-relaxed text-text-tertiary">
            切换范围不会自动抽取，点下面的按钮才会显示一条帖子。
          </p>
          <Button size="sm" onClick={() => void draw(seen)}>
            <Shuffle className="h-3.5 w-3.5" />
            {drawButtonLabel(false)}
          </Button>
        </div>
      )}
    </div>
  )
}
