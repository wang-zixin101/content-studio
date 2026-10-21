import { useEffect, useState } from "react"
import { Link } from "react-router-dom"
import { Info, Loader2, RefreshCw, Sparkles } from "lucide-react"
import { toast } from "sonner"

import { PageHeader } from "@/components/common"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { api } from "@/lib/api"
import type { StyleProfile, StyleStatus } from "@/lib/types"
import { cn, formatTime } from "@/lib/utils"

// 档案字段展示配置
const ARRAY_FIELDS: [string, string][] = [
  ["common_openings", "常用开头"],
  ["common_endings", "常见结尾"],
  ["common_structures", "常见结构"],
  ["common_tones", "常用语气"],
  ["common_expressions", "高频表达"],
  ["high_freq_words", "高频词"],
  ["common_content_types", "常见内容类型"],
  ["what_he_deletes", "会删掉的表达"],
  ["disliked_expressions", "不喜欢的表达"],
]

const TEXT_FIELDS: [string, string][] = [
  ["avg_length", "平均长度"],
  ["long_short_ratio", "长短句比例"],
  ["first_person_habit", "第一人称习惯"],
  ["emotion_profile", "情绪特点"],
  ["viewpoint_style", "观点表达方式"],
]

export default function StylePage() {
  const [profile, setProfile] = useState<StyleProfile | null>(null)
  const [status, setStatus] = useState<StyleStatus | null>(null)
  const [evidence, setEvidence] = useState<{
    eligible_count: number
    human_original: number
    imported_self: number
    ai_assisted: number
    excluded_count: number
    profile_sample_count: number
    profile_invalidated_samples: number
  } | null>(null)
  const [loading, setLoading] = useState(true)
  const [computing, setComputing] = useState(false)

  const load = async () => {
    try {
      const [p, s, e] = await Promise.all([
        api.getStyleProfile(),
        api.getStyleStatus(),
        api.getStyleEvidence(),
      ])
      setProfile(p)
      setStatus(s)
      setEvidence(e)
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const recompute = async () => {
    setComputing(true)
    try {
      const p = await api.recomputeStyle()
      setProfile(p)
      await load()
      toast.success(`风格档案已更新到 v${p.version}，基于 ${p.based_on_count} 条内容`)
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setComputing(false)
    }
  }

  const count = status?.mine_count ?? 0
  const canCompute = (status?.mine_count ?? 0) >= (status?.min_required ?? 3)
  /**
   * B3 #48：真正「有可用风格样本」= 有 eligible 样本（或档案已有 based_on）。
   * 只有 mine_count 达标但一条都没分析过时，eligible = 0 —— 这时不能把
   * 「人工原创 0 / 导入旧作 0 / AI 辅助后确认 0」这类 schema 字段摆给用户。
   */
  const eligible = evidence?.eligible_count ?? profile?.based_on_count ?? 0
  const hasSample = eligible > 0
  const data = (profile?.profile_json ?? {}) as Record<string, unknown>

  return (
    <div className="mx-auto w-full max-w-4xl px-6 py-8">
      {/*
        任务 07 S3：页头走公共 PageHeader（与「我的」同一套 22px/600 + 顶部间距）。
        原手写页头旁的 Fingerprint 图标一并去掉 —— 其余已用公共页头的页面
        （我的 / 跨作者洞察 / 外部分析）都没有图标，保留它反而是不一致。
      */}
      <PageHeader
        title="我的风格档案"
        description="从「我的内容」自动归纳的个人表达特征，改写时会被引用。"
        className="mb-0"
        actions={
          /* 没有可用样本时不给 Primary CTA（B3 #49） */
          hasSample ? (
            <Button
              onClick={() => void recompute()}
              disabled={computing || !canCompute}
              size="sm"
            >
              {computing ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <RefreshCw className="h-3.5 w-3.5" />
              )}
              {profile ? "更新风格档案" : "生成风格档案"}
            </Button>
          ) : undefined
        }
      />

      {/*
        样本统计卡：原来在标题**上方**，把标题顶到页面第 146px 处；
        S3 移到标题下方（与「我的」的区块节奏一致）。
        统计项目、数据内容与说明文字原样保留。
      */}
      {evidence && hasSample && (
        <div className="mt-6 rounded-lg border border-border bg-canvas p-4">
          <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2 text-xs">
            <span className="text-sm font-medium">
              有效样本：{evidence.eligible_count}
            </span>
            <span className="text-text-secondary">
              人工原创 <span className="text-foreground/80">{evidence.human_original}</span>
            </span>
            <span className="text-text-secondary">
              导入旧作 <span className="text-foreground/80">{evidence.imported_self}</span>
            </span>
            <span className="text-text-secondary">
              AI 辅助后确认 <span className="text-foreground/80">{evidence.ai_assisted}</span>
            </span>
            <span className="text-text-secondary">
              排除 <span className="text-foreground/80">{evidence.excluded_count}</span>
            </span>
          </div>
          <p className="mt-1.5 text-[11px] leading-relaxed text-text-secondary">
            有效样本 = 属于「我的内容」且你允许参与风格学习、且已完成分析的内容。
            「排除」是「我的内容」中你关闭了风格学习的那部分。
            {evidence.profile_invalidated_samples > 0 && (
              <span className="text-warning">
                {" "}当前档案里有 {evidence.profile_invalidated_samples} 条已被排除，建议重新生成。
              </span>
            )}
          </p>
        </div>
      )}

      {/* 状态区 */}
      <div className="mt-4 flex flex-wrap items-center gap-2.5 text-xs">
        <Badge variant="outline">我的内容 {count} 条</Badge>
        {profile && (
          <>
            <Badge variant="outline">档案 v{profile.version}</Badge>
            <Badge variant="outline">基于 {profile.based_on_count} 条</Badge>
            <span className="text-text-tertiary">
              更新于 {formatTime(profile.created_at)}
            </span>
          </>
        )}
        {status?.should_recompute && (
          <span className="flex items-center gap-1 text-warning">
            <Info className="h-3.5 w-3.5" />
            有 {status.new_since} 条新内容，建议重算
          </span>
        )}
      </div>

      {/*
        B3 #48 / #49：0 可用样本时的空状态 ——
        说清「我有什么 / 缺什么 / 下一步」，不出现 schema 字段名，也不给 Primary CTA。
      */}
      {!loading && !hasSample && (
        <div className="mt-8 rounded-lg border border-dashed border-border px-6 py-12">
          <p className="text-[15px] font-semibold">暂时还没有可用的风格样本</p>
          <div className="mt-3 space-y-1 text-[13px]">
            <div className="flex items-baseline justify-between gap-4">
              <span className="text-text-tertiary">我的内容</span>
              <span className="tabular-nums text-text-secondary">{count}</span>
            </div>
            <div className="flex items-baseline justify-between gap-4">
              <span className="text-text-tertiary">可用于风格学习</span>
              <span className="tabular-nums text-text-secondary">{eligible}</span>
            </div>
          </div>
          <p className="mt-4 text-[12px] leading-relaxed text-text-secondary">
            需要先分析符合条件的「我的内容」，分析完成后会逐步形成个人表达画像。
          </p>
          <div className="mt-5">
            <Button size="sm" variant="outline" asChild>
              <Link to="/library">去内容库</Link>
            </Button>
          </div>
        </div>
      )}

      {!loading && !canCompute && hasSample && (
        <div className="mt-8 rounded-lg border border-dashed border-border px-6 py-12">
          <p className="text-[15px] font-semibold">内容还不够</p>
          <p className="mx-auto mt-2 max-w-md text-[12px] leading-relaxed text-text-secondary">
            风格档案至少需要 {status?.min_required ?? 3} 条「我的内容」，
            建议积累到 {status?.recommended ?? 10} 条以上质量更稳定。当前 {count} 条。
          </p>
          <div className="mt-5 flex gap-2">
            <Button size="sm" variant="outline" asChild>
              <Link to="/write">去写作</Link>
            </Button>
            <Button size="sm" variant="outline" asChild>
              <Link to="/import">批量导入</Link>
            </Button>
          </div>
        </div>
      )}

      {loading && (
        <div className="flex justify-center py-20">
          <Loader2 className="h-5 w-5 animate-spin text-text-secondary" />
        </div>
      )}

      {profile && (
        <div className="mt-8 space-y-5">
          {/* 核心指导 */}
          {profile.summary_text && (
            <Card className="border-muted-foreground/20 bg-canvas p-5">
              <div className="flex items-center gap-2 text-[13px] font-semibold leading-none text-text-secondary">
                <Sparkles className="h-3.5 w-3.5" />
                给改写模型的核心指导
              </div>
              <p className="mt-3 whitespace-pre-wrap text-sm leading-relaxed text-foreground/90">
                {profile.summary_text}
              </p>
            </Card>
          )}

          {/* 文本字段 */}
          <Card className="divide-y divide-border">
            {TEXT_FIELDS.filter(([key]) => data[key]).map(([key, label]) => (
              <Row key={key} label={label}>
                <span className="text-sm text-foreground/90">{String(data[key])}</span>
              </Row>
            ))}
          </Card>

          {/* 数组字段 */}
          {ARRAY_FIELDS.filter(([key]) => Array.isArray(data[key]) && (data[key] as unknown[]).length > 0).map(
            ([key, label]) => (
              <div key={key}>
                <p className="mb-2.5 text-[13px] font-semibold leading-none text-text-secondary">
                  {label}
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {(data[key] as (string | number)[]).map((v, i) => (
                    <span
                      key={`${key}-${i}`}
                      className={cn(
                        "rounded border border-border px-2 py-1 text-xs",
                        key === "high_freq_words"
                          ? "bg-accent/50 text-foreground"
                          : "text-foreground/85"
                      )}
                    >
                      {String(v)}
                    </span>
                  ))}
                </div>
              </div>
            )
          )}
        </div>
      )}
    </div>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline gap-2 px-5 py-3.5">
      <span className="w-24 shrink-0 text-xs text-text-secondary">{label}</span>
      <div className="flex-1">{children}</div>
    </div>
  )
}
