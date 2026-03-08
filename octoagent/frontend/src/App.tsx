/**
 * App 主组件 -- React Router 配置
 *
 * 路由：
 * - / -> TaskList
 * - /tasks/:taskId -> TaskDetail
 */

import { BrowserRouter, Routes, Route } from "react-router-dom";
import ControlPlane from "./pages/ControlPlane";
import TaskDetail from "./pages/TaskDetail";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<ControlPlane />} />
        <Route path="/tasks/:taskId" element={<TaskDetail />} />
      </Routes>
    </BrowserRouter>
  );
}
