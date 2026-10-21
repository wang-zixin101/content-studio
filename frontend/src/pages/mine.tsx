import { useCallback, useEffect, useRef, useState } from "react"
import {
  FileUp,
  Loader2,
  Pencil,
  Plus,
  Target,
  Trash2,
} from "lucide-react"
import { toast } from "sonner"

import {
  EmptyState,
  ListSkeleton,
  PageHeader,
  SectionHeader,
  Segmented,
} from "@/components/common"
import { CHART_COLORS, MetricChart } from "@/components/ui/metric-chart"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { api } from "@/lib/api"
import {
  dailyRequiredText,
  deadlineText,
  differenceText,
  formatCount,
  formatPercent,
  goalDialogCopy,
  goalDialogMode,
  goalFixedInfo,
  goalFormValues,
  GOAL_STATUS_LABEL,
  toAccountChartRows,
  toGoalChartRows,
  type GoalDialogMode,
} from "@/lib/mine"
import {
  MINE_METRICS,
  type GoalState,
  type MineMetricKey,
  type QualifiedSnapshot,
  type AccountMetric,
} from "@/lib/types"

/**
 * 「我的」V1。
 *
 * 三块内容，各自独立、互不耦合：
 *   1. 90 天目标          —— 合格浏览量总量达到 500,000
 *   2. 合格浏览量记录     —— 用户手动录入的账本
 *   3. 账号表现           —— Account Overview CSV 导入 + 指标趋势
 *
 * 全程 0 AI：没有一处会调用模型。
 *
 * 刻意的产品边界：**合格浏览量 ≠ X 后台的 Impressions**。
 * 两者不互相推算，界面也不暗示它们等价（见区块 2 的说明文案）。
 */

const STATUS_VARIANT = {
  active: "info",
  achieved: "success",
  expired: "warning",
} as const

function todayISO(): string {
  const d = new Date()
  const m = String(d.getMonth() + 1).padStart(2, "0")
  const day = String(d.getDate()).padStart(2, "0")
  return `${d.getFullYear()}-${m}-${day}`
}

export default function MinePage() {
  // ---- 目标 + 快照 ----
  const [goal, setGoal] = useState<GoalState | null>(null)
  const [loading, setLoading] = useState(true)

  // null = 对话框关闭；"create" / "edit" 共用一个 Dialog
  const [goalMode, setGoalMode] = useState<GoalDialogMode | null>(null)
  const [goalDate, setGoalDate] = useState(todayISO())
  const [goalBaseline, setGoalBaseline] = useState("0")
  const [goalSaving, setGoalSaving] = useState(false)

  const [snapDate, setSnapDate] = useState(todayISO())
  const [snapValue, setSnapValue] = useState("")
  const [snapSaving, setSnapSaving] = useState(false)

  const [editing, setEditing] = useState<QualifiedSnapshot | null>(null)
  const [editDate, setEditDate] = useState("")
  const [editValue, setEditValue] = useState("")

  // ---- 账号表现 ----
  const [metricKey, setMetricKey] = useState<MineMetricKey>("impressions")
  const [chartKind, setChartKind] = useState<"line" | "bar">("line")
  const [metric, setMetric] = useState<AccountMetric | null>(null)
  const [metricLoading, setMetricLoading] = useState(true)
  const [importing, setImporting] = useState(false)
  const fileRef = useRef<File | null>(null)

  const refreshGoal = useCallback(async () => {
    try {
      setGoal(await api.getMineGoal())
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  const refreshMetric = useCallback(async () => {
    setMetricLoading(true)
    try {
      setMetric(await api.getAccountMetric(metricKey))
    } catch (err) {
      toast.error((err as Error).message)
      setMetric(null)
    } finally {
      setMetricLoading(false)
    }
  }, [metricKey])

  useEffect(() => {
    void refreshGoal()
  }, [refreshGoal])

  useEffect(() => {
    void refreshMetric()
  }, [refreshMetric])

  // ---------------------------------------------------------------- 目标

  /**
   * 打开目标对话框。
   * 回填当前值 —— 编辑时看到的就是库里已有的那一条，不是空白表单。
   */
  const openGoalDialog = (mode: GoalDialogMode) => {
    const values = goalFormValues(goal, todayISO())
    setGoalDate(values.startDate)
    setGoalBaseline(values.baseline)
    setGoalMode(mode)
  }

  /**
   * 建立 / 编辑走同一个提交函数：
   *   create → POST，只有库里没目标时才可能走到
   *   edit   → PATCH，就地修正，不新建、不留历史
   * TARGET / DURATION 是固定规则，两种模式都不发送、也不可编辑。
   */
  const submitGoal = async () => {
    const mode = goalMode
    if (!mode) return
    const baseline = Number(goalBaseline)
    if (!goalDate || Number.isNaN(baseline) || baseline < 0) {
      toast.error("请填写开始日期与有效的基线值")
      return
    }
    const copy = goalDialogCopy(mode)
    setGoalSaving(true)
    try {
      const payload = {
        start_date: goalDate,
        baseline_qualified_impressions: Math.round(baseline),
      }
      setGoal(
        mode === "edit"
          ? await api.updateMineGoal(payload)
          : await api.createMineGoal(payload)
      )
      setGoalMode(null)
      toast.success(copy.success)
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setGoalSaving(false)
    }
  }

  // ---------------------------------------------------------------- 快照

  const addSnapshot = async () => {
    const value = Number(snapValue)
    if (!snapDate || snapValue === "" || Number.isNaN(value) || value < 0) {
      toast.error("请填写日期与合格的浏览量数值")
      return
    }
    setSnapSaving(true)
    try {
      await api.addMineSnapshot({
        recorded_at: snapDate,
        qualified_impressions: Math.round(value),
      })
      // 新快照会改变目标进度，必须重新读目标，不能拿返回的快照当目标用。
      await refreshGoal()
      setSnapValue("")
      toast.success("已记录")
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setSnapSaving(false)
    }
  }

  const openEdit = (snap: QualifiedSnapshot) => {
    setEditing(snap)
    setEditDate(snap.recorded_at.slice(0, 10))
    setEditValue(String(snap.qualified_impressions))
  }

  const submitEdit = async () => {
    if (!editing) return
    const value = Number(editValue)
    if (!editDate || Number.isNaN(value) || value < 0) {
      toast.error("请填写有效的日期与数值")
      return
    }
    try {
      await api.editMineSnapshot(editing.id, {
        recorded_at: editDate,
        qualified_impressions: Math.round(value),
      })
      setEditing(null)
      await refreshGoal()
      toast.success("已更新")
    } catch (err) {
      toast.error((err as Error).message)
    }
  }

  const removeSnapshot = async (snap: QualifiedSnapshot) => {
    try {
      await api.deleteMineSnapshot(snap.id)
      await refreshGoal()
      toast.success("已删除")
    } catch (err) {
      toast.error((err as Error).message)
    }
  }

  // ---------------------------------------------------------------- CSV 导入

  const runImport = async (file: File) => {
    setImporting(true)
    try {
      const res = await api.importAccountMetrics(file)
      toast.success(
        `已导入 ${res.input_rows} 行（新增 ${res.inserted}，覆盖 ${res.updated}）`
      )
      await refreshMetric()
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setImporting(false)
    }
  }

  // ---------------------------------------------------------------- 渲染

  const snapshots = goal?.snapshots ?? []
  const goalRows = toGoalChartRows(goal?.chart)
  const accountRows = toAccountChartRows(metric?.items)
  const diff = differenceText(metric?.difference)
  // 关着对话框时按 create 取文案即可 —— 内容根本没挂载，看不到
  const goalCopy = goalDialogCopy(goalMode ?? "create")

  return (
    <div className="mx-auto w-full max-w-4xl px-6 py-8">
      <PageHeader
        title="我的"
        description="目标进度、合格浏览量账本，以及账号表现的原始趋势。"
      />

      {/* ============================ 区块 1：目标 ============================ */}
      <section className="mt-8">
        <SectionHeader
          title="90 天目标"
          hint={
            goal
              ? `合格浏览量 ${formatCount(goal.target)}`
              : "合格浏览量 500,000"
          }
          actions={
            goalDialogMode(goal) === "edit" ? (
              // 低强调入口：目标已存在时，只留下「编辑」，不再出现「建立」。
              <Button
                variant="ghost"
                size="sm"
                onClick={() => openGoalDialog("edit")}
              >
                <Pencil className="h-3.5 w-3.5" />
                编辑目标
              </Button>
            ) : null
          }
        />

        {loading ? (
          <div className="mt-4">
            <ListSkeleton rows={3} />
          </div>
        ) : !goal?.exists ? (
          <EmptyState
            className="mt-4"
            title="还没有目标"
            description="设定一个开始日期与当前基线，之后只靠手动记录推进进度。"
            action={
              <Button size="sm" onClick={() => openGoalDialog("create")}>
                <Target className="h-3.5 w-3.5" />
                建立目标
              </Button>
            }
          />
        ) : (
          <div className="mt-4 space-y-4 rounded-lg border border-border px-4 py-4">
            {/* 进度条 */}
            <div className="space-y-2">
              <div className="flex items-baseline justify-between gap-3">
                <div className="flex items-baseline gap-2">
                  <span className="text-[22px] font-semibold tabular-nums tracking-tight">
                    {formatCount(goal.current)}
                  </span>
                  <span className="text-[13px] text-text-secondary tabular-nums">
                    / {formatCount(goal.target)}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <Badge variant={STATUS_VARIANT[goal.status ?? "active"]}>
                    {GOAL_STATUS_LABEL[goal.status ?? "active"]}
                  </Badge>
                  <span className="text-[12px] text-text-secondary tabular-nums">
                    {formatPercent(goal.progress)}
                  </span>
                </div>
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-hover">
                <div
                  className="h-full rounded-full bg-foreground transition-[width] duration-300"
                  style={{ width: `${Math.round((goal.progress ?? 0) * 100)}%` }}
                />
              </div>
            </div>

            {/* 关键数字 */}
            <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2 border-t border-divider pt-3 text-[12px]">
              <span className="text-text-tertiary">
                基线{" "}
                <span className="tabular-nums text-text-secondary">
                  {formatCount(goal.baseline)}
                </span>
              </span>
              <span className="text-text-tertiary">
                已获得{" "}
                <span className="tabular-nums text-text-secondary">
                  {formatCount(goal.gained)}
                </span>
              </span>
              <span className="text-text-tertiary">
                还差{" "}
                <span className="tabular-nums text-text-secondary">
                  {formatCount(goal.remaining)}
                </span>
              </span>
              <span className="text-text-tertiary">
                {deadlineText(goal.status, goal.remaining_days)}
              </span>
              {dailyRequiredText(goal.current_daily_required) && (
                <span className="text-text-tertiary">
                  {dailyRequiredText(goal.current_daily_required)}
                </span>
              )}
            </div>

            {/* 目标图：line only，两条序列 */}
            {goalRows.length > 0 && (
              <div className="border-t border-divider pt-3">
                <MetricChart
                  data={goalRows}
                  xKey="day"
                  kind="line"
                  height={240}
                  tickFormatter={(v) => `第 ${v} 天`}
                  series={[
                    { key: "ideal", label: "理想推进", color: CHART_COLORS.ideal },
                    { key: "actual", label: "实际记录", color: CHART_COLORS.actual },
                  ]}
                />
                <p className="mt-2 text-[11px] leading-relaxed text-text-tertiary">
                  灰线是 90 天均匀推进的理想线，仅作参照；深色线只连接你真实录入的记录点，
                  没有记录的日子不做估算。
                </p>
              </div>
            )}
          </div>
        )}
      </section>

      {/* ============================ 区块 2：合格浏览量 ============================ */}
      <section className="mt-10">
        <SectionHeader
          title="合格浏览量记录"
          hint="手动录入"
        />

        {loading ? (
          <div className="mt-4">
            <ListSkeleton rows={3} />
          </div>
        ) : (
          <div className="mt-4 space-y-3 rounded-lg border border-border px-4 py-4">
            <p className="text-[12px] leading-relaxed text-text-tertiary">
              合格浏览量是你自己定义的口径，手动记录。它与下面账号表现里的
              Impressions 不是同一个指标，系统不会互相推算。
            </p>

            {/* 录入行 */}
            <div className="flex flex-wrap items-end gap-2">
              <div className="space-y-1">
                <label className="block text-[11px] text-text-secondary">日期</label>
                <Input
                  type="date"
                  value={snapDate}
                  onChange={(e) => setSnapDate(e.target.value)}
                  className="w-[150px]"
                />
              </div>
              <div className="space-y-1">
                <label className="block text-[11px] text-text-secondary">
                  合格浏览量
                </label>
                <Input
                  type="number"
                  min={0}
                  placeholder="0"
                  value={snapValue}
                  onChange={(e) => setSnapValue(e.target.value)}
                  className="w-[150px]"
                />
              </div>
              <Button size="sm" onClick={() => void addSnapshot()} disabled={snapSaving}>
                {snapSaving ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Plus className="h-3.5 w-3.5" />
                )}
                记录
              </Button>
            </div>

            {/* 历史：div 行式，不新建表格组件 */}
            {snapshots.length === 0 ? (
              <p className="border-t border-divider pt-3 text-[12px] text-text-tertiary">
                还没有记录。录入第一条后，目标进度会立刻开始计算。
              </p>
            ) : (
              <div className="divide-y divide-divider border-t border-divider">
                {snapshots.map((s) => (
                  <div
                    key={s.id}
                    className="flex items-center gap-3 py-2 text-[13px]"
                  >
                    <span className="w-[92px] shrink-0 tabular-nums text-text-secondary">
                      {s.recorded_at.slice(0, 10)}
                    </span>
                    <span className="min-w-0 flex-1 tabular-nums font-medium">
                      {formatCount(s.qualified_impressions)}
                    </span>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7"
                      onClick={() => openEdit(s)}
                      aria-label="编辑"
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7"
                      onClick={() => void removeSnapshot(s)}
                      aria-label="删除"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </section>

      {/* ============================ 区块 3：账号表现 ============================ */}
      <section className="mt-10">
        <SectionHeader
          title="账号表现"
          hint="Account Overview CSV"
          actions={
            <>
              <input
                type="file"
                accept=".csv,text/csv"
                className="hidden"
                id="mine-account-csv"
                onChange={(e) => {
                  const file = e.target.files?.[0] ?? null
                  fileRef.current = file
                  if (file) void runImport(file)
                  e.target.value = ""
                }}
              />
              <Button variant="outline" size="sm" asChild disabled={importing}>
                <label
                  htmlFor="mine-account-csv"
                  className={importing ? "pointer-events-none cursor-default" : "cursor-pointer"}
                >
                  {importing ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <FileUp className="h-3.5 w-3.5" />
                  )}
                  导入 CSV
                </label>
              </Button>
            </>
          }
        />

        <div className="mt-4 space-y-4 rounded-lg border border-border px-4 py-4">
          <p className="text-[12px] leading-relaxed text-text-tertiary">
            导入 X 后台导出的 Account Overview CSV（日粒度）。同一天重复导入会
            <span className="text-text-secondary">整行覆盖</span>
            ，不是累加；任何一行格式错误都会
            <span className="text-text-secondary">整份回滚</span>
            ，不会留下半成品。
          </p>

          {metricLoading ? (
            <ListSkeleton rows={3} />
          ) : !metric || metric.items.length === 0 ? (
            <EmptyState
              bordered={false}
              className="py-10"
              title="还没有账号指标"
              description="点右上角「导入 CSV」上传一份 Account Overview CSV，即可看到任意指标的日趋势。"
            />
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
                <Select
                  value={metricKey}
                  onValueChange={(v) => setMetricKey(v as MineMetricKey)}
                >
                  <SelectTrigger className="w-[190px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {MINE_METRICS.map((m) => (
                      <SelectItem key={m.key} value={m.key}>
                        {m.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>

                <Segmented
                  size="sm"
                  value={chartKind}
                  onChange={setChartKind}
                  options={[
                    { key: "line", label: "折线" },
                    { key: "bar", label: "柱状" },
                  ]}
                />

                <div className="ml-auto flex items-baseline gap-4 text-[12px]">
                  <span className="text-text-tertiary">
                    最新{" "}
                    <span className="tabular-nums font-medium text-foreground">
                      {formatCount(metric.latest?.value)}
                    </span>
                  </span>
                  {metric.previous && (
                    <span className="text-text-tertiary">
                      上次{" "}
                      <span className="tabular-nums text-text-secondary">
                        {formatCount(metric.previous.value)}
                      </span>
                    </span>
                  )}
                  {diff && (
                    <span className="tabular-nums text-text-secondary">{diff}</span>
                  )}
                </div>
              </div>

              <MetricChart
                data={accountRows}
                xKey="date"
                kind={chartKind}
                height={240}
                series={[
                  {
                    key: "value",
                    label:
                      MINE_METRICS.find((m) => m.key === metricKey)?.label ??
                      metricKey,
                    color: CHART_COLORS.actual,
                  },
                ]}
              />

              <p className="text-[11px] text-text-tertiary">
                共 {metric.items.length} 天（{metric.items[0]?.metric_date} ~{" "}
                {metric.items[metric.items.length - 1]?.metric_date}）。
                空白单元格表示那天没有采集到，图中不显示。
              </p>
            </>
          )}
        </div>
      </section>

      {/* ==================== 建立 / 编辑目标（共用一个 Dialog） ==================== */}
      <Dialog
        open={goalMode !== null}
        onOpenChange={(o) => !o && setGoalMode(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{goalCopy.title}</DialogTitle>
            <DialogDescription>{goalCopy.description}</DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <div className="space-y-1">
              <label className="block text-[12px] text-text-secondary">开始日期</label>
              <Input
                type="date"
                value={goalDate}
                onChange={(e) => setGoalDate(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <label className="block text-[12px] text-text-secondary">
                {goalMode === "edit" ? "起始有效曝光" : "当前合格浏览量（基线）"}
              </label>
              <Input
                type="number"
                min={0}
                value={goalBaseline}
                onChange={(e) => setGoalBaseline(e.target.value)}
              />
            </div>

            {/* 固定规则：只读文本，刻意不是输入框 —— 500,000 与 90 天改不了 */}
            <div className="flex items-baseline gap-x-6 border-t border-divider pt-3 text-[12px]">
              {goalFixedInfo().map((item) => (
                <span key={item.label} className="text-text-tertiary">
                  {item.label}{" "}
                  <span className="tabular-nums text-text-secondary">
                    {item.value}
                  </span>
                </span>
              ))}
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" size="sm" onClick={() => setGoalMode(null)}>
              取消
            </Button>
            <Button size="sm" onClick={() => void submitGoal()} disabled={goalSaving}>
              {goalSaving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {goalCopy.submit}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ============================ 编辑记录 ============================ */}
      <Dialog open={editing !== null} onOpenChange={(o) => !o && setEditing(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>编辑记录</DialogTitle>
            <DialogDescription>修改这条记录的日期或数值。</DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <div className="space-y-1">
              <label className="block text-[12px] text-text-secondary">日期</label>
              <Input
                type="date"
                value={editDate}
                onChange={(e) => setEditDate(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <label className="block text-[12px] text-text-secondary">
                合格浏览量
              </label>
              <Input
                type="number"
                min={0}
                value={editValue}
                onChange={(e) => setEditValue(e.target.value)}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" size="sm" onClick={() => setEditing(null)}>
              取消
            </Button>
            <Button size="sm" onClick={() => void submitEdit()}>
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
