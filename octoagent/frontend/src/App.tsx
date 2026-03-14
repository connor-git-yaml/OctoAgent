import { lazy, Suspense, type ReactNode } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import RootErrorBoundary from "./components/shell/RootErrorBoundary";
import WorkbenchLayout from "./components/shell/WorkbenchLayout";

const AdvancedControlPlane = lazy(() => import("./pages/AdvancedControlPlane"));
const AgentCenter = lazy(() => import("./pages/AgentCenter"));
const ChatWorkbench = lazy(() => import("./pages/ChatWorkbench"));
const Home = lazy(() => import("./pages/Home"));
const MemoryCenter = lazy(() => import("./pages/MemoryCenter"));
const McpProviderCenter = lazy(() => import("./pages/McpProviderCenter"));
const SettingsCenter = lazy(() => import("./pages/SettingsCenter"));
const SkillProviderCenter = lazy(() => import("./pages/SkillProviderCenter"));
const TaskDetail = lazy(() => import("./pages/TaskDetail"));
const WorkbenchBoard = lazy(() => import("./pages/WorkbenchBoard"));

function RouteFallback() {
  return (
    <div className="wb-route-fallback">
      <div className="wb-route-fallback-card">
        <p className="wb-kicker">OctoAgent Workbench</p>
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
            <Route index element={withRouteSuspense(<Home />)} />
            <Route path="chat" element={withRouteSuspense(<ChatWorkbench />)} />
            <Route path="agents" element={withRouteSuspense(<AgentCenter />)} />
            <Route
              path="agents/skills"
              element={withRouteSuspense(<SkillProviderCenter />)}
            />
            <Route
              path="agents/mcp"
              element={withRouteSuspense(<McpProviderCenter />)}
            />
            <Route path="work" element={withRouteSuspense(<WorkbenchBoard />)} />
            <Route path="memory" element={withRouteSuspense(<MemoryCenter />)} />
            <Route path="settings" element={withRouteSuspense(<SettingsCenter />)} />
            <Route
              path="advanced"
              element={withRouteSuspense(<AdvancedControlPlane />)}
            />
          </Route>
          <Route path="/tasks/:taskId" element={withRouteSuspense(<TaskDetail />)} />
        </Routes>
      </BrowserRouter>
    </RootErrorBoundary>
  );
}
