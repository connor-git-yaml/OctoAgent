import Combine
import Foundation

enum HealthImportPhase: String, CaseIterable {
    case unavailable
    case idle
    case requestingPermission
    case noReadableDataOrLimitedAccess
    case reviewing
    case submitting
    case analyzing
    case completed
    case offline
    case revoked
    case deleting
    case deletionFailed
}

@MainActor
final class HealthImportCoordinator: ObservableObject {
    private let store: HealthDataStore
    private let now: () -> Date
    @Published private(set) var phase: HealthImportPhase = .idle
    @Published private(set) var preview: HealthPreview?
    @Published private(set) var analysisSummary: String?
    @Published private(set) var notice = ""
    private var operationGeneration = 0
    private var isOnline = true
    private var isRevoked = false

    init(store: HealthDataStore, now: @escaping () -> Date = Date.init) {
        self.store = store
        self.now = now
    }

    func userRequestedRead(window: HealthReadWindow, calendar: Calendar) async {
        guard !isRevoked else {
            phase = .revoked
            return
        }
        guard store.isAvailable() else {
            clearPreview()
            phase = .unavailable
            return
        }
        operationGeneration += 1
        let generation = operationGeneration
        preview = nil
        analysisSummary = nil
        notice = ""
        phase = .requestingPermission
        do {
            try await store.requestReadAuthorization(for: Set(HealthDataType.allCases))
            guard generation == operationGeneration else { return }
            let candidate = try await store.readPreview(window: window, calendar: calendar)
            guard generation == operationGeneration else { return }
            preview = candidate
            phase = .reviewing
        } catch let error as HealthDataStoreError {
            guard generation == operationGeneration else { return }
            clearPreview()
            phase = phase(for: error)
        } catch {
            guard generation == operationGeneration else { return }
            clearPreview()
            phase = baselinePhase
        }
    }

    func cancel() {
        invalidateOperations()
        clearPreview()
        phase = baselinePhase
    }

    func expirePreviewIfNeeded(at date: Date) -> Bool {
        guard let preview,
              let expiry = ISO8601DateFormatter().date(from: preview.expiresAtUtc),
              date >= expiry
        else {
            return false
        }
        invalidateOperations()
        clearPreview()
        phase = baselinePhase
        return true
    }

    func sessionDidEnd() {
        invalidateOperations()
        clearPreview()
        phase = baselinePhase
    }

    func connectivityDidChange(isOnline: Bool) {
        self.isOnline = isOnline
        if isRevoked {
            phase = .revoked
        } else if !isOnline {
            phase = .offline
        } else if phase == .offline {
            phase = preview == nil ? .idle : .reviewing
        }
    }

    func deviceWasRevoked() {
        isRevoked = true
        invalidateOperations()
        phase = .revoked
    }

    func beginSubmission() -> HealthPreview? {
        _ = expirePreviewIfNeeded(at: now())
        guard let preview else { return nil }
        guard !isRevoked else {
            phase = .revoked
            return nil
        }
        guard isOnline else {
            phase = .offline
            return nil
        }
        phase = .submitting
        return preview
    }

    func beginAnalysis() {
        guard phase == .submitting, preview != nil, isOnline, !isRevoked else { return }
        phase = .analyzing
    }

    func analysisDidComplete(summary: String? = nil) {
        guard phase == .analyzing else { return }
        clearPreview()
        analysisSummary = summary
        notice = ""
        phase = .completed
    }

    func analysisDidFail(isOffline: Bool) {
        guard phase == .submitting || phase == .analyzing else { return }
        notice = isOffline ? "当前离线，预览仍只保存在这台 iPhone。" : "这次分析没有完成，请稍后重试。"
        phase = isOffline ? .offline : .reviewing
    }

    func beginDeletion() {
        invalidateOperations()
        phase = .deleting
    }

    func deletionDidFail() {
        guard phase == .deleting else { return }
        notice = "删除尚未完成，可以安全重试。"
        phase = .deletionFailed
    }

    func deletionDidComplete() {
        guard phase == .deleting else { return }
        clearPreview()
        analysisSummary = nil
        notice = ""
        phase = baselinePhase
    }

    func deleteLocalPreview() {
        invalidateOperations()
        clearPreview()
        phase = baselinePhase
    }

    private var baselinePhase: HealthImportPhase {
        if isRevoked { return .revoked }
        if !isOnline { return .offline }
        return .idle
    }

    private func phase(for error: HealthDataStoreError) -> HealthImportPhase {
        switch error {
        case .unavailable:
            .unavailable
        case .noReadableDataOrLimitedAccess:
            .noReadableDataOrLimitedAccess
        case .authorizationRequired, .invalidReadTypes, .queryFailed:
            baselinePhase
        }
    }

    private func invalidateOperations() {
        operationGeneration += 1
    }

    private func clearPreview() {
        preview = nil
    }
}
