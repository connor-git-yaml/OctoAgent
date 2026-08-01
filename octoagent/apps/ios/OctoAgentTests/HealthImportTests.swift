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
