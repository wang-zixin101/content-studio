import { describe, it } from "node:test"
import assert from "node:assert"

import {
  dailyRequiredText,
  deadlineText,
  differenceText,
  formatCount,
  formatPercent,
  GOAL_FIXED_DURATION_DAYS,
  GOAL_FIXED_TARGET,
  GOAL_STATUS_LABEL,
  goalDialogCopy,
  goalDialogMode,
  goalFixedInfo,
  goalFormValues,
  toAccountChartRows,
  toGoalChartRows,
} from "./mine.ts"
import type { GoalChart, GoalState } from "./types.ts"

/**
 * 「我的」V1 纯计算层测试。
 *
 * 图表数据算错在页面上是看不出来的（线照样画得出来），
 * 所以这部分必须直接对**生产代码**做断言。
 */

// ---------------------------------------------------------------- formatCount

describe("formatCount", () => {
  it("null / undefined → —", () => {
    assert.equal(formatCount(null), "—")
    assert.equal(formatCount(undefined), "—")
  })

  it("小于 1 万：保留千分位，不写成 0.8 万", () => {
    assert.equal(formatCount(0), "0")
    assert.equal(formatCount(999), "999")
    assert.equal(formatCount(8000), "8,000")
    assert.equal(formatCount(9999), "9,999")
  })

  it("1 万边界：10000 → 1 万（不是 1.0 万）", () => {
    assert.equal(formatCount(10000), "1 万")
    assert.equal(formatCount(12345), "1.2 万")
    assert.equal(formatCount(99999), "10 万")
  })

  it("1 亿边界", () => {
    assert.equal(formatCount(100000000), "1 亿")
    assert.equal(formatCount(123456789), "1.2 亿")
  })

  it("目标值 500000 → 50 万", () => {
    assert.equal(formatCount(500000), "50 万")
  })

  it("负数保留符号", () => {
    assert.equal(formatCount(-12345), "-1.2 万")
  })
})

describe("formatPercent", () => {
  it("0.0834 → 8.3%", () => {
    assert.equal(formatPercent(0.0834), "8.3%")
  })

  it("0 → 0.0%，1 → 100.0%", () => {
    assert.equal(formatPercent(0), "0.0%")
    assert.equal(formatPercent(1), "100.0%")
  })

  it("null → —", () => {
    assert.equal(formatPercent(null), "—")
  })
})

// ---------------------------------------------------------------- 目标文案

describe("GOAL_STATUS_LABEL", () => {
  it("三档状态都有中文文案", () => {
    assert.equal(GOAL_STATUS_LABEL.active, "进行中")
    assert.equal(GOAL_STATUS_LABEL.achieved, "已达成")
    assert.equal(GOAL_STATUS_LABEL.expired, "已过期")
  })
})

describe("deadlineText", () => {
  it("已达成优先于剩余天数", () => {
    assert.equal(deadlineText("achieved", 0), "已达成")
    assert.equal(deadlineText("achieved", 30), "已达成")
  })

  it("已过期：即使 remaining_days 是 0 也不能说「今天截止」", () => {
    assert.equal(deadlineText("expired", 0), "已过期")
  })

  it("active 且 remaining_days = 0 → 今天截止（还没过期）", () => {
    assert.equal(deadlineText("active", 0), "今天截止")
  })

  it("active 且还有天数 → 还剩 N 天", () => {
    assert.equal(deadlineText("active", 90), "还剩 90 天")
    assert.equal(deadlineText("active", 1), "还剩 1 天")
  })

  it("无状态 / 无天数 → 空串", () => {
    assert.equal(deadlineText(null, null), "")
    assert.equal(deadlineText("active", null), "")
  })
})

describe("dailyRequiredText", () => {
  it("null → null（剩余 0 天时绝不补一个没有意义的数字）", () => {
    assert.equal(dailyRequiredText(null), null)
  })

  it("有值 → 日均需 X", () => {
    assert.equal(dailyRequiredText(5423), "接下来日均需 5,423")
    assert.equal(dailyRequiredText(60000), "接下来日均需 6 万")
  })

  it("0 → 仍然显示（说明已无需再推进）", () => {
    assert.equal(dailyRequiredText(0), "接下来日均需 0")
  })
})

// ---------------------------------------------------------------- 目标对话框

const goalState = (over: Partial<GoalState> = {}): GoalState =>
  ({
    exists: false,
    goal: null,
    target: GOAL_FIXED_TARGET,
    duration_days: GOAL_FIXED_DURATION_DAYS,
    baseline: null,
    current: null,
    gained: null,
    remaining: null,
    progress: null,
    status: null,
    elapsed_days: null,
    remaining_days: null,
    current_daily_required: null,
    original_daily_required: null,
    planned_today: null,
    ahead_behind: null,
    snapshots: [],
    chart: { line: [], snapshots: [] },
    ...over,
  }) as GoalState

/** 正式库里真实存在的那一条（验收时用户自己建的）。 */
const PROD_GOAL = goalState({
  exists: true,
  goal: {
    id: 1,
    start_date: "2026-10-07",
    baseline_qualified_impressions: 4700,
    created_at: "2026-10-07T14:31:35+08:00",
  },
  baseline: 4700,
  current: 4700,
})

describe("目标固定规则", () => {
  it("500,000 / 90 天是常量，不是从接口读来的可变量", () => {
    assert.equal(GOAL_FIXED_TARGET, 500000)
    assert.equal(GOAL_FIXED_DURATION_DAYS, 90)
  })
})

describe("goalDialogMode", () => {
  it("没有目标 → create", () => {
    assert.equal(goalDialogMode(goalState()), "create")
  })

  it("已有目标 → edit（入口从「建立」变成「编辑目标」）", () => {
    assert.equal(goalDialogMode(PROD_GOAL), "edit")
  })

  it("首屏还没加载完（null / undefined）→ create，不会闪出「编辑」", () => {
    assert.equal(goalDialogMode(null), "create")
    assert.equal(goalDialogMode(undefined), "create")
  })
})

describe("goalDialogCopy", () => {
  it("edit：标题「编辑目标」，提交「保存」，成功「目标已更新」", () => {
    const copy = goalDialogCopy("edit")
    assert.equal(copy.title, "编辑目标")
    assert.equal(copy.submit, "保存")
    assert.equal(copy.success, "目标已更新")
    assert.ok(copy.description.includes("计划参数"), "要说清这是可修正的计划参数")
  })

  it("create：保留原来的建立文案", () => {
    const copy = goalDialogCopy("create")
    assert.equal(copy.title, "建立 90 天目标")
    assert.equal(copy.submit, "建立")
    assert.equal(copy.success, "目标已建立")
  })
})

describe("goalFormValues", () => {
  it("没有目标 → 今天 / 0，不留空表单", () => {
    assert.deepEqual(goalFormValues(goalState(), "2026-10-07"), {
      startDate: "2026-10-07",
      baseline: "0",
    })
  })

  it("有目标 → 回填库里当前值（2026-10-07 / 4700）", () => {
    assert.deepEqual(goalFormValues(PROD_GOAL, "2026-11-01"), {
      startDate: "2026-10-07",
      baseline: "4700",
    })
  })

  it("start_date 带时间后缀时只取日期部分", () => {
    const g = goalState({
      exists: true,
      goal: {
        id: 1,
        start_date: "2026-10-07T14:31:35+08:00",
        baseline_qualified_impressions: 1,
        created_at: "2026-10-07T14:31:35+08:00",
      },
      baseline: 1,
    })
    assert.equal(goalFormValues(g, "2026-11-01").startDate, "2026-10-07")
  })

  it("baseline 为 null 时退回 0，不出现 NaN", () => {
    const g = goalState({ exists: true, goal: {
      id: 1,
      start_date: "2026-10-07",
      baseline_qualified_impressions: 0,
      created_at: "2026-10-07T00:00:00+08:00",
    }, baseline: null })
    assert.equal(goalFormValues(g, "2026-11-01").baseline, "0")
  })

  it("只有两个键：target / duration 不会变成可编辑字段", () => {
    assert.deepEqual(Object.keys(goalFormValues(PROD_GOAL, "2026-11-01")).sort(), [
      "baseline",
      "startDate",
    ])
  })
})

describe("goalFixedInfo", () => {
  it("只有两项：目标 500,000、周期 90 天", () => {
    const info = goalFixedInfo()
    assert.equal(info.length, 2)
    assert.deepEqual(info.map((i) => i.label), ["目标", "周期"])
    // 用完整数字，和对话框说明里的「500,000」保持同一写法
    assert.deepEqual(info.map((i) => i.value), ["500,000", "90 天"])
  })

  it("是只读文本条目（只有 label / value），没有给输入框留位置", () => {
    for (const item of goalFixedInfo()) {
      assert.deepEqual(Object.keys(item).sort(), ["label", "value"])
    }
  })
})

// ---------------------------------------------------------------- 图表数据

const chart = (line: [number, number][], snaps: [number, number][]): GoalChart => ({
  line: line.map(([day, value]) => ({ day, date: `d${day}`, value })),
  snapshots: snaps.map(([day, value]) => ({ day, date: `d${day}`, value })),
})

describe("toGoalChartRows", () => {
  it("空 / null → []", () => {
    assert.deepEqual(toGoalChartRows(null), [])
    assert.deepEqual(toGoalChartRows(undefined), [])
    assert.deepEqual(toGoalChartRows({ line: [], snapshots: [] }), [])
  })

  it("没有快照的日子 actual 为 null —— 不做插值", () => {
    const rows = toGoalChartRows(chart([[0, 100], [1, 200], [2, 300]], [[0, 100]]))
    assert.deepEqual(rows, [
      { day: 0, date: "d0", ideal: 100, actual: 100 },
      { day: 1, date: "", ideal: 200, actual: null },
      { day: 2, date: "", ideal: 300, actual: null },
    ])
  })

  it("两组数据合并后按 day 升序去重", () => {
    const rows = toGoalChartRows(
      chart([[0, 0], [1, 10], [2, 20]], [[2, 25], [1, 15]])
    )
    assert.deepEqual(rows.map((r) => r.day), [0, 1, 2])
    assert.deepEqual(rows.map((r) => r.actual), [null, 15, 25])
  })

  it("快照落在 0~90 之外时保留真实 day（不裁剪、不外推）", () => {
    const rows = toGoalChartRows(chart([[0, 0], [90, 500000]], [[-3, 50], [95, 600000]]))
    assert.deepEqual(rows.map((r) => r.day), [-3, 0, 90, 95])
    assert.deepEqual(rows.map((r) => r.ideal), [null, 0, 500000, null])
  })

  it("快照点的日期会被带出来（供 tooltip 用）", () => {
    const rows = toGoalChartRows(chart([[0, 0]], [[0, 10]]))
    assert.equal(rows[0].date, "d0")
  })

  it("理想线 91 个点时行数正确", () => {
    const line = Array.from({ length: 91 }, (_, d) => [d, d] as [number, number])
    const rows = toGoalChartRows(chart(line, []))
    assert.equal(rows.length, 91)
    assert.equal(rows[0].day, 0)
    assert.equal(rows[90].day, 90)
  })

  it("baseline >= TARGET：目标线恒为 500000，不出现先高后降的线", () => {
    // 后端 build_plan_line 在 baseline >= target 时返回 91 个 500000。
    // 这里守的是前端合并后的结果：ideal 序列必须单调不降且不超过 500000。
    const line = Array.from(
      { length: 91 },
      (_, d) => [d, 500000] as [number, number]
    )
    const rows = toGoalChartRows(chart(line, [[0, 520000]]))
    assert.equal(rows.length, 91)
    for (const r of rows) {
      assert.equal(r.ideal, 500000)
      assert.ok(r.ideal! <= 500000, "目标线不得高于 TARGET")
    }
    for (let i = 1; i < rows.length; i++) {
      assert.ok(
        rows[i].ideal! >= rows[i - 1].ideal!,
        `目标线在第 ${i} 点下降了 —— 那意味着要求用户把浏览量做回去`
      )
    }
  })
})

describe("toAccountChartRows", () => {
  it("空 / null → []", () => {
    assert.deepEqual(toAccountChartRows(null), [])
    assert.deepEqual(toAccountChartRows([]), [])
  })

  it("原样映射日期与数值", () => {
    assert.deepEqual(
      toAccountChartRows([
        { metric_date: "2026-10-01", value: 100 },
        { metric_date: "2026-10-02", value: 200 },
      ]),
      [
        { date: "2026-10-01", value: 100 },
        { date: "2026-10-02", value: 200 },
      ]
    )
  })
})

// ---------------------------------------------------------------- 差值

describe("differenceText", () => {
  it("正差带 +，负差用 −（U+2212），零无符号", () => {
    assert.equal(differenceText(100), "+100")
    assert.equal(differenceText(-100), "−100")
    assert.equal(differenceText(0), "0")
  })

  it("大数走紧凑格式", () => {
    assert.equal(differenceText(12000), "+1.2 万")
  })

  it("null → null（只有一天数据时没有差值）", () => {
    assert.equal(differenceText(null), null)
  })
})
