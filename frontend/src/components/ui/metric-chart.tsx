import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"

import { formatCount } from "@/lib/mine"

/**
 * 唯一的图表组件（Recharts）。
 *
 * 刻意保持最小：
 *   - 只支持 line / bar 两种形态；
 *   - 无渐变、无阴影、无彩色堆叠 —— 与 2026 现代审美一致；
 *   - 高度由调用方给，宽度自适应（ResponsiveContainer）。
 *
 * 不手写 SVG：图表交给成熟库，避免自己实现坐标轴与刻度。
 */

export interface MetricSeries {
  /** 在数据行里取值的键 */
  key: string
  label: string
  color: string
}

/**
 * 泛型行类型：调用方保留自己具体的行结构（如 GoalChartRow），
 * 组件本身只把 data 交给 Recharts，不逐个字段访问。
 */
export interface MetricChartProps<T extends object> {
  data: T[]
  xKey: string
  series: MetricSeries[]
  kind?: "line" | "bar"
  height?: number
  /** X 轴刻度文案（如 day 0 → "第 0 天"） */
  tickFormatter?: (value: unknown) => string
}

// 只用两种克制的中性色，不做彩色图表。
const IDEAL_COLOR = "#94a3b8"
const ACTUAL_COLOR = "#0f172a"

export function MetricChart<T extends object>({
  data,
  xKey,
  series,
  kind = "line",
  height = 260,
  tickFormatter,
}: MetricChartProps<T>) {
  const axis = {
    stroke: "#cbd5e1",
    tick: { fontSize: 11, fill: "#94a3b8" },
    tickLine: false,
    axisLine: false,
  }

  const shared = (
    <>
      <CartesianGrid stroke="#f1f5f9" vertical={false} />
      <XAxis dataKey={xKey} {...axis} tickFormatter={tickFormatter} minTickGap={24} />
      <YAxis
        {...axis}
        width={56}
        tickFormatter={(v: number) => formatCount(v)}
      />
      <Tooltip
        contentStyle={{
          borderRadius: 8,
          border: "1px solid #e2e8f0",
          fontSize: 12,
          boxShadow: "0 4px 16px rgba(15,23,42,0.08)",
        }}
        labelStyle={{ color: "#64748b", fontSize: 11 }}
        formatter={(value: unknown, name: unknown) => [
          formatCount(Number(value)),
          String(name),
        ]}
      />
    </>
  )

  if (kind === "bar") {
    return (
      <div style={{ height }} className="w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
            {shared}
            {series.map((s) => (
              <Bar
                key={s.key}
                dataKey={s.key}
                name={s.label}
                fill={s.color}
                radius={[2, 2, 0, 0]}
                maxBarSize={28}
              />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
    )
  }

  return (
    <div style={{ height }} className="w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          {shared}
          {series.map((s) => (
            <Line
              key={s.key}
              type="monotone"
              dataKey={s.key}
              name={s.label}
              stroke={s.color}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 3 }}
              /* 断线而不是连线 —— 没有数据的日子不被插值补出来 */
              connectNulls={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

export const CHART_COLORS = { ideal: IDEAL_COLOR, actual: ACTUAL_COLOR }
