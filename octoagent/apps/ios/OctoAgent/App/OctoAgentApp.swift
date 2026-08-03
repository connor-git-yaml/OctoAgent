import SwiftUI

@main
@MainActor
struct OctoAgentApp: App {
    var body: some Scene {
        WindowGroup {
            launchContent
        }
    }

    @ViewBuilder
    private var launchContent: some View {
#if DEBUG
        if isUITesting,
           let phase = HealthImportPhase(
               rawValue: ProcessInfo.processInfo.environment["OCTOAGENT_UI_TEST_HEALTH_PHASE"] ?? ""
           )
        {
            NavigationStack {
                HealthReviewView(coordinator: .uiTest(phase: phase))
            }
            .environment(\.dynamicTypeSize, uiTestDynamicTypeSize)
        } else {
            registrationContent
        }
#else
        registrationContent
#endif
    }

    private var registrationContent: some View {
        RegistrationView(
            viewModel: launchViewModel(),
            restoresStoredConnection: !isUITesting
        )
    }

    private var isUITesting: Bool {
#if DEBUG
        ProcessInfo.processInfo.environment["OCTOAGENT_UI_TESTING"] == "1"
#else
        false
#endif
    }

#if DEBUG
    private var uiTestDynamicTypeSize: DynamicTypeSize {
        uiTestAccessibilityMode ? .accessibility3 : .large
    }

    private var uiTestAccessibilityMode: Bool {
        ProcessInfo.processInfo.environment["OCTOAGENT_UI_TEST_ACCESSIBILITY"] == "1"
    }
#endif

    private func launchViewModel() -> RegistrationViewModel {
#if DEBUG
        let environment = ProcessInfo.processInfo.environment
        if isUITesting,
           let phase = RegistrationPhase(uiTestValue: environment["OCTOAGENT_UI_TEST_PHASE"])
        {
            return RegistrationViewModel(initialPhase: phase)
        }
#endif
        return RegistrationViewModel()
    }
}

#if DEBUG
private extension RegistrationPhase {
    init?(uiTestValue: String?) {
        switch uiTestValue {
        case "disconnected":
            self = .disconnected
        case "connecting":
            self = .connecting
        case "awaiting-approval":
            self = .awaitingApproval
        case "connected":
            self = .connected(deviceName: "我的 iPhone")
        case "revoked":
            self = .revoked
        case "offline":
            self = .offline
        default:
            return nil
        }
    }
}
#endif
