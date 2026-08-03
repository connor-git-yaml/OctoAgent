import Foundation
import Security

final class DeviceKeyStore: DeviceSigningKeyProvider {
    private static let credentialsService = "io.octoagent.device-trust"
    private static let primarySlot = Data("primary".utf8)
    private static let secondarySlot = Data("secondary".utf8)

    private let applicationTag: Data
    private let secondaryApplicationTag: Data
    private let credentialsAccount: String
    private let activeKeyAccount: String

    init(
        applicationTag: Data = Data("io.octoagent.device-trust.signing-key".utf8),
        credentialsAccount: String = "active-device"
    ) {
        self.applicationTag = applicationTag
        secondaryApplicationTag = applicationTag + Data(".secondary".utf8)
        self.credentialsAccount = credentialsAccount
        activeKeyAccount = "\(credentialsAccount).active-key-slot"
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
        let activeKeyStatus = SecItemDelete(activeKeyQuery() as CFDictionary)
        if activeKeyStatus != errSecSuccess && activeKeyStatus != errSecItemNotFound {
            throw DeviceTrustKeyError.keychain(activeKeyStatus)
        }
        for tag in [applicationTag, secondaryApplicationTag] {
            try deletePrivateKey(applicationTag: tag)
        }
    }

    func beginKeyRotation() throws -> DeviceKeyRotationSession {
        let currentSlot = try activeSlot()
        let candidateSlot = currentSlot == Self.primarySlot
            ? Self.secondarySlot
            : Self.primarySlot
        let candidateTag = applicationTag(for: candidateSlot)
        try deletePrivateKey(applicationTag: candidateTag)
        let candidateKey = try createPrivateKey(applicationTag: candidateTag)
        return DeviceKeyRotationSession(
            privateKey: candidateKey,
            commit: { [weak self] in
                guard let self else {
                    throw DeviceTrustKeyError.keychain(errSecNotAvailable)
                }
                try self.commitRotation(from: currentSlot, to: candidateSlot)
            },
            cancel: { [weak self] in
                guard let self else {
                    return
                }
                try self.cancelRotation(candidateSlot: candidateSlot)
            }
        )
    }

    private func loadOrCreatePrivateKey() throws -> SecKey {
        let tag = applicationTag(for: try activeSlot())
        return try loadOrCreatePrivateKey(applicationTag: tag)
    }

    private func loadOrCreatePrivateKey(applicationTag: Data) throws -> SecKey {
        do {
            return try loadPrivateKey(applicationTag: applicationTag)
        } catch DeviceTrustKeyError.keychain(let status)
            where status == errSecItemNotFound
        {
            return try createPrivateKey(applicationTag: applicationTag)
        }
    }

    private func loadPrivateKey(applicationTag: Data) throws -> SecKey {
        var query = privateKeyQuery(applicationTag: applicationTag)
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
        throw DeviceTrustKeyError.keychain(status)
    }

    private func createPrivateKey(applicationTag: Data) throws -> SecKey {
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

    private func privateKeyQuery(applicationTag: Data) -> [String: Any] {
        [
            kSecClass as String: kSecClassKey,
            kSecAttrKeyType as String: kSecAttrKeyTypeECSECPrimeRandom,
            kSecAttrApplicationTag as String: applicationTag,
        ]
    }

    private func activeSlot() throws -> Data {
        var query = activeKeyQuery()
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound {
            return Self.primarySlot
        }
        guard status == errSecSuccess, let slot = result as? Data else {
            throw DeviceTrustKeyError.keychain(status)
        }
        guard slot == Self.primarySlot || slot == Self.secondarySlot else {
            throw DeviceTrustKeyError.credentialsCorrupt
        }
        return slot
    }

    private func commitRotation(from currentSlot: Data, to candidateSlot: Data) throws {
        guard try activeSlot() == currentSlot else {
            throw DeviceTrustKeyError.credentialsCorrupt
        }
        _ = try loadPrivateKey(
            applicationTag: applicationTag(for: candidateSlot)
        )
        try saveActiveSlot(candidateSlot)
        try deletePrivateKey(applicationTag: applicationTag(for: currentSlot))
    }

    private func cancelRotation(candidateSlot: Data) throws {
        guard try activeSlot() != candidateSlot else {
            return
        }
        try deletePrivateKey(applicationTag: applicationTag(for: candidateSlot))
    }

    private func saveActiveSlot(_ slot: Data) throws {
        let query = activeKeyQuery()
        let update = SecItemUpdate(
            query as CFDictionary,
            [kSecValueData as String: slot] as CFDictionary
        )
        if update == errSecSuccess {
            return
        }
        guard update == errSecItemNotFound else {
            throw DeviceTrustKeyError.keychain(update)
        }
        var attributes = query
        attributes[kSecValueData as String] = slot
        attributes[kSecAttrAccessible as String] =
            kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let add = SecItemAdd(attributes as CFDictionary, nil)
        guard add == errSecSuccess else {
            throw DeviceTrustKeyError.keychain(add)
        }
    }

    private func deletePrivateKey(applicationTag: Data) throws {
        let status = SecItemDelete(
            privateKeyQuery(applicationTag: applicationTag) as CFDictionary
        )
        if status != errSecSuccess && status != errSecItemNotFound {
            throw DeviceTrustKeyError.keychain(status)
        }
    }

    private func applicationTag(for slot: Data) -> Data {
        slot == Self.primarySlot ? applicationTag : secondaryApplicationTag
    }

    private func credentialsQuery() -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: Self.credentialsService,
            kSecAttrAccount as String: credentialsAccount,
        ]
    }

    private func activeKeyQuery() -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: Self.credentialsService,
            kSecAttrAccount as String: activeKeyAccount,
        ]
    }
}

final class DeviceKeyRotationSession: DeviceSigningKeyProvider {
    private let privateKey: SecKey
    private let commitHandler: () throws -> Void
    private let cancelHandler: () throws -> Void
    private(set) var isCommitted = false

    init(
        privateKey: SecKey,
        commit: @escaping () throws -> Void,
        cancel: @escaping () throws -> Void
    ) {
        self.privateKey = privateKey
        commitHandler = commit
        cancelHandler = cancel
    }

    func publicKeyX963() throws -> Data {
        guard
            let publicKey = SecKeyCopyPublicKey(privateKey),
            let bytes = SecKeyCopyExternalRepresentation(publicKey, nil) as Data?
        else {
            throw DeviceTrustKeyError.publicKeyUnavailable
        }
        return bytes
    }

    func sign(_ payload: Data) throws -> Data {
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

    func commit() throws {
        try commitHandler()
        isCommitted = true
    }

    func cancel() throws {
        guard !isCommitted else {
            return
        }
        try cancelHandler()
    }
}
