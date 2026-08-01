import CryptoKit
import Foundation
import Security

struct CanonicalRequestProof: Equatable, Sendable {
    let method: String
    let canonicalPath: String
    let bodySHA256: String
    let timestamp: String
    let nonce: String
    let tokenID: String

    func canonicalBytes() throws -> Data {
        try canonicalJSON([
            "body_sha256": bodySHA256,
            "canonical_path": canonicalPath,
            "method": method,
            "nonce": nonce,
            "timestamp": timestamp,
            "token_id": tokenID,
        ])
    }
}

struct CanonicalKeyRotationProof: Equatable, Sendable {
    let currentKeyThumbprint: String
    let deviceID: String
    let mobileOrigin: String
    let newPublicKeyX963: String
    let rotationChallengeID: String
    let serverChallengeSHA256: String
    let timestamp: String

    func canonicalBytes() throws -> Data {
        try canonicalJSON([
            "current_key_thumbprint": currentKeyThumbprint,
            "device_id": deviceID,
            "mobile_origin": mobileOrigin,
            "new_public_key_x963": newPublicKeyX963,
            "rotation_challenge_id": rotationChallengeID,
            "server_challenge_sha256": serverChallengeSHA256,
            "timestamp": timestamp,
        ])
    }
}

enum DeviceTrustClientError: Error, Equatable {
    case invalidOrigin
    case invalidKeyMaterial
    case invalidResponse
    case offline
    case cancelled
    case edgeContractMismatch
    case revoked
    case credentialsExpired
    case serverUnavailable
    case rejected(String)

    static func transport(_ error: Error) -> DeviceTrustClientError {
        if error is CancellationError || Task.isCancelled {
            return .cancelled
        }
        guard let urlError = error as? URLError else {
            return .serverUnavailable
        }
        switch urlError.code {
        case .cancelled:
            return .cancelled
        case .notConnectedToInternet, .networkConnectionLost, .cannotFindHost,
             .cannotConnectToHost, .dnsLookupFailed, .timedOut:
            return .offline
        default:
            return .serverUnavailable
        }
    }

    static func response(
        statusCode: Int,
        contentType: String?,
        data: Data
    ) -> DeviceTrustClientError {
        guard contentType?.lowercased().contains("application/json") == true else {
            return .edgeContractMismatch
        }
        if statusCode == 429 || statusCode >= 500 {
            return .serverUnavailable
        }
        let code = gatewayErrorCode(data)
        switch code {
        case "DEVICE_NOT_ACTIVE":
            return .revoked
        case "DEVICE_TOKEN_INVALID", "DEVICE_REQUEST_EXPIRED":
            return .credentialsExpired
        case let value?:
            return .rejected(value)
        default:
            return .invalidResponse
        }
    }
}

struct DeviceTrustRetryPolicy: Equatable, Sendable {
    let maxRetries: Int

    init(maxRetries: Int = 1) {
        self.maxRetries = min(max(maxRetries, 0), 1)
    }

    func shouldRetry(
        _ error: DeviceTrustClientError,
        attempt: Int,
        isIdempotent: Bool
    ) -> Bool {
        guard isIdempotent, attempt < maxRetries else {
            return false
        }
        return error == .offline || error == .serverUnavailable
    }
}

final class DeviceTrustClient {
    private let serverOrigin: URL
    private let session: URLSession
    private let signer: RequestProofSigner
    private let retryPolicy: DeviceTrustRetryPolicy
    private let now: () -> Date
    private let nonce: () throws -> String

    init(
        serverOrigin: URL,
        session: URLSession? = nil,
        signer: RequestProofSigner,
        retryPolicy: DeviceTrustRetryPolicy = DeviceTrustRetryPolicy(),
        now: @escaping () -> Date = Date.init,
        nonce: @escaping () throws -> String = secureNonce
    ) throws {
        guard let canonicalOrigin = canonicalHTTPSOrigin(serverOrigin) else {
            throw DeviceTrustClientError.invalidOrigin
        }
        self.serverOrigin = canonicalOrigin
        self.session = session ?? Self.makeSession()
        self.signer = signer
        self.retryPolicy = retryPolicy
        self.now = now
        self.nonce = nonce
    }

    func submitEnrollment(
        link: DeviceRegistrationLink,
        displayName: String,
        attestationState: DeviceAttestationState = .unsupported,
        attestationObject: String? = nil
    ) async throws -> DeviceEnrollmentStatus {
        guard canonicalHTTPSOrigin(link.mobileOrigin) == serverOrigin else {
            throw DeviceTrustClientError.invalidOrigin
        }
        let publicKey = try signer.publicKeyX963()
        guard publicKey.count == 65, publicKey.first == 0x04 else {
            throw DeviceTrustClientError.invalidKeyMaterial
        }
        let timestamp = utcSecond(now())
        let signaturePayload = try canonicalJSON([
            "challenge_id": link.challengeID,
            "challenge_secret_sha256": sha256Hex(Data(link.challengeSecret.utf8)),
            "display_name": displayName,
            "mobile_origin": serverOrigin.absoluteString,
            "public_key_x963": base64URL(publicKey),
            "timestamp": timestamp,
        ])
        let signature = try signer.sign(signaturePayload).signatureDER
        let body = EnrollmentRequest(
            challengeID: link.challengeID,
            challengeSecret: link.challengeSecret,
            displayName: displayName,
            mobileOrigin: serverOrigin.absoluteString,
            publicKeyX963: base64URL(publicKey),
            challengeSignatureDER: base64URL(signature),
            attestationState: attestationState,
            attestationObject: attestationObject,
            timestamp: timestamp
        )
        return try await request(
            path: "/api/mobile/v1/enrollments",
            method: "POST",
            body: encode(body),
            headers: [:],
            isIdempotent: false
        )
    }

    func issueToken(deviceID: String) async throws -> DeviceCredentials {
        try await issueToken(deviceID: deviceID, using: signer)
    }

    func rotateKey(
        credentials: DeviceCredentials,
        keyStore: DeviceKeyStore
    ) async throws -> DeviceCredentials {
        guard credentials.serverOrigin == serverOrigin else {
            throw DeviceTrustClientError.invalidOrigin
        }
        let safeDeviceID = try pathComponent(credentials.deviceID)
        let currentPublicKey = try signer.publicKeyX963()
        let currentThumbprint = sha256Hex(currentPublicKey)
        let rotation = try keyStore.beginKeyRotation()
        var cancelCandidate = true
        defer {
            if cancelCandidate {
                try? rotation.cancel()
            }
        }
        let newPublicKey = try rotation.publicKeyX963()
        let newThumbprint = sha256Hex(newPublicKey)
        guard currentThumbprint != newThumbprint else {
            throw DeviceTrustClientError.invalidKeyMaterial
        }
        let challenge: DeviceKeyRotationChallenge = try await request(
            path: "/api/mobile/v1/key-rotation-challenges/\(safeDeviceID)",
            method: "POST",
            body: nil,
            headers: [:],
            isIdempotent: false
        )
        guard
            challenge.deviceID == credentials.deviceID,
            challenge.mobileOrigin == serverOrigin.absoluteString
        else {
            throw DeviceTrustClientError.invalidResponse
        }
        let timestamp = utcSecond(now())
        let proof = CanonicalKeyRotationProof(
            currentKeyThumbprint: currentThumbprint,
            deviceID: challenge.deviceID,
            mobileOrigin: challenge.mobileOrigin,
            newPublicKeyX963: base64URL(newPublicKey),
            rotationChallengeID: challenge.rotationChallengeID,
            serverChallengeSHA256: sha256Hex(Data(challenge.serverChallenge.utf8)),
            timestamp: timestamp
        )
        let payload = try proof.canonicalBytes()
        let result: DeviceKeyRotationResult = try await request(
            path: "/api/mobile/v1/key-rotations",
            method: "POST",
            body: encode(
                KeyRotationRequest(
                    rotationChallengeID: challenge.rotationChallengeID,
                    deviceID: challenge.deviceID,
                    serverChallenge: challenge.serverChallenge,
                    mobileOrigin: challenge.mobileOrigin,
                    currentKeyThumbprint: currentThumbprint,
                    newPublicKeyX963: base64URL(newPublicKey),
                    currentKeySignatureDER: base64URL(
                        try signer.sign(payload).signatureDER
                    ),
                    newKeySignatureDER: base64URL(try rotation.sign(payload)),
                    timestamp: timestamp
                )
            ),
            headers: [:],
            isIdempotent: false
        )
        cancelCandidate = false
        guard
            result.deviceID == credentials.deviceID,
            result.previousKeyThumbprint == currentThumbprint,
            result.currentKeyThumbprint == newThumbprint,
            result.overlapExpiresAt > result.rotatedAt,
            result.overlapExpiresAt.timeIntervalSince(result.rotatedAt) <= 15 * 60
        else {
            throw DeviceTrustClientError.invalidResponse
        }
        try rotation.commit()
        return try await issueToken(
            deviceID: credentials.deviceID,
            using: RequestProofSigner(keyProvider: rotation)
        )
    }

    private func issueToken(
        deviceID: String,
        using tokenSigner: RequestProofSigner
    ) async throws -> DeviceCredentials {
        let safeDeviceID = try pathComponent(deviceID)
        let challenge: DeviceTokenChallenge = try await request(
            path: "/api/mobile/v1/token-challenges/\(safeDeviceID)",
            method: "POST",
            body: nil,
            headers: [:],
            isIdempotent: false
        )
        guard challenge.mobileOrigin == serverOrigin.absoluteString else {
            throw DeviceTrustClientError.invalidOrigin
        }
        let timestamp = utcSecond(now())
        let signaturePayload = try canonicalJSON([
            "device_id": challenge.deviceID,
            "mobile_origin": challenge.mobileOrigin,
            "server_challenge_sha256": sha256Hex(Data(challenge.serverChallenge.utf8)),
            "timestamp": timestamp,
            "token_challenge_id": challenge.tokenChallengeID,
        ])
        let signature = try tokenSigner.sign(signaturePayload).signatureDER
        let token: DeviceToken = try await request(
            path: "/api/mobile/v1/tokens",
            method: "POST",
            body: encode(
                TokenRequest(
                    tokenChallengeID: challenge.tokenChallengeID,
                    deviceID: challenge.deviceID,
                    serverChallenge: challenge.serverChallenge,
                    mobileOrigin: challenge.mobileOrigin,
                    challengeSignatureDER: base64URL(signature),
                    timestamp: timestamp
                )
            ),
            headers: [:],
            isIdempotent: false
        )
        return DeviceCredentials(
            deviceID: token.grant.deviceID,
            serverOrigin: serverOrigin,
            token: token.opaqueToken,
            tokenID: token.grant.tokenID,
            tokenExpiresAt: token.grant.expiresAt
        )
    }

    func ready(credentials: DeviceCredentials) async throws -> MobileReady {
        try await protectedRequest(
            path: "/api/mobile/v1/ready",
            method: "GET",
            body: nil,
            credentials: credentials,
            isIdempotent: true
        )
    }

    func deviceProfile(
        credentials: DeviceCredentials
    ) async throws -> MobileDeviceProfile {
        try await protectedRequest(
            path: "/api/mobile/v1/device-profile",
            method: "GET",
            body: nil,
            credentials: credentials,
            isIdempotent: true
        )
    }

    func submitHealthReview(
        body: Data,
        credentials: DeviceCredentials
    ) async throws -> HealthReviewAccepted {
        try await protectedRequest(
            path: "/api/mobile/v1/health/reviews",
            method: "POST",
            body: body,
            credentials: credentials,
            isIdempotent: false
        )
    }

    func submitHealthAnalysis(
        body: Data,
        credentials: DeviceCredentials
    ) async throws -> HealthAnalysisAccepted {
        try await protectedRequest(
            path: "/api/mobile/v1/health/analyses",
            method: "POST",
            body: body,
            credentials: credentials,
            isIdempotent: false
        )
    }

    func deleteHealthSource(
        _ sourceHash: String,
        credentials: DeviceCredentials
    ) async throws -> HealthDeletionReceipt {
        guard
            sourceHash.count == 64,
            sourceHash.allSatisfy({ $0.isNumber || ("a" ... "f").contains($0) })
        else {
            throw DeviceTrustClientError.invalidResponse
        }
        return try await protectedRequest(
            path: "/api/mobile/v1/health/sources/\(sourceHash)",
            method: "DELETE",
            body: nil,
            credentials: credentials,
            isIdempotent: true
        )
    }

    func cancelAll() {
        session.invalidateAndCancel()
    }

    private func protectedRequest<Response: Decodable>(
        path: String,
        method: String,
        body: Data?,
        credentials: DeviceCredentials,
        isIdempotent: Bool
    ) async throws -> Response {
        let requestTime = now()
        guard credentials.serverOrigin == serverOrigin else {
            throw DeviceTrustClientError.invalidOrigin
        }
        guard requestTime < credentials.tokenExpiresAt else {
            throw DeviceTrustClientError.credentialsExpired
        }
        let timestamp = utcSecond(requestTime)
        let proof = CanonicalRequestProof(
            method: method,
            canonicalPath: path,
            bodySHA256: sha256Hex(body ?? Data()),
            timestamp: timestamp,
            nonce: try nonce(),
            tokenID: credentials.tokenID
        )
        let signature = try signer.sign(proof.canonicalBytes()).signatureDER
        return try await request(
            path: path,
            method: method,
            body: body,
            headers: [
                "Authorization": "OctoDevice \(credentials.token)",
                "X-Octo-Device-Timestamp": timestamp,
                "X-Octo-Device-Nonce": proof.nonce,
                "X-Octo-Device-Signature": base64URL(signature),
            ],
            isIdempotent: isIdempotent
        )
    }

    private func request<Response: Decodable>(
        path: String,
        method: String,
        body: Data?,
        headers: [String: String],
        isIdempotent: Bool
    ) async throws -> Response {
        var attempt = 0
        while true {
            do {
                return try await requestOnce(
                    path: path,
                    method: method,
                    body: body,
                    headers: headers
                )
            } catch {
                let mapped = error as? DeviceTrustClientError
                    ?? DeviceTrustClientError.transport(error)
                guard retryPolicy.shouldRetry(
                    mapped,
                    attempt: attempt,
                    isIdempotent: isIdempotent
                ) else {
                    throw mapped
                }
                attempt += 1
            }
        }
    }

    private func requestOnce<Response: Decodable>(
        path: String,
        method: String,
        body: Data?,
        headers: [String: String]
    ) async throws -> Response {
        var request = URLRequest(url: try endpoint(path))
        request.httpMethod = method
        request.httpBody = body
        request.timeoutInterval = 15
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if body != nil {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        for (name, value) in headers {
            request.setValue(value, forHTTPHeaderField: name)
        }
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw DeviceTrustClientError.invalidResponse
        }
        let contentType = http.value(forHTTPHeaderField: "Content-Type")
        guard (200 ... 299).contains(http.statusCode) else {
            throw DeviceTrustClientError.response(
                statusCode: http.statusCode,
                contentType: contentType,
                data: data
            )
        }
        guard contentType?.lowercased().contains("application/json") == true else {
            throw DeviceTrustClientError.edgeContractMismatch
        }
        do {
            return try decoder().decode(Response.self, from: data)
        } catch {
            throw DeviceTrustClientError.invalidResponse
        }
    }

    private func endpoint(_ path: String) throws -> URL {
        guard path.hasPrefix("/api/mobile/v1/"), !path.contains("?") else {
            throw DeviceTrustClientError.invalidResponse
        }
        var components = URLComponents(url: serverOrigin, resolvingAgainstBaseURL: false)
        components?.path = path
        guard let url = components?.url else {
            throw DeviceTrustClientError.invalidResponse
        }
        return url
    }

    static func sessionConfiguration() -> URLSessionConfiguration {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.httpCookieStorage = nil
        configuration.httpShouldSetCookies = false
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        configuration.timeoutIntervalForRequest = 15
        configuration.timeoutIntervalForResource = 30
        return configuration
    }

    private static func makeSession() -> URLSession {
        URLSession(configuration: sessionConfiguration())
    }
}

private struct EnrollmentRequest: Encodable {
    let challengeID: String
    let challengeSecret: String
    let displayName: String
    let mobileOrigin: String
    let publicKeyX963: String
    let challengeSignatureDER: String
    let attestationState: DeviceAttestationState
    let attestationObject: String?
    let timestamp: String

    enum CodingKeys: String, CodingKey {
        case challengeID = "challenge_id"
        case challengeSecret = "challenge_secret"
        case displayName = "display_name"
        case mobileOrigin = "mobile_origin"
        case publicKeyX963 = "public_key_x963"
        case challengeSignatureDER = "challenge_signature_der"
        case attestationState = "attestation_state"
        case attestationObject = "attestation_object"
        case timestamp
    }
}

private struct TokenRequest: Encodable {
    let tokenChallengeID: String
    let deviceID: String
    let serverChallenge: String
    let mobileOrigin: String
    let challengeSignatureDER: String
    let timestamp: String

    enum CodingKeys: String, CodingKey {
        case tokenChallengeID = "token_challenge_id"
        case deviceID = "device_id"
        case serverChallenge = "server_challenge"
        case mobileOrigin = "mobile_origin"
        case challengeSignatureDER = "challenge_signature_der"
        case timestamp
    }
}

private struct KeyRotationRequest: Encodable {
    let rotationChallengeID: String
    let deviceID: String
    let serverChallenge: String
    let mobileOrigin: String
    let currentKeyThumbprint: String
    let newPublicKeyX963: String
    let currentKeySignatureDER: String
    let newKeySignatureDER: String
    let timestamp: String

    enum CodingKeys: String, CodingKey {
        case rotationChallengeID = "rotation_challenge_id"
        case deviceID = "device_id"
        case serverChallenge = "server_challenge"
        case mobileOrigin = "mobile_origin"
        case currentKeyThumbprint = "current_key_thumbprint"
        case newPublicKeyX963 = "new_public_key_x963"
        case currentKeySignatureDER = "current_key_signature_der"
        case newKeySignatureDER = "new_key_signature_der"
        case timestamp
    }
}

private func canonicalJSON(_ value: [String: String]) throws -> Data {
    guard JSONSerialization.isValidJSONObject(value) else {
        throw DeviceTrustClientError.invalidResponse
    }
    return try JSONSerialization.data(
        withJSONObject: value,
        options: [.sortedKeys, .withoutEscapingSlashes]
    )
}

private func encode<Value: Encodable>(_ value: Value) throws -> Data {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
    encoder.dateEncodingStrategy = .iso8601
    return try encoder.encode(value)
}

private func decoder() -> JSONDecoder {
    let decoder = JSONDecoder()
    decoder.dateDecodingStrategy = .iso8601
    return decoder
}

private func sha256Hex(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

private func base64URL(_ data: Data) -> String {
    data.base64EncodedString()
        .replacingOccurrences(of: "+", with: "-")
        .replacingOccurrences(of: "/", with: "_")
        .replacingOccurrences(of: "=", with: "")
}

private func utcSecond(_ date: Date) -> String {
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime]
    return formatter.string(
        from: Date(timeIntervalSince1970: floor(date.timeIntervalSince1970))
    )
}

private func canonicalHTTPSOrigin(_ url: URL) -> URL? {
    guard let components = URLComponents(url: url, resolvingAgainstBaseURL: false) else {
        return nil
    }
    guard
        components.scheme == "https",
        let host = components.host?.lowercased(),
        !host.isEmpty,
        components.user == nil,
        components.password == nil,
        components.port == nil,
        components.path.isEmpty || components.path == "/",
        components.query == nil,
        components.fragment == nil
    else {
        return nil
    }
    return URL(string: "https://\(host)")
}

private func pathComponent(_ value: String) throws -> String {
    let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "_-"))
    guard !value.isEmpty, value.unicodeScalars.allSatisfy(allowed.contains) else {
        throw DeviceTrustClientError.invalidResponse
    }
    return value
}

private func secureNonce() throws -> String {
    var bytes = [UInt8](repeating: 0, count: 24)
    guard SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) == errSecSuccess else {
        throw DeviceTrustClientError.serverUnavailable
    }
    return base64URL(Data(bytes))
}

private func gatewayErrorCode(_ data: Data) -> String? {
    guard
        let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    else {
        return nil
    }
    if let code = object["code"] as? String {
        return code
    }
    return (object["detail"] as? [String: Any])?["code"] as? String
}
