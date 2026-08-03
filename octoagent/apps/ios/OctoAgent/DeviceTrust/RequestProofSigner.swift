import Foundation

protocol DeviceSigningKeyProvider: AnyObject {
    func publicKeyX963() throws -> Data
    func sign(_ payload: Data) throws -> Data
}

struct RequestProofSigner {
    private let keyProvider: any DeviceSigningKeyProvider

    init(keyProvider: any DeviceSigningKeyProvider) {
        self.keyProvider = keyProvider
    }

    func publicKeyX963() throws -> Data {
        try keyProvider.publicKeyX963()
    }

    func sign(_ canonicalPayload: Data) throws -> DeviceSignature {
        DeviceSignature(
            publicKeyX963: try keyProvider.publicKeyX963(),
            signatureDER: try keyProvider.sign(canonicalPayload)
        )
    }
}
