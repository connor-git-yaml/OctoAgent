import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import type { SkillDetail, SkillItem } from "../types";
import SkillCenter from "./SkillCenter";

const skillApi = vi.hoisted(() => ({
  fetchSkills: vi.fn(),
  fetchSkillDetail: vi.fn(),
  installSkill: vi.fn(),
  uninstallSkill: vi.fn(),
}));

vi.mock("../api/f149/adapters", () => ({
  fetchF149Skills: skillApi.fetchSkills,
  fetchF149SkillDetail: skillApi.fetchSkillDetail,
  installF149Skill: skillApi.installSkill,
  uninstallF149Skill: skillApi.uninstallSkill,
}));

const BUILTIN_SKILL: SkillItem = {
  name: "meeting-notes",
  description: "把会议内容整理成结构化纪要",
  version: "1.0.0",
  author: "OctoAgent",
  tags: ["会议"],
  source: "builtin",
};

const USER_SKILL: SkillItem = {
  name: "weekly-report",
  description: "按团队模板生成周报",
  version: "2.0.0",
  author: "团队",
  tags: ["周报"],
  source: "user",
};

const USER_DETAIL: SkillDetail = {
  ...USER_SKILL,
  trigger_patterns: ["写周报", "weekly report"],
  tools_required: ["read_tasks", "write_artifact"],
  content: "# Weekly Report\n<script>unsafe()</script>",
};

function renderPage() {
  return render(
    <MemoryRouter>
      <SkillCenter />
    </MemoryRouter>,
  );
}

function defaultList() {
  skillApi.fetchSkills.mockResolvedValue({
    items: [USER_SKILL, BUILTIN_SKILL],
    total: 2,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  defaultList();
  skillApi.fetchSkillDetail.mockResolvedValue(USER_DETAIL);
  skillApi.installSkill.mockResolvedValue({
    name: "custom-skill",
    source: "user",
    source_path: "skills/custom-skill/SKILL.md",
    message: "installed",
  });
  skillApi.uninstallSkill.mockResolvedValue({
    name: USER_SKILL.name,
    message: "deleted",
  });
});

describe("SkillCenter v2", () => {
  it("先显示加载态，再展示技能卡和来源计数", async () => {
    let resolveList:
      ((value: { items: SkillItem[]; total: number }) => void) | undefined;
    skillApi.fetchSkills.mockReturnValue(
      new Promise((resolve) => {
        resolveList = resolve;
      }),
    );

    renderPage();
    expect(screen.getByText(/正在加载/)).toBeInTheDocument();

    resolveList?.({ items: [USER_SKILL, BUILTIN_SKILL], total: 2 });
    await waitFor(() =>
      expect(screen.getByText("weekly-report")).toBeInTheDocument(),
    );
    expect(screen.getByText("共 2 个")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /查看详情/ })).toHaveLength(2);
  });

  it("空态保留可见安装入口", async () => {
    skillApi.fetchSkills.mockResolvedValue({ items: [], total: 0 });
    renderPage();

    await waitFor(() =>
      expect(screen.getByText("还没有安装技能")).toBeInTheDocument(),
    );
    expect(
      screen.getByRole("button", { name: "安装 Skill" }),
    ).toBeInTheDocument();
  });

  it("列表失败使用用户语言并可重试，不显示原始错误", async () => {
    skillApi.fetchSkills
      .mockRejectedValueOnce(new Error("sqlite skill_registry exploded"))
      .mockResolvedValueOnce({ items: [USER_SKILL], total: 1 });
    const user = userEvent.setup();
    renderPage();

    await waitFor(() =>
      expect(screen.getByText("技能列表加载失败")).toBeInTheDocument(),
    );
    expect(
      screen.queryByText(/sqlite skill_registry exploded/),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() =>
      expect(screen.getByText("weekly-report")).toBeInTheDocument(),
    );
    expect(skillApi.fetchSkills).toHaveBeenCalledTimes(2);
  });

  it("origin 403 归页面资源权限，不提供重新登录动作", async () => {
    skillApi.fetchSkills.mockRejectedValue(
      new ApiError("upstream details", { status: 403 }),
    );
    renderPage();

    await waitFor(() =>
      expect(screen.getByText("当前账号没有权限管理技能")).toBeInTheDocument(),
    );
    expect(screen.queryByText("upstream details")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /重新登录/ }),
    ).not.toBeInTheDocument();
  });

  it("详情普通区不暴露技术正文，Advanced 展开后净化显示并归还焦点", async () => {
    const user = userEvent.setup();
    renderPage();
    const detailTrigger = await screen.findByRole("button", {
      name: /weekly-report.*查看详情/,
    });
    await user.click(detailTrigger);

    const dialog = await screen.findByRole("dialog", {
      name: "weekly-report",
    });
    expect(
      within(dialog).getByText(USER_DETAIL.description),
    ).toBeInTheDocument();
    expect(within(dialog).queryByText("写周报")).not.toBeInTheDocument();
    expect(within(dialog).queryByText("read_tasks")).not.toBeInTheDocument();
    expect(within(dialog).queryByText(/Weekly Report/)).not.toBeInTheDocument();

    await user.click(
      within(dialog).getByRole("button", { name: "高级 · 技术正文" }),
    );
    expect(within(dialog).getByText("写周报")).toBeInTheDocument();
    expect(within(dialog).getByText("read_tasks")).toBeInTheDocument();
    expect(within(dialog).getByText(/# Weekly Report/)).toBeInTheDocument();
    expect(dialog.querySelector("script")).toBeNull();

    await user.click(within(dialog).getByRole("button", { name: "关闭" }));
    await waitFor(() => expect(detailTrigger).toHaveFocus());
  });

  it("安装只提交文件事实，后端校验冲突留在对话框供用户重试", async () => {
    skillApi.installSkill.mockRejectedValue(
      new ApiError("技能名称已存在，请换一个名称", { status: 409 }),
    );
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("weekly-report");
    await user.click(screen.getByRole("button", { name: "安装 Skill" }));

    const dialog = screen.getByRole("dialog", { name: "安装 Skill" });
    const file = new File(
      ["---\nname: custom-skill\ndescription: 自定义技能\n---\n# Custom Skill"],
      "SKILL.md",
      { type: "text/markdown" },
    );
    await user.upload(within(dialog).getByLabelText("选择 SKILL.md"), file);
    await waitFor(() =>
      expect(within(dialog).getByText("custom-skill")).toBeInTheDocument(),
    );
    await user.click(within(dialog).getByRole("button", { name: "安装" }));

    await waitFor(() =>
      expect(skillApi.installSkill).toHaveBeenCalledWith(
        "custom-skill",
        expect.stringContaining("# Custom Skill"),
      ),
    );
    expect(
      await within(dialog).findByText("技能名称已存在，请换一个名称"),
    ).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "重试" })).toBeEnabled();
  });

  it("用户技能卸载使用页面内二次确认并刷新列表", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(
      await screen.findByRole("button", {
        name: /weekly-report.*卸载/,
      }),
    );

    const dialog = screen.getByRole("dialog", { name: "确认卸载技能" });
    expect(within(dialog).getByText(/weekly-report/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "确认卸载" }));

    await waitFor(() =>
      expect(skillApi.uninstallSkill).toHaveBeenCalledWith("weekly-report"),
    );
    expect(skillApi.fetchSkills).toHaveBeenCalledTimes(2);
  });

  it("页面移除旧 wb 视觉类和内联样式，视觉只由同页 CSS 与 --cp-* 驱动", async () => {
    const { container } = renderPage();
    await screen.findByText("weekly-report");

    expect(container.querySelector("[class*='wb-']")).toBeNull();
    expect(container.querySelector("[style]")).toBeNull();
    expect(container.querySelector(".f149-skills-page")).toBeInTheDocument();
  });
});
