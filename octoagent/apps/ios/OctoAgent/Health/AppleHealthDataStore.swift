import Foundation
import HealthKit

enum HealthDataStoreError: Error, Equatable {
    case unavailable
    case authorizationRequired
    case invalidReadTypes
    case noReadableDataOrLimitedAccess
    case queryFailed
}

protocol HealthDataStore {
    func isAvailable() -> Bool
    func requestReadAuthorization(for types: Set<HealthDataType>) async throws
    func readPreview(window: HealthReadWindow, calendar: Calendar) async throws -> HealthPreview
}

protocol HealthKitBackend: AnyObject {
    func isAvailable() -> Bool
    func requestAuthorization(
        readTypes: Set<HealthDataType>,
        shareTypes: Set<HealthDataType>
    ) async throws
    func readSteps(window: HealthReadWindow) async throws -> [StepSample]
    func readSleep(window: HealthReadWindow) async throws -> [SleepSample]
}

final class AppleHealthDataStore: HealthDataStore {
    private let backend: HealthKitBackend
    private let now: () -> Date
    private let previewID: () -> String
    private var authorizationRequested = false

    convenience init() {
        self.init(backend: SystemHealthKitBackend())
    }

    init(
        backend: HealthKitBackend,
        now: @escaping () -> Date = Date.init,
        previewID: @escaping () -> String = { UUID().uuidString.lowercased() }
    ) {
        self.backend = backend
        self.now = now
        self.previewID = previewID
    }

    func isAvailable() -> Bool {
        backend.isAvailable()
    }

    func requestReadAuthorization(for types: Set<HealthDataType>) async throws {
        guard isAvailable() else { throw HealthDataStoreError.unavailable }
        guard types == Set(HealthDataType.allCases) else {
            throw HealthDataStoreError.invalidReadTypes
        }
        do {
            try await backend.requestAuthorization(readTypes: types, shareTypes: [])
            authorizationRequested = true
        } catch let error as HealthDataStoreError {
            throw error
        } catch {
            throw HealthDataStoreError.queryFailed
        }
    }

    func readPreview(
        window: HealthReadWindow,
        calendar: Calendar
    ) async throws -> HealthPreview {
        guard isAvailable() else { throw HealthDataStoreError.unavailable }
        guard authorizationRequested else {
            throw HealthDataStoreError.authorizationRequired
        }
        let steps: [StepSample]
        let sleep: [SleepSample]
        do {
            steps = try await backend.readSteps(window: window)
            sleep = try await backend.readSleep(window: window)
        } catch {
            throw HealthDataStoreError.queryFailed
        }
        guard !steps.isEmpty || !sleep.isEmpty else {
            throw HealthDataStoreError.noReadableDataOrLimitedAccess
        }
        let capturedAt = now()
        return try HealthPreview.make(
            previewID: previewID(),
            capturedAt: capturedAt,
            window: window,
            dataTypes: readableTypes(steps: steps, sleep: sleep),
            dailySteps: HealthImportNormalizer.dailySteps(
                steps,
                window: window,
                calendar: calendar
            ),
            sleep: sleep.isEmpty
                ? nil
                : HealthImportNormalizer.sleepSummary(sleep, window: window),
            completenessNotice: "Apple 健康数据可能不完整或受读取权限限制",
            expiresAt: capturedAt.addingTimeInterval(86_400)
        )
    }

    private func readableTypes(
        steps: [StepSample],
        sleep: [SleepSample]
    ) -> [HealthDataType] {
        var result: [HealthDataType] = []
        if !sleep.isEmpty { result.append(.sleepAnalysis) }
        if !steps.isEmpty { result.append(.stepCount) }
        return result
    }
}

final class SystemHealthKitBackend: HealthKitBackend {
    private let store: HKHealthStore

    init(store: HKHealthStore = HKHealthStore()) {
        self.store = store
    }

    func isAvailable() -> Bool {
        HKHealthStore.isHealthDataAvailable()
    }

    func requestAuthorization(
        readTypes: Set<HealthDataType>,
        shareTypes: Set<HealthDataType>
    ) async throws {
        guard shareTypes.isEmpty, readTypes == Set(HealthDataType.allCases) else {
            throw HealthDataStoreError.invalidReadTypes
        }
        try await store.requestAuthorization(
            toShare: Set<HKSampleType>(),
            read: try objectTypes(readTypes)
        )
    }

    func readSteps(window: HealthReadWindow) async throws -> [StepSample] {
        guard let type = HKObjectType.quantityType(forIdentifier: .stepCount) else {
            throw HealthDataStoreError.unavailable
        }
        return try await samples(type: type, window: window).map { rawSample in
            guard let sample = rawSample as? HKQuantitySample else {
                throw HealthDataStoreError.queryFailed
            }
            let count = sample.quantity.doubleValue(for: .count())
            guard count.isFinite,
                  count >= 0,
                  let decimal = Decimal(
                      string: String(count),
                      locale: Locale(identifier: "en_US_POSIX")
                  )
            else {
                throw HealthModelError.invalidSample
            }
            return StepSample(start: sample.startDate, end: sample.endDate, count: decimal)
        }
    }

    func readSleep(window: HealthReadWindow) async throws -> [SleepSample] {
        guard let type = HKObjectType.categoryType(forIdentifier: .sleepAnalysis) else {
            throw HealthDataStoreError.unavailable
        }
        return try await samples(type: type, window: window).map { rawSample in
            guard let sample = rawSample as? HKCategorySample,
                  let category = sleepCategory(sample.value)
            else {
                throw HealthModelError.invalidSample
            }
            return SleepSample(
                start: sample.startDate,
                end: sample.endDate,
                category: category
            )
        }
    }

    private func objectTypes(
        _ types: Set<HealthDataType>
    ) throws -> Set<HKObjectType> {
        var result: Set<HKObjectType> = []
        for type in types {
            switch type {
            case .stepCount:
                guard let value = HKObjectType.quantityType(forIdentifier: .stepCount) else {
                    throw HealthDataStoreError.unavailable
                }
                result.insert(value)
            case .sleepAnalysis:
                guard let value = HKObjectType.categoryType(forIdentifier: .sleepAnalysis) else {
                    throw HealthDataStoreError.unavailable
                }
                result.insert(value)
            }
        }
        return result
    }

    private func samples(
        type: HKSampleType,
        window: HealthReadWindow
    ) async throws -> [HKSample] {
        let predicate = HKQuery.predicateForSamples(
            withStart: window.start,
            end: window.end,
            options: [.strictStartDate]
        )
        return try await withCheckedThrowingContinuation { continuation in
            let query = HKSampleQuery(
                sampleType: type,
                predicate: predicate,
                limit: HKObjectQueryNoLimit,
                sortDescriptors: nil
            ) { _, samples, error in
                if let error {
                    continuation.resume(throwing: error)
                } else {
                    continuation.resume(returning: samples ?? [])
                }
            }
            store.execute(query)
        }
    }

    private func sleepCategory(_ rawValue: Int) -> HealthSleepCategory? {
        switch rawValue {
        case HKCategoryValueSleepAnalysis.inBed.rawValue:
            .inBed
        case HKCategoryValueSleepAnalysis.asleepUnspecified.rawValue:
            .unspecified
        case HKCategoryValueSleepAnalysis.awake.rawValue:
            .awake
        case HKCategoryValueSleepAnalysis.asleepCore.rawValue:
            .core
        case HKCategoryValueSleepAnalysis.asleepDeep.rawValue:
            .deep
        case HKCategoryValueSleepAnalysis.asleepREM.rawValue:
            .rem
        default:
            nil
        }
    }
}
