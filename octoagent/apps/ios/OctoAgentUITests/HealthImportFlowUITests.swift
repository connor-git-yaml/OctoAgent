import XCTest

final class HealthImportFlowUITests: XCTestCase {
    private static let oracle = "F154_HEALTH_VISUAL_CONTRACT_MISSING"
    private static let simulatorOracle = "F154_HEALTH_SIMULATOR_CONTRACT_MISSING"
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

    func test_cold_launch_background_and_relaunch_do_not_request_health_permission() {
        launchRegistration()
        assertRegistrationWithoutHealthPermission(stage: "cold launch")

        XCUIDevice.shared.press(.home)
        app.activate()
        assertRegistrationWithoutHealthPermission(stage: "background restore")

        app.terminate()
        launchRegistration()
        assertRegistrationWithoutHealthPermission(stage: "process relaunch")
    }

    func test_live_health_read_preview_and_optional_approval() throws {
#if targetEnvironment(simulator)
        throw XCTSkip("仅由显式真机 HealthKit transaction 启用")
#else
        guard ProcessInfo.processInfo.environment["OCTOAGENT_LIVE_HEALTH"] == "1" else {
            throw XCTSkip("未显式启用真机 HealthKit transaction")
        }
        let lockCycle = ProcessInfo.processInfo.environment[
            "OCTOAGENT_LIVE_HEALTH_LOCK_CYCLE"
        ] == "1"
        if lockCycle,
           ProcessInfo.processInfo.environment["OCTOAGENT_LIVE_HEALTH_APPROVE"] == "1"
        {
            XCTFail("锁屏生命周期 transaction 禁止批准或上传健康摘要")
            return
        }

        startLiveApp()
        XCTAssertTrue(
            app.staticTexts["已连接"].waitForExistence(timeout: 20),
            "真机 HealthKit transaction 开始前设备连接未恢复"
        )
        XCTAssertEqual(systemAlerts.count, 0, "用户动作前不应出现系统权限面板")

        let healthEntry = app.buttons["health-entry"]
        XCTAssertTrue(healthEntry.waitForExistence(timeout: 10), "已连接首页缺少健康概览入口")
        healthEntry.tap()

        let screen = app.scrollViews["health-screen"]
        XCTAssertTrue(screen.waitForExistence(timeout: 10), "未进入真实 HealthKit 页面")
        XCTAssertTrue(app.staticTexts["尚未读取"].exists, "进入页面时不应自动读取 HealthKit")
        XCTAssertEqual(systemAlerts.count, 0, "进入 HealthKit 页面不应自动弹权限面板")

        let range = ProcessInfo.processInfo.environment["OCTOAGENT_LIVE_HEALTH_RANGE"] ?? "7 天"
        let rangeButton = app.segmentedControls.buttons[range]
        XCTAssertTrue(rangeButton.exists, "未知的真机健康时间范围：\(range)")
        rangeButton.tap()
        app.buttons["从 Apple 健康读取"].tap()

        let reviewing = app.staticTexts["等待你的批准"]
        let limited = app.staticTexts["无可读概览"]
        let outcomeDeadline = Date().addingTimeInterval(180)
        while !reviewing.exists, !limited.exists, Date() < outcomeDeadline {
            RunLoop.current.run(until: Date().addingTimeInterval(0.25))
        }
        XCTAssertTrue(
            reviewing.exists || limited.exists,
            "系统授权后没有形成真实预览，也没有诚实报告无可读数据"
        )

        if ProcessInfo.processInfo.environment["OCTOAGENT_LIVE_HEALTH_CAPTURE"] != "0" {
            keepLiveScreenshot(named: "health-live-\(range)-read-outcome")
        }
        guard reviewing.exists else { return }
        let previewCard = app.descendants(matching: .any)
            .matching(identifier: "health-preview-card")
            .firstMatch
        XCTAssertTrue(previewCard.exists)

        if lockCycle {
            waitForExternalLockCycle(reviewing: reviewing)
        } else {
            XCUIDevice.shared.press(.home)
            app.activate()
            XCTAssertTrue(
                reviewing.waitForExistence(timeout: 20),
                "真机从后台恢复后丢失当前 session 内的未批准预览"
            )
        }

        if ProcessInfo.processInfo.environment["OCTOAGENT_LIVE_HEALTH_APPROVE"] == "1" {
            app.buttons["批准并分析"].tap()
            XCTAssertTrue(
                app.staticTexts["已完成"].waitForExistence(timeout: 240),
                "真实健康概览没有完成设备签名提交与单次分析"
            )
            keepLiveScreenshot(named: "health-live-analysis-completed")
            app.buttons["删除这次数据"].tap()
            XCTAssertTrue(
                app.staticTexts["尚未读取"].waitForExistence(timeout: 60),
                "真实健康数据链删除后没有返回空闲状态"
            )
        } else {
            app.buttons["删除本地预览"].tap()
            XCTAssertTrue(
                app.staticTexts["尚未读取"].waitForExistence(timeout: 20),
                "删除未批准本地预览后没有返回空闲状态"
            )
        }
#endif
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

    private func launchRegistration() {
        app.launchEnvironment = [
            "OCTOAGENT_UI_TESTING": "1",
            "OCTOAGENT_UI_TEST_PHASE": "disconnected",
        ]
        app.launchArguments = [
            "-AppleLanguages",
            "(zh-Hans)",
            "-AppleLocale",
            "zh_CN",
        ]
        app.launch()
    }

    private func startLiveApp() {
        app.launchArguments = [
            "-AppleLanguages",
            "(zh-Hans)",
            "-AppleLocale",
            "zh_CN",
        ]
        if ProcessInfo.processInfo.environment["OCTOAGENT_LIVE_PRELAUNCHED"] == "1" {
            guard app.wait(for: .runningForeground, timeout: 20) else {
                XCTFail("外部真机启动器未在时限内启动 OctoAgent")
                return
            }
            app.activate()
        } else {
            app.launch()
        }
    }

    private var systemAlerts: XCUIElementQuery {
        XCUIApplication(bundleIdentifier: "com.apple.springboard").alerts
    }

    private func waitForExternalLockCycle(reviewing: XCUIElement) {
        print("F154_LOCKSCREEN_READY: 请锁定 iPhone")
        let lockDeadline = Date().addingTimeInterval(180)
        while app.state == .runningForeground, Date() < lockDeadline {
            RunLoop.current.run(until: Date().addingTimeInterval(0.25))
        }
        XCTAssertNotEqual(
            app.state,
            .runningForeground,
            "等待期间未观察到 iPhone 锁屏导致 App 离开前台"
        )

        print("F154_UNLOCK_REQUIRED: 请解锁并返回 OctoAgent")
        let unlockDeadline = Date().addingTimeInterval(180)
        while app.state != .runningForeground, Date() < unlockDeadline {
            RunLoop.current.run(until: Date().addingTimeInterval(0.25))
        }
        XCTAssertEqual(app.state, .runningForeground, "解锁后 OctoAgent 未恢复前台")
        XCTAssertTrue(
            reviewing.waitForExistence(timeout: 20),
            "锁屏→解锁后丢失当前 session 内的未批准预览"
        )
        XCTAssertFalse(app.staticTexts["提交中"].exists, "锁屏恢复不应自动提交健康摘要")
        XCTAssertFalse(app.staticTexts["分析中"].exists, "锁屏恢复不应自动启动健康分析")
        XCTAssertFalse(app.staticTexts["已完成"].exists, "锁屏恢复不应自动完成健康分析")
    }

    private func assertRegistrationWithoutHealthPermission(stage: String) {
        XCTAssertTrue(
            app.staticTexts["连接你的 Octo"].waitForExistence(timeout: 3),
            "\(Self.simulatorOracle): \(stage) 未保持注册入口"
        )
        XCTAssertFalse(
            app.scrollViews["health-screen"].exists,
            "\(Self.simulatorOracle): \(stage) 意外进入健康 DI 界面"
        )
        XCTAssertEqual(
            app.alerts.count,
            0,
            "\(Self.simulatorOracle): \(stage) 意外弹出 App 权限面板"
        )
        XCTAssertEqual(
            XCUIApplication(bundleIdentifier: "com.apple.springboard").alerts.count,
            0,
            "\(Self.simulatorOracle): \(stage) 意外弹出系统权限面板"
        )
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

    private func keepLiveScreenshot(named name: String) {
        let attachment = XCTAttachment(
            screenshot: XCUIScreen.main.screenshot(),
            quality: .original
        )
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
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
