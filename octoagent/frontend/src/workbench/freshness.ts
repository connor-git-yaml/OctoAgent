import type {
  BundledToolDefinition,
  CapabilityPackDocument,
  ContextContinuityDocument,
  WorkProjectionItem,
  WorkerCapabilityProfile,
} from "../types";

const WORKER_TYPE_LABELS: Record<string, string> = {
  general: "Butler",
  ops: "Ops Worker",
  research: "Research Worker",
  dev: "Dev Worker",
};

const TOOL_PROFILE_LABELS: Record<string, string> = {
  minimal: "最小工具面",
  standard: "标准工具面",
  privileged: "扩展工具面",
};

const FRESHNESS_TOOL_CONFIG = [
  {
    name: "runtime.now",
    label: "当前时间",
    readySummary: "可以读取当前本地日期、时间、星期与 timezone 事实。",
    missingSummary: "当前还没有暴露 `runtime.now`，今天/本地时间只能靠上下文猜测。",
  },
  {
    name: "web.search",
    label: "网页搜索",
    readySummary: "可以查找最新网页资料与公开信息来源。",
    missingSummary: "当前没有可用的网页搜索能力，最新资料只能退化处理。",
  },
  {
    name: "browser.status",
    label: "网页浏览",
    readySummary: "可以在受治理前提下打开页面、继续导航和查看状态。",
    missingSummary: "当前没有可用的 browser 路径，官网/页面操作会受限。",
  },
] as const;

const FRESHNESS_INTENT_TOKENS = [
  "天气",
  "今天",
  "最新",
  "官网",
  "官方",
  "网页",
  "网站",
  "站点",
  "公告",
  "browser",
  "navigate",
  "latest",
  "today",
  "weather",
  "website",
  "official",
  "announcement",
  "news",
] as const;

export interface FreshnessToolState {
  label: string;
  statusLabel: string;
  tone: "success" | "warning" | "danger";
  summary: string;
}

export interface FreshnessReadiness {
  badge: string;
  label: string;
  tone: "success" | "warning" | "danger";
  summary: string;
  workerSummary: string;
  relevantWorkSummary: string;
  limitations: string[];
  tools: FreshnessToolState[];
}

function uniqueStrings(values: Array<string | null | undefined>): string[] {
  return Array.from(
    new Set(
      values
        .map((value) => String(value ?? "").trim())
        .filter(Boolean)
    )
  );
}

function splitReasons(rawValue: string): string[] {
  return rawValue
    .split(/[;,]/g)
    .map((item) => item.trim())
    .filter(Boolean);
}

function formatWorkerType(workerType: string): string {
  return WORKER_TYPE_LABELS[workerType] ?? workerType;
}

export function formatToolProfile(profile: string): string {
  return TOOL_PROFILE_LABELS[profile] ?? profile;
}

function summarizeRouteReason(routeReason: string): string {
  const parts = routeReason
    .split("|")
    .map((item) => item.trim())
    .filter(Boolean);
  if (parts.length === 0) {
    return "当前还没有记录路由原因。";
  }
  const summary = parts.map((part) => {
    if (part === "planner") {
      return "由规划器直接分派";
    }
    if (part === "single_worker_default") {
      return "当前按默认单 Worker 路径执行";
    }
    if (part.startsWith("worker_type=")) {
      return `已明确交给 ${formatWorkerType(part.slice("worker_type=".length))}`;
    }
    if (part.startsWith("fallback=")) {
      const fallback = part.slice("fallback=".length).trim();
      if (fallback === "single_worker") {
        return "当前按单 Worker 路径执行";
      }
      return `当前按 ${fallback.replace(/_/g, " ")} 路径执行`;
    }
    return part.replace(/_/g, " ");
  });
  return summary.join("；");
}

export function formatFreshnessReason(reason: string): string {
  switch (reason) {
    case "owner_timezone_missing":
      return "owner timezone 未配置，今天/本地时间会回退到 UTC。";
    case "owner_timezone_invalid":
      return "owner timezone 配置无效，当前时间事实已回退到 UTC。";
    case "owner_locale_missing":
      return "owner locale 未配置，日期文案会退回默认格式。";
    case "context_frames_empty":
      return "当前作用域还没有 context frame，可解释的运行事实还不完整。";
    case "context_budget_trimmed":
      return "上下文超出预算，部分运行事实已被裁剪。";
    case "bootstrap_pending":
      return "bootstrap 还没准备好，Butler / Worker 指引可能不完整。";
    case "browser_controller_missing":
      return "当前环境缺少浏览器控制器，browser 路径会受限。";
    case "browser_session_missing":
      return "当前没有打开中的 browser session，后续页面动作无法继续。";
    case "browser_env_missing":
      return "当前 runtime 没有可用的浏览器环境，页面操作会受限。";
    default:
      return reason.replace(/_/g, " ");
  }
}

export function formatFreshnessLimitations(limitations: string[]): string {
  return limitations
    .map((item) => item.trim().replace(/[。；]+$/, ""))
    .filter(Boolean)
    .join("；");
}

function toolTone(tool: BundledToolDefinition | null): "success" | "warning" | "danger" {
  if (!tool) {
    return "danger";
  }
  if (tool.availability === "available") {
    return "success";
  }
  if (tool.availability === "degraded") {
    return "warning";
  }
  return "danger";
}

function toolStatusLabel(tool: BundledToolDefinition | null): string {
  if (!tool) {
    return "未暴露";
  }
  switch (tool.availability) {
    case "available":
      return "可用";
    case "degraded":
      return "受限";
    case "install_required":
      return "待安装";
    case "unavailable":
      return "不可用";
    default:
      return tool.availability;
  }
}

function toolSummary(
  tool: BundledToolDefinition | null,
  readySummary: string,
  missingSummary: string
): string {
  if (!tool) {
    return missingSummary;
  }
  if (tool.availability === "available") {
    return readySummary;
  }
  return formatFreshnessReason(tool.availability_reason || tool.install_hint || missingSummary);
}

function isToolOperational(tool: BundledToolDefinition | null): boolean {
  return tool?.availability === "available" || tool?.availability === "degraded";
}

function formatWorkerProfile(profile: WorkerCapabilityProfile): string {
  const groups = profile.default_tool_groups.slice(0, 3).join(" / ") || "未记录工具组";
  return `${formatWorkerType(profile.worker_type)} · ${formatToolProfile(profile.default_tool_profile)} · ${groups}`;
}

function hasFreshnessIntent(value: string): boolean {
  const lowered = value.trim().toLowerCase();
  if (!lowered) {
    return false;
  }
  return FRESHNESS_INTENT_TOKENS.some((token) => lowered.includes(token));
}

function sortWorksByUpdate(works: WorkProjectionItem[]): WorkProjectionItem[] {
  return [...works].sort((left, right) =>
    String(right.updated_at ?? "").localeCompare(String(left.updated_at ?? ""))
  );
}

export function isFreshnessRelevantWork(work: WorkProjectionItem): boolean {
  const requestedToolProfile = String(work.runtime_summary.requested_tool_profile ?? "")
    .trim()
    .toLowerCase();
  const requestedWorkerType = String(work.runtime_summary.requested_worker_type ?? "")
    .trim()
    .toLowerCase();
  const routeReason = String(work.route_reason ?? "").trim().toLowerCase();
  const selectedTools = work.selected_tools.map((tool) => tool.toLowerCase());
  const hasFreshnessTool = selectedTools.some(
    (tool) => tool === "runtime.now" || tool.startsWith("web.") || tool.startsWith("browser.")
  );
  if (hasFreshnessTool) {
    return true;
  }
  const hasWorkerRouting =
    work.selected_worker_type === "research" ||
    work.selected_worker_type === "ops" ||
    requestedWorkerType === "research" ||
    requestedWorkerType === "ops" ||
    routeReason.includes("worker_type=research") ||
    routeReason.includes("worker_type=ops");
  const hasElevatedProfile =
    requestedToolProfile === "standard" || requestedToolProfile === "privileged";
  return hasFreshnessIntent(work.title) && (hasWorkerRouting || hasElevatedProfile);
}

export function describeFreshnessWorkPath(work: WorkProjectionItem): string {
  if (!isFreshnessRelevantWork(work)) {
    return "";
  }
  const requestedToolProfile = String(work.runtime_summary.requested_tool_profile ?? "").trim();
  const effectiveWorkerType =
    String(work.runtime_summary.requested_worker_type ?? "").trim() || work.selected_worker_type;
  const selectedTools = work.selected_tools.join(", ");
  const routeSummary = summarizeRouteReason(work.route_reason);
  const toolSummary = requestedToolProfile
    ? formatToolProfile(requestedToolProfile)
    : "未显式记录工具级别";
  const toolLine = selectedTools
    ? `已挑选工具：${selectedTools}。`
    : "工具清单会在执行期继续按治理规则补齐。";
  return `${formatWorkerType(effectiveWorkerType)} 会按${toolSummary}处理这条工作。${routeSummary}。${toolLine}`;
}

export function buildFreshnessReadiness({
  context,
  capabilityPack,
  works,
}: {
  context: ContextContinuityDocument;
  capabilityPack: CapabilityPackDocument;
  works: WorkProjectionItem[];
}): FreshnessReadiness {
  const newestFrame =
    [...context.frames].sort((left, right) =>
      String(right.created_at ?? "").localeCompare(String(left.created_at ?? ""))
    )[0] ?? null;
  const tools = FRESHNESS_TOOL_CONFIG.map((config) => {
    const tool = capabilityPack.pack.tools.find((item) => item.tool_name === config.name) ?? null;
    return {
      label: config.label,
      statusLabel: toolStatusLabel(tool),
      tone: toolTone(tool),
      summary: toolSummary(tool, config.readySummary, config.missingSummary),
    } satisfies FreshnessToolState;
  });
  const runtimeTool = capabilityPack.pack.tools.find((item) => item.tool_name === "runtime.now");
  const webTool = capabilityPack.pack.tools.find((item) => item.tool_name === "web.search");
  const browserTool = capabilityPack.pack.tools.find((item) => item.tool_name === "browser.status");
  const timeReady = isToolOperational(runtimeTool ?? null);
  const networkReady = isToolOperational(webTool ?? null) || isToolOperational(browserTool ?? null);
  const workerProfiles = capabilityPack.pack.worker_profiles.filter(
    (profile) =>
      profile.worker_type === "research" ||
      profile.worker_type === "ops" ||
      profile.default_tool_groups.some((group) => group === "network" || group === "browser")
  );
  const relevantWork = sortWorksByUpdate(works).find((work) => isFreshnessRelevantWork(work)) ?? null;
  const limitations = uniqueStrings([
    ...context.degraded.reasons,
    ...splitReasons(newestFrame?.degraded_reason ?? ""),
    capabilityPack.pack.degraded_reason,
    runtimeTool && runtimeTool.availability !== "available"
      ? runtimeTool.availability_reason || runtimeTool.install_hint
      : "",
    webTool && webTool.availability !== "available"
      ? webTool.availability_reason || webTool.install_hint
      : "",
    browserTool && browserTool.availability !== "available"
      ? browserTool.availability_reason || browserTool.install_hint
      : "",
  ]).map((reason) => formatFreshnessReason(reason));

  if (timeReady && networkReady && limitations.length === 0) {
    return {
      badge: "已就绪",
      label: "实时问题可以直接委派给 Worker",
      tone: "success",
      summary:
        "Butler 已具备“今天 / 天气 / 官网 / 最新资料”这类问题的可解释执行路径，不应该再直接回答成没有能力。",
      workerSummary:
        workerProfiles.length > 0
          ? workerProfiles.slice(0, 2).map((profile) => formatWorkerProfile(profile)).join("；")
          : "当前 capability pack 还没有公开可复用的 freshness worker 配置。",
      relevantWorkSummary:
        relevantWork !== null
          ? describeFreshnessWorkPath(relevantWork)
          : "当前还没有产出过一条 freshness 相关 work，可直接去 Chat 触发一次。",
      limitations,
      tools,
    };
  }

  if (timeReady || networkReady || relevantWork !== null) {
    return {
      badge: "部分可用",
      label: "实时问题能力已经部分可用",
      tone: "warning",
      summary:
        "主链已经存在，但当前还有降级或环境限制；回答时应该解释限制，并尽量转交给合适的 Worker。",
      workerSummary:
        workerProfiles.length > 0
          ? workerProfiles.slice(0, 2).map((profile) => formatWorkerProfile(profile)).join("；")
          : "当前 capability pack 还没有公开可复用的 freshness worker 配置。",
      relevantWorkSummary:
        relevantWork !== null
          ? describeFreshnessWorkPath(relevantWork)
          : "当前还没有相关 work，可通过一次天气/官网/最新资料问题验证整条链路。",
      limitations,
      tools,
    };
  }

  return {
    badge: "未就绪",
    label: "实时问题路径还没准备好",
    tone: "danger",
    summary:
      "当前还不适合直接处理“今天 / 天气 / 官网 / 最新资料”类问题，先补齐时间事实或网页执行能力。",
    workerSummary:
      workerProfiles.length > 0
        ? workerProfiles.slice(0, 2).map((profile) => formatWorkerProfile(profile)).join("；")
        : "当前 capability pack 还没有公开可复用的 freshness worker 配置。",
    relevantWorkSummary:
      relevantWork !== null
        ? describeFreshnessWorkPath(relevantWork)
        : "当前没有相关 work，因而还看不到这条路径的实际执行证据。",
    limitations,
    tools,
  };
}
