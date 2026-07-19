import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
// F148 Web v2：Figtree 变体字体 + remixicon 图标（自托管，Vite 打包不外链）
import "@fontsource-variable/figtree";
import "remixicon/fonts/remixicon.css";
import "./styles/tokens.css";
import "./styles/primitives.css";
import "./styles/shell.css";
import "./styles/workbench-ui.css";
import "./index.css";
// v2 主题层与工作台布局——末位导入，覆盖 index.css 深色媒体块
import "./styles/theme-v2.css";
import "./styles/workbench-v2.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
