import SwiftUI

enum HealthReviewActionError: Error, Equatable {
    case unavailable
    case offline
}

struct HealthReviewActions {
    let analyze: (HealthPreview) async throws -> String
    let delete: () async throws -> Void

    static let unavailable = HealthReviewActions(
        analyze: { _ in throw HealthReviewActionError.unavailable },
        delete: { throw HealthReviewActionError.unavailable }
    )
}

struct HealthReviewPresentation: Equatable {
    let title: String
    let detail: String
    let statusLabel: String
    let actionTitle: String?
    let symbol: String

    init(phase: HealthImportPhase) {
        guard let presentation = Self.values[phase] else {
            preconditionFailure("未覆盖的健康导入状态：\(phase)")
        }
        self = presentation
    }

    private static let values: [HealthImportPhase: HealthReviewPresentation] = [
        .unavailable: .init(
            title: "这台设备无法读取 Apple 健康",
            detail: "Apple 健康在当前设备或系统环境中不可用。",
            statusLabel: "不可用",
            actionTitle: nil,
            symbol: "heart.slash"
        ),
        .idle: .init(
            title: "只读取你本次选择的概览",
            detail: "先选择时间范围，再由你主动发起读取。App 不会在后台读取。",
            statusLabel: "尚未读取",
            actionTitle: "从 Apple 健康读取",
            symbol: "heart.text.square"
        ),
        .requestingPermission: .init(
            title: "请在系统面板中确认",
            detail: "只请求步数和睡眠分析的读取权限，不请求写入权限。",
            statusLabel: "等待系统确认",
            actionTitle: nil,
            symbol: "hand.raised.fill"
        ),
        .noReadableDataOrLimitedAccess: .init(
            title: "没有可显示的数据",
            detail: "可能是所选时间内没有记录，也可能是读取范围受限；App 不会猜测具体原因。",
            statusLabel: "无可读概览",
            actionTitle: "重新读取",
            symbol: "heart.slash.circle"
        ),
        .reviewing: .init(
            title: "发送前请先检查",
            detail: "下面只包含汇总后的步数和睡眠时长，不包含原始健康样本。",
            statusLabel: "等待你的批准",
            actionTitle: "批准并分析",
            symbol: "checkmark.shield"
        ),
        .submitting: .init(
            title: "正在提交这次批准",
            detail: "仅发送你刚刚看到的概览；离开本页不会扩大读取范围。",
            statusLabel: "提交中",
            actionTitle: nil,
            symbol: "arrow.up.circle"
        ),
        .analyzing: .init(
            title: "正在生成健康概览",
            detail: "分析仅使用这次批准的汇总事实，不会自动写入记忆。",
            statusLabel: "分析中",
            actionTitle: nil,
            symbol: "sparkles"
        ),
        .completed: .init(
            title: "这次概览已完成",
            detail: "结果是普通语言说明，不是医疗建议。你可以删除这次数据链。",
            statusLabel: "已完成",
            actionTitle: "删除这次数据",
            symbol: "checkmark.circle.fill"
        ),
        .offline: .init(
            title: "当前离线",
            detail: "未批准的预览仍只保存在这台 iPhone；网络恢复前不会提交。",
            statusLabel: "等待网络",
            actionTitle: "网络恢复后重试",
            symbol: "wifi.slash"
        ),
        .revoked: .init(
            title: "设备连接已撤销",
            detail: "不会再向 Octo 提交数据；仍可清除这台 iPhone 上的本地预览。",
            statusLabel: "连接已撤销",
            actionTitle: "删除本地预览",
            symbol: "xmark.shield.fill"
        ),
        .deleting: .init(
            title: "正在删除这次数据",
            detail: "删除会沿着来源、批准、分析和待确认记忆候选清理整条数据链。",
            statusLabel: "删除中",
            actionTitle: nil,
            symbol: "trash"
        ),
        .deletionFailed: .init(
            title: "删除尚未完成",
            detail: "已保留可重试的删除记录，不会把部分删除误报为完成。",
            statusLabel: "需要重试",
            actionTitle: "重试删除",
            symbol: "exclamationmark.arrow.triangle.2.circlepath"
        ),
    ]

    private init(
        title: String,
        detail: String,
        statusLabel: String,
        actionTitle: String?,
        symbol: String
    ) {
        self.title = title
        self.detail = detail
        self.statusLabel = statusLabel
        self.actionTitle = actionTitle
        self.symbol = symbol
    }
}

@MainActor
struct HealthReviewView: View {
    @StateObject private var coordinator: HealthImportCoordinator
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var preset: HealthReadWindow.Preset = .last24Hours
    private let actions: HealthReviewActions
    private let now: () -> Date

    init() {
        _coordinator = StateObject(
            wrappedValue: HealthImportCoordinator(store: AppleHealthDataStore())
        )
        actions = .unavailable
        now = Date.init
    }

    init(
        coordinator: HealthImportCoordinator,
        actions: HealthReviewActions = .unavailable,
        now: @escaping () -> Date = Date.init
    ) {
        _coordinator = StateObject(wrappedValue: coordinator)
        self.actions = actions
        self.now = now
    }

    var body: some View {
        ZStack {
            HealthPalette.background.ignoresSafeArea()
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    header
                    permissionCard
                    windowPicker
                    statusCard
                    if let preview = coordinator.preview {
                        previewCard(preview)
                    }
                    if let summary = coordinator.analysisSummary {
                        resultCard(summary)
                    }
                    actionsArea
                }
                .frame(maxWidth: 620, alignment: .leading)
                .padding(.horizontal, 20)
                .padding(.vertical, 18)
            }
        }
        .navigationTitle("健康概览")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(HealthPalette.background, for: .navigationBar)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .preferredColorScheme(.dark)
        .accessibilityIdentifier("health-screen")
        .animation(
            reduceMotion ? nil : .easeOut(duration: 0.2),
            value: coordinator.phase
        )
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("APPLE 健康")
                .font(.caption2.weight(.bold))
                .tracking(1.2)
                .foregroundStyle(HealthPalette.accent)
            Text("把健康数据留在你的掌控中")
                .font(.largeTitle.weight(.bold))
            Text("读取、预览、批准和删除都由你主动触发。")
                .font(.body)
                .foregroundStyle(HealthPalette.secondary)
        }
    }

    private var permissionCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("只读：步数与睡眠分析", systemImage: "lock.shield")
                .font(.headline)
            Text("不会写入 Apple 健康，也不会读取心率、位置、医疗记录或其它类别。")
                .font(.footnote)
                .foregroundStyle(HealthPalette.muted)
        }
        .healthCard()
    }

    private var windowPicker: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("时间范围")
                .font(.headline)
            Picker("健康数据时间范围", selection: $preset) {
                Text("24 小时").tag(HealthReadWindow.Preset.last24Hours)
                Text("3 天").tag(HealthReadWindow.Preset.last3Days)
                Text("7 天").tag(HealthReadWindow.Preset.last7Days)
            }
            .pickerStyle(.segmented)
            .disabled(coordinator.phase != .idle)
        }
        .healthCard()
        .accessibilityIdentifier("health-window-picker")
    }

    private var statusCard: some View {
        let presentation = HealthReviewPresentation(phase: coordinator.phase)
        return HStack(alignment: .top, spacing: 14) {
            Image(systemName: presentation.symbol)
                .font(.title2.weight(.semibold))
                .foregroundStyle(statusColor)
                .frame(width: 34, height: 34)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 7) {
                Text(presentation.statusLabel.uppercased())
                    .font(.caption2.weight(.bold))
                    .tracking(1.1)
                    .foregroundStyle(statusColor)
                Text(presentation.title)
                    .font(.title3.weight(.bold))
                Text(presentation.detail)
                    .font(.body)
                    .foregroundStyle(HealthPalette.secondary)
                if !coordinator.notice.isEmpty {
                    Text(coordinator.notice)
                        .font(.callout)
                        .foregroundStyle(HealthPalette.warning)
                }
                if isBusy {
                    ProgressView()
                        .tint(HealthPalette.accent)
                        .accessibilityLabel(presentation.statusLabel)
                }
            }
        }
        .healthCard()
        .accessibilityElement(children: .combine)
    }

    private func previewCard(_ preview: HealthPreview) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("即将发送的概览")
                .font(.headline)
            ForEach(preview.dailySteps, id: \.localDay) { day in
                metricRow(label: day.localDay, value: "\(day.count) 步")
            }
            if let sleep = preview.sleep {
                metricRow(label: "睡眠", value: "\(sleep.totalAsleepMinutes) 分钟")
            }
            Divider().overlay(HealthPalette.border)
            Text(preview.completenessNotice)
                .font(.footnote)
                .foregroundStyle(HealthPalette.muted)
        }
        .healthCard()
        .accessibilityIdentifier("health-preview-card")
    }

    private func resultCard(_ summary: String) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("本次分析")
                .font(.headline)
            Text(summary)
                .font(.body)
                .foregroundStyle(HealthPalette.secondary)
            Text("这不是医疗建议")
                .font(.caption.weight(.semibold))
                .foregroundStyle(HealthPalette.muted)
        }
        .healthCard()
    }

    @ViewBuilder
    private var actionsArea: some View {
        let presentation = HealthReviewPresentation(phase: coordinator.phase)
        if let title = presentation.actionTitle {
            Button(title) {
                Task { await performPrimaryAction() }
            }
            .buttonStyle(HealthPrimaryButtonStyle())
            .accessibilityIdentifier("health-primary-action")
        }
        if coordinator.preview != nil,
           coordinator.phase == .reviewing || coordinator.phase == .offline
        {
            Button("删除本地预览", role: .destructive) {
                coordinator.deleteLocalPreview()
            }
            .frame(maxWidth: .infinity, minHeight: 44)
            .accessibilityIdentifier("health-delete-action")
        }
    }

    private func performPrimaryAction() async {
        switch coordinator.phase {
        case .idle, .noReadableDataOrLimitedAccess:
            await readHealthPreview()
        case .reviewing:
            await analyzePreview()
        case .completed, .deletionFailed:
            await deleteApprovedChain()
        case .offline:
            coordinator.connectivityDidChange(isOnline: true)
        case .revoked:
            coordinator.deleteLocalPreview()
        default:
            break
        }
    }

    private func readHealthPreview() async {
        let end = now()
        guard let window = try? HealthReadWindow(
            start: end.addingTimeInterval(-preset.duration),
            end: end,
            preset: preset,
            referenceNow: end
        ) else { return }
        await coordinator.userRequestedRead(window: window, calendar: .autoupdatingCurrent)
    }

    private func analyzePreview() async {
        guard let preview = coordinator.beginSubmission() else { return }
        coordinator.beginAnalysis()
        do {
            coordinator.analysisDidComplete(summary: try await actions.analyze(preview))
        } catch {
            coordinator.analysisDidFail(isOffline: error as? HealthReviewActionError == .offline)
        }
    }

    private func deleteApprovedChain() async {
        coordinator.beginDeletion()
        do {
            try await actions.delete()
            coordinator.deletionDidComplete()
        } catch {
            coordinator.deletionDidFail()
        }
    }

    private func metricRow(label: String, value: String) -> some View {
        HStack {
            Text(label).foregroundStyle(HealthPalette.secondary)
            Spacer()
            Text(value).fontWeight(.semibold)
        }
        .font(.body)
    }

    private var isBusy: Bool {
        [.requestingPermission, .submitting, .analyzing, .deleting]
            .contains(coordinator.phase)
    }

    private var statusColor: Color {
        switch coordinator.phase {
        case .completed, .reviewing:
            HealthPalette.accent
        case .offline, .deletionFailed:
            HealthPalette.warning
        case .revoked, .unavailable:
            HealthPalette.danger
        default:
            HealthPalette.accent
        }
    }
}

private extension HealthReadWindow.Preset {
    var duration: TimeInterval {
        switch self {
        case .last24Hours: 86_400
        case .last3Days: 3 * 86_400
        case .last7Days: 7 * 86_400
        }
    }
}

private struct HealthPrimaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.body.weight(.semibold))
            .foregroundStyle(Color.black)
            .frame(maxWidth: .infinity, minHeight: 52)
            .background(HealthPalette.accent.opacity(configuration.isPressed ? 0.72 : 1))
            .clipShape(RoundedRectangle(cornerRadius: 14))
    }
}

private enum HealthPalette {
    static let background = Color(red: 0.035, green: 0.035, blue: 0.035)
    static let surface = Color(red: 0.075, green: 0.075, blue: 0.075)
    static let border = Color.white.opacity(0.08)
    static let accent = Color(red: 0.11, green: 0.86, blue: 0.38)
    static let secondary = Color.white.opacity(0.72)
    static let muted = Color.white.opacity(0.5)
    static let warning = Color(red: 1.0, green: 0.68, blue: 0.24)
    static let danger = Color(red: 1.0, green: 0.35, blue: 0.34)
}

private extension View {
    func healthCard() -> some View {
        padding(18)
            .background(HealthPalette.surface)
            .overlay {
                RoundedRectangle(cornerRadius: 16)
                    .stroke(HealthPalette.border, lineWidth: 1)
            }
            .clipShape(RoundedRectangle(cornerRadius: 16))
    }
}
