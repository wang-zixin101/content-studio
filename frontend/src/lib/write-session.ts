/**
 * 写作页会话状态（任务 06）。
 *
 * ------------------------------------------------------------------
 * 为什么需要它
 * ------------------------------------------------------------------
 * `WritePage` 挂在 `<Route path="write">` 上：用户点「内容库」再回来，
 * React Router 会把整个页面**卸载重建**，页面里二十多个 useState 全部归零 ——
 * 输入的主题、素材、三候选、未保存的编辑稿一起消失。
 *
 * 这里把「写作现场」放进一个**模块级单例**：模块在应用运行期间只加载一次，
 * 所以只要不刷新页面（刷新即重新加载模块）状态就一直在。
 * 不建草稿表、不自动保存、不做草稿管理器 —— 只要求应用运行期间保留。
 *
 * ------------------------------------------------------------------
 * 用法（形状与 useState 完全一致，改声明行即可）
 * ------------------------------------------------------------------
 *     const [draft, setDraft] = useWriteField("draft")
 *
 * ------------------------------------------------------------------
 * 纪律
 * ------------------------------------------------------------------
 * - 只有「写作现场」进这里；在途标志（generating / saving…）与
 *   弹窗开合、异步读回来的画像**不进** —— 它们每次挂载重新取即可，
 *   持久化只会造成「回来后卡在生成中」这类假象。
 * - `epoch` 是「× 清空」的代次：清空后递增。在途请求回来时必须比对它，
 *   否则迟到的响应会把已经清掉的现场又写回来。
 */

import { useCallback, useSyncExternalStore } from "react"

import type { Author, Material, Rewrite, RewriteCandidate, RewriteCandidatesResult } from "./types"
import type { ReferenceMode } from "./rewrite-flow"

/** 两个一级入口（任务 06 页面整合）。 */
export type WriteEntry = "create" | "benchmark"

/** 素材入口：粘贴/指定 vs 随机抽取。 */
export type SourceEntryKey = "specified" | "random"

/** 手写内容保存时的身份：成稿（参与风格学习）vs 灵感（不参与）。 */
export type SaveAs = "final" | "idea"

/** 随机素材面板的临时界面状态 —— 也要跨页面保留。 */
export interface PickerSession {
  pool: "all" | "blank" | "author"
  poolAuthor: number | null
  unusedFirst: boolean
  material: Material | null
  seen: number[]
}

export interface WriteSession {
  /** 当前入口 */
  entry: WriteEntry
  /** 主题 / 原帖 / 手写正文 */
  sourceText: string
  /** 右侧编辑区的稿子（单稿或已采用候选） */
  draft: string
  /** AI 初稿基线 —— 编辑距离与「还原初稿」都靠它 */
  aiDraft: string
  rewrite: Rewrite | null
  saved: boolean
  /** 手写保存时按成稿还是灵感（决定 style_eligible） */
  saveAs: SaveAs
  sourceUrl: string
  sourceContentId: number | null
  sourceEntry: SourceEntryKey
  sourceAuthorId: number | null
  sourceAuthorLabel: string | null
  /** 「指定素材」里手选的素材作者（与随机素材的作者区分） */
  materialAuthor: Author | null
  useProfile: boolean
  refMode: ReferenceMode
  fixedAuthorId: number | null
  fixedAuthor: Author | null
  candidates: RewriteCandidate[] | null
  candidateResult: RewriteCandidatesResult | null
  adoptedKey: string | null
  picker: PickerSession
  /** 「× 清空」的代次 —— 在途请求回来时用它判断是否已被清空 */
  epoch: number
}

export function initialPickerSession(): PickerSession {
  return {
    pool: "all",
    poolAuthor: null,
    unusedFirst: true,
    material: null,
    seen: [],
  }
}

export function initialWriteSession(): WriteSession {
  return {
    entry: "create",
    sourceText: "",
    draft: "",
    aiDraft: "",
    rewrite: null,
    saved: false,
    saveAs: "final",
    sourceUrl: "",
    sourceContentId: null,
    sourceEntry: "specified",
    sourceAuthorId: null,
    sourceAuthorLabel: null,
    materialAuthor: null,
    useProfile: true,
    refMode: "none",
    fixedAuthorId: null,
    fixedAuthor: null,
    candidates: null,
    candidateResult: null,
    adoptedKey: null,
    picker: initialPickerSession(),
    epoch: 0,
  }
}

// ---------------------------------------------------------------- 模块级单例

let session: WriteSession = initialWriteSession()
const listeners = new Set<() => void>()

function emit(): void {
  for (const fn of listeners) fn()
}

export function subscribeWriteSession(fn: () => void): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

/** 当前会话快照。整体替换引用 —— useSyncExternalStore 靠它判等。 */
export function getWriteSession(): WriteSession {
  return session
}

export function setWriteField<K extends keyof WriteSession>(
  key: K,
  value: WriteSession[K]
): void {
  if (Object.is(session[key], value)) return
  session = { ...session, [key]: value }
  emit()
}

export function setPickerField<K extends keyof PickerSession>(
  key: K,
  value: PickerSession[K]
): void {
  if (Object.is(session.picker[key], value)) return
  session = { ...session, picker: { ...session.picker, [key]: value } }
  emit()
}

/**
 * 清空创作现场，只保留指定入口。
 *
 * 「× 清空」与「切换入口」共用同一套清空动作 —— 两者要做的事是一样的：
 * 把这一轮的输入、候选、编辑稿、临时选择全部丢掉。
 *
 * - `epoch` **继续递增**（不重置为 0）：在途请求拿着的旧 epoch 一定对不上，
 *   迟到的响应会被丢弃，不会把刚清掉的内容写回来。
 * - 只动内存，不碰任何已经保存到内容库的数据。
 */
export function clearWriteSession(entry: WriteEntry): void {
  session = { ...initialWriteSession(), entry, epoch: session.epoch + 1 }
  emit()
}

/**
 * 「× 清空当前创作」：回到**当前入口**的空白初始态。
 * 入口本身保持不变 —— 用户清空是为了马上开始下一条，不是要换入口。
 */
export function resetWriteSession(): void {
  clearWriteSession(session.entry)
}

/** 当前代次 —— 发请求前记下，响应回来时用 `isSameEpoch` 校验。 */
export function sessionEpoch(): number {
  return session.epoch
}

// ---------------------------------------------------------------- URL 素材簿记
//
// `/write/:id` 上的素材预填要区分两件事：
//
//   打开**新的**素材 ID  → 去拉取，把原帖填进来
//   恢复已有写作现场     → 什么都不做（更不能把已经恢复的内容覆盖掉）
//
// 为此需要记住「这个 id 我已经处理过了」。关键是它**不属于创作现场**：
//   - 放进会话 → `× 清空` / 切换入口会把它一起清掉 → effect 再次触发 →
//     把用户刚清掉的原帖又填回来（正是要修的问题之一）。
// 所以它单独放在模块级，与 `epoch` 并列，只增不清。
//
// 不放进会话还有一个好处：它不参与渲染，改它不需要触发重渲染。

/**
 * 已经处理过的素材 id **集合**。
 *
 * 必须记全部而不是只记最后一个：依次主动打开 A、B 之后，浏览器历史返回 A 时，
 * 「只记最后一个」会得出 `A !== handledB` → 误判成需要重新载入，
 * 于是旧 A 的原文覆盖掉当前 B 的写作现场。
 * 记全之后，历史返回命中的一定是「已处理」→ 走恢复，不再拉取。
 */
const handledSourceIds = new Set<number>()

export function getHandledSourceIds(): number[] {
  return [...handledSourceIds]
}

export function hasHandledSource(id: number): boolean {
  return handledSourceIds.has(id)
}

/** 标记「这个素材 ID 已经处理过」。成功预填后调用。 */
export function markSourceHandled(id: number | null): void {
  if (id !== null) handledSourceIds.add(id)
}

/** 仅供测试复位 —— 生产路径没有任何地方需要清空这个集合。 */
export function resetHandledSources(): void {
  handledSourceIds.clear()
}

/**
 * 这次挂载要不要去拉取 URL 上的素材？
 *
 * 返回 false 的两种情况都必须做到「不发请求 + 不显示加载动画」：
 *   - 没有 id（普通 /write）；
 *   - 这个 id 已经处理过（切页回来 / × 清空之后 / 历史返回一个更早打开过的素材）。
 */
export function shouldLoadSource(sourceId: number | null): boolean {
  return sourceId !== null && !handledSourceIds.has(sourceId)
}

// ---------------------------------------------------------------- 主动打开素材
//
// 「从内容库点『改写这条』」是一次**明确的打开意图**，与「切页再回来」在路由上
// 长得一模一样（同一个 path），但要求完全相反：
//
//   主动打开 → 即使之前打开并 × 清空过同一个 ID，也要**重新载入**
//   切页回来 → 原样恢复现场，绝不重新载入
//
// 用导航 state 带一张**一次性票据**来区分：票据兑现过就不再算「主动打开」。
// 这样浏览器后退（history state 里还带着那张旧票据）不会被误判成一次新打开。

export interface MaterialOpenState {
  openMaterial?: { id: number; ticket: string }
}

/**
 * 已兑现票据的**集合**。
 *
 * 同样不能只记最后一张：打开 A（票据 A）→ 打开 B（票据 B）→ 浏览器历史返回 A 时，
 * 「只记最后一张」会让票据 A 重新变成「未使用」→ 又执行一次主动打开，
 * 把当前现场冲掉。记全之后，任何兑现过的票据都永远不会再次生效。
 */
const consumedTickets = new Set<string>()

/** 内容库侧生成票据 —— 每次点击「改写这条」都是新的。 */
export function newMaterialOpenTicket(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

/** 这张票据是不是「尚未兑现过的主动打开」。 */
export function isFreshMaterialOpen(ticket: string | null | undefined): boolean {
  return Boolean(ticket) && !consumedTickets.has(ticket as string)
}

/** 兑现票据（幂等）。 */
export function markMaterialOpenConsumed(ticket: string | null | undefined): void {
  if (ticket) consumedTickets.add(ticket)
}

/** 仅供测试复位。 */
export function resetConsumedTickets(): void {
  consumedTickets.clear()
}

/**
 * 这一轮创作是不是什么都没做过。
 *
 * 用来决定「× 清空」要不要给反馈：已经是空的时候按 × 应该安静地什么都不做，
 * 而不是弹一句「已清空」。逐字段比对（而不是列几个字段检查）——
 * 漏掉一个字段就会在「只抽了素材、还没写字」这类状态下误判成空。
 */
export function isWriteSessionPristine(): boolean {
  const base = initialWriteSession()
  for (const key of Object.keys(base) as (keyof WriteSession)[]) {
    if (key === "entry" || key === "epoch") continue
    if (key === "picker") {
      const p = session.picker
      const bp = base.picker
      for (const pk of Object.keys(bp) as (keyof PickerSession)[]) {
        if (!sameValue(p[pk], bp[pk])) return false
      }
      continue
    }
    if (!sameValue(session[key], base[key])) return false
  }
  return true
}

/**
 * 值相等判定。
 *
 * 不能只用 Object.is：初始态里的空数组（picker.seen）每次都是**新实例**，
 * Object.is([], []) 为 false，会把「全新现场」误判成「做过事」。
 */
function sameValue(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((v, i) => Object.is(v, b[i]))
  }
  return false
}

/**
 * 响应是否仍属于「清空之前」的那一次操作。
 * 返回 false 时必须整段丢弃：不能 setRewrite / setDraft / setCandidates。
 */
export function isSameEpoch(epoch: number): boolean {
  return session.epoch === epoch
}

// ---------------------------------------------------------------- React 绑定

type Setter<T> = (next: T | ((prev: T) => T)) => void

/**
 * 与 `useState` 形状一致的会话字段绑定。
 *
 * 之所以做成 hook 而不是让页面直接读写单例：漏一个字段就会在
 * 「离开再回来」时静默丢状态 —— 而漏掉的方式是把 useState 写回去，
 * 那种写法在 code review 里看得见，在这里则根本编译不过。
 */
export function useWriteField<K extends keyof WriteSession>(
  key: K
): [WriteSession[K], Setter<WriteSession[K]>] {
  const value = useSyncExternalStore(
    subscribeWriteSession,
    () => session[key],
    () => session[key]
  )
  const set = useCallback<Setter<WriteSession[K]>>((next) => {
    const prev = session[key]
    const v =
      typeof next === "function"
        ? (next as (p: WriteSession[K]) => WriteSession[K])(prev)
        : next
    setWriteField(key, v)
  }, [key])
  return [value, set]
}

/** 随机素材面板的字段绑定（存在 session.picker 下，同样跨页面保留）。 */
export function usePickerField<K extends keyof PickerSession>(
  key: K
): [PickerSession[K], Setter<PickerSession[K]>] {
  const value = useSyncExternalStore(
    subscribeWriteSession,
    () => session.picker[key],
    () => session.picker[key]
  )
  const set = useCallback<Setter<PickerSession[K]>>((next) => {
    const prev = session.picker[key]
    const v =
      typeof next === "function"
        ? (next as (p: PickerSession[K]) => PickerSession[K])(prev)
        : next
    setPickerField(key, v)
  }, [key])
  return [value, set]
}

/** 整份 picker 快照（面板需要一次性读多个字段时用）。 */
export function usePickerSession(): PickerSession {
  return useSyncExternalStore(
    subscribeWriteSession,
    () => session.picker,
    () => session.picker
  )
}
