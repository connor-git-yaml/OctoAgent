export interface HomeReadinessState {
  label: string;
  tone: "danger" | "warning" | "success";
  summary: string;
}

export function computeReadinessLabel(
  setupReady: boolean,
  wizardStatus: string,
  diagnosticsStatus: string,
  pendingCount: number
): HomeReadinessState {
  if (!setupReady) {
    return {
      label: "先完成基础配置",
      tone: "danger",
      summary: "模型连接、权限或技能依赖还没准备好，先去设置页补齐。",
    };
  }
  if (wizardStatus !== "ready") {
    return {
      label: "再检查一次设置",
      tone: "warning",
      summary: "还有一些初始化步骤没完成，建议先把配置和诊断补齐。",
    };
  }
  if (diagnosticsStatus !== "ready" && diagnosticsStatus !== "ok") {
    return {
      label: "系统需要检查",
      tone: "danger",
      summary: "当前运行状态有异常，建议先查看诊断结果。",
    };
  }
  if (pendingCount > 0) {
    return {
      label: "有待你确认的事项",
      tone: "warning",
      summary: "系统已经可以使用，但还有审批或协作请求待处理。",
    };
  }
  return {
    label: "可以开始使用",
    tone: "success",
    summary: "你可以直接开始对话、查看工作进度，或继续完善配置。",
  };
}
