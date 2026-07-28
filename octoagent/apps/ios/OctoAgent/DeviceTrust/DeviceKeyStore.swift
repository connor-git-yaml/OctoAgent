import Foundation
import Security

final class DeviceKeyStore: DeviceSigningKeyProvider {
    private static let credentialsService = "io.octoagent.device-trust"

    private let applicationTag: Data
    private let credentialsAccount: String

    init(
        applicationTag: Data = Data("io.octoagent.device-trust.signing-key".utf8),
        credentialsAccount: String = "active-device"
    ) {
        self.applicationTag = applicationTag
        self.credentialsAccount = credentialsAccount
    }

    static func secureEnclaveAttributes(applicationTag: Data) throws -> CFDictionary {
        guard let accessControl = SecAccessControlCreateWithFlags(
            nil,
            kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
            .privateKeyUsage,
            nil
        ) else {
            throw DeviceTrustKeyError.secureEnclaveUnavailable
        }
        return [
            kSecAttrKeyType as String: kSecAttrKeyTypeECSECPrimeRandom,
            kSecAttrKeySizeInBits as String: 256,
            kSecAttrTokenID as String: kSecAttrTokenIDSecureEnclave,
            kSecPrivateKeyAttrs as String: [
                kSecAttrIsPermanent as String: true,
                kSecAttrApplicationTag as String: applicationTag,
                kSecAttrAccessControl as String: accessControl,
            ],
        ] as CFDictionary
    }

    func publicKeyX963() throws -> Data {
        let privateKey = try loadOrCreatePrivateKey()
        guard
            let publicKey = SecKeyCopyPublicKey(privateKey),
            let bytes = SecKeyCopyExternalRepresentation(publicKey, nil) as Data?
        else {
            throw DeviceTrustKeyError.publicKeyUnavailable
        }
        return bytes
    }

    func sign(_ payload: Data) throws -> Data {
        let privateKey = try loadOrCreatePrivateKey()
        var error: Unmanaged<CFError>?
        guard
            let signature = SecKeyCreateSignature(
                privateKey,
                .ecdsaSignatureMessageX962SHA256,
                payload as CFData,
                &error
            ) as Data?
        else {
            throw DeviceTrustKeyError.signingFailed
        }
        return signature
    }

    func saveCredentials(_ credentials: DeviceCredentials) throws {
        let data = try JSONEncoder().encode(credentials)
        let query = credentialsQuery()
        let status = SecItemUpdate(
            query as CFDictionary,
            [kSecValueData as String: data] as CFDictionary
        )
        if status == errSecSuccess {
            return
        }
        if status != errSecItemNotFound {
            throw DeviceTrustKeyError.keychain(status)
        }
        var attributes = query
        attributes[kSecValueData as String] = data
        attributes[kSecAttrAccessible as String] =
            kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let addStatus = SecItemAdd(attributes as CFDictionary, nil)
        guard addStatus == errSecSuccess else {
            throw DeviceTrustKeyError.keychain(addStatus)
        }
    }

    func loadCredentials() throws -> DeviceCredentials? {
        var query = credentialsQuery()
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound {
            return nil
        }
        guard status == errSecSuccess, let data = result as? Data else {
            throw DeviceTrustKeyError.keychain(status)
        }
        do {
            return try JSONDecoder().decode(DeviceCredentials.self, from: data)
        } catch {
            throw DeviceTrustKeyError.credentialsCorrupt
        }
    }

    func clear() throws {
        let credentialsStatus = SecItemDelete(credentialsQuery() as CFDictionary)
        if credentialsStatus != errSecSuccess && credentialsStatus != errSecItemNotFound {
            throw DeviceTrustKeyError.keychain(credentialsStatus)
        }
        let keyStatus = SecItemDelete(privateKeyQuery() as CFDictionary)
        if keyStatus != errSecSuccess && keyStatus != errSecItemNotFound {
            throw DeviceTrustKeyError.keychain(keyStatus)
        }
    }

    private func loadOrCreatePrivateKey() throws -> SecKey {
        var query = privateKeyQuery()
        query[kSecReturnRef as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecSuccess {
            guard let result, CFGetTypeID(result) == SecKeyGetTypeID() else {
                throw DeviceTrustKeyError.keychain(errSecInternalError)
            }
            // SecItemCopyMatching is untyped; the CFTypeID guard makes this cast exact.
            return result as! SecKey
        }
        guard status == errSecItemNotFound else {
            throw DeviceTrustKeyError.keychain(status)
        }
        var error: Unmanaged<CFError>?
        guard
            let key = SecKeyCreateRandomKey(
                try Self.secureEnclaveAttributes(applicationTag: applicationTag),
                &error
            )
        else {
            throw DeviceTrustKeyError.secureEnclaveUnavailable
        }
        return key
    }

    private func privateKeyQuery() -> [String: Any] {
        [
            kSecClass as String: kSecClassKey,
            kSecAttrKeyType as String: kSecAttrKeyTypeECSECPrimeRandom,
            kSecAttrApplicationTag as String: applicationTag,
        ]
    }

    private func credentialsQuery() -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: Self.credentialsService,
            kSecAttrAccount as String: credentialsAccount,
        ]
    }
}
