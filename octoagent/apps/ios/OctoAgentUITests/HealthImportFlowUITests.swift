import XCTest

final class HealthImportFlowUITests: XCTestCase {
    private static let oracle = "F154_HEALTH_VISUAL_CONTRACT_MISSING"
    private let app = XCUIApplication()

    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func test_health_states_match_claude_early_visual_baseline() {
        continueAfterFailure = true
        for state in healthStates {
            launch(phase: state.phase)
            let screen = app.scrollViews["health-screen"]
            XCTAssertTrue(
                screen.waitForExistence(timeout: 3),
                "\(Self.oracle): \(state.phase) 未进入原生健康界面"
            )
            XCTAssertTrue(
                app.staticTexts[state.title].exists,
                "\(Self.oracle): \(state.phase) 缺少普通语言标题"
            )
            XCTAssertTrue(
                app.staticTexts[state.status].exists,
                "\(Self.oracle): \(state.phase) 缺少状态标签"
            )
            if screen.exists {
                keepScreenshot(named: "health-\(state.phase)")
            }
            if let action = state.action {
                let button = app.buttons[action]
                XCTAssertTrue(
                    button.exists,
                    "\(Self.oracle): \(state.phase) 缺少 exact action"
                )
                if button.exists {
                    XCTAssertGreaterThanOrEqual(
                        button.frame.height,
                        44,
                        "\(Self.oracle): \(state.phase) action 小于 44pt"
                    )
                }
            }
            app.terminate()
        }
    }

    func test_health_view_supports_dynamic_type_voiceover_and_44pt_actions() {
        launch(phase: "reviewing", accessibilityMode: true)

        let screen = app.scrollViews["health-screen"]
        XCTAssertTrue(
            screen.waitForExistence(timeout: 3),
            "\(Self.oracle): accessibility launch 未进入健康界面"
        )
        XCTAssertTrue(
            app.staticTexts["发送前请先检查"].exists,
            "\(Self.oracle): accessibility XXXL 隐藏了主标题"
        )
        XCTAssertTrue(
            app.staticTexts["不会写入 Apple 健康，也不会读取心率、位置、医疗记录或其它类别。"]
                .exists,
            "\(Self.oracle): VoiceOver 可读的只读边界缺失"
        )
        if screen.exists {
            keepScreenshot(named: "health-reviewing-accessibility-xxxl")
        }
        let approveButton = app.buttons["批准并分析"]
        if !approveButton.isHittable {
            app.swipeUp()
        }
        XCTAssertTrue(
            approveButton.exists,
            "\(Self.oracle): accessibility XXXL 隐藏了批准动作"
        )
        if approveButton.exists {
            XCTAssertGreaterThanOrEqual(
                approveButton.frame.height,
                44,
                "\(Self.oracle): accessibility action 小于 44pt"
            )
        }
    }

    private var healthStates: [HealthStateExpectation] {
        [
            .init("unavailable", "这台设备无法读取 Apple 健康", "不可用", nil),
            .init("idle", "只读取你本次选择的概览", "尚未读取", "从 Apple 健康读取"),
            .init("requestingPermission", "请在系统面板中确认", "等待系统确认", nil),
            .init(
                "noReadableDataOrLimitedAccess",
                "没有可显示的数据",
                "无可读概览",
                "重新读取"
            ),
            .init("reviewing", "发送前请先检查", "等待你的批准", "批准并分析"),
            .init("submitting", "正在提交这次批准", "提交中", nil),
            .init("analyzing", "正在生成健康概览", "分析中", nil),
            .init("completed", "这次概览已完成", "已完成", "删除这次数据"),
            .init("offline", "当前离线", "等待网络", "网络恢复后重试"),
            .init("revoked", "设备连接已撤销", "连接已撤销", "删除本地预览"),
            .init("deleting", "正在删除这次数据", "删除中", nil),
            .init("deletionFailed", "删除尚未完成", "需要重试", "重试删除"),
        ]
    }

    private func launch(phase: String, accessibilityMode: Bool = false) {
        app.launchEnvironment = [
            "OCTOAGENT_UI_TESTING": "1",
            "OCTOAGENT_UI_TEST_HEALTH_PHASE": phase,
        ]
        app.launchArguments = [
            "-AppleLanguages",
            "(zh-Hans)",
            "-AppleLocale",
            "zh_CN",
        ]
        if accessibilityMode {
            app.launchEnvironment["OCTOAGENT_UI_TEST_ACCESSIBILITY"] = "1"
        }
        app.launch()
    }

    private func keepScreenshot(named name: String) {
        let screenshot = app.screenshot()
        let attachment = XCTAttachment(screenshot: screenshot, quality: .original)
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
        VisualSnapshotVerifier.assertMatchesBaseline(
            screenshot.pngRepresentation,
            named: name,
            failurePrefix: Self.oracle
        )
    }
}

private struct HealthStateExpectation {
    let phase: String
    let title: String
    let status: String
    let action: String?

    init(_ phase: String, _ title: String, _ status: String, _ action: String?) {
        self.phase = phase
        self.title = title
        self.status = status
        self.action = action
    }
}
