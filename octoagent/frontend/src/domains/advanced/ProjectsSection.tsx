import type { ProjectOption, WorkspaceOption } from "../../types";

interface ProjectsSectionProps {
  availableProjects: ProjectOption[];
  availableWorkspaces: WorkspaceOption[];
  currentProjectId: string;
  busyActionId: string | null;
  onSelectWorkspace: (projectId: string, workspaceId: string) => void;
  formatRelativeStatus: (value: string) => string;
}

export default function ProjectsSection({
  availableProjects,
  availableWorkspaces,
  currentProjectId,
  busyActionId,
  onSelectWorkspace,
  formatRelativeStatus,
}: ProjectsSectionProps) {
  return (
    <section className="stack-section">
      {availableProjects.map((project) => (
        <article key={project.project_id} className="panel">
          <div className="panel-head">
            <div>
              <p className="eyebrow">{project.project_id}</p>
              <h3>{project.name}</h3>
            </div>
            <span
              className={`tone-chip ${
                project.project_id === currentProjectId ? "success" : "neutral"
              }`}
            >
              {project.project_id === currentProjectId
                ? "当前"
                : formatRelativeStatus(project.status)}
            </span>
          </div>
          <p className="muted">Slug: {project.slug}</p>
          <div className="workspace-list">
            {availableWorkspaces
              .filter((workspace) => workspace.project_id === project.project_id)
              .map((workspace) => (
                <div key={workspace.workspace_id} className="workspace-card">
                  <div>
                    <strong>{workspace.name}</strong>
                    <p>{workspace.root_path || workspace.slug}</p>
                  </div>
                  <button
                    type="button"
                    className="ghost-button"
                    onClick={() =>
                      onSelectWorkspace(project.project_id, workspace.workspace_id)
                    }
                    disabled={busyActionId === "project.select"}
                  >
                    切换到 {workspace.name}
                  </button>
                </div>
              ))}
          </div>
        </article>
      ))}
    </section>
  );
}
