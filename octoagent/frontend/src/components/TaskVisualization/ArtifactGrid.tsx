/**
 * ArtifactGrid -- Artifacts 网格
 *
 * CSS grid 布局，每个 artifact card 显示文件类型图标（基于 mime/扩展名）、
 * 名称和友好大小。无 artifacts 时不渲染。
 */

import type { Artifact } from "../../types";
import { formatFileSize } from "../../utils/phaseClassifier";

interface ArtifactGridProps {
  artifacts: Artifact[];
}

/** 根据 mime 类型或文件扩展名返回 Unicode 图标 */
function getFileIcon(artifact: Artifact): string {
  // 优先检查 parts 中的 mime
  const mime = artifact.parts?.[0]?.mime ?? "";
  const name = artifact.name.toLowerCase();

  if (mime.startsWith("image/") || /\.(png|jpg|jpeg|gif|svg|webp)$/.test(name)) {
    return "\u{1F5BC}\uFE0F"; // 画框 - 图片
  }
  if (mime === "application/json" || name.endsWith(".json")) {
    return "\u{1F4CB}"; // 剪贴板 - JSON
  }
  if (mime === "application/pdf" || name.endsWith(".pdf")) {
    return "\u{1F4D1}"; // 书签标签 - PDF
  }
  if (mime.startsWith("text/") || /\.(txt|md|csv|log|yaml|yml|toml)$/.test(name)) {
    return "\u{1F4C4}"; // 文件 - 文本
  }
  if (/\.(zip|tar|gz|bz2|7z|rar)$/.test(name)) {
    return "\u{1F4E6}"; // 包裹 - 压缩包
  }
  if (/\.(py|ts|tsx|js|jsx|rs|go|java|cpp|c|h|sh)$/.test(name)) {
    return "\u{1F4BB}"; // 电脑 - 代码
  }
  return "\u{1F4CE}"; // 回形针 - 通用文件
}

export default function ArtifactGrid({ artifacts }: ArtifactGridProps) {
  if (artifacts.length === 0) return null;

  return (
    <div className="tv-artifact-section">
      <div className="tv-artifact-section-title">
        产出文件 ({artifacts.length})
      </div>
      <div className="tv-artifact-grid">
        {artifacts.map((artifact) => (
          <div key={artifact.artifact_id} className="tv-artifact-card">
            <div className="tv-artifact-icon">
              {getFileIcon(artifact)}
            </div>
            <div className="tv-artifact-info">
              <div className="tv-artifact-name" title={artifact.name}>
                {artifact.name}
              </div>
              <div className="tv-artifact-size">
                {formatFileSize(artifact.size)}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
