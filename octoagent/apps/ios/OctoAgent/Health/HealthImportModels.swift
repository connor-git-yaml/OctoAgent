import Foundation
import CryptoKit

enum HealthModelError: Error, Equatable {
    case invalidWindow
    case invalidSample
}

struct HealthReadWindow: Codable, Equatable {
    enum Preset: String, Codable, CaseIterable {
        case last24Hours
        case last3Days
        case last7Days
    }

    let start: Date
    let end: Date
    let preset: Preset

    init(start: Date, end: Date, preset: Preset, referenceNow: Date) throws {
        let duration = end.timeIntervalSince(start)
        guard duration > 0, duration <= 7 * 86_400, end <= referenceNow else {
            throw HealthModelError.invalidWindow
        }
        self.start = start
        self.end = end
        self.preset = preset
    }
}

enum HealthDataType: String, Codable, CaseIterable {
    case stepCount
    case sleepAnalysis
}

struct DailyStepSummary: Codable, Equatable {
    let localDay: String
    let count: String
    let unit: String
}

enum SleepStage: String, Codable, CaseIterable {
    case awake
    case core
    case deep
    case rem
    case unspecified
}

struct SleepStageMinutes: Codable, Equatable {
    let awake: String
    let core: String
    let deep: String
    let rem: String
    let unspecified: String
}

struct SleepSummary: Codable, Equatable {
    let windowStartUtc: String
    let windowEndUtc: String
    let totalAsleepMinutes: String
    let stageMinutes: SleepStageMinutes
    let unit: String
}

struct HealthPreview: Codable, Equatable {
    let previewID: String
    let capturedAtUtc: String
    let window: HealthReadWindow
    let dataTypes: [HealthDataType]
    let dailySteps: [DailyStepSummary]
    let sleep: SleepSummary?
    let completenessNotice: String
    let canonicalSha256: String
    let expiresAtUtc: String

    static func make(
        previewID: String,
        capturedAt: Date,
        window: HealthReadWindow,
        dataTypes: [HealthDataType],
        dailySteps: [DailyStepSummary],
        sleep: SleepSummary?,
        completenessNotice: String,
        expiresAt: Date
    ) throws -> HealthPreview {
        guard !previewID.isEmpty,
              !completenessNotice.isEmpty,
              expiresAt > capturedAt,
              expiresAt.timeIntervalSince(capturedAt) <= 86_400
        else {
            throw HealthModelError.invalidSample
        }
        let orderedTypes = dataTypes.sorted { $0.rawValue < $1.rawValue }
        let orderedSteps = dailySteps.sorted { $0.localDay < $1.localDay }
        let capturedAtUtc = HealthCanonicalValue.utc(capturedAt)
        let expiresAtUtc = HealthCanonicalValue.utc(expiresAt)
        let payload = HealthPreviewPayload(
            previewID: previewID,
            capturedAtUtc: capturedAtUtc,
            windowStartUtc: HealthCanonicalValue.utc(window.start),
            windowEndUtc: HealthCanonicalValue.utc(window.end),
            windowPreset: window.preset.rawValue,
            dataTypes: orderedTypes.map(\.rawValue),
            dailySteps: orderedSteps,
            sleep: sleep,
            completenessNotice: completenessNotice,
            expiresAtUtc: expiresAtUtc
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        let digest = SHA256.hash(data: try encoder.encode(payload))
        let hash = digest.map { String(format: "%02x", $0) }.joined()
        return HealthPreview(
            previewID: previewID,
            capturedAtUtc: capturedAtUtc,
            window: window,
            dataTypes: orderedTypes,
            dailySteps: orderedSteps,
            sleep: sleep,
            completenessNotice: completenessNotice,
            canonicalSha256: hash,
            expiresAtUtc: expiresAtUtc
        )
    }
}

struct HealthReviewAccepted: Decodable, Equatable {
    let bundleSHA256: String
    let expiresAt: Date

    enum CodingKeys: String, CodingKey {
        case bundleSHA256 = "bundle_sha256"
        case expiresAt = "expires_at"
    }
}

struct HealthAnalysisAccepted: Decodable, Equatable {
    let summary: String
}

struct HealthDeletionReceipt: Decodable, Equatable {
    let sourceHash: String
    let status: String

    enum CodingKeys: String, CodingKey {
        case sourceHash = "source_hash"
        case status
    }
}

enum HealthTransportPayloadError: Error, Equatable {
    case invalidPreview
    case invalidResponse
}

enum HealthTransportPayload {
    static let purpose = "summarize_recent_activity_and_sleep"
    static let capabilities = [
        "device.profile.read",
        "device.ready.read",
        "health.analysis.run",
        "health.review.submit",
        "health.source.delete",
    ]

    private static let dataTypes = [
        "apple_health.sleep_analysis",
        "apple_health.step_count",
    ]
    private static let fieldManifest = [
        "daily_steps[].local_day",
        "daily_steps[].count",
        "daily_steps[].unit",
        "sleep.window_start_utc",
        "sleep.window_end_utc",
        "sleep.total_asleep_minutes",
        "sleep.stage_minutes.awake",
        "sleep.stage_minutes.core",
        "sleep.stage_minutes.deep",
        "sleep.stage_minutes.rem",
        "sleep.stage_minutes.unspecified",
        "sleep.unit",
        "completeness_notice",
    ]

    static func review(
        preview: HealthPreview,
        ownerID: String,
        deviceID: String,
        approvedAt: Date
    ) throws -> Data {
        let provenance = try provenance(
            preview: preview,
            ownerID: ownerID,
            deviceID: deviceID
        )
        let expiry = approvedAt.addingTimeInterval(86_400)
        return try canonicalData([
            "stage": "review_bundle",
            "bundle_id": "health-review-\(UUID().uuidString.lowercased())",
            "purpose": purpose,
            "provenance": [provenance],
            "fact_count": preview.dailySteps.count + (preview.sleep == nil ? 0 : 1),
            "field_manifest": fieldManifest,
            "preview_hash": preview.canonicalSha256,
            "retention": retention(
                stage: "review_bundle",
                createdAt: approvedAt,
                expiresAt: expiry
            ),
        ])
    }

    static func analysis(
        preview: HealthPreview,
        sourceHash: String,
        ownerID: String,
        deviceID: String,
        approvedAt: Date
    ) throws -> Data {
        guard isSHA256(sourceHash) else {
            throw HealthTransportPayloadError.invalidResponse
        }
        let provenance = try provenance(
            preview: preview,
            ownerID: ownerID,
            deviceID: deviceID
        )
        let consentID = "health-consent-\(UUID().uuidString.lowercased())"
        let expiry = approvedAt.addingTimeInterval(15 * 60)
        let facts = approvedFacts(preview: preview)
        let packet: [String: Any] = [
            "stage": "approved_analysis_packet",
            "packet_id": "health-packet-\(UUID().uuidString.lowercased())",
            "purpose": purpose,
            "facts_sha256": sha256Hex(try canonicalData(facts)),
            "provenance": [provenance],
            "consent_id": consentID,
            "retention": retention(
                stage: "approved_analysis_packet",
                createdAt: approvedAt,
                expiresAt: expiry
            ),
        ]
        return try canonicalData([
            "source_hash": sourceHash,
            "consent": [
                "consent_id": consentID,
                "bundle_sha256": sourceHash,
                "approved_packet_sha256": sha256Hex(try canonicalData(packet)),
                "purpose": purpose,
                "owner_id": ownerID,
                "device_id": deviceID,
                "approved_at": HealthCanonicalValue.utc(approvedAt),
                "expires_at": HealthCanonicalValue.utc(expiry),
                "used_at": NSNull(),
            ],
            "packet": packet,
            "approved_facts": facts,
        ])
    }

    private static func provenance(
        preview: HealthPreview,
        ownerID: String,
        deviceID: String
    ) throws -> [String: Any] {
        guard
            !ownerID.isEmpty,
            !deviceID.isEmpty,
            preview.dataTypes == [.sleepAnalysis, .stepCount],
            isSHA256(preview.canonicalSha256)
        else {
            throw HealthTransportPayloadError.invalidPreview
        }
        return [
            "source_kind": "healthkit",
            "source_object_hash": preview.canonicalSha256,
            "owner_id": ownerID,
            "device_id": deviceID,
            "captured_at": preview.capturedAtUtc,
            "time_range": [
                "start": HealthCanonicalValue.utc(preview.window.start),
                "end": HealthCanonicalValue.utc(preview.window.end),
            ],
            "data_types": dataTypes,
        ]
    }

    private static func approvedFacts(preview: HealthPreview) -> [String: Any] {
        var result: [String: Any] = [
            "purpose": purpose,
            "time_range": [
                "start": HealthCanonicalValue.utc(preview.window.start),
                "end": HealthCanonicalValue.utc(preview.window.end),
            ],
            "daily_steps": preview.dailySteps.map {
                ["local_day": $0.localDay, "count": $0.count, "unit": $0.unit]
            },
            "completeness_notice": preview.completenessNotice,
        ]
        if let sleep = preview.sleep {
            result["sleep"] = [
                "window_start_utc": sleep.windowStartUtc,
                "window_end_utc": sleep.windowEndUtc,
                "total_asleep_minutes": sleep.totalAsleepMinutes,
                "stage_minutes": [
                    "awake": sleep.stageMinutes.awake,
                    "core": sleep.stageMinutes.core,
                    "deep": sleep.stageMinutes.deep,
                    "rem": sleep.stageMinutes.rem,
                    "unspecified": sleep.stageMinutes.unspecified,
                ],
                "unit": sleep.unit,
            ]
        } else {
            result["sleep"] = NSNull()
        }
        return result
    }

    private static func retention(
        stage: String,
        createdAt: Date,
        expiresAt: Date
    ) -> [String: Any] {
        [
            "stage": stage,
            "created_at": HealthCanonicalValue.utc(createdAt),
            "expires_at": HealthCanonicalValue.utc(expiresAt),
            "session_ends_at": NSNull(),
        ]
    }

    private static func canonicalData(_ value: [String: Any]) throws -> Data {
        guard JSONSerialization.isValidJSONObject(value) else {
            throw HealthTransportPayloadError.invalidPreview
        }
        return try JSONSerialization.data(
            withJSONObject: value,
            options: [.sortedKeys, .withoutEscapingSlashes]
        )
    }

    private static func sha256Hex(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    private static func isSHA256(_ value: String) -> Bool {
        value.count == 64 && value.allSatisfy { $0.isNumber || ("a" ... "f").contains($0) }
    }
}

private struct HealthPreviewPayload: Encodable {
    let previewID: String
    let capturedAtUtc: String
    let windowStartUtc: String
    let windowEndUtc: String
    let windowPreset: String
    let dataTypes: [String]
    let dailySteps: [DailyStepSummary]
    let sleep: SleepSummary?
    let completenessNotice: String
    let expiresAtUtc: String
}

enum HealthCanonicalValue {
    static func decimal(_ value: Decimal) throws -> String {
        NSDecimalNumber(decimal: value).stringValue
    }

    static func utc(_ date: Date) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.string(from: Date(timeIntervalSince1970: floor(date.timeIntervalSince1970)))
    }
}

struct StepSample: Equatable {
    let start: Date
    let end: Date
    let count: Decimal
}

enum HealthSleepCategory: String, CaseIterable {
    case awake
    case core
    case deep
    case rem
    case unspecified
    case inBed
}

struct SleepSample: Equatable {
    let start: Date
    let end: Date
    let category: HealthSleepCategory
}

enum HealthImportNormalizer {
    static func dailySteps(
        _ samples: [StepSample],
        window: HealthReadWindow,
        calendar: Calendar
    ) throws -> [DailyStepSummary] {
        var totals: [String: Decimal] = [:]
        for sample in samples {
            guard sample.start < sample.end,
                  sample.start >= window.start,
                  sample.end <= window.end,
                  sample.count >= 0
            else {
                throw HealthModelError.invalidSample
            }
            let components = calendar.dateComponents([.year, .month, .day], from: sample.start)
            guard let year = components.year, let month = components.month, let day = components.day else {
                throw HealthModelError.invalidSample
            }
            let localDay = String(format: "%04d-%02d-%02d", year, month, day)
            totals[localDay, default: 0] += sample.count
        }
        return try totals.keys.sorted().map { localDay in
            DailyStepSummary(
                localDay: localDay,
                count: try HealthCanonicalValue.decimal(totals[localDay]!),
                unit: "count"
            )
        }
    }

    static func sleepSummary(
        _ samples: [SleepSample],
        window: HealthReadWindow
    ) throws -> SleepSummary {
        var intervals: [HealthSleepCategory: [DateInterval]] = [:]
        for sample in samples {
            guard sample.start < sample.end else {
                throw HealthModelError.invalidSample
            }
            let clippedStart = max(sample.start, window.start)
            let clippedEnd = min(sample.end, window.end)
            guard clippedStart < clippedEnd else {
                if sample.start >= window.end {
                    throw HealthModelError.invalidSample
                }
                continue
            }
            guard sample.category != .inBed else { continue }
            intervals[sample.category, default: []].append(
                DateInterval(start: clippedStart, end: clippedEnd)
            )
        }

        let stageDurations = try stageMinutes(intervals)
        let asleepIntervals = [.core, .deep, .rem, .unspecified]
            .flatMap { intervals[$0, default: []] }
        let totalAsleep = minutes(merged(asleepIntervals).reduce(0) { $0 + $1.duration })
        return SleepSummary(
            windowStartUtc: HealthCanonicalValue.utc(window.start),
            windowEndUtc: HealthCanonicalValue.utc(window.end),
            totalAsleepMinutes: try HealthCanonicalValue.decimal(totalAsleep),
            stageMinutes: stageDurations,
            unit: "min"
        )
    }

    private static func stageMinutes(
        _ intervals: [HealthSleepCategory: [DateInterval]]
    ) throws -> SleepStageMinutes {
        func value(_ category: HealthSleepCategory) throws -> String {
            let seconds = merged(intervals[category, default: []]).reduce(0) { $0 + $1.duration }
            return try HealthCanonicalValue.decimal(minutes(seconds))
        }
        return try SleepStageMinutes(
            awake: value(.awake),
            core: value(.core),
            deep: value(.deep),
            rem: value(.rem),
            unspecified: value(.unspecified)
        )
    }

    private static func merged(_ intervals: [DateInterval]) -> [DateInterval] {
        let ordered = intervals.sorted { $0.start < $1.start }
        return ordered.reduce(into: []) { result, interval in
            guard let previous = result.last, interval.start <= previous.end else {
                result.append(interval)
                return
            }
            result[result.count - 1] = DateInterval(
                start: previous.start,
                end: max(previous.end, interval.end)
            )
        }
    }

    private static func minutes(_ seconds: TimeInterval) -> Decimal {
        Decimal(Int64(seconds.rounded())) / 60
    }
}
