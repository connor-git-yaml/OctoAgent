import { lazy, Suspense, type ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import RootErrorBoundary from "./components/shell/RootErrorBoundary";
import WorkbenchLayout from "./components/shell/WorkbenchLayout";

const AgentCenter = lazy(() => import("./pages/AgentCenter"));
const ChatWorkbench = lazy(() => import("./pages/ChatWorkbench"));
const MemoryCenter = lazy(() => import("./pages/MemoryCenter"));
const McpProviderCenter = lazy(() => import("./pages/McpProviderCenter"));
const SettingsCenter = lazy(() => import("./pages/SettingsCenter"));
const SkillCenter = lazy(() => import("./pages/SkillCenter"));
const TaskDetail = lazy(() => import("./pages/TaskDetail"));

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

function withRouteSuspense(element: ReactNode) {
  return <Suspense fallback={<RouteFallback />}>{element}</Suspense>;
}

export default function App() {
  return (
    <RootErrorBoundary>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<WorkbenchLayout />}>
            <Route index element={withRouteSuspense(<ChatWorkbench />)} />
            <Route path="chat" element={<Navigate to="/" replace />} />
            <Route path="chat/:sessionId" element={withRouteSuspense(<ChatWorkbench />)} />
            <Route path="agents" element={withRouteSuspense(<AgentCenter />)} />
            <Route path="skills" element={withRouteSuspense(<SkillCenter />)} />
            <Route path="mcp" element={withRouteSuspense(<McpProviderCenter />)} />
            {/* 兼容旧路径 */}
            <Route path="agents/skills" element={<Navigate to="/skills" replace />} />
            <Route path="agents/mcp" element={<Navigate to="/mcp" replace />} />
            <Route path="memory" element={withRouteSuspense(<MemoryCenter />)} />
            <Route path="settings" element={withRouteSuspense(<SettingsCenter />)} />
          </Route>
          <Route path="/tasks/:taskId" element={withRouteSuspense(<TaskDetail />)} />
        </Routes>
      </BrowserRouter>
    </RootErrorBoundary>
  );
}
