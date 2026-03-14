# Verification Report - Feature 051

## 验证范围

- `WorkbenchLayout` 普通用户 shell 降噪
- `Home` 主叙事、真实待处理事项和切换器显隐
- `Settings` 首屏最少必要路径
- 前端构建
- 运行实例热同步与 `ready` 检查

## 自动化验证

```bash
cd /Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/octoagent/frontend
npm test -- --run src/components/shell/WorkbenchLayout.test.tsx src/domains/home/HomePage.test.tsx src/domains/settings/SettingsPage.test.tsx
npm run build
```

结果：

- `3` 个测试文件通过
- `11` 个测试通过
- `vite build` 通过

## 运行实例验证

已将以下文件同步到运行实例：

- `frontend/src/components/shell/WorkbenchLayout.tsx`
- `frontend/src/domains/home/HomePage.tsx`
- `frontend/src/domains/home/index.ts`
- `frontend/src/domains/settings/SettingsOverview.tsx`

实例路径：

- `/Users/connorlu/.octoagent/app/octoagent`

已执行：

```bash
cd /Users/connorlu/.octoagent/app/octoagent/frontend
npm run build
~/.octoagent/bin/octo restart
curl "http://127.0.0.1:8000/ready?profile=core"
```

结果：

- 前端构建通过
- `restart` 成功
- `ready` 返回 `status=ready`

## 手动预期检查点

- shell 不再显示 `待确认 / 可见 work / 记忆记录 / current records`
- 首页不再显示“背景记忆”“当前提醒”“历史累计 work”
- 首页只在有多个上下文选项时显示 Project / Workspace 切换器
- Settings 首屏先强调最少必要步骤与验证出口
