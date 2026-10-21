import { Navigate, Route, Routes } from "react-router-dom"
import { Toaster } from "sonner"

import { AppLayout } from "@/components/layout/app-layout"
import { ConfigProvider } from "@/components/config-provider"
import { TooltipProvider } from "@/components/ui/tooltip"
import AuthorDetailPage from "@/pages/author-detail"
import AuthorsPage from "@/pages/authors"
import ImportPage from "@/pages/import"
import ExternalAnalysisPage from "@/pages/external-analysis"
import ImportsPage from "@/pages/imports"
import InsightsPage from "@/pages/insights"
import LibraryPage from "@/pages/library"
import MinePage from "@/pages/mine"
import StylePage from "@/pages/style"
import WritePage from "@/pages/write"

export default function App() {
  return (
    <TooltipProvider delayDuration={200}>
      <ConfigProvider>
        <Routes>
          <Route element={<AppLayout />}>
            {/* 旧「快速输入」入口已合并进写作页：/ 只做跳转，不保留第二套逻辑 */}
            <Route index element={<Navigate to="/write" replace />} />
            {/* 「我的」必须注册在 * 之前，否则会被兜底重定向吃掉 */}
            <Route path="mine" element={<MinePage />} />
            <Route path="write" element={<WritePage />} />
            <Route path="write/:id" element={<WritePage />} />
            <Route path="library" element={<LibraryPage />} />
            <Route path="import" element={<ImportPage />} />
            <Route path="imports" element={<ImportsPage />} />
            <Route path="external-analysis" element={<ExternalAnalysisPage />} />
            {/* 作者管理页：全部正式 authors；作者主页：单个作者的内容与概览 */}
            <Route path="authors" element={<AuthorsPage />} />
            <Route path="authors/:id" element={<AuthorDetailPage />} />
            {/* 跨作者洞察：本质跨作者，因此是独立页面而不是某个作者主页的标签 */}
            <Route path="insights" element={<InsightsPage />} />
            <Route path="style" element={<StylePage />} />
            <Route path="*" element={<Navigate to="/write" replace />} />
          </Route>
        </Routes>
        <Toaster
          theme="light"
          position="bottom-right"
          toastOptions={{
            className:
              "border-border bg-surface text-foreground shadow-popover rounded-lg",
          }}
        />
      </ConfigProvider>
    </TooltipProvider>
  )
}
