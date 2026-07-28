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
