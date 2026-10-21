import { NavLink, Outlet } from "react-router-dom"
import {
  Fingerprint,
  FlaskConical,
  History,
  Library,
  Network,
  PenLine,
  SquarePen,
  TriangleAlert,
  Upload,
  User,
  Users,
} from "lucide-react"

import { useConfig } from "@/components/config-provider"
import { cn } from "@/lib/utils"

/**
 * 导航按**日常工作流**排列（B3 §7）：
 *   我的 → 写 → 看 → 研究作者 → 跨账号规律 → 自己的风格
 * 「我的」是主入口第一项：先看自己的进度，再进入工作内容。
 * 导入是周期性动作，放到第二组，不与高频路径抢位置。
 */
const NAV_MAIN = [
  { to: "/mine", label: "我的", icon: User, end: false },
  // 原「快速输入」已合并进写作页（/ → /write 重定向），不再保留独立入口
  { to: "/write", label: "写作", icon: SquarePen, end: false },
  { to: "/library", label: "内容库", icon: Library, end: false },
  { to: "/authors", label: "作者", icon: Users, end: false },
  { to: "/insights", label: "跨作者洞察", icon: Network, end: false },
  { to: "/style", label: "风格档案", icon: Fingerprint, end: false },
]

const NAV_DATA = [
  { to: "/import", label: "批量导入", icon: Upload, end: false },
  { to: "/external-analysis", label: "外部分析", icon: FlaskConical, end: false },
  { to: "/imports", label: "导入记录", icon: History, end: false },
]

const NAV = [...NAV_MAIN, ...NAV_DATA]

/** 侧栏项：图标与文字尺寸统一，selected 靠背景 + 字重，不用彩色竖条。 */
function NavItem({ item }: { item: (typeof NAV)[number] }) {
  return (
    <NavLink
      to={item.to}
      end={item.end}
      className={({ isActive }) =>
        cn(
          "flex items-center gap-2.5 rounded px-2.5 py-[7px] text-[13px] transition-colors duration-150",
          isActive
            ? "bg-selected font-medium text-foreground"
            : "text-text-secondary hover:bg-hover hover:text-foreground"
        )
      }
    >
      <item.icon className="h-4 w-4 shrink-0" />
      <span className="truncate">{item.label}</span>
    </NavLink>
  )
}

export function AppLayout() {
  const { config } = useConfig()

  return (
    <div className="flex h-screen w-full overflow-hidden bg-canvas">
      {/* 侧边栏：实色背景，靠明度差与主区分层，不用边框描边 */}
      <aside className="hidden w-[216px] shrink-0 flex-col border-r border-divider bg-sidebar md:flex">
        <div className="flex h-[52px] items-center gap-2.5 px-4">
          <div className="flex h-[22px] w-[22px] items-center justify-center rounded bg-foreground text-background">
            <PenLine className="h-3 w-3" />
          </div>
          <span className="text-[13px] font-medium tracking-tight">内容工作台</span>
        </div>

        <nav className="flex flex-1 flex-col gap-px px-2 py-2">
          {NAV_MAIN.map((item) => (
            <NavItem key={item.to} item={item} />
          ))}
          <div className="mt-4 px-2.5 pb-1 text-[11px] text-text-tertiary">数据</div>
          {NAV_DATA.map((item) => (
            <NavItem key={item.to} item={item} />
          ))}
        </nav>

        <div className="border-t border-divider px-4 py-3">
          <div className="flex items-center gap-2 text-[11px] text-text-secondary">
            <span
              className={cn(
                "h-1.5 w-1.5 rounded-full",
                config?.ai_configured ? "bg-success" : "bg-warning"
              )}
            />
            <span>
              {config?.ai_configured ? "生成服务已连接" : "生成服务未配置"}
            </span>
          </div>
          {config && (
            <p className="mt-1 truncate font-mono text-[11px] text-text-tertiary">
              {config.models.analysis}
            </p>
          )}
        </div>
      </aside>

      {/* 主区 */}
      <div className="flex min-w-0 flex-1 flex-col bg-surface">
        {/* 移动端导航 */}
        <div className="flex h-[52px] items-center gap-0.5 overflow-x-auto border-b border-divider px-2 md:hidden">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                cn(
                  "flex shrink-0 items-center gap-1.5 rounded px-2.5 py-1.5 text-xs transition-colors",
                  isActive
                    ? "bg-selected font-medium text-foreground"
                    : "text-text-secondary"
                )
              }
            >
              <item.icon className="h-3.5 w-3.5" />
              {item.label}
            </NavLink>
          ))}
        </div>

        {config && !config.ai_configured && (
          <div className="flex items-center gap-2 border-b border-warning/20 bg-warning/[0.07] px-4 py-2 text-xs text-warning">
            <TriangleAlert className="h-3.5 w-3.5 shrink-0" />
            <span>
              AI 密钥未配置：内容可以正常保存，但分析与改写会失败。请在
              <code className="mx-1 rounded-sm bg-warning/10 px-1 py-0.5 font-mono">
                backend/.env
              </code>
              中填入 MiMo API Key 后重启后端。
            </span>
          </div>
        )}

        <main className="min-h-0 flex-1 overflow-y-auto bg-surface">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
