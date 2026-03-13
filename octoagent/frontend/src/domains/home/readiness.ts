export interface HomeReadinessState {
  label: string;
  tone: "danger" | "warning" | "success";
  summary: string;
  primaryActionLabel: string;
  primaryActionTo: string;
}

interface ComputeHomeReadinessOptions {
  usingEchoMode: boolean;
  setupReady: boolean;
  wizardStatus: string;
  diagnosticsStatus: string;
  pendingCount: number;
  activeWorkCount: number;
}

export function computeReadinessLabel({
  usingEchoMode,
  setupReady,
  wizardStatus,
  diagnosticsStatus,
  pendingCount,
  activeWorkCount,
}: ComputeHomeReadinessOptions): HomeReadinessState {
  if (usingEchoMode) {
    return {
      label: "先连接真实模型",
      tone: "warning",
      summary: "现在还是体验模式。连好模型后，OctoAgent 才能稳定查实时信息、委派专门角色并给出真实结果。",
      primaryActionLabel: "打开设置",
      primaryActionTo: "/settings",
    };
  }

  if (!setupReady) {
    return {
      label: "还差最后几项配置",
      tone: "danger",
      summary: "基础连接还没完全通过。先把阻塞项补齐，再开始聊天会更稳。",
      primaryActionLabel: "继续完成设置",
      primaryActionTo: "/settings",
    };
  }

  if (wizardStatus !== "ready") {
    return {
      label: "先做一次启动检查",
      tone: "warning",
      summary: "主要配置已经齐了，但首次检查还没走完。先确认一遍，再开始会更顺。",
      primaryActionLabel: "回到设置检查",
      primaryActionTo: "/settings",
    };
  }

  if (pendingCount > 0) {
    return {
      label: "先处理待确认事项",
      tone: "warning",
      summary: `现在有 ${pendingCount} 项待你确认。先看一眼再继续，会少很多来回折返。`,
      primaryActionLabel: "查看待处理工作",
      primaryActionTo: "/work",
    };
  }

  if (diagnosticsStatus !== "ready" && diagnosticsStatus !== "ok") {
    return {
      label: "系统能继续用，但建议先检查一下",
      tone: "warning",
      summary: "当前运行环境有降级或异常。基础聊天还能继续，但实时能力和外部连接可能受影响。",
      primaryActionLabel: "查看诊断",
      primaryActionTo: "/advanced",
    };
  }

  if (activeWorkCount > 0) {
    return {
      label: "系统正在替你处理事情",
      tone: "success",
      summary: `当前有 ${activeWorkCount} 项任务还在进行中。你可以继续聊天，也可以先去看进度。`,
      primaryActionLabel: "查看当前工作",
      primaryActionTo: "/work",
    };
  }

  return {
    label: "现在可以直接开始聊天",
    tone: "success",
    summary: "模型、项目和工作区都已经准备好。发第一条消息，就能直接体验 Butler 和 Worker 的协作。",
    primaryActionLabel: "进入聊天",
    primaryActionTo: "/chat",
  };
}
