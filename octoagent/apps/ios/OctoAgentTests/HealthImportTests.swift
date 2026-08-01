import Foundation
import XCTest
@testable import OctoAgent

final class HealthImportTests: XCTestCase {
    private let utc = TimeZone(secondsFromGMT: 0)!

    func testHealthModelFiniteWindowDecimalAndCanonicalHash() throws {
        var issues: [String] = []
        let now = date("2026-03-10T12:00:00Z")
        let valid = try HealthReadWindow(
            start: date("2026-03-09T12:00:00Z"),
            end: now,
            preset: .last24Hours,
            referenceNow: now
        )

        expectThrows(&issues, "inverted window") {
            _ = try HealthReadWindow(start: now, end: valid.start, preset: .last24Hours, referenceNow: now)
        }
        expectThrows(&issues, "future window") {
            _ = try HealthReadWindow(
                start: now,
                end: now.addingTimeInterval(1),
                preset: .last24Hours,
                referenceNow: now
            )
        }
        expectThrows(&issues, "oversized window") {
            _ = try HealthReadWindow(
                start: now.addingTimeInterval(-(7 * 86_400 + 1)),
                end: now,
                preset: .last7Days,
                referenceNow: now
            )
        }

        if HealthDataType.allCases != [.stepCount, .sleepAnalysis] {
            issues.append("health data type set is not exact")
        }
        if (try? JSONDecoder().decode(HealthDataType.self, from: Data("\"heartRate\"".utf8))) != nil {
            issues.append("unknown health type decoded")
        }
        if try HealthCanonicalValue.decimal(Decimal(string: "12.3400")!) != "12.34" {
            issues.append("decimal is not canonical")
        }

        let preview = try makePreview(window: valid, stepCount: "42")
        let samePreview = try makePreview(window: valid, stepCount: "42")
        let changedPreview = try makePreview(window: valid, stepCount: "43")
        if preview.canonicalSha256.count != 64 || preview.canonicalSha256 != samePreview.canonicalSha256 {
            issues.append("preview hash is not deterministic SHA-256")
        }
        if preview.canonicalSha256 == changedPreview.canonicalSha256 {
            issues.append("preview hash ignores approved value changes")
        }
        let encoded = String(data: try JSONEncoder().encode(preview), encoding: .utf8)!
        for forbidden in ["metadata", "source", "device", "uuid", "token", "owner"]
        where encoded.localizedCaseInsensitiveContains(forbidden) {
            issues.append("preview leaked raw field: \(forbidden)")
        }

        XCTAssertTrue(issues.isEmpty, "F154_HEALTH_MODEL_MISSING: \(issues.joined(separator: "; "))")
    }

    func testStepAggregationUsesFinalLocalDayAcrossDst() throws {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "America/Los_Angeles")!
        let window = try HealthReadWindow(
            start: date("2026-03-07T00:00:00Z"),
            end: date("2026-03-10T00:00:00Z"),
            preset: .last3Days,
            referenceNow: date("2026-03-10T00:00:00Z")
        )
        let samples = [
            StepSample(
                start: date("2026-03-08T07:30:00Z"),
                end: date("2026-03-08T07:40:00Z"),
                count: 10
            ),
            StepSample(
                start: date("2026-03-08T09:30:00Z"),
                end: date("2026-03-08T09:40:00Z"),
                count: 20
            ),
            StepSample(
                start: date("2026-03-08T10:30:00Z"),
                end: date("2026-03-08T10:40:00Z"),
                count: 5
            ),
        ]
        let summaries = try HealthImportNormalizer.dailySteps(samples, window: window, calendar: calendar)
        XCTAssertEqual(
            summaries,
            [
                DailyStepSummary(localDay: "2026-03-07", count: "10", unit: "count"),
                DailyStepSummary(localDay: "2026-03-08", count: "25", unit: "count"),
            ],
            "F154_HEALTH_NORMALIZATION_MISSING: DST/local-day aggregation drift"
        )
    }

    func testSleepIntervalsAreClippedAndUnionedWithoutInBedDoubleCount() throws {
        let window = try HealthReadWindow(
            start: date("2026-03-09T00:00:00Z"),
            end: date("2026-03-09T08:00:00Z"),
            preset: .last24Hours,
            referenceNow: date("2026-03-09T08:00:00Z")
        )
        let samples = [
            SleepSample(start: date("2026-03-08T23:00:00Z"), end: date("2026-03-09T02:00:00Z"), category: .deep),
            SleepSample(start: date("2026-03-09T01:00:00Z"), end: date("2026-03-09T03:00:00Z"), category: .deep),
            SleepSample(start: date("2026-03-09T03:00:00Z"), end: date("2026-03-09T04:00:00Z"), category: .core),
            SleepSample(start: date("2026-03-09T00:00:00Z"), end: date("2026-03-09T05:00:00Z"), category: .inBed),
            SleepSample(start: date("2026-03-09T04:00:00Z"), end: date("2026-03-09T04:30:00Z"), category: .awake),
        ]
        let summary = try HealthImportNormalizer.sleepSummary(samples, window: window)
        XCTAssertEqual(summary.totalAsleepMinutes, "240", "F154_HEALTH_NORMALIZATION_MISSING: sleep union drift")
        XCTAssertEqual(summary.stageMinutes.deep, "180", "F154_HEALTH_NORMALIZATION_MISSING: deep overlap drift")
        XCTAssertEqual(summary.stageMinutes.core, "60", "F154_HEALTH_NORMALIZATION_MISSING: core duration drift")
        XCTAssertEqual(summary.stageMinutes.awake, "30", "F154_HEALTH_NORMALIZATION_MISSING: awake duration drift")
    }

    func testNormalizationRejectsInvalidOrOutOfWindowSamples() throws {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = utc
        let window = try HealthReadWindow(
            start: date("2026-03-09T00:00:00Z"),
            end: date("2026-03-10T00:00:00Z"),
            preset: .last24Hours,
            referenceNow: date("2026-03-10T00:00:00Z")
        )
        var issues: [String] = []
        expectThrows(&issues, "negative step") {
            _ = try HealthImportNormalizer.dailySteps(
                [StepSample(start: window.start, end: window.end, count: -1)],
                window: window,
                calendar: calendar
            )
        }
        expectThrows(&issues, "future step") {
            _ = try HealthImportNormalizer.dailySteps(
                [StepSample(start: window.start, end: window.end.addingTimeInterval(1), count: 1)],
                window: window,
                calendar: calendar
            )
        }
        expectThrows(&issues, "inverted sleep") {
            _ = try HealthImportNormalizer.sleepSummary(
                [SleepSample(start: window.end, end: window.start, category: .unspecified)],
                window: window
            )
        }
        XCTAssertTrue(
            issues.isEmpty,
            "F154_HEALTH_NORMALIZATION_MISSING: \(issues.joined(separator: "; "))"
        )
    }

    func testHealthStoreRequiresExplicitReadOnlyAuthorization() async throws {
        let fixture = try healthStoreFixture()
        let store = fixture.store
        let backend = fixture.backend
        var issues: [String] = []

        if !store.isAvailable() {
            issues.append("available backend reported unavailable")
        }
        do {
            _ = try await store.readPreview(window: fixture.window, calendar: fixture.calendar)
            issues.append("read succeeded before explicit authorization")
        } catch let error as HealthDataStoreError where error == .authorizationRequired {
        } catch {
            issues.append("pre-authorization read returned wrong error")
        }
        if !backend.authorizationCalls.isEmpty {
            issues.append("read triggered authorization implicitly")
        }

        do {
            try await store.requestReadAuthorization(for: Set(HealthDataType.allCases))
            let preview = try await store.readPreview(window: fixture.window, calendar: fixture.calendar)
            if preview.dataTypes != [.sleepAnalysis, .stepCount] || preview.dailySteps.count != 1 {
                issues.append("preview omitted exact normalized types")
            }
        } catch {
            issues.append("explicit authorization/read failed: \(error)")
        }
        if backend.authorizationCalls != [
            .init(read: Set(HealthDataType.allCases), share: [])
        ] {
            issues.append("authorization was not exact read-only")
        }
        if backend.stepReads != 1 || backend.sleepReads != 1 {
            issues.append("preview did not execute one bounded query per type")
        }

        XCTAssertTrue(
            issues.isEmpty,
            "F154_HEALTH_STORE_CONTRACT_MISSING: \(issues.joined(separator: "; "))"
        )
    }

    func testHealthStoreReportsUnavailableAndEmptyResultsHonestly() async throws {
        let unavailable = FakeHealthKitBackend(available: false)
        let unavailableStore = AppleHealthDataStore(backend: unavailable)
        var issues: [String] = []
        do {
            try await unavailableStore.requestReadAuthorization(for: Set(HealthDataType.allCases))
            issues.append("unavailable store authorized")
        } catch let error as HealthDataStoreError where error == .unavailable {
        } catch {
            issues.append("unavailable store returned wrong error")
        }

        let fixture = try healthStoreFixture(steps: [], sleep: [])
        do {
            try await fixture.store.requestReadAuthorization(for: Set(HealthDataType.allCases))
            do {
                _ = try await fixture.store.readPreview(
                    window: fixture.window,
                    calendar: fixture.calendar
                )
                issues.append("empty query produced a preview")
            } catch let error as HealthDataStoreError where error == .noReadableDataOrLimitedAccess {
            } catch {
                issues.append("empty query claimed a permission verdict")
            }
        } catch {
            issues.append("available empty store failed authorization")
        }
        do {
            try await fixture.store.requestReadAuthorization(for: [.stepCount])
            issues.append("partial type request was accepted")
        } catch let error as HealthDataStoreError where error == .invalidReadTypes {
        } catch {
            issues.append("partial type request returned wrong error")
        }

        XCTAssertTrue(
            issues.isEmpty,
            "F154_HEALTH_STORE_CONTRACT_MISSING: \(issues.joined(separator: "; "))"
        )
    }

    @MainActor
    func testCoordinatorFiniteReadCancelExpiryAndSessionCleanup() async throws {
        let fixture = try healthStoreFixture()
        let now = fixture.window.end
        let coordinator = HealthImportCoordinator(store: fixture.store, now: { now })
        var issues: [String] = []

        if HealthImportPhase.allCases != [
            .unavailable, .idle, .requestingPermission,
            .noReadableDataOrLimitedAccess, .reviewing, .submitting,
            .analyzing, .completed, .offline, .revoked, .deleting,
            .deletionFailed,
        ] {
            issues.append("phase set is not exact")
        }
        await coordinator.userRequestedRead(window: fixture.window, calendar: fixture.calendar)
        if coordinator.phase != .reviewing || coordinator.preview == nil {
            issues.append("user read did not reach reviewing")
        }
        coordinator.cancel()
        if coordinator.phase != .idle || coordinator.preview != nil {
            issues.append("cancel retained normalized preview")
        }

        await coordinator.userRequestedRead(window: fixture.window, calendar: fixture.calendar)
        if let expiry = coordinator.preview.flatMap({
            ISO8601DateFormatter().date(from: $0.expiresAtUtc)
        }) {
            if !coordinator.expirePreviewIfNeeded(at: expiry) {
                issues.append("expiry did not report cleanup")
            }
        } else {
            issues.append("read did not produce an expirable preview")
        }
        if coordinator.phase != .idle || coordinator.preview != nil {
            issues.append("expiry retained normalized preview")
        }

        await coordinator.userRequestedRead(window: fixture.window, calendar: fixture.calendar)
        coordinator.sessionDidEnd()
        if coordinator.phase != .idle || coordinator.preview != nil {
            issues.append("session end retained normalized preview")
        }
        XCTAssertTrue(
            issues.isEmpty,
            "F154_HEALTH_COORDINATOR_MISSING: \(issues.joined(separator: "; "))"
        )
    }

    @MainActor
    func testCoordinatorOfflineRevokedSubmissionAndDeletionAreFailClosed() async throws {
        let fixture = try healthStoreFixture()
        let coordinator = HealthImportCoordinator(store: fixture.store, now: { fixture.window.end })
        var issues: [String] = []

        let unavailable = HealthImportCoordinator(
            store: AppleHealthDataStore(backend: FakeHealthKitBackend(available: false))
        )
        await unavailable.userRequestedRead(window: fixture.window, calendar: fixture.calendar)
        if unavailable.phase != .unavailable || unavailable.preview != nil {
            issues.append("unavailable Health data was not reported honestly")
        }
        let emptyFixture = try healthStoreFixture(steps: [], sleep: [])
        let empty = HealthImportCoordinator(store: emptyFixture.store)
        await empty.userRequestedRead(window: emptyFixture.window, calendar: emptyFixture.calendar)
        if empty.phase != .noReadableDataOrLimitedAccess || empty.preview != nil {
            issues.append("empty or limited Health data was misreported")
        }

        await coordinator.userRequestedRead(window: fixture.window, calendar: fixture.calendar)
        coordinator.connectivityDidChange(isOnline: false)
        if coordinator.phase != .offline || coordinator.preview == nil {
            issues.append("offline transition discarded local deletion input")
        }
        if coordinator.beginSubmission() != nil || coordinator.phase != .offline {
            issues.append("offline preview entered submission")
        }
        coordinator.connectivityDidChange(isOnline: true)
        if coordinator.phase != .reviewing {
            issues.append("online recovery did not restore review")
        }
        coordinator.deviceWasRevoked()
        if coordinator.phase != .revoked || coordinator.preview == nil {
            issues.append("revocation lost local deletion capability")
        }
        if coordinator.beginSubmission() != nil {
            issues.append("revoked preview entered submission")
        }
        coordinator.deleteLocalPreview()
        if coordinator.phase != .revoked || coordinator.preview != nil {
            issues.append("local delete failed after revocation")
        }

        let onlineFixture = try healthStoreFixture()
        let online = HealthImportCoordinator(
            store: onlineFixture.store,
            now: { onlineFixture.window.end }
        )
        await online.userRequestedRead(window: onlineFixture.window, calendar: onlineFixture.calendar)
        if online.beginSubmission() == nil || online.phase != .submitting {
            issues.append("review did not enter submission")
        }
        online.beginAnalysis()
        if online.phase != .analyzing {
            issues.append("submission did not enter analysis")
        }
        online.analysisDidComplete()
        if online.phase != .completed || online.preview != nil {
            issues.append("analysis completion retained preview")
        }
        online.beginDeletion()
        online.deletionDidFail()
        if online.phase != .deletionFailed {
            issues.append("deletion failure was reported as complete")
        }
        online.beginDeletion()
        online.deletionDidComplete()
        if online.phase != .idle || online.preview != nil {
            issues.append("deletion completion did not clear local state")
        }

        XCTAssertTrue(
            issues.isEmpty,
            "F154_HEALTH_COORDINATOR_MISSING: \(issues.joined(separator: "; "))"
        )
    }

    @MainActor
    func testHealthSwiftUIPresentationAndConnectedEntryContract() throws {
        var issues: [String] = []
        let expectedActions: [HealthImportPhase: String?] = [
            .unavailable: nil,
            .idle: "从 Apple 健康读取",
            .requestingPermission: nil,
            .noReadableDataOrLimitedAccess: "重新读取",
            .reviewing: "批准并分析",
            .submitting: nil,
            .analyzing: nil,
            .completed: "删除这次数据",
            .offline: "网络恢复后重试",
            .revoked: "删除本地预览",
            .deleting: nil,
            .deletionFailed: "重试删除",
        ]
        for phase in HealthImportPhase.allCases {
            let presentation = HealthReviewPresentation(phase: phase)
            if presentation.title.isEmpty
                || presentation.detail.isEmpty
                || presentation.statusLabel.isEmpty
                || presentation.symbol.isEmpty
            {
                issues.append("\(phase.rawValue) lacks ordinary-language presentation")
            }
            if presentation.actionTitle != expectedActions[phase] {
                issues.append("\(phase.rawValue) action is not exact")
            }
        }

        let healthSource = try sourceText("OctoAgent/Health/HealthReviewView.swift")
        let registrationSource = try sourceText("OctoAgent/App/RegistrationView.swift")
        for required in [
            "不会写入 Apple 健康",
            "health-window-picker",
            "health-preview-card",
            "health-primary-action",
            "health-delete-action",
        ] where !healthSource.contains(required) {
            issues.append("health view is missing \(required)")
        }
        for required in ["NavigationLink", "健康概览", "health-entry"]
        where !registrationSource.contains(required) {
            issues.append("connected entry is missing \(required)")
        }
        if healthSource.contains("WebView") || healthSource.contains("WKWebView") {
            issues.append("native health view escaped to WebView")
        }

        XCTAssertTrue(
            issues.isEmpty,
            "F154_HEALTH_UI_MISSING: \(issues.joined(separator: "; "))"
        )
    }

    private func makePreview(window: HealthReadWindow, stepCount: String) throws -> HealthPreview {
        try HealthPreview.make(
            previewID: "preview-1",
            capturedAt: window.end,
            window: window,
            dataTypes: [.sleepAnalysis, .stepCount],
            dailySteps: [DailyStepSummary(localDay: "2026-03-09", count: stepCount, unit: "count")],
            sleep: nil,
            completenessNotice: "数据可能不完整",
            expiresAt: window.end.addingTimeInterval(3_600)
        )
    }

    private func date(_ value: String) -> Date {
        ISO8601DateFormatter().date(from: value)!
    }

    private func sourceText(_ relativePath: String) throws -> String {
        let tests = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        let appRoot = tests.deletingLastPathComponent()
        return try String(
            contentsOf: appRoot.appendingPathComponent(relativePath),
            encoding: .utf8
        )
    }

    private func healthStoreFixture(
        steps: [StepSample]? = nil,
        sleep: [SleepSample]? = nil
    ) throws -> HealthStoreFixture {
        let now = date("2026-03-10T00:00:00Z")
        let window = try HealthReadWindow(
            start: date("2026-03-09T00:00:00Z"),
            end: now,
            preset: .last24Hours,
            referenceNow: now
        )
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = utc
        let backend = FakeHealthKitBackend(
            available: true,
            steps: steps ?? [StepSample(start: window.start, end: window.end, count: 42)],
            sleep: sleep ?? [
                SleepSample(
                    start: date("2026-03-09T01:00:00Z"),
                    end: date("2026-03-09T02:00:00Z"),
                    category: .deep
                )
            ]
        )
        return HealthStoreFixture(
            store: AppleHealthDataStore(backend: backend, now: { now }, previewID: { "preview-1" }),
            backend: backend,
            window: window,
            calendar: calendar
        )
    }

    private func expectThrows(
        _ issues: inout [String],
        _ label: String,
        operation: () throws -> Void
    ) {
        do {
            try operation()
            issues.append("\(label) was accepted")
        } catch {}
    }
}

private struct AuthorizationCall: Equatable {
    let read: Set<HealthDataType>
    let share: Set<HealthDataType>
}

private struct HealthStoreFixture {
    let store: AppleHealthDataStore
    let backend: FakeHealthKitBackend
    let window: HealthReadWindow
    let calendar: Calendar
}

private final class FakeHealthKitBackend: HealthKitBackend {
    let available: Bool
    let steps: [StepSample]
    let sleep: [SleepSample]
    var authorizationCalls: [AuthorizationCall] = []
    var stepReads = 0
    var sleepReads = 0

    init(
        available: Bool,
        steps: [StepSample] = [],
        sleep: [SleepSample] = []
    ) {
        self.available = available
        self.steps = steps
        self.sleep = sleep
    }

    func isAvailable() -> Bool {
        available
    }

    func requestAuthorization(
        readTypes: Set<HealthDataType>,
        shareTypes: Set<HealthDataType>
    ) async throws {
        authorizationCalls.append(.init(read: readTypes, share: shareTypes))
    }

    func readSteps(window: HealthReadWindow) async throws -> [StepSample] {
        stepReads += 1
        return steps
    }

    func readSleep(window: HealthReadWindow) async throws -> [SleepSample] {
        sleepReads += 1
        return sleep
    }
}
