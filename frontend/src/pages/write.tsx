import { useCallback, useEffect, useRef, useState } from "react"
import { Link, useLocation, useParams } from "react-router-dom"
import {
  Check,
  Info,
  Loader2,
  RefreshCw,
  Save,
  Sparkles,
  Undo2,
  X,
} from "lucide-react"
import { toast } from "sonner"

import { AuthorSelector } from "@/components/author-selector"
import {
  EmptyState,
  HelpText,
  PageHeader,
  Segmented,
} from "@/components/common"
import { MaterialPicker } from "@/components/material-picker"
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Separator } from "@/components/ui/separator"
import { api } from "@/lib/api"
import { authorLabel } from "@/lib/author-display"
import {
  canGenerate,
  defaultReferenceMode,
  hasSourceInput,
  referenceModeOnSourceChange,
  resolveCreationPath,
  resolveReferenceAuthor,
  selectReferenceAuthor,
  sourceFieldsFor,
} from "@/lib/rewrite-flow"
import type { CreationPath } from "@/lib/rewrite-flow"
import {
  clearWriteSession,
  isFreshMaterialOpen,
  isSameEpoch,
  isWriteSessionPristine,
  markMaterialOpenConsumed,
  markSourceHandled,
  resetWriteSession,
  sessionEpoch,
  shouldLoadSource,
  useWriteField,
} from "@/lib/write-session"
import type { MaterialOpenState, SaveAs, WriteEntry } from "@/lib/write-session"
import type {
  Author,
  AuthorWritingProfile,
  Material,
  PersonalizationState,
  Rewrite,
  RewriteCandidate,
} from "@/lib/types"
import { cn } from "@/lib/utils"

const ITERATIONS: { label: string; instruction: string }[] = [
  { label: "更像我", instruction: "更贴近我的表达习惯，使用我更常用的句式、用词和节奏。" },
  { label: "更短", instruction: "更短。砍掉冗余和铺垫，只保留核心信息，保持原意。" },
  { label: "更直接", instruction: "更直接。开门见山，第一句就给结论，减少修饰。" },
]

const STATE_LABEL: Record<string, string> = {
  COLD_START: "冷启动",
  LEARNING: "学习中",
  PERSONALIZED: "已个性化",
}

/**
 * 任务 06：两个一级入口。
 *
 * 旧的四种模式（灵感起稿 / 成稿收录 / 作者起稿 / 对标改写）合并成两个：
 *
 *   我的创作 = 灵感起稿 + 成稿收录 + 作者起稿
 *     三者的差别只是「手里有什么」，不是三个不同的页面 ——
 *     有主题就按主题创作、只有参考作者就让 AI 自主选题、
 *     自己写完了就保存，全在同一个输入区里完成。
 *
 *   对标改写 = 对标改写（不变）
 *
 * 合并**不删任何生成路径**：单稿、三候选、编辑、保存全部保留。
 * 04-C 的五条创作路径（rewrite / rewrite_author / topic / topic_author /
 * author_auto）也全部保留 —— 由 `genMode` 从「入口 + 有没有输入」推导。
 */
const ENTRIES: { key: WriteEntry; label: string; hint: string }[] = [
  {
    key: "create",
    label: "我的创作",
    hint: "写主题或想法让 AI 起稿；也可以只选参考作者让 AI 自主选题；自己写完了直接保存。",
  },
  {
    key: "benchmark",
    label: "对标改写",
    hint: "别人的内容作为素材，生成 A/B/C 三候选。",
  },
]

/** 可用上下文标记：有 = ✓，没有 = —（缺哪层跳哪层，绝不阻塞生成）。 */
function contextMark(ok: boolean | null | undefined): string {
  return ok ? "✓" : "—"
}

/** 传入 resolveCreationPath 的形态（与 04-C 的枚举保持一致）。 */
type GenMode = "idea" | "benchmark" | "author"

const PLACEHOLDER: Record<WriteEntry, string> = {
  create:
    "写下主题或想法，让 AI 起稿；或者直接写下你自己写好的完整内容，点保存收录。",
  benchmark: "粘贴对标账号的帖子原文…",
}

const SAVE_AS_HINT: Record<SaveAs, string> = {
  final: "按「成稿」保存：这是你认可的原创内容，会参与个人风格学习。",
  idea: "按「灵感」保存：只是没成形的记录，**不**参与个人风格学习。",
}

export default function WritePage() {
  const { id } = useParams()
  const sourceId = id ? Number(id) : null
  const location = useLocation()
  /** 「从内容库点『改写这条』」带过来的一次性票据（见 write-session 注释）。 */
  const materialOpen = (location.state as MaterialOpenState | null)?.openMaterial
  /** 本次挂载已判定为主动打开的票据 —— 让判定在 StrictMode 双跑下保持稳定。 */
  const freshOpenRef = useRef<{ ticket: string } | null>(null)

  // ------------------------------------------------------------------
  // 写作现场：全部走会话存储（任务 06）
  //
  // 声明形状与 useState 一模一样，但值存在模块级单例里 ——
  // 用户切到「内容库」再回来，页面被 React Router 卸载重建，
  // 这些字段照样还在。在途标志（generating / saving / loading）与
  // 弹窗开合**不**进会话：它们每次挂载重新开始才对。
  // ------------------------------------------------------------------
  // entry 只能经 switchEntry / clearWriteSession 改（两者都要连带清空现场）
  const [entry] = useWriteField("entry")
  const [sourceText, setSourceText] = useWriteField("sourceText")
  const [draft, setDraft] = useWriteField("draft")
  const [aiDraft, setAiDraft] = useWriteField("aiDraft")
  const [rewrite, setRewrite] = useWriteField("rewrite")
  const [saved, setSaved] = useWriteField("saved")
  const [saveAs, setSaveAs] = useWriteField("saveAs")
  const [personalization, setPersonalization] = useState<PersonalizationState | null>(null)

  // 指定素材里手选的素材作者（与随机素材的作者区分）
  const [author, setAuthor] = useWriteField("materialAuthor")
  const [sourceUrl, setSourceUrl] = useWriteField("sourceUrl")
  const [authorSelectorOpen, setAuthorSelectorOpen] = useState(false)

  // 参考方式：04-C 验收修复 ② —— 选了参考作者就**默认启用**其可用写作画像，
  // 不需要额外操作。这里只在用户主动取消勾选时才变成 false。
  const [useProfile, setUseProfile] = useWriteField("useProfile")
  const [refProfile, setRefProfile] = useState<AuthorWritingProfile | null>(null)
  const [profileLoading, setProfileLoading] = useState(false)

  // ---- 阶段 10：双入口 + 参考作者（素材作者与参考作者彻底分开）----
  const [sourceEntry, setSourceEntry] = useWriteField("sourceEntry")
  const [sourceAuthorId, setSourceAuthorId] = useWriteField("sourceAuthorId")
  // 素材作者的**展示名**：素材卡阶段就能拿到，确定素材后必须继续带着走，
  // 否则「跟随素材作者 / 来源」会退化成一个裸 @（B3 最终验收 #7）。
  const [sourceAuthorLabel, setSourceAuthorLabel] = useWriteField("sourceAuthorLabel")
  const [sourceContentId, setSourceContentId] = useWriteField("sourceContentId")
  const [refMode, setRefMode] = useWriteField("refMode")
  const [fixedAuthorId, setFixedAuthorId] = useWriteField("fixedAuthorId")
  const [fixedAuthor, setFixedAuthor] = useWriteField("fixedAuthor")
  const [refSelectorOpen, setRefSelectorOpen] = useState(false)
  const [candidates, setCandidates] = useWriteField("candidates")
  const [candidateResult, setCandidateResult] = useWriteField("candidateResult")
  const [adoptedKey, setAdoptedKey] = useWriteField("adoptedKey")
  const [generatingCandidates, setGeneratingCandidates] = useState(false)

  // 在途状态：不持久化 —— 回来后不该显示「正在生成…」
  //
  // loading 的初值必须与「这次到底会不会去拉取」保持一致，
  // 否则 `/write/:id` 切页回来时初值是 true、而 effect 又提前返回，
  // 就会永远卡在加载动画上（任务 06 独立验收问题 1）。
  const [loading, setLoading] = useState(() => shouldLoadSource(sourceId))
  const [generating, setGenerating] = useState(false)
  const [saving, setSaving] = useState(false)

  // 载入 personalization 状态
  useEffect(() => {
    void api
      .getPersonalization()
      .then(setPersonalization)
      .catch(() => setPersonalization(null))
  }, [])

  // 从内容页进入时预填素材。
  //
  // 任务 06 验收修复：把「打开**新的**素材 ID」与「恢复已有写作现场」分开。
  // 判据是模块级的 `handledSourceId`（**不**属于创作现场）：
  //
  //   - 第一次来到 /write/123        → 未处理过 → 拉取并预填
  //   - 切到内容库再回来（同一地址） → 已处理过 → 什么都不做，且**不能**留下加载态
  //   - 点 × 清空后（同一地址）      → 已处理过 → 不因 URL 把刚清掉的原帖填回来
  //
  // 旧实现把这个标记放在会话里，× 与切换入口会把它一起清掉，
  // 于是 effect 再次触发、刚清空的正文被填回来（问题 2）。
  useEffect(() => {
    const open = materialOpen
    /*
      这次挂载是不是一次「新的主动打开」（内容库点「改写这条」）？
      StrictMode 会把 effect 跑两遍，因此判定必须**稳定**：
      用 ref 记住「这张票据已判定为主动打开」，第二遍得到同样的结论，
      否则第二遍会走恢复分支、把第一遍的请求取消掉 → 永远预填不上。
    */
    const freshOpen =
      open != null &&
      open.id === sourceId &&
      (freshOpenRef.current?.ticket === open.ticket ||
        isFreshMaterialOpen(open.ticket))
    if (freshOpen && open) {
      freshOpenRef.current = { ticket: open.ticket }
      markMaterialOpenConsumed(open.ticket)
    }

    // 主动打开**无视**「这个 id 处理过了」—— 即使之前打开并 × 清空过同一条，
    // 也要重新载入。普通切页返回则走 shouldLoadSource（已处理 → 恢复现场）。
    if (!freshOpen && !shouldLoadSource(sourceId)) {
      // 恢复现场这条路径：**不发请求、不清现场**。把 loading 收掉是必须的 ——
      // 到这里可能是上一次挂载遗留的 true（永久转圈）。
      setLoading(false)
      return
    }
    let cancelled = false
    setLoading(true)
    const epoch = sessionEpoch()
    void api
      .getContent(sourceId as number)
      .then((data) => {
        if (cancelled || !isSameEpoch(epoch)) return
        /*
          载入成功 → **以干净的「对标改写」现场接收新素材**。

          旧实现只覆盖原帖与作者字段，于是上一条的候选、已采用状态、
          编辑稿与 rewrite 关联记录会留在屏幕上，和新素材并存。
          这里走会话模块**唯一**的清空动作：
            · 清掉 candidates / adoptedKey / draft / aiDraft / rewrite / 随机素材现场；
            · entry 直接落到 benchmark（否则原帖会被当成"主题"，路径走 topic）；
            · epoch 递增 → 上一轮所有在途请求（生成 / 采用 / 抽素材）就此作废。
          放在 `.then()` 里而不是请求前：**只有真的载入成功才清** ——
          取内容失败时不该把用户手里的现场一起丢掉。
        */
        clearWriteSession("benchmark")
        markSourceHandled(sourceId)
        setSourceContentId(sourceId)
        setSourceText(data.raw_text)
        // 素材作者（客观 metadata）：这条 benchmark content 原本是谁发的
        const aid = data.author_id ?? null
        setSourceAuthorId(aid)
        setSourceAuthorLabel(
          aid ? authorLabel(null, data.author_username ?? "") : null
        )
        setRefMode(defaultReferenceMode(aid))
        if (data.status !== "analyzed") {
          toast.info("该内容尚未分析完成，初稿质量可能受影响")
        }
      })
      .catch((err) => toast.error((err as Error).message))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [
    sourceId,
    materialOpen,
    setSourceContentId,
    setSourceText,
    setSourceAuthorId,
    setSourceAuthorLabel,
    setRefMode,
  ])

  // 参考作者 id：**由策略解析**，与素材作者完全独立。
  const referenceAuthorId = resolveReferenceAuthor(
    refMode,
    sourceAuthorId,
    fixedAuthorId
  )

  /**
   * 生成形态：从「入口 + 手里有没有输入」推导，不额外存一份状态。
   *
   *   benchmark 入口            → benchmark（文本是原帖）
   *   我的创作 + 有文本          → idea（文本是主题 / 灵感）
   *   我的创作 + 没文本          → author（只选作者，AI 自主选题）
   *
   * 这样 04-C 的五条创作路径一条不少：rewrite / rewrite_author 来自
   * benchmark；topic / topic_author 来自 create+文本；
   * author_auto 来自 create+无文本。
   */
  const hasText = hasSourceInput({ sourceText, sourceContentId })
  const genMode: GenMode =
    entry === "benchmark" ? "benchmark" : hasText ? "idea" : "author"

  /**
   * 04-C：创作输入路径。**唯一**决定点，纯函数推导。
   * 返回 null 表示当前还不满足生成条件（没有素材 / 没选参考作者）。
   *
   * 第二轮验收：`hasSourceInput()` 把 sourceContentId 也算作素材 ——
   * 只传 content_id 时服务端同样会取回原文，只看文本会漏判。
   */
  const creationPath: CreationPath | null = resolveCreationPath({
    mode: genMode,
    hasSource: hasText,
    referenceAuthorId,
    useProfile,
  })
  const canGenerateNow = canGenerate(creationPath)

  // 选定参考作者后，只**读取**其已聚合好的画像（0 AI、不触发分析）。
  // 缺哪层就显示哪层为「—」，**绝不阻塞生成**。
  const [refPerf, setRefPerf] = useState<{ eligible: number } | null>(null)
  const [refCross, setRefCross] = useState<boolean | null>(null)

  useEffect(() => {
    if (referenceAuthorId == null) {
      setRefProfile(null)
      setRefPerf(null)
      return
    }
    let cancelled = false
    setProfileLoading(true)
    void Promise.all([
      api
        .getAuthorWritingProfile(referenceAuthorId)
        .catch(() => null),
      api
        .getAuthorPerformanceProfile(referenceAuthorId)
        .catch(() => null),
    ])
      .then(([wp, pp]) => {
        if (cancelled) return
        setRefProfile(wp)
        const eligible =
          (pp as unknown as { sample_summary?: { analyzed_eligible_count?: number } })
            ?.sample_summary?.analyzed_eligible_count ?? 0
        setRefPerf({ eligible })
      })
      .finally(() => {
        if (!cancelled) setProfileLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [referenceAuthorId])

  // 跨账号规律：不需要参考作者也照样可用
  useEffect(() => {
    let cancelled = false
    void api
      .getCrossAccountInsights()
      .then((data) => {
        if (cancelled) return
        const has = (data.content_dimensions ?? []).some(
          (d) => d.common_high.length > 0 || d.common_low.length > 0
        ) || (data.writing_dimensions ?? []).some(
          (d) => d.common_high.length > 0 || d.common_low.length > 0
        )
        setRefCross(has)
      })
      .catch(() => !cancelled && setRefCross(false))
    return () => {
      cancelled = true
    }
  }, [entry])

  /**
   * 切换一级入口：清空这一轮的创作现场（走会话模块里**唯一**的清空动作，
   * 不逐条 setState —— 漏一个字段就会串味，04-C 第二轮就栽在
   * 「清了 sourceText 却漏了 sourceContentId」上）。
   */
  const switchEntry = (next: WriteEntry) => {
    if (next === entry) return
    clearWriteSession(next)
    setAuthorSelectorOpen(false)
    setRefSelectorOpen(false)
    setRefProfile(null)
  }

  /**
   * 「× 清空当前创作」。
   *
   * 与「切到别的页面再回来」是**两种不同的行为**：
   *   跳转 = 什么都不动，回来原样恢复；
   *   ×   = 主动丢弃这一轮的现场，回到当前入口的空白初始态。
   *
   * 只清内存，不动内容库里已经保存的任何东西。
   */
  const clearCurrent = () => {
    // 逐字段比对而不是列几个字段检查 —— 只抽了素材、还没写字这类状态
    // 也算「有东西」，漏判会让 × 看起来坏了。
    if (isWriteSessionPristine()) return
    resetWriteSession()
    setRefProfile(null)
    setAuthorSelectorOpen(false)
    setRefSelectorOpen(false)
    toast.success("已清空当前创作")
  }

  /** 素材确定：随机入口「就改这条」与指定入口共用后续流程。 */
  const pickMaterial = (material: Material) => {
    setSourceText(material.raw_text)
    setSourceContentId(material.content_id)
    setSourceAuthorId(material.author_id)
    setSourceAuthorLabel(
      material.author_id
        ? authorLabel(material.author_display_name ?? null, material.author_username ?? "")
        : null
    )
    setCandidates(null)
    setCandidateResult(null)
    setAdoptedKey(null)
    setDraft("")
    setAiDraft("")
    setRewrite(null)
    setSaved(false)
    // 换素材后保持参考作者策略（fixed 不变 / none 不变 / follow 跟随新素材）
    const next = referenceModeOnSourceChange(
      refMode,
      material.author_id,
      fixedAuthorId
    )
    setRefMode(next.mode)
    setFixedAuthorId(next.fixedAuthorId)
    if (next.mode !== "fixed") setFixedAuthor(null)
    // 不弹 toast：右侧/左侧已经变成「已选素材」状态，遮挡式提示是噪音
  }

  /** 生成 3 个候选：**恰好 1 次 AI**。 */
  const generateCandidates = async () => {
    // 04-C：author_auto 没有素材文本，不能直接拿 hasSource 当门槛
    if (!canGenerateNow) {
      toast.error(
        genMode === "author"
          ? "请先选择一个参考作者，由 AI 依据该作者的数据自主选题"
          : "请先确定素材"
      )
      return
    }
    // 记下代次：若响应回来前用户点了「× 清空」，这次结果整体丢弃
    const epoch = sessionEpoch()
    setGeneratingCandidates(true)
    try {
      const res = await api.generateCandidates({
        // 04-C 第二轮验收：素材字段走唯一出口，切换入口后不会残留旧 id
        ...sourceFieldsFor({ sourceText, sourceContentId }),
        reference_author_id: referenceAuthorId,
        // 04-C 验收修复 ②：三态语义（默认勾选 → 不传，让服务端用可用画像）
        use_reference_profile: useProfile ? undefined : false,
        creation_path: creationPath,
      })
      if (!isSameEpoch(epoch)) return
      setCandidates(res.candidates)
      setCandidateResult(res)
      setAdoptedKey(null)
      if (res.personalization) setPersonalization(res.personalization)
      // 候选区已经出现在屏幕上，不需要遮挡式 success toast
    } catch (err) {
      if (!isSameEpoch(epoch)) return
      toast.error((err as Error).message)
    } finally {
      if (isSameEpoch(epoch)) setGeneratingCandidates(false)
    }
  }

  /** 采用某个候选：只放进编辑器，绝不自动保存 / 自动分析。 */
  const adoptCandidate = async (candidate: RewriteCandidate) => {
    // 任务 06 验收修复 ③：采用也是异步的，同样要有代次守卫。
    // 否则「采用请求还没回来就点 × / 切入口」时，旧响应会把编辑稿、
    // ai 基线与已采用状态写回刚清空的会话里。
    const epoch = sessionEpoch()
    try {
      const res = await api.adoptCandidate({
        text: candidate.text,
        source_content_id: sourceContentId,
        model: candidateResult?.model ?? null,
      })
      if (!isSameEpoch(epoch)) return
      const row: Rewrite = {
        id: res.rewrite_id,
        benchmark_content_id: res.benchmark_content_id,
        ai_draft: res.ai_draft,
        final_text: res.ai_draft,
        is_saved_as_mine: false,
        saved_content_id: null,
        examples_used: [],
        style_profile_version: null,
        edit_distance: null,
        edit_ratio: null,
        prompt_version: null,
        personalization_state: null,
        model: candidateResult?.model ?? null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      }
      setRewrite(row)
      setDraft(candidate.text)
      setAiDraft(candidate.text)
      setAdoptedKey(candidate.key)
      setSaved(false)
      // 已进入编辑态，不需要 toast
    } catch (err) {
      if (!isSameEpoch(epoch)) return
      toast.error((err as Error).message)
    }
  }

  /**
   * 选择参考作者的**唯一**入口：只写 refMode / fixedAuthorId / fixedAuthor。
   * 走纯函数 selectReferenceAuthor，从结构上拿不到任何素材字段，
   * 因此不可能重置素材入口 / 素材 id / 正文 / 素材作者（Stage 11 P0 回归）。
   */
  const chooseReference = (
    choice:
      | { kind: "none" }
      | { kind: "follow_source" }
      | { kind: "fixed"; author: Author }
  ) => {
    const next = selectReferenceAuthor(
      { mode: refMode, fixedAuthorId },
      choice.kind === "fixed"
        ? { kind: "fixed", authorId: choice.author.id }
        : { kind: choice.kind }
    )
    setRefMode(next.mode)
    setFixedAuthorId(next.fixedAuthorId)
    setFixedAuthor(choice.kind === "fixed" ? choice.author : null)
  }

  /**
   * 切换素材入口。入口与素材来源必须**同进同出**：
   * 之前两者解耦，会出现「入口=指定素材 + 素材已清空」的孤儿态
   * （表现为空 textarea / 0 字 / 空白作者，且生成按钮不可点）。
   */
  const switchSourceEntry = (next: "specified" | "random") => {
    setSourceEntry(next)
    resetMaterial()
  }

  /** 丢弃已选素材，回到抽素材阶段（参考作者策略保持不变）。 */
  const resetMaterial = () => {
    setSourceContentId(null)
    setSourceAuthorId(null)
    setSourceAuthorLabel(null)
    setSourceText("")
    setCandidates(null)
    setCandidateResult(null)
    setAdoptedKey(null)
  }

  /** 连续生产：回到随机入口，保留素材池与参考作者策略。 */
  const continueNext = () => {
    resetMaterial()
    setDraft("")
    setAiDraft("")
    setRewrite(null)
    setSaved(false)
    setSourceEntry("random")
    const next = referenceModeOnSourceChange(refMode, null, fixedAuthorId)
    setRefMode(next.mode)
    setFixedAuthorId(next.fixedAuthorId)
  }

  const generate = useCallback(
    async (instruction?: string) => {
      if (!canGenerateNow) {
        toast.error("请先输入内容")
        return
      }
      const epoch = sessionEpoch()
      setGenerating(true)
      try {
        const res = await api.generateDraft({
          // 04-C 第二轮验收：与三候选共用同一个素材出口。
          // 旧版这里用的是 URL 带入的 sourceId —— 它**不会**随模式切换清除，
          // 于是从内容页进来后切模式，请求仍然带着旧素材 id。
          ...sourceFieldsFor({ sourceText, sourceContentId }),
          instruction,
          base_text: instruction ? draft || undefined : undefined,
          reference_author_id: referenceAuthorId,
          // 与三候选同一套三态语义：勾选（默认）→ 不传，让服务端用可用画像；
          // 取消勾选 → 显式 false = 仅记录来源
          use_reference_profile: useProfile ? undefined : false,
          // 04-C：路径由 UI 状态唯一推导，服务端只做校验
          creation_path: creationPath,
        })
        // 响应回来前若已「× 清空」，整段丢弃 —— 不把旧现场写回来
        if (!isSameEpoch(epoch)) return
        setRewrite(res)
        setDraft(res.ai_draft)
        setAiDraft(res.ai_draft)
        setSaved(false)
        if (res.personalization) setPersonalization(res.personalization)
        // 初稿已出现在编辑区，不需要遮挡式 toast
      } catch (err) {
        if (!isSameEpoch(epoch)) return
        toast.error((err as Error).message)
      } finally {
        if (isSameEpoch(epoch)) setGenerating(false)
      }
    },
    [
      sourceText,
      sourceContentId,
      draft,
      genMode,
      referenceAuthorId,
      useProfile,
      creationPath,
      canGenerateNow,
    ]
  )

  /**
   * 收录**手写**内容：0 AI 调用、不自动分析。
   *
   * 来源规则（任务 06 六）：
   *   我的创作 + 成稿 → source=mine、provenance=human_original、style_eligible=true
   *   我的创作 + 灵感 → source=mine、provenance=human_original、style_eligible=false
   *   对标改写        → source=benchmark（作者可选）
   *
   * provenance 由后端按 source 决定（mine → human_original），前端不自行编造。
   */
  const collect = async () => {
    const value = sourceText.trim()
    if (!value) {
      toast.error("请先输入内容")
      return
    }
    // 对标内容的作者是**可选**的：
    //   选了 → author_id = 正式作者 id（author_username 一起维护）
    //   不选 → author_id = NULL，这条素材进入「空白作者」素材池
    // 两种情况都**绝不创建**「空白作者 / Unknown / 未分类」这类假作者 ——
    // blank 只是一个池的口径（author_id IS NULL），不是 authors 表里的记录。
    const epoch = sessionEpoch()
    setSaving(true)
    try {
      if (entry === "benchmark") {
        await api.createContent({
          raw_text: value,
          source: "benchmark",
          style_eligible: false,
          analyze: false,
          author_id: author?.id ?? null,
          source_url: sourceUrl.trim() || undefined,
        })
        if (!isSameEpoch(epoch)) return
        toast.success("对标内容已收录")
      } else {
        // 成稿 = 认可的原创内容，参与风格学习；
        // 灵感 = 本人所写但不成文 → 不作为成熟写作风格样本。
        const asFinal = saveAs === "final"
        await api.createContent({
          raw_text: value,
          source: "mine",
          style_eligible: asFinal,
          analyze: false,
        })
        if (!isSameEpoch(epoch)) return
        toast.success(
          asFinal
            ? "成稿已收录到我的内容库，参与风格学习"
            : "灵感已收录，不参与风格学习"
        )
      }
      setSourceText("")
      setSourceUrl("")
      setAuthor(null)
    } catch (err) {
      if (!isSameEpoch(epoch)) return
      toast.error((err as Error).message)
    } finally {
      if (isSameEpoch(epoch)) setSaving(false)
    }
  }

  /** 保存最终稿：始终是我的内容 / ai_assisted，与参考作者无关。 */
  const saveFinal = async () => {
    if (!rewrite || !draft.trim()) return
    const epoch = sessionEpoch()
    setSaving(true)
    try {
      const res = await api.saveRewrite(rewrite.id, draft, true)
      if (!isSameEpoch(epoch)) return
      setRewrite(res)
      setSaved(true)
      // 保存终稿不自动分析：ANALYSIS 由用户之后显式触发
      // 底部已显示「✓ 已保存到我的内容」，不弹遮挡式 toast
    } catch (err) {
      if (!isSameEpoch(epoch)) return
      toast.error((err as Error).message)
    } finally {
      if (isSameEpoch(epoch)) setSaving(false)
    }
  }

  /**
   * 阶段 → 决定左右栏权重（B3 #11）：
   *   picking   抽素材          左 55–60% / 右 40–45%
   *   ready     素材已确定      左 40%    / 右 60%
   *   compare   三候选          左 30–35% / 右 65–70%
   *   editing   已采用编辑      左 22–30% / 右 70–78%
   */
  const stagePicking =
    entry === "benchmark" && sourceEntry === "random" && sourceContentId === null
  const stage: "picking" | "ready" | "compare" | "editing" = stagePicking
    ? "picking"
    : adoptedKey
      ? "editing"
      : candidates
        ? "compare"
        : "ready"

  const leftWidth =
    stage === "picking"
      ? "lg:grid-cols-[minmax(0,58fr)_minmax(0,42fr)]"
      : stage === "compare"
        ? "lg:grid-cols-[minmax(0,32fr)_minmax(0,68fr)]"
        : stage === "editing"
          ? "lg:grid-cols-[minmax(0,26fr)_minmax(0,74fr)]"
          : "lg:grid-cols-[minmax(0,40fr)_minmax(0,60fr)]"

  const edited = draft !== aiDraft
  const state = personalization?.state ?? "COLD_START"
  const currentHint = ENTRIES.find((m) => m.key === entry)?.hint ?? ""
  /**
   * 右侧走「候选区」还是「单稿编辑区」——按**结果**决定，不再按入口写死。
   *
   * 对标改写天然是候选流程；「我的创作」两种都可能：生成三候选后是候选区，
   * 生成单稿后是单稿编辑区。这样合并入口不会删掉任何一条生成路径。
   */
  const usesCandidates =
    entry === "benchmark" || candidates !== null || adoptedKey !== null

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin text-text-secondary" />
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      {/*
        顶栏：任务 06 删掉左上角「返回内容库」箭头 ——
        全局导航已经有这一项，页内再放一个只是重复。
      */}
      {/*
        任务 07 S6：页头走公共 PageHeader（22px/600，与「我的」等页一致）。
        个性化徽标与「× 清空」进 actions —— 位置、条件与行为都不变。
      */}
      <PageHeader
        title="写作"
        description="两条路径：我的创作 / 对标改写"
        className="border-b border-border px-6 py-3.5"
        actions={
          <>
        {personalization && (
          <Badge
            variant={state === "COLD_START" ? "outline" : "mine"}
            className="shrink-0"
            title={personalization.reason}
          >
            {STATE_LABEL[state] ?? state} · {personalization.sample_count} 篇样本
          </Badge>
        )}
        {/*
          「× 清空当前创作」——与「切走再回来」是两种明确不同的行为：
          跳转什么都不动；× 主动丢弃这一轮的现场，回到空白初始态。
          只清内存，不删内容库里任何已保存的数据。
        */}
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8 shrink-0"
          onClick={clearCurrent}
          title="清空当前创作（不影响已保存的内容）"
          aria-label="清空当前创作"
        >
          <X className="h-4 w-4" />
        </Button>
          </>
        }
      />

      {/* 一级入口：只有两个 */}
      <div className="flex flex-wrap items-center gap-3 border-b border-border px-6 py-2.5">
        <Segmented
          options={ENTRIES.map((m) => ({ key: m.key, label: m.label }))}
          value={entry}
          onChange={switchEntry}
        />
        <span className="text-[12px] text-text-tertiary">{currentHint}</span>
      </div>

      {/* 冷启动提示 */}
      {state === "COLD_START" && (
        <div className="flex items-center gap-2 border-b border-border bg-hover/60 px-6 py-2 text-[12px] text-text-secondary">
          <Info className="h-3.5 w-3.5 shrink-0" />
          <span>
            目前没有可靠的个人风格证据，AI 只提供通用高质量初稿，不会假装模仿你的文风。
            多保存几篇你确认过的内容后会自动进入学习阶段。
          </span>
        </div>
      )}

      {/*
        任务 06：「成稿收录」不再是一个独立入口 ——
        它变成「我的创作」里的一个保存选项（成稿 / 灵感），
        输入区与 AI 起稿共用同一块 textarea。
      */}
      <div className={cn("grid min-h-0 flex-1 grid-cols-1", leftWidth)}>
          {/* 左：素材 / 灵感 / 对标原文 */}
          <div className="flex min-h-0 flex-col border-b border-border lg:border-b-0 lg:border-r">
            <div className="flex items-center gap-2 px-5 py-3">
              <Badge variant={entry === "benchmark" ? "benchmark" : "mine"}>
                {entry === "benchmark" ? "对标内容" : "我的创作"}
              </Badge>
              <span className="text-[11px] text-text-secondary">
                {entry === "benchmark"
                  ? "可直接编辑"
                  : hasText
                    ? "主题 / 想法 / 手写正文 · 可直接编辑"
                    : "由 AI 自主选题"}
              </span>
            </div>

            {/* 阶段 10：对标模式内的两个素材入口（指定 / 随机） */}
            {/* 对标改写：两个素材入口（指定 / 随机） */}
            {entry === "benchmark" && (
              <div className="space-y-3 px-6 pb-3">
                {/*
                  二级：素材入口。视觉弱于一级内容类型 —— 用下划线 tab，不用 segmented。
                  **已采用后整块收起**：只留轻量来源摘要（B3 #15）。
                */}
                {!adoptedKey && (
                  <div className="flex items-center gap-1 border-b border-divider">
                    {(
                      [
                        { key: "specified", label: "指定素材" },
                        { key: "random", label: "随机素材" },
                      ] as const
                    ).map((opt) => (
                      <button
                        key={opt.key}
                        type="button"
                        onClick={() => switchSourceEntry(opt.key)}
                        className={cn(
                          "-mb-px border-b-2 px-1 pb-1.5 text-[13px] transition-colors duration-150",
                          sourceEntry === opt.key
                            ? "border-foreground font-medium text-foreground"
                            : "border-transparent text-text-secondary hover:text-foreground"
                        )}
                      >
                        {opt.label}
                      </button>
                    ))}
                  </div>
                )}

                {/* 已采用：左侧只留轻量来源摘要 */}
                {adoptedKey && (
                  <div className="space-y-1.5 py-1">
                    <p className="text-[12px] text-text-tertiary">
                      来源{" "}
                      <span className="text-text-secondary">
                        {sourceAuthorLabel ?? "空白作者"}
                        {sourceContentId ? " · 随机素材" : " · 粘贴素材"} ·{" "}
                        <span className="tabular-nums">{sourceText.trim().length}</span> 字
                      </span>
                    </p>
                    <p className="text-[12px] text-text-tertiary">
                      参考{" "}
                      <span className="text-text-secondary">
                        {referenceAuthorId
                          ? refMode === "fixed" && fixedAuthor
                            ? authorLabel(fixedAuthor.display_name, fixedAuthor.username)
                            : (sourceAuthorLabel ?? "素材作者")
                          : "不指定"}
                      </span>
                    </p>
                    {/*
                      任务 05：完整展示帖子原文 —— 去掉 line-clamp-3。
                      长帖放进可滚动阅读区，保留换行与段落。
                    */}
                    {sourceText.trim() && (
                      <div className="max-h-56 overflow-y-auto rounded border border-divider bg-canvas px-2.5 py-2">
                        <p className="whitespace-pre-wrap break-words text-[12px] leading-relaxed text-text-secondary">
                          {sourceText.trim()}
                        </p>
                      </div>
                    )}
                  </div>
                )}

                {/* 入口 A：指定素材（粘贴 / 从内容库带入） */}
                {!adoptedKey && sourceEntry === "specified" && (
                  <div className="space-y-2.5">
                    <div className="flex items-center gap-2">
                      <span className="w-14 shrink-0 text-[12px] text-text-secondary">
                        素材作者
                      </span>
                      <button
                        type="button"
                        onClick={() => setAuthorSelectorOpen(true)}
                        className="flex h-8 min-w-0 flex-1 items-center justify-between rounded border border-border px-2.5 text-left text-[12px] transition-colors hover:bg-hover"
                      >
                        <span className="truncate">
                          {author
                            ? authorLabel(author.display_name, author.username)
                            : "选择作者…"}
                        </span>
                        <span className="ml-2 shrink-0 text-[11px] text-text-tertiary">
                          {author ? "更改" : "可选"}
                        </span>
                      </button>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="w-14 shrink-0 text-[12px] text-text-secondary">
                        原链接
                      </span>
                      <Input
                        value={sourceUrl}
                        onChange={(e) => setSourceUrl(e.target.value)}
                        placeholder="https://x.com/…（可选）"
                        className="h-8 flex-1 text-[12px]"
                      />
                    </div>
                    <HelpText>
                      不选作者也可以收录：素材会进入「空白作者」池，不会创建任何「空白作者」账号。
                    </HelpText>
                  </div>
                )}

                {/* 入口 B：随机素材 —— 未确定前只做「选池 + 抽素材」 */}
                {!adoptedKey && sourceEntry === "random" && sourceContentId === null && (
                  <MaterialPicker onPick={pickMaterial} />
                )}

                {/*
                  素材已确定：必须保留素材摘要（B3 #6），
                  否则用户不知道自己正在改哪一条。
                */}
                {!adoptedKey && sourceContentId !== null && (
                  <div className="space-y-2">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="text-[12px] text-text-secondary">
                        已选素材 ·{" "}
                        <span className="text-foreground">
                          {sourceAuthorLabel ?? "空白作者"}
                        </span>{" · "}
                        <span className="tabular-nums">{sourceText.trim().length}</span> 字
                      </span>
                      <Button variant="ghost" size="sm" onClick={resetMaterial}>
                        重新抽素材
                      </Button>
                    </div>
                    {/*
                      任务 05：这里是「选定素材后的预览区域」，必须能完整阅读全文。
                      旧实现是 line-clamp-4 —— 只显示 4 行，长帖中段与结尾都读不到。
                      改成可滚动阅读区：不截断、不省略，保留换行与段落。
                    */}
                    <div className="max-h-64 overflow-y-auto rounded border border-divider bg-canvas px-3 py-2">
                      <p className="whitespace-pre-wrap break-words text-[12px] leading-relaxed text-text-secondary">
                        {sourceText.trim()}
                      </p>
                    </div>
                  </div>
                )}
              </div>
            )}

            {/*
              04-C：参考作者 —— **所有生成型模式共用**。
              旧实现只在「对标模式 + 素材已确定」时出现，导致
              「主题 + 作者」「仅参考作者自主选题」两条路径在 UI 上根本不存在，
              粘贴素材也选不了参考作者。
              抽素材阶段与已采用阶段仍然收起（保持 B3 #4 / #15）。
            */}
            {!stagePicking && !adoptedKey && (
              <div className="space-y-2 px-6 pb-3">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="w-14 shrink-0 text-[12px] text-text-secondary">
                    参考作者
                  </span>
                  {sourceAuthorId !== null && entry === "benchmark" && (
                    <button
                      type="button"
                      onClick={() => chooseReference({ kind: "follow_source" })}
                      className={cn(
                        "rounded border px-2.5 py-1 text-[12px] transition-colors",
                        refMode === "follow_source"
                          ? "border-foreground/30 bg-selected font-medium"
                          : "border-border text-text-secondary hover:text-foreground"
                      )}
                    >
                      跟随素材作者 · {sourceAuthorLabel}
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => chooseReference({ kind: "none" })}
                    className={cn(
                      "rounded border px-2.5 py-1 text-[12px] transition-colors",
                      refMode === "none"
                        ? "border-foreground/30 bg-selected font-medium"
                        : "border-border text-text-secondary hover:text-foreground"
                    )}
                  >
                    不指定
                  </button>
                  <button
                    type="button"
                    onClick={() => setRefSelectorOpen(true)}
                    className={cn(
                      "rounded border px-2.5 py-1 text-[12px] transition-colors",
                      refMode === "fixed"
                        ? "border-foreground/30 bg-selected font-medium"
                        : "border-border text-text-secondary hover:text-foreground"
                    )}
                  >
                    {refMode === "fixed" && fixedAuthor
                      ? authorLabel(fixedAuthor.display_name, fixedAuthor.username)
                      : "指定其他作者"}
                  </button>
                </div>

                {/*
                  04-C 验收修复 ②：选了参考作者就**默认使用**其可用写作画像，
                  不需要额外操作。勾选框只用于**主动排除**这一层。
                  作者没有已分析内容时服务端跳过这一层并如实记录，
                  不虚构画像、不自动调用 ANALYSIS。
                */}
                {referenceAuthorId !== null && (
                  <Checkbox
                    checked={useProfile}
                    onChange={setUseProfile}
                    className="text-[12px] text-text-secondary"
                  >
                    使用作者写作画像（默认开启；取消勾选 = 仅记录来源，仍会用流量与跨作者数据）
                  </Checkbox>
                )}

                {referenceAuthorId !== null && (
                  <p className="text-[12px] text-text-tertiary">
                    本次会使用{" "}
                    <span className="tabular-nums">
                      写作画像 {profileLoading ? "…" : contextMark(refProfile && refProfile.analyzed_sample_count > 0)} · 流量画像 {contextMark(Boolean(refPerf && refPerf.eligible > 0))} · 跨作者规律 {contextMark(refCross)}
                    </span>
                  </p>
                )}
              </div>
            )}
            {/* 我的创作且还没写东西：说清这个入口能干哪几件事 */}
            {entry === "create" && !hasText && !adoptedKey && (
              <div className="px-6 pb-3">
                <div className="rounded-lg border border-dashed border-border px-3.5 py-3">
                  <p className="text-[12px] leading-relaxed text-text-tertiary">
                    这个入口有三条路：
                    <br />· 写下<b>主题或想法</b> → 点「生成初稿」或「生成 3 个候选」
                    <br />· 什么都不写、只选<b>参考作者</b> → AI 自主选题
                    <br />· 自己<b>写完了</b> → 直接点「保存原文」
                  </p>
                </div>
              </div>
            )}

            {/* 输入区：我的创作总是可写；对标改写只在「指定素材」时可写 */}
            {!adoptedKey &&
              (entry === "create" || sourceEntry === "specified") && (
              <div className="min-h-0 flex-1 px-6">
                <textarea
                  value={sourceText}
                  onChange={(e) => setSourceText(e.target.value)}
                  placeholder={PLACEHOLDER[entry]}
                  className="h-full w-full resize-none rounded-lg border border-border bg-canvas p-4 text-sm leading-relaxed outline-none focus:border-brand"
                  spellCheck={false}
                />
              </div>
            )}

            {/*
              底部动作条（B3 #4 / #9）：
              - 抽素材阶段：不显示字数、也不显示生成按钮（右侧同样没有 CTA）
              - 素材已确定且还没有候选：这里是**唯一**的 Primary「生成 3 个候选」
              - 已有候选 / 已采用：生成入口移到候选区头部，这里不再重复
            */}
            {!stagePicking && !adoptedKey && (
              <div className="space-y-2 px-6 py-3">
                {/*
                  「保存原文」与 AI 稿是两件事：
                  - 这里保存的是**用户自己写的**原始输入 → mine / human_original；
                  - AI 稿的保存走右侧「保存为我的」→ mine / ai_assisted。
                  成稿 / 灵感只决定 style_eligible，不改变 provenance。
                */}
                {entry === "create" && !usesCandidates && (
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-[11px] text-text-tertiary">保存原文为</span>
                    <Segmented
                      size="sm"
                      options={[
                        { key: "final", label: "成稿" },
                        { key: "idea", label: "灵感" },
                      ]}
                      value={saveAs}
                      onChange={setSaveAs}
                    />
                    <span className="text-[11px] leading-relaxed text-text-tertiary">
                      {SAVE_AS_HINT[saveAs]}
                    </span>
                  </div>
                )}

                {/*
                  S1：空间不足时这里曾被压成"一列单字"。
                  根因是 flex 的默认 min-width:auto 允许文字项被挤到远小于内容宽度
                  （CJK 任意两字之间都可断行），于是逐字换行。
                  处理：容器允许换行（放不下时按钮整组换到下一行、右对齐），
                  提示占满剩余宽度（flex-1 + min-w-0）并按正常行高折行，
                  按钮组 shrink-0 保证按钮自身永不变形。
                */}
                <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1.5">
                  {/*
                    用 grow 而不是 flex-1：flex-1 的 basis 是 0%，
                    会让「提示 + 按钮」在换行计算里永远"放得下"，
                    于是 flex-wrap 永远不触发、提示还是被挤扁。
                    grow 保留 basis:auto（提示的真实宽度），
                    放不下时按钮组才会真的换到下一行。
                  */}
                  <span className="min-w-0 grow text-[12px] leading-snug tabular-nums text-text-tertiary">
                    {hasText
                      ? `${sourceText.trim().length} 字`
                      : "未输入内容 · 选参考作者可让 AI 自主选题"}
                  </span>
                  <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
                    {entry === "benchmark" && sourceEntry === "specified" && (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => void collect()}
                        disabled={saving || !sourceText.trim()}
                      >
                        {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                        仅收录
                      </Button>
                    )}
                    {entry === "create" && (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => void collect()}
                        disabled={saving || !sourceText.trim()}
                      >
                        {saving ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <Save className="h-3.5 w-3.5" />
                        )}
                        保存原文
                      </Button>
                    )}

                    {/*
                      生成入口**只在这里**（任务 06：右侧空状态里那个重复的
                      「生成初稿」已删）。
                      我的创作两条生成路径都保留：单稿 + 三候选。
                    */}
                    {entry === "create" && !usesCandidates && (
                      <Button
                        size="sm"
                        onClick={() => void generate()}
                        disabled={generating || !canGenerateNow}
                      >
                        {generating ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <Sparkles className="h-3.5 w-3.5" />
                        )}
                        生成初稿
                      </Button>
                    )}
                    {!candidates && (
                      <Button
                        size="sm"
                        variant={usesCandidates ? "default" : "outline"}
                        onClick={() => void generateCandidates()}
                        disabled={generatingCandidates || !canGenerateNow}
                      >
                        {generatingCandidates ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <Sparkles className="h-3.5 w-3.5" />
                        )}
                        生成 3 个候选
                      </Button>
                    )}
                  </div>
                </div>
              </div>
            )}
          </div>

          {usesCandidates ? (
            <div className="flex min-h-0 flex-col">
              {/*
                抽素材阶段（B3 #4）：右侧不做空状态、不做 CTA、不出现魔法棒，
                只给一句极轻的引导。
              */}
              {stagePicking ? (
                <div className="flex min-h-0 flex-1 items-center justify-center px-6">
                  <p className="text-[13px] text-text-tertiary">
                    先在左侧确定一条素材
                  </p>
                </div>
              ) : (
                <>
                  {/* 候选区头部：标题 + 重新生成（B3 #12） */}
                  <div className="flex items-center justify-between gap-2 border-b border-border px-6 py-2.5">
                    <span className="text-[13px] font-semibold">
                      {adoptedKey
                        ? `已采用 · ${candidates?.find((c) => c.key === adoptedKey)?.label ?? ""}`
                        : "3 个候选"}
                    </span>
                    <div className="flex items-center gap-2">
                      {adoptedKey && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setAdoptedKey(null)}
                        >
                          返回候选
                        </Button>
                      )}
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => void generateCandidates()}
                        disabled={generatingCandidates || !canGenerateNow}
                      >
                        {generatingCandidates ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <RefreshCw className="h-3.5 w-3.5" />
                        )}
                        重新生成
                      </Button>
                    </div>
                  </div>

                  {/* 已采用：来源 / 参考 回显（B3 #14） */}
                  {adoptedKey && (
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-border px-6 py-2 text-[12px] text-text-tertiary">
                      <span>
                        来源{" "}
                        <span className="text-text-secondary">
                          {sourceAuthorLabel ?? "空白作者"}
                          {sourceContentId ? " · 随机素材" : " · 粘贴素材"}
                        </span>
                      </span>
                      <span>
                        参考{" "}
                        <span className="text-text-secondary">
                          {referenceAuthorId
                            ? refMode === "fixed" && fixedAuthor
                              ? authorLabel(fixedAuthor.display_name, fixedAuthor.username)
                              : (sourceAuthorLabel ?? "素材作者")
                            : "不指定"}
                        </span>
                      </span>
                    </div>
                  )}

                  {/* 生成中 */}
                  {generatingCandidates && !candidates && (
                    <div className="flex min-h-0 flex-1 items-center justify-center">
                      <div className="flex items-center gap-2 text-[13px] text-text-secondary">
                        <Loader2 className="h-4 w-4 animate-spin" />
                        正在生成 3 个候选…
                      </div>
                    </div>
                  )}

                  {/* 候选空态（B3 #10）：不要魔法棒、不要按钮、不要黑色 CTA */}
                  {!candidates && !generatingCandidates && !adoptedKey && (
                    <EmptyState
                      bordered={false}
                      className="min-h-0 flex-1 px-6 py-10"
                      title="候选稿"
                      description="生成后会在这里比较：反差切入 / 结论前置 / 场景重构。三版都追求流量，只是切入角度与表达机制不同。"
                    />
                  )}

                  {/* 候选列表（未采用） */}
                  {candidates && !adoptedKey && (
                    <div className="min-h-0 flex-1 divide-y divide-divider overflow-y-auto">
                      {candidates.map((c) => (
                        <section key={c.key} className="px-6 py-4">
                          <div className="mb-2 flex items-baseline gap-2">
                            <span className="text-[13px] font-semibold">
                              {c.key} · {c.label}
                            </span>
                            <span className="text-[12px] tabular-nums text-text-tertiary">
                              {c.text.length} 字
                            </span>
                          </div>

                          <p className="whitespace-pre-wrap break-words text-[14px] leading-relaxed text-foreground">
                            {c.text}
                          </p>

                          <div className="mt-2.5 flex items-center justify-between gap-3">
                            {c.strategy_tags.length > 0 ? (
                              <p className="min-w-0 truncate text-[12px] text-text-tertiary">
                                {c.strategy_tags.slice(0, 3).join(" · ")}
                              </p>
                            ) : (
                              <span />
                            )}
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => void adoptCandidate(c)}
                            >
                              <Check className="h-3.5 w-3.5" />
                              采用
                            </Button>
                          </div>
                        </section>
                      ))}
                    </div>
                  )}

                  {/* 已采用 → 编辑态 */}
                  {adoptedKey && (
                    <>
                      <div className="min-h-0 flex-1 px-6 py-4">
                        <textarea
                          value={draft}
                          onChange={(e) => setDraft(e.target.value)}
                          className="h-full w-full resize-none rounded-lg border border-border bg-canvas p-4 text-sm leading-relaxed outline-none focus:border-brand"
                          placeholder="改到你满意为止…"
                          spellCheck={false}
                        />
                      </div>
                      <Separator />
                      <div className="flex items-center justify-between gap-2 px-6 py-3">
                        <span className="text-[12px] tabular-nums text-text-tertiary">
                          {draft.length} 字
                        </span>
                        {/* B3 #16：保存成功后不再并排一个 disabled 的保存按钮 */}
                        {saved ? (
                          <div className="flex items-center gap-3">
                            <span className="text-[12px] text-success">
                              ✓ 已保存到我的内容
                            </span>
                            <Button size="sm" onClick={continueNext}>
                              <RefreshCw className="h-3.5 w-3.5" />
                              继续下一条
                            </Button>
                          </div>
                        ) : (
                          <Button
                            size="sm"
                            onClick={() => void saveFinal()}
                            disabled={saving || !draft.trim()}
                          >
                            {saving ? (
                              <Loader2 className="h-3.5 w-3.5 animate-spin" />
                            ) : (
                              <Save className="h-3.5 w-3.5" />
                            )}
                            保存为我的
                          </Button>
                        )}
                      </div>
                    </>
                  )}
                </>
              )}
            </div>
          ) : (
            <div className="flex min-h-0 flex-col">
            <div className="flex flex-wrap items-center gap-2 px-5 py-3">
              <Badge variant="mine">初稿</Badge>
              {edited && (
                <span className="flex items-center gap-1 text-[11px] text-warning">
                  <Check className="h-3 w-3" />
                  已编辑
                </span>
              )}
              {rewrite?.edit_ratio != null && saved && (
                <span className="text-[11px] text-text-secondary">
                  修改幅度 {Math.round(rewrite.edit_ratio * 100)}%
                </span>
              )}
            </div>

            {!rewrite && !generating ? (
              /*
                空状态**不放按钮**（任务 06）：生成操作只在左侧动作条上，
                这里重复一个「生成初稿」只会让用户不确定该点哪个。
              */
              <EmptyState
                bordered={false}
                className="min-h-0 flex-1 px-6"
                icon={
                  <div className="flex h-12 w-12 items-center justify-center rounded-full border border-border">
                    <Sparkles className="h-5 w-5 text-text-secondary" />
                  </div>
                }
                title="还没有初稿"
                description="写好主题或想法后，点左侧的「生成初稿」——AI 给一版有内容的起点，你再改成自己的。"
              />
            ) : generating && !rewrite ? (
              <div className="flex min-h-0 flex-1 items-center justify-center">
                <div className="flex items-center gap-2 text-sm text-text-secondary">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  正在生成初稿…
                </div>
              </div>
            ) : (
              <>
                <div className="min-h-0 flex-1 px-5">
                  <textarea
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    className="h-full w-full resize-none rounded-lg border border-border bg-canvas p-4 text-sm leading-relaxed outline-none focus:border-muted-foreground/40"
                    placeholder="初稿会出现在这里，直接改就行…"
                    spellCheck={false}
                    disabled={generating}
                  />
                </div>

                <div className="flex flex-wrap items-center gap-1.5 px-5 pt-3">
                  {ITERATIONS.map((it) => (
                    <Button
                      key={it.label}
                      variant="outline"
                      size="sm"
                      className="h-8 text-xs"
                      disabled={generating || !draft.trim()}
                      onClick={() => void generate(it.instruction)}
                    >
                      {it.label}
                    </Button>
                  ))}
                  {edited && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-8 text-xs text-text-secondary"
                      onClick={() => setDraft(aiDraft)}
                    >
                      <Undo2 className="h-3.5 w-3.5" />
                      还原初稿
                    </Button>
                  )}
                  {rewrite && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-8 text-xs text-text-secondary"
                      disabled={generating}
                      onClick={() => void generate()}
                    >
                      <RefreshCw className={cn("h-3.5 w-3.5", generating && "animate-spin")} />
                      重新生成
                    </Button>
                  )}
                </div>

                {rewrite && rewrite.examples_used.length > 0 && (
                  <div className="px-5 pt-3.5">
                    <p className="mb-1.5 text-[11px] text-text-secondary">
                      参考了你确认过的 {rewrite.examples_used.length} 篇内容
                    </p>
                    <div className="flex gap-2 overflow-x-auto pb-1 scrollbar-none">
                      {rewrite.examples_used.map((ex) => (
                        <div
                          key={ex.id}
                          className="w-44 shrink-0 rounded border border-border bg-canvas p-2.5"
                          title={ex.excerpt}
                        >
                          <p className="line-clamp-3 text-[11px] leading-relaxed text-text-secondary">
                            {ex.excerpt}
                          </p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                <Separator className="mt-3.5" />

                <div className="flex items-center justify-between gap-2 p-4">
                  <span className="text-[11px] text-text-tertiary">
                    {draft.length} 字
                  </span>
                  <div className="flex items-center gap-2">
                    {saved && (
                      <Button variant="ghost" size="sm" className="text-xs" asChild>
                        <Link to="/library">去内容库查看</Link>
                      </Button>
                    )}
                    <Button
                      size="sm"
                      onClick={() => void saveFinal()}
                      disabled={saving || !draft.trim() || saved}
                    >
                      {saving ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <Save className="h-4 w-4" />
                      )}
                      {saved ? "已保存为我的" : "保存为我的"}
                    </Button>
                  </div>
                </div>
              </>
            )}
          </div>
        )}
      </div>

      {/* 素材作者（用于「仅收录」与 follow_source 展示） */}
      <AuthorSelector
        open={authorSelectorOpen}
        onOpenChange={setAuthorSelectorOpen}
        onSelect={(next) => {
          setAuthor(next)
          setSourceAuthorId(next.id)
          if (refMode !== "fixed") setRefMode("follow_source")
        }}
      />
      {/* 参考作者（与素材作者完全独立） */}
      <AuthorSelector
        open={refSelectorOpen}
        onOpenChange={setRefSelectorOpen}
        onSelect={(next) => chooseReference({ kind: "fixed", author: next })}
      />
    </div>
  )
}
