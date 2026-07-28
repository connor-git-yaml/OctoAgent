import SwiftUI

enum RegistrationPhase: Equatable {
    case disconnected
    case connecting
    case awaitingApproval
    case connected(deviceName: String)
    case revoked
    case offline
}

struct RegistrationPresentation: Equatable {
    let title: String
    let detail: String
    let statusLabel: String
    let actionTitle: String?
    let symbol: String

    init(phase: RegistrationPhase) {
        switch phase {
        case .disconnected:
            self.init(
                title: "连接你的 Octo",
                detail: "在电脑 Web 的“手机连接”中复制连接信息，然后粘贴到这里。",
                statusLabel: "尚未连接",
                actionTitle: "连接此 Octo",
                symbol: "link"
            )
        case .connecting:
            self.init(
                title: "正在建立安全连接",
                detail: "正在创建这台 iPhone 的设备密钥并验证连接信息。",
                statusLabel: "连接中",
                actionTitle: nil,
                symbol: "key.fill"
            )
        case .awaitingApproval:
            self.init(
                title: "等待电脑批准",
                detail: "回到电脑 Web，核对设备名称后批准这次连接。",
                statusLabel: "等待批准",
                actionTitle: "我已在电脑上批准",
                symbol: "checkmark.shield"
            )
        case let .connected(deviceName):
            self.init(
                title: deviceName,
                detail: "这台 iPhone 已通过设备密钥连接，可以安全读取已授权内容。",
                statusLabel: "已连接",
                actionTitle: "检查连接",
                symbol: "checkmark.circle.fill"
            )
        case .revoked:
            self.init(
                title: "连接已撤销",
                detail: "这台设备的访问已被取消。如需继续，请重新发起连接。",
                statusLabel: "需要重新连接",
                actionTitle: "重新连接",
                symbol: "xmark.shield.fill"
            )
        case .offline:
            self.init(
                title: "暂时无法连接",
                detail: "设备信息仍安全保存在本机。网络恢复后可以继续。",
                statusLabel: "当前离线",
                actionTitle: "重试",
                symbol: "wifi.slash"
            )
        }
    }

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
final class RegistrationViewModel: ObservableObject {
    @Published var phase: RegistrationPhase
    @Published var linkText = ""
    @Published var deviceName = "我的 iPhone"
    @Published private(set) var notice = ""

    private let keyStore: DeviceKeyStore
    private var client: DeviceTrustClient?
    private var pendingDeviceID: String?
    private var restored = false

    init(
        keyStore: DeviceKeyStore = DeviceKeyStore(),
        initialPhase: RegistrationPhase = .disconnected
    ) {
        self.keyStore = keyStore
        phase = initialPhase
    }

    func restore() async {
        guard !restored else {
            return
        }
        restored = true
        do {
            guard let credentials = try keyStore.loadCredentials() else {
                return
            }
            client = try makeClient(origin: credentials.serverOrigin)
            try await refreshOrRenew(credentials: credentials)
        } catch {
            handle(error)
        }
    }

    func performPrimaryAction() async {
        switch phase {
        case .disconnected:
            await connect()
        case .connecting:
            return
        case .awaitingApproval:
            await completeApproval()
        case .connected:
            await refreshStoredConnection()
        case .revoked:
            resetForReconnect()
        case .offline:
            await refreshStoredConnection()
        }
    }

    private func connect() async {
        phase = .connecting
        notice = ""
        do {
            let link = try DeviceRegistrationLink.parse(linkText)
            let name = deviceName.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !name.isEmpty else {
                throw DeviceRegistrationLinkError.invalidFormat
            }
            let nextClient = try makeClient(origin: link.mobileOrigin)
            let status = try await nextClient.submitEnrollment(
                link: link,
                displayName: name
            )
            guard
                status.state == .pending,
                let deviceID = status.deviceID
            else {
                throw DeviceTrustClientError.invalidResponse
            }
            client = nextClient
            pendingDeviceID = deviceID
            phase = .awaitingApproval
        } catch {
            phase = .disconnected
            handle(error)
        }
    }

    private func completeApproval() async {
        guard let client, let pendingDeviceID else {
            phase = .disconnected
            notice = "连接信息已失效，请重新连接。"
            return
        }
        phase = .connecting
        do {
            let credentials = try await client.issueToken(deviceID: pendingDeviceID)
            try keyStore.saveCredentials(credentials)
            try await refresh(credentials: credentials)
        } catch DeviceTrustClientError.revoked {
            phase = .awaitingApproval
            notice = "电脑端还没有批准这台设备。"
        } catch {
            handle(error)
        }
    }

    private func refreshStoredConnection() async {
        do {
            guard let credentials = try keyStore.loadCredentials() else {
                phase = .disconnected
                return
            }
            if client == nil {
                client = try makeClient(origin: credentials.serverOrigin)
            }
            try await refreshOrRenew(credentials: credentials)
        } catch {
            handle(error)
        }
    }

    private func refresh(credentials: DeviceCredentials) async throws {
        guard let client else {
            throw DeviceTrustClientError.invalidResponse
        }
        _ = try await client.ready(credentials: credentials)
        let profile = try await client.deviceProfile(credentials: credentials)
        phase = .connected(deviceName: profile.displayName)
        notice = ""
    }

    private func refreshOrRenew(credentials: DeviceCredentials) async throws {
        do {
            try await refresh(credentials: credentials)
        } catch DeviceTrustClientError.credentialsExpired {
            guard let client else {
                throw DeviceTrustClientError.invalidResponse
            }
            let renewed = try await client.issueToken(deviceID: credentials.deviceID)
            try keyStore.saveCredentials(renewed)
            try await refresh(credentials: renewed)
        }
    }

    private func makeClient(origin: URL) throws -> DeviceTrustClient {
        try DeviceTrustClient(
            serverOrigin: origin,
            signer: RequestProofSigner(keyProvider: keyStore)
        )
    }

    private func handle(_ error: Error) {
        switch error {
        case DeviceTrustClientError.offline:
            phase = .offline
            notice = "请检查网络后重试。"
        case DeviceTrustClientError.revoked:
            phase = .revoked
            notice = ""
        case DeviceTrustClientError.credentialsExpired:
            phase = .offline
            notice = "短期连接凭证未能自动更新，请重试。"
        case DeviceTrustClientError.edgeContractMismatch:
            phase = .offline
            notice = "当前地址不是 Octo 手机连接入口。"
        case is DeviceRegistrationLinkError:
            notice = "连接信息格式不正确，请从电脑 Web 重新复制。"
        default:
            notice = "连接未能完成，请稍后重试。"
        }
    }

    private func resetForReconnect() {
        try? keyStore.clear()
        client?.cancelAll()
        client = nil
        pendingDeviceID = nil
        linkText = ""
        phase = .disconnected
        notice = ""
    }
}

@MainActor
struct RegistrationView: View {
    @StateObject private var viewModel: RegistrationViewModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    private let restoresStoredConnection: Bool

    init() {
        _viewModel = StateObject(wrappedValue: RegistrationViewModel())
        restoresStoredConnection = true
    }

    init(
        viewModel: RegistrationViewModel,
        restoresStoredConnection: Bool = true
    ) {
        _viewModel = StateObject(wrappedValue: viewModel)
        self.restoresStoredConnection = restoresStoredConnection
    }

    var body: some View {
        NavigationStack {
            ZStack {
                OctoPalette.background.ignoresSafeArea()
                ScrollView {
                    VStack(alignment: .leading, spacing: 20) {
                        brandHeader
                        connectionCard
                        if viewModel.phase == .disconnected {
                            connectionForm
                        }
                        privacyCard
                    }
                    .frame(maxWidth: 560, alignment: .leading)
                    .padding(.horizontal, 20)
                    .padding(.vertical, 18)
                }
            }
            .toolbar(.hidden, for: .navigationBar)
        }
        .preferredColorScheme(.dark)
        .task {
            if restoresStoredConnection {
                await viewModel.restore()
            }
        }
        .animation(
            reduceMotion ? nil : .easeOut(duration: 0.2),
            value: viewModel.phase
        )
    }

    private var brandHeader: some View {
        Group {
            if dynamicTypeSize.isAccessibilitySize {
                VStack(alignment: .leading, spacing: 12) {
                    HStack {
                        brandMark
                        Spacer()
                        statusIndicator
                    }
                    brandTitle
                }
            } else {
                HStack(spacing: 12) {
                    brandMark
                    brandTitle
                    Spacer()
                    statusIndicator
                }
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel(
            "OctoAgent，\(RegistrationPresentation(phase: viewModel.phase).statusLabel)"
        )
    }

    private var brandMark: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 10)
                .fill(OctoPalette.accent.opacity(0.16))
                .frame(width: 42, height: 42)
            Image(systemName: "circle.hexagongrid.fill")
                .foregroundStyle(OctoPalette.accent)
                .font(.title3)
        }
    }

    private var brandTitle: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("OctoAgent")
                .font(.headline.weight(.semibold))
            Text("原生设备连接")
                .font(.caption)
                .foregroundStyle(OctoPalette.muted)
        }
    }

    private var statusIndicator: some View {
        Circle()
            .fill(statusColor)
            .frame(width: 9, height: 9)
            .accessibilityHidden(true)
    }

    private var connectionCard: some View {
        let presentation = RegistrationPresentation(phase: viewModel.phase)
        return VStack(alignment: .leading, spacing: 18) {
            HStack(alignment: .top, spacing: 14) {
                Image(systemName: presentation.symbol)
                    .font(.title2.weight(.semibold))
                    .foregroundStyle(statusColor)
                    .frame(width: 34, height: 34)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 6) {
                    Text(presentation.statusLabel.uppercased())
                        .font(.caption2.weight(.bold))
                        .tracking(1.1)
                        .foregroundStyle(statusColor)
                    Text(presentation.title)
                        .font(.title2.weight(.bold))
                    Text(presentation.detail)
                        .font(.body)
                        .foregroundStyle(OctoPalette.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            if viewModel.phase == .connecting {
                ProgressView()
                    .tint(OctoPalette.accent)
                    .accessibilityLabel("正在连接")
            }
            if !viewModel.notice.isEmpty {
                Text(viewModel.notice)
                    .font(.callout)
                    .foregroundStyle(OctoPalette.warning)
                    .accessibilityLabel("提示：\(viewModel.notice)")
            }
            if let actionTitle = presentation.actionTitle,
               viewModel.phase != .disconnected
            {
                primaryButton(actionTitle)
            }
        }
        .octoCard()
    }

    private var connectionForm: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("连接信息")
                .font(.headline)
            TextField("设备名称", text: $viewModel.deviceName)
                .textContentType(.name)
                .octoField()
                .accessibilityLabel("这台设备的名称")
            TextField("粘贴电脑 Web 生成的连接信息", text: $viewModel.linkText)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .keyboardType(.URL)
                .octoField()
                .accessibilityLabel("Octo 连接信息")
            primaryButton("连接此 Octo")
                .disabled(
                    viewModel.linkText.trimmingCharacters(
                        in: .whitespacesAndNewlines
                    ).isEmpty
                )
        }
        .octoCard()
    }

    private var privacyCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("设备密钥只保存在这台 iPhone", systemImage: "lock.shield")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(OctoPalette.primary)
            Text("连接需要你在电脑 Web 上明确批准。App 不保存 Web 登录、服务令牌或部署凭证。")
                .font(.footnote)
                .foregroundStyle(OctoPalette.muted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .octoCard()
    }

    private func primaryButton(_ title: String) -> some View {
        Button {
            Task {
                await viewModel.performPrimaryAction()
            }
        } label: {
            HStack {
                Text(title)
                    .font(.body.weight(.semibold))
                Spacer()
                Image(systemName: "arrow.up.right")
                    .accessibilityHidden(true)
            }
            .foregroundStyle(Color.black)
            .frame(maxWidth: .infinity, minHeight: 52)
            .padding(.horizontal, 18)
            .background(OctoPalette.accent)
            .clipShape(RoundedRectangle(cornerRadius: 14))
        }
        .buttonStyle(.plain)
        .accessibilityLabel(title)
    }

    private var statusColor: Color {
        switch viewModel.phase {
        case .disconnected:
            return OctoPalette.muted
        case .connected:
            return OctoPalette.accent
        case .revoked:
            return OctoPalette.danger
        case .offline:
            return OctoPalette.warning
        default:
            return OctoPalette.accent
        }
    }
}

private enum OctoPalette {
    static let background = Color(red: 0.035, green: 0.035, blue: 0.035)
    static let surface = Color(red: 0.075, green: 0.075, blue: 0.075)
    static let border = Color.white.opacity(0.08)
    static let accent = Color(red: 0.11, green: 0.86, blue: 0.38)
    static let primary = Color.white.opacity(0.94)
    static let secondary = Color.white.opacity(0.72)
    static let muted = Color.white.opacity(0.5)
    static let warning = Color(red: 1.0, green: 0.68, blue: 0.24)
    static let danger = Color(red: 1.0, green: 0.35, blue: 0.34)
}

private extension View {
    func octoCard() -> some View {
        padding(18)
            .background(OctoPalette.surface)
            .overlay {
                RoundedRectangle(cornerRadius: 16)
                    .stroke(OctoPalette.border, lineWidth: 1)
            }
            .clipShape(RoundedRectangle(cornerRadius: 16))
    }

    func octoField() -> some View {
        padding(.horizontal, 14)
            .frame(minHeight: 50)
            .background(Color.white.opacity(0.055))
            .overlay {
                RoundedRectangle(cornerRadius: 12)
                    .stroke(OctoPalette.border, lineWidth: 1)
            }
            .clipShape(RoundedRectangle(cornerRadius: 12))
    }
}
