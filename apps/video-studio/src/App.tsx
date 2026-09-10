import { Toaster } from '@/components/ui/toaster'
import { Toaster as Sonner } from '@/components/ui/sonner'
import { TooltipProvider } from '@/components/ui/tooltip'
import { ThemeProvider } from 'next-themes'
import { HashRouter, Routes, Route } from 'react-router-dom'
import { LanguageProvider } from './i18n/LanguageContext'
import { LanguageRoute } from './components/LanguageRoute'
import SelectionHub from './components/SelectionHub'
import NotFound from './pages/NotFound'
import DeepAgentWorkspacePage from './pages/DeepAgentWorkspacePage'
import IncrementalVideoWorkspacePage from './pages/IncrementalVideoWorkspacePage'
import StudioWorkspacePage from './pages/StudioWorkspacePage'
const App = () => {
  // HashRouter: /#/en/create or /#/zh/create
  // 优点：刷新任何页面都不会 404，无需后端 SPA fallback 支持
  // 语言通过URL路径设置：/en/... 或 /zh/...
  return (
    <ThemeProvider attribute="class" defaultTheme="dark" forcedTheme="dark" enableSystem={false}>
      <TooltipProvider delayDuration={150}>
        <Toaster />
        <Sonner />
        <HashRouter
          future={{
            v7_startTransition: true,
            v7_relativeSplatPath: true,
          }}
        >
          <LanguageProvider>
            <Routes>
              {/* 根路径直接渲染，语言从 localStorage 读取，默认 en */}
              <Route path="/" element={<SelectionHub />} />

              {/* 所有路由都包含语言前缀 /:lang */}
              <Route path="/:lang/*" element={
                <LanguageRoute>
                  <Routes>
                    <Route path="/" element={<SelectionHub />} />
                    <Route path="/create" element={<DeepAgentWorkspacePage />} />
                    <Route path="/create/:threadId" element={<DeepAgentWorkspacePage />} />
                    <Route path="/deep-agent-v2" element={<DeepAgentWorkspacePage />} />
                    <Route path="/deep-agent-v2/:threadId" element={<DeepAgentWorkspacePage />} />
                    <Route path="/studio" element={<StudioWorkspacePage />} />
                    <Route path="/studio/:threadId" element={<StudioWorkspacePage />} />
                    <Route path="/video" element={<IncrementalVideoWorkspacePage />} />
                    <Route path="/video/:projectId" element={<IncrementalVideoWorkspacePage />} />
                    {/* ADD ALL CUSTOM ROUTES ABOVE THE CATCH-ALL "*" ROUTE */}
                    <Route path="*" element={<NotFound />} />
                  </Routes>
                </LanguageRoute>
              } />
            </Routes>
          </LanguageProvider>
        </HashRouter>
      </TooltipProvider>
    </ThemeProvider>
  )
}

export default App
