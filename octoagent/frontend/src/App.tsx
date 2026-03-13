import { BrowserRouter, Routes, Route } from "react-router-dom";
import WorkbenchLayout from "./components/shell/WorkbenchLayout";
import AdvancedControlPlane from "./pages/AdvancedControlPlane";
import AgentCenter from "./pages/AgentCenter";
import ChatWorkbench from "./pages/ChatWorkbench";
import Home from "./pages/Home";
import MemoryCenter from "./pages/MemoryCenter";
import McpProviderCenter from "./pages/McpProviderCenter";
import SettingsCenter from "./pages/SettingsCenter";
import SkillProviderCenter from "./pages/SkillProviderCenter";
import TaskDetail from "./pages/TaskDetail";
import WorkbenchBoard from "./pages/WorkbenchBoard";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<WorkbenchLayout />}>
          <Route index element={<Home />} />
          <Route path="chat" element={<ChatWorkbench />} />
          <Route path="agents" element={<AgentCenter />} />
          <Route path="work" element={<WorkbenchBoard />} />
          <Route path="memory" element={<MemoryCenter />} />
          <Route path="settings" element={<SettingsCenter />} />
          <Route path="settings/skills" element={<SkillProviderCenter />} />
          <Route path="settings/mcp" element={<McpProviderCenter />} />
          <Route path="advanced" element={<AdvancedControlPlane />} />
        </Route>
        <Route path="/tasks/:taskId" element={<TaskDetail />} />
      </Routes>
    </BrowserRouter>
  );
}
