import Foundation

struct DeviceCredentials: Codable, Equatable, Sendable {
    let deviceID: String
    let serverOrigin: URL
    let token: String
    let tokenID: String
    let tokenExpiresAt: Date

    enum CodingKeys: String, CodingKey {
        case deviceID = "device_id"
        case serverOrigin = "server_origin"
        case token
        case tokenID = "token_id"
        case tokenExpiresAt = "token_expires_at"
    }
}

struct DeviceSignature: Equatable, Sendable {
    let publicKeyX963: Data
    let signatureDER: Data
}

enum DeviceTrustKeyError: Error, Equatable {
    case secureEnclaveUnavailable
    case keychain(OSStatus)
    case publicKeyUnavailable
    case signingFailed
    case credentialsCorrupt
}

enum DeviceAttestationState: String, Codable, Equatable, Sendable {
    case unsupported
    case unverified
    case verified
}

enum DeviceEnrollmentState: String, Codable, Equatable, Sendable {
    case pending
    case active
    case rejected
    case expired
    case revoked
}

struct DeviceRegistrationLink: Equatable, Sendable {
    let challengeID: String
    let challengeSecret: String
    let mobileOrigin: URL

    static func parse(_ value: String) throws -> DeviceRegistrationLink {
        guard
            let components = URLComponents(string: value),
            components.scheme == "octoagent",
            components.host == "connect",
            components.path.isEmpty,
            components.fragment == nil,
            let items = components.queryItems,
            items.count == 3
        else {
            throw DeviceRegistrationLinkError.invalidFormat
        }
        var query: [String: String] = [:]
        for item in items {
            guard
                ["challenge_id", "challenge_secret", "mobile_origin"].contains(item.name),
                let itemValue = item.value,
                query.updateValue(itemValue, forKey: item.name) == nil
            else {
                throw DeviceRegistrationLinkError.invalidFormat
            }
        }
        guard
            let challengeID = query["challenge_id"],
            !challengeID.isEmpty,
            challengeID.count <= 256,
            let challengeSecret = query["challenge_secret"],
            challengeSecret.count == 43,
            challengeSecret.allSatisfy({
                $0.isLetter || $0.isNumber || $0 == "_" || $0 == "-"
            }),
            let originText = query["mobile_origin"],
            let mobileOrigin = URL(string: originText),
            let origin = URLComponents(
                url: mobileOrigin,
                resolvingAgainstBaseURL: false
            ),
            origin.scheme == "https",
            origin.host?.isEmpty == false,
            origin.user == nil,
            origin.password == nil,
            origin.port == nil,
            origin.path.isEmpty || origin.path == "/",
            origin.query == nil,
            origin.fragment == nil
        else {
            throw DeviceRegistrationLinkError.invalidFormat
        }
        return DeviceRegistrationLink(
            challengeID: challengeID,
            challengeSecret: challengeSecret,
            mobileOrigin: mobileOrigin
        )
    }
}

enum DeviceRegistrationLinkError: Error, Equatable {
    case invalidFormat
}

struct DeviceEnrollmentStatus: Codable, Equatable, Sendable {
    let challengeID: String
    let state: DeviceEnrollmentState
    let deviceID: String?
    let deviceKeyThumbprint: String?
    let attestationState: DeviceAttestationState?
    let reasonCode: String

    enum CodingKeys: String, CodingKey {
        case challengeID = "challenge_id"
        case state
        case deviceID = "device_id"
        case deviceKeyThumbprint = "device_key_thumbprint"
        case attestationState = "attestation_state"
        case reasonCode = "reason_code"
    }
}

struct DeviceCapabilityGrant: Codable, Equatable, Sendable {
    let deviceID: String
    let capabilities: [String]
    let expiresAt: Date
    let tokenID: String

    enum CodingKeys: String, CodingKey {
        case deviceID = "device_id"
        case capabilities
        case expiresAt = "expires_at"
        case tokenID = "token_id"
    }
}

struct DeviceTokenChallenge: Codable, Equatable, Sendable {
    let tokenChallengeID: String
    let deviceID: String
    let serverChallenge: String
    let mobileOrigin: String
    let expiresAt: Date

    enum CodingKeys: String, CodingKey {
        case tokenChallengeID = "token_challenge_id"
        case deviceID = "device_id"
        case serverChallenge = "server_challenge"
        case mobileOrigin = "mobile_origin"
        case expiresAt = "expires_at"
    }
}

struct DeviceKeyRotationChallenge: Codable, Equatable, Sendable {
    let rotationChallengeID: String
    let deviceID: String
    let serverChallenge: String
    let mobileOrigin: String
    let expiresAt: Date

    enum CodingKeys: String, CodingKey {
        case rotationChallengeID = "rotation_challenge_id"
        case deviceID = "device_id"
        case serverChallenge = "server_challenge"
        case mobileOrigin = "mobile_origin"
        case expiresAt = "expires_at"
    }
}

struct DeviceKeyRotationResult: Codable, Equatable, Sendable {
    let deviceID: String
    let previousKeyThumbprint: String
    let currentKeyThumbprint: String
    let rotatedAt: Date
    let overlapExpiresAt: Date

    enum CodingKeys: String, CodingKey {
        case deviceID = "device_id"
        case previousKeyThumbprint = "previous_key_thumbprint"
        case currentKeyThumbprint = "current_key_thumbprint"
        case rotatedAt = "rotated_at"
        case overlapExpiresAt = "overlap_expires_at"
    }
}

struct DeviceToken: Codable, Equatable, Sendable {
    let opaqueToken: String
    let grant: DeviceCapabilityGrant

    enum CodingKeys: String, CodingKey {
        case opaqueToken = "opaque_token"
        case grant
    }
}

struct MobileReady: Codable, Equatable, Sendable {
    let status: String
    let deviceID: String
    let serverTime: Date

    enum CodingKeys: String, CodingKey {
        case status
        case deviceID = "device_id"
        case serverTime = "server_time"
    }
}

struct MobileDeviceProfile: Codable, Equatable, Sendable {
    let deviceID: String
    let displayName: String
    let attestationState: DeviceAttestationState
    let capabilities: [String]

    enum CodingKeys: String, CodingKey {
        case deviceID = "device_id"
        case displayName = "display_name"
        case attestationState = "attestation_state"
        case capabilities
    }
}
