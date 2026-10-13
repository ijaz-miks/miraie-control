import Foundation
import Security

@MainActor public protocol CredentialStore {
    func load() throws -> Credentials?
    func save(_ credentials: Credentials) throws
    func clear() throws
}

/// Keychain access is limited to this app's single generic-password item.
@MainActor public struct KeychainStore: CredentialStore {
    public init() {}
    private var query: [String: Any] { [kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: "io.github.ijaz.Miraie.macOS",
        kSecAttrAccount as String: "miraie-account"] }

    public func load() throws -> Credentials? {
        var attributes = query
        attributes[kSecReturnData as String] = true
        attributes[kSecMatchLimit as String] = kSecMatchLimitOne
        var value: CFTypeRef?
        let result = SecItemCopyMatching(attributes as CFDictionary, &value)
        if result == errSecItemNotFound { return nil }
        guard result == errSecSuccess, let data = value as? Data,
              let credentials = try? JSONDecoder().decode(Credentials.self, from: data) else {
            throw MirAIeError("Your saved account could not be opened. Unlock your Keychain or sign in again.")
        }
        return credentials
    }

    public func save(_ credentials: Credentials) throws {
        let data = try JSONEncoder().encode(credentials)
        let update: [String: Any] = [kSecValueData as String: data]
        var result = SecItemUpdate(query as CFDictionary, update as CFDictionary)
        if result == errSecItemNotFound {
            var attributes = query
            attributes[kSecValueData as String] = data
            attributes[kSecAttrLabel as String] = "MirAIe Control account"
            attributes[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
            result = SecItemAdd(attributes as CFDictionary, nil)
        }
        guard result == errSecSuccess else { throw MirAIeError("macOS Keychain could not save the account.") }
    }

    public func clear() throws {
        let result = SecItemDelete(query as CFDictionary)
        guard result == errSecSuccess || result == errSecItemNotFound else {
            throw MirAIeError("macOS Keychain could not sign out. Unlock your Keychain and try again.")
        }
    }
}
