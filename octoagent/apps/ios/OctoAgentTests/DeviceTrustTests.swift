import Foundation
import Security
import XCTest
@testable import OctoAgent

final class DeviceTrustTests: XCTestCase {
    func test_secure_enclave_key_attributes_are_permanent_and_device_only() throws {
        let attributes = try DeviceKeyStore.secureEnclaveAttributes(
            applicationTag: Data("device-key".utf8)
        ) as NSDictionary

        XCTAssertEqual(
            attributes[kSecAttrKeyType as String] as? String,
            kSecAttrKeyTypeECSECPrimeRandom as String
        )
        XCTAssertEqual(
            attributes[kSecAttrTokenID as String] as? String,
            kSecAttrTokenIDSecureEnclave as String
        )
        XCTAssertEqual(attributes[kSecAttrKeySizeInBits as String] as? Int, 256)
        let privateAttributes = try XCTUnwrap(
            attributes[kSecPrivateKeyAttrs as String] as? NSDictionary
        )
        XCTAssertEqual(privateAttributes[kSecAttrIsPermanent as String] as? Bool, true)
        XCTAssertNotNil(privateAttributes[kSecAttrAccessControl as String])
    }

    func test_credentials_round_trip_without_private_key_bytes() throws {
        let credentials = DeviceCredentials(
            deviceID: "device-1",
            serverOrigin: URL(string: "https://native.example.test")!,
            token: "opaque-token",
            tokenID: "token-1",
            tokenExpiresAt: Date(timeIntervalSince1970: 1_900_000_000)
        )
        let encoded = try JSONEncoder().encode(credentials)
        let decoded = try JSONDecoder().decode(DeviceCredentials.self, from: encoded)

        XCTAssertEqual(decoded, credentials)
        XCTAssertFalse(String(decoding: encoded, as: UTF8.self).contains("private"))
    }

    func test_request_signer_uses_injected_key_provider() throws {
        let provider = StubSigningKeyProvider(
            publicKey: Data([0x04, 0x01]),
            signature: Data([0x30, 0x01])
        )
        let signer = RequestProofSigner(keyProvider: provider)
        let result = try signer.sign(Data("canonical-proof".utf8))

        XCTAssertEqual(result.publicKeyX963, provider.publicKey)
        XCTAssertEqual(result.signatureDER, provider.signature)
        XCTAssertEqual(provider.receivedPayloads, [Data("canonical-proof".utf8)])
    }

    func test_canonical_request_proof_matches_python_contract() throws {
        let emptyBodySHA256 =
            "e3b0c44298fc1c149afbf4c8996fb924"
            + "27ae41e4649b934ca495991b7852b855"
        let proof = CanonicalRequestProof(
            method: "GET",
            canonicalPath: "/api/mobile/v1/ready",
            bodySHA256: emptyBodySHA256,
            timestamp: "2026-07-28T10:00:00Z",
            nonce: String(repeating: "a", count: 32),
            tokenID: "token-1"
        )

        XCTAssertEqual(
            String(decoding: try proof.canonicalBytes(), as: UTF8.self),
            """
            {"body_sha256":"\(emptyBodySHA256)","canonical_path":\
            "/api/mobile/v1/ready","method":"GET","nonce":"\(String(repeating: "a", count: 32))",\
            "timestamp":"2026-07-28T10:00:00Z","token_id":"token-1"}
            """
        )
    }

    func test_canonical_key_rotation_proof_binds_current_and_new_keys() throws {
        let proof = CanonicalKeyRotationProof(
            currentKeyThumbprint: String(repeating: "a", count: 64),
            deviceID: "device-1",
            mobileOrigin: "https://native.example.test",
            newPublicKeyX963: "BAAA",
            rotationChallengeID: "rotation-1",
            serverChallengeSHA256: String(repeating: "b", count: 64),
            timestamp: "2026-08-01T14:30:00Z"
        )

        XCTAssertEqual(
            String(decoding: try proof.canonicalBytes(), as: UTF8.self),
            """
            {"current_key_thumbprint":"\(String(repeating: "a", count: 64))",\
            "device_id":"device-1","mobile_origin":"https://native.example.test",\
            "new_public_key_x963":"BAAA","rotation_challenge_id":"rotation-1",\
            "server_challenge_sha256":"\(String(repeating: "b", count: 64))",\
            "timestamp":"2026-08-01T14:30:00Z"}
            """
        )
    }

    func test_client_errors_are_typed_and_retry_is_bounded() throws {
        let html = Data("<html>Cloudflare Access</html>".utf8)
        XCTAssertEqual(
            DeviceTrustClientError.response(
                statusCode: 200,
                contentType: "text/html",
                data: html
            ),
            .edgeContractMismatch
        )
        let revoked = Data(
            #"{"detail":{"code":"DEVICE_NOT_ACTIVE","message":"已撤销"}}"#.utf8
        )
        XCTAssertEqual(
            DeviceTrustClientError.response(
                statusCode: 401,
                contentType: "application/json",
                data: revoked
            ),
            .revoked
        )
        XCTAssertEqual(
            DeviceTrustClientError.transport(URLError(.notConnectedToInternet)),
            .offline
        )
        XCTAssertEqual(
            DeviceTrustClientError.transport(CancellationError()),
            .cancelled
        )

        let retry = DeviceTrustRetryPolicy(maxRetries: 1)
        XCTAssertTrue(retry.shouldRetry(.offline, attempt: 0, isIdempotent: true))
        XCTAssertFalse(retry.shouldRetry(.offline, attempt: 1, isIdempotent: true))
        XCTAssertFalse(retry.shouldRetry(.offline, attempt: 0, isIdempotent: false))
        XCTAssertFalse(retry.shouldRetry(.revoked, attempt: 0, isIdempotent: true))
        XCTAssertEqual(DeviceTrustRetryPolicy(maxRetries: 100).maxRetries, 1)
    }

    func test_client_session_has_no_ambient_cookie_or_cache() {
        let configuration = DeviceTrustClient.sessionConfiguration()

        XCTAssertNil(configuration.httpCookieStorage)
        XCTAssertFalse(configuration.httpShouldSetCookies)
        XCTAssertEqual(
            configuration.requestCachePolicy,
            .reloadIgnoringLocalCacheData
        )
        XCTAssertEqual(configuration.timeoutIntervalForRequest, 15)
        XCTAssertEqual(configuration.timeoutIntervalForResource, 30)
    }

    func test_client_rejects_non_https_origin() throws {
        XCTAssertThrowsError(
            try DeviceTrustClient(
                serverOrigin: XCTUnwrap(URL(string: "http://mobile.example.test")),
                session: .shared,
                signer: RequestProofSigner(
                    keyProvider: StubSigningKeyProvider(
                        publicKey: Data([0x04, 0x01]),
                        signature: Data([0x30, 0x01])
                    )
                )
            )
        ) { error in
            XCTAssertEqual(error as? DeviceTrustClientError, .invalidOrigin)
        }
    }

    func test_registration_states_have_distinct_plain_language_copy() {
        XCTAssertEqual(
            RegistrationPresentation(phase: .disconnected).title,
            "连接你的 Octo"
        )
        XCTAssertEqual(
            RegistrationPresentation(phase: .awaitingApproval).actionTitle,
            "我已在电脑上批准"
        )
        XCTAssertEqual(
            RegistrationPresentation(
                phase: .connected(deviceName: "Connor 的 iPhone")
            ).statusLabel,
            "已连接"
        )
        XCTAssertEqual(
            RegistrationPresentation(phase: .revoked).actionTitle,
            "重新连接"
        )
        XCTAssertEqual(
            RegistrationPresentation(phase: .offline).statusLabel,
            "当前离线"
        )
    }

    func test_registration_link_parser_accepts_only_native_https_payload() throws {
        let link = try DeviceRegistrationLink.parse(
            "octoagent://connect?challenge_id=challenge-1"
                + "&challenge_secret=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                + "&mobile_origin=https%3A%2F%2Fmobile.example.test"
        )

        XCTAssertEqual(link.challengeID, "challenge-1")
        XCTAssertEqual(link.mobileOrigin.absoluteString, "https://mobile.example.test")
        XCTAssertThrowsError(
            try DeviceRegistrationLink.parse(
                "https://mobile.example.test/api/mobile/v1/enrollments"
            )
        )
    }

    func test_live_identical_signed_request_is_rejected_as_replay() async throws {
#if targetEnvironment(simulator)
        throw XCTSkip("仅由显式真机 replay transaction 启用")
#else
        guard ProcessInfo.processInfo.environment["OCTOAGENT_LIVE_REPLAY"] == "1" else {
            throw XCTSkip("未显式启用真机 replay transaction")
        }

        let keyStore = DeviceKeyStore()
        var credentials = try XCTUnwrap(keyStore.loadCredentials())
        let signer = RequestProofSigner(keyProvider: keyStore)
        let client = try DeviceTrustClient(
            serverOrigin: credentials.serverOrigin,
            signer: signer
        )
        if credentials.tokenExpiresAt <= Date().addingTimeInterval(5) {
            credentials = try await client.issueToken(deviceID: credentials.deviceID)
            try keyStore.saveCredentials(credentials)
        }

        let path = "/api/mobile/v1/ready"
        let timestampFormatter = ISO8601DateFormatter()
        timestampFormatter.formatOptions = [.withInternetDateTime]
        let timestamp = timestampFormatter.string(
            from: Date(timeIntervalSince1970: floor(Date().timeIntervalSince1970))
        )
        let nonce = String(repeating: "r", count: 32)
        let emptyBodySHA256 =
            "e3b0c44298fc1c149afbf4c8996fb924"
            + "27ae41e4649b934ca495991b7852b855"
        let proof = CanonicalRequestProof(
            method: "GET",
            canonicalPath: path,
            bodySHA256: emptyBodySHA256,
            timestamp: timestamp,
            nonce: nonce,
            tokenID: credentials.tokenID
        )
        let signature = try signer.sign(proof.canonicalBytes()).signatureDER
        let encodedSignature = signature.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
        var request = URLRequest(
            url: credentials.serverOrigin.appending(path: String(path.dropFirst()))
        )
        request.httpMethod = "GET"
        request.setValue(
            "OctoDevice \(credentials.token)",
            forHTTPHeaderField: "Authorization"
        )
        request.setValue(proof.timestamp, forHTTPHeaderField: "X-Octo-Device-Timestamp")
        request.setValue(proof.nonce, forHTTPHeaderField: "X-Octo-Device-Nonce")
        request.setValue(
            encodedSignature,
            forHTTPHeaderField: "X-Octo-Device-Signature"
        )

        let session = URLSession(configuration: DeviceTrustClient.sessionConfiguration())
        defer { session.invalidateAndCancel() }
        let (_, firstResponse) = try await session.data(for: request)
        XCTAssertEqual((firstResponse as? HTTPURLResponse)?.statusCode, 200)

        let (replayData, replayResponse) = try await session.data(for: request)
        XCTAssertEqual((replayResponse as? HTTPURLResponse)?.statusCode, 401)
        let replayJSON = try XCTUnwrap(
            JSONSerialization.jsonObject(with: replayData) as? [String: Any]
        )
        let detail = try XCTUnwrap(replayJSON["detail"] as? [String: Any])
        XCTAssertEqual(detail["code"] as? String, "REQUEST_REPLAYED")
#endif
    }

    func test_live_expired_token_rotates_without_replacing_device_key() async throws {
#if targetEnvironment(simulator)
        throw XCTSkip("仅由显式真机 token expiry transaction 启用")
#else
        guard ProcessInfo.processInfo.environment["OCTOAGENT_LIVE_TOKEN_ROTATION"] == "1"
        else {
            throw XCTSkip("未显式启用真机 token expiry transaction")
        }

        let keyStore = DeviceKeyStore()
        let expired = try XCTUnwrap(keyStore.loadCredentials())
        XCTAssertLessThanOrEqual(expired.tokenExpiresAt, Date())
        let keyBefore = try keyStore.publicKeyX963()
        let client = try DeviceTrustClient(
            serverOrigin: expired.serverOrigin,
            signer: RequestProofSigner(keyProvider: keyStore)
        )

        let renewed = try await client.issueToken(deviceID: expired.deviceID)
        XCTAssertEqual(renewed.deviceID, expired.deviceID)
        XCTAssertNotEqual(renewed.tokenID, expired.tokenID)
        XCTAssertNotEqual(renewed.token, expired.token)
        XCTAssertGreaterThan(renewed.tokenExpiresAt, Date())
        XCTAssertEqual(try keyStore.publicKeyX963(), keyBefore)
        try keyStore.saveCredentials(renewed)

        let ready = try await client.ready(credentials: renewed)
        XCTAssertEqual(ready.status, "ready")
        XCTAssertEqual(ready.deviceID, renewed.deviceID)
#endif
    }

    func test_live_device_key_rotation_replaces_secure_enclave_key() async throws {
#if targetEnvironment(simulator)
        throw XCTSkip("仅由显式真机 device key rotation transaction 启用")
#else
        guard ProcessInfo.processInfo.environment["OCTOAGENT_LIVE_KEY_ROTATION"] == "1"
        else {
            throw XCTSkip("未显式启用真机 device key rotation transaction")
        }

        let keyStore = DeviceKeyStore()
        let credentials = try XCTUnwrap(keyStore.loadCredentials())
        let keyBefore = try keyStore.publicKeyX963()
        let client = try DeviceTrustClient(
            serverOrigin: credentials.serverOrigin,
            signer: RequestProofSigner(keyProvider: keyStore)
        )

        let rotated = try await client.rotateKey(
            credentials: credentials,
            keyStore: keyStore
        )
        let keyAfter = try keyStore.publicKeyX963()
        XCTAssertEqual(rotated.deviceID, credentials.deviceID)
        XCTAssertNotEqual(keyAfter, keyBefore)
        XCTAssertNotEqual(rotated.tokenID, credentials.tokenID)
        XCTAssertGreaterThan(rotated.tokenExpiresAt, Date())
        try keyStore.saveCredentials(rotated)

        let ready = try await client.ready(credentials: rotated)
        XCTAssertEqual(ready.status, "ready")
        XCTAssertEqual(ready.deviceID, rotated.deviceID)
#endif
    }
}

private final class StubSigningKeyProvider: DeviceSigningKeyProvider {
    let publicKey: Data
    let signature: Data
    private(set) var receivedPayloads: [Data] = []

    init(publicKey: Data, signature: Data) {
        self.publicKey = publicKey
        self.signature = signature
    }

    func publicKeyX963() throws -> Data {
        publicKey
    }

    func sign(_ payload: Data) throws -> Data {
        receivedPayloads.append(payload)
        return signature
    }
}
