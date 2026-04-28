import { lazy, Suspense, type ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import BuildVersionWatcher from "./components/shell/BuildVersionWatcher";
import RootErrorBoundary from "./components/shell/RootErrorBoundary";
import RouteErrorBoundary from "./components/shell/RouteErrorBoundary";
import WorkbenchLayout from "./components/shell/WorkbenchLayout";

const AgentCenter = lazy(() => import("./pages/AgentCenter"));
const ChatWorkbench = lazy(() => import("./pages/ChatWorkbench"));
const MemoryCenter = lazy(() => import("./pages/MemoryCenter"));
const McpProviderCenter = lazy(() => import("./pages/McpProviderCenter"));
const SettingsCenter = lazy(() => import("./pages/SettingsCenter"));
const SkillCenter = lazy(() => import("./pages/SkillCenter"));
const TaskDetail = lazy(() => import("./pages/TaskDetail"));
const MemoryCandidates = lazy(() => import("./pages/MemoryCandidates"));
const TaskList = lazy(() => import("./pages/TaskList"));

function RouteFallback() {
  return (
    <div className="wb-route-fallback">
      <div className="wb-route-fallback-card">
        <p className="wb-kicker">OctoAgent Workbench</p>
        <img
          className="wb-route-fallback-logo"
          src="/octo-mark.svg"
          alt="OctoAgent logo"
          width={56}
          height={56}
        />
        <h1>正在切换页面</h1>
        <p>我们在加载当前页面的数据与界面骨架。</p>
      </div>
    </div>
  );
}

// Feature 079 Phase 1：lazy chunk 404 / 子树渲染异常只局限在单条 route 内，
// 不再让 RootErrorBoundary 覆盖整棵 App（shell + banner 会一起被吞）。
// ErrorBoundary 必须在 Suspense 外层，才能捕获 lazy() import 失败。
function withRouteSuspense(element: ReactNode, pageLabel?: string) {
  return (
    <RouteErrorBoundary pageLabel={pageLabel}>
      <Suspense fallback={<RouteFallback />}>{element}</Suspense>
    </RouteErrorBoundary>
  );
}

export default function App() {
  return (
    <RootErrorBoundary>
      {/* Feature 079 Phase 3：build-id 漂移检测，事前告警替代事后 chunk 404 灾难 */}
      <BuildVersionWatcher />
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<WorkbenchLayout />}>
            <Route index element={withRouteSuspense(<ChatWorkbench />, "聊天工作台")} />
            <Route path="chat" element={<Navigate to="/" replace />} />
            <Route
              path="chat/:sessionId"
              element={withRouteSuspense(<ChatWorkbench />, "聊天工作台")}
            />
            <Route path="agents" element={withRouteSuspense(<AgentCenter />, "Agent 中心")} />
            <Route path="skills" element={withRouteSuspense(<SkillCenter />, "Skill 中心")} />
            <Route path="mcp" element={withRouteSuspense(<McpProviderCenter />, "MCP 中心")} />
            {/* 兼容旧路径 */}
            <Route path="agents/skills" element={<Navigate to="/skills" replace />} />
            <Route path="agents/mcp" element={<Navigate to="/mcp" replace />} />
            <Route path="work" element={withRouteSuspense(<TaskList />, "任务列表")} />
            <Route path="memory" element={withRouteSuspense(<MemoryCenter />, "记忆中心")} />
            <Route
              path="memory/candidates"
              element={withRouteSuspense(<MemoryCandidates />, "待确认记忆")}
            />
            <Route path="settings" element={withRouteSuspense(<SettingsCenter />, "设置中心")} />
          </Route>
          <Route path="/tasks/:taskId" element={withRouteSuspense(<TaskDetail />, "任务详情")} />
        </Routes>
      </BrowserRouter>
    </RootErrorBoundary>
  );
}
