import CoreGraphics
import ImageIO
import XCTest

final class RegistrationFlowUITests: XCTestCase {
    private let app = XCUIApplication()

    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func test_registration_states_render_plain_language_and_visual_evidence() {
        continueAfterFailure = true
        let states = [
            ("disconnected", "连接你的 Octo", "尚未连接"),
            ("connecting", "正在建立安全连接", "连接中"),
            ("awaiting-approval", "等待电脑批准", "等待批准"),
            ("connected", "我的 iPhone", "已连接"),
            ("revoked", "连接已撤销", "需要重新连接"),
            ("offline", "暂时无法连接", "当前离线"),
        ]

        for (phase, title, status) in states {
            launch(phase: phase)
            XCTAssertTrue(app.staticTexts[title].waitForExistence(timeout: 3))
            XCTAssertTrue(app.staticTexts[status].exists)
            keepScreenshot(named: "registration-\(phase)")
            app.terminate()
        }
    }

    func test_disconnected_form_is_accessible_and_requires_connection_information() {
        launch(phase: "disconnected")

        let deviceName = app.textFields["这台设备的名称"]
        let connectionInformation = app.textFields["Octo 连接信息"]
        let connectButton = app.buttons["连接此 Octo"]

        XCTAssertTrue(deviceName.waitForExistence(timeout: 3))
        XCTAssertEqual(deviceName.value as? String, "我的 iPhone")
        XCTAssertTrue(connectionInformation.exists)
        XCTAssertTrue(connectButton.exists)
        XCTAssertFalse(connectButton.isEnabled)

        connectionInformation.tap()
        connectionInformation.typeText("not-a-valid-connection")
        XCTAssertTrue(connectButton.isEnabled)
        connectButton.tap()

        XCTAssertTrue(
            app.staticTexts["提示：连接信息格式不正确，请从电脑 Web 重新复制。"]
                .waitForExistence(timeout: 3)
        )
    }

    func test_accessibility_labels_and_minimum_action_size_are_exposed() {
        launch(phase: "awaiting-approval")

        XCTAssertTrue(
            app.staticTexts["OctoAgent，等待批准"].waitForExistence(timeout: 3)
        )
        let approveButton = app.buttons["我已在电脑上批准"]
        XCTAssertTrue(approveButton.exists)
        XCTAssertGreaterThanOrEqual(approveButton.frame.height, 44)
    }

    private func launch(phase: String) {
        app.launchEnvironment = [
            "OCTOAGENT_UI_TESTING": "1",
            "OCTOAGENT_UI_TEST_PHASE": phase,
        ]
        app.launchArguments = [
            "-AppleLanguages",
            "(zh-Hans)",
            "-AppleLocale",
            "zh_CN",
        ]
        app.launch()
    }

    private func keepScreenshot(named name: String) {
        let screenshot = app.screenshot()
        let attachment = XCTAttachment(screenshot: screenshot, quality: .original)
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
        assertMatchesBaseline(screenshot.pngRepresentation, named: name)
    }

    private func assertMatchesBaseline(_ actualData: Data, named name: String) {
        guard let expectedURL = Bundle(for: Self.self).url(
            forResource: name,
            withExtension: "png",
            subdirectory: "__Snapshots__"
        ) else {
            XCTFail("缺少 iOS 视觉基线：\(name).png；先人工审查 attachment 再建立基线。")
            return
        }
        guard
            let expectedData = try? Data(contentsOf: expectedURL),
            let expected = decodedPixels(expectedData),
            let actual = decodedPixels(actualData)
        else {
            XCTFail("无法解码 iOS 视觉基线：\(name).png")
            return
        }
        XCTAssertEqual(actual.width, expected.width)
        XCTAssertEqual(actual.height, expected.height)
        guard actual.width == expected.width, actual.height == expected.height else {
            return
        }

        var differingPixels = 0
        for index in stride(from: 0, to: actual.bytes.count, by: 4) {
            let channelRange = index ..< index + 4
            if channelRange.contains(where: {
                abs(Int(actual.bytes[$0]) - Int(expected.bytes[$0])) > 12
            }) {
                differingPixels += 1
            }
        }
        let totalPixels = actual.width * actual.height
        XCTAssertLessThanOrEqual(
            Double(differingPixels) / Double(totalPixels),
            0.02,
            "\(name) 与 Claude 早期视觉基线偏差超过 2%"
        )
    }

    private func decodedPixels(
        _ data: Data
    ) -> (width: Int, height: Int, bytes: [UInt8])? {
        guard
            let source = CGImageSourceCreateWithData(data as CFData, nil),
            let image = CGImageSourceCreateImageAtIndex(source, 0, nil)
        else {
            return nil
        }
        let width = image.width
        let height = image.height
        var bytes = [UInt8](repeating: 0, count: width * height * 4)
        guard
            let context = CGContext(
                data: &bytes,
                width: width,
                height: height,
                bitsPerComponent: 8,
                bytesPerRow: width * 4,
                space: CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
            )
        else {
            return nil
        }
        context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
        return (width, height, bytes)
    }
}
