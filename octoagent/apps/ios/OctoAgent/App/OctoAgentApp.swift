import SwiftUI

@main
@MainActor
struct OctoAgentApp: App {
    var body: some Scene {
        WindowGroup {
            RegistrationView(
                viewModel: launchViewModel(),
                restoresStoredConnection: !isUITesting
            )
        }
    }

    private var isUITesting: Bool {
#if DEBUG
        ProcessInfo.processInfo.environment["OCTOAGENT_UI_TESTING"] == "1"
#else
        false
#endif
    }

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
