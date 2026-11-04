// A disposable fixture key. Never reads or exports a Keychain item.
import Foundation
import CryptoKit
let path = CommandLine.arguments[1]
let key = Curve25519.Signing.PrivateKey()
let manager = FileManager.default
let attributes: [FileAttributeKey: Any] = [.posixPermissions: 0o600]
guard manager.createFile(atPath: path, contents: Data(key.rawRepresentation.base64EncodedString().utf8), attributes: attributes),
      manager.createFile(atPath: path + ".public", contents: Data(key.publicKey.rawRepresentation.base64EncodedString().utf8), attributes: attributes) else {
    fatalError("Could not write disposable fixture key")
}
