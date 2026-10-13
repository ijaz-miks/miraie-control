import Foundation
import MirAIeKit

/// Explicit demo mode has no cloud transport and never opens the user's Keychain.
@MainActor final class DemoStore: CredentialStore {
    private var account: Credentials? = Credentials(username: "demo@example.invalid", password: "synthetic-password")
    func load() throws -> Credentials? { account }
    func save(_ credentials: Credentials) throws { account = credentials }
    func clear() throws { account = nil }
}

actor DemoAPI: ACAPI {
    private var fixtures: [Device]
    private(set) var writes = 0

    init() {
        let attributes: [(String, String, [JSONValue])] = [
            ("acmd", "Mode", ["auto", "cool", "dry", "fan"].map(JSONValue.string)),
            ("acfs", "Fan", ["auto", "low", "medium", "high", "quiet"].map(JSONValue.string)),
            ("acvs", "Swing", (0...5).map { .number(Double($0)) }),
            ("achs", "Swing", (0...5).map { .number(Double($0)) }),
            ("acdc", "Display", []), ("acng", "NanoeG", []),
            ("cnv", "Capacity", [0, 80, 100].map { .number(Double($0)) })]
        let entries: [JSONValue] = attributes.map { wire, type, values in
            .object(["capability": .string(type), "attributes": .array([
                .object(["key": .string(wire), wire == "cnv" ? "intValues" : "values": .array(values)])])])
        }
        var online = Device(id: "demo-ac-1", name: "Living room AC", room: "Living room", model: "Demo model",
            detail: ["_model": .object(["controlCapabilities": .array(entries)])])
        online.apply(["onlineStatus": .string("true"), "ps": .string("on"), "actmp": .string("24"),
            "rmtmp": .string("27.5"), "acmd": .string("cool"), "acfs": .string("auto"),
            "acvs": .number(0), "achs": .number(0), "acdc": .string("on"), "acng": .string("off"), "cnv": .number(0)])
        var offline = online
        offline = Device(id: "demo-ac-2", name: "Studio AC", room: "Studio", model: "Demo model", detail: online.detail)
        offline.apply(online.raw.merging(["onlineStatus": .string("false")]) { _, new in new })
        fixtures = [online, offline]
    }
    func signIn(_ credentials: Credentials) async throws {}
    func discover() async throws -> [Device] { fixtures }
    func status(_ identity: String) async throws -> [String: JSONValue] {
        guard let device = fixtures.first(where: { $0.id == identity }) else { throw MirAIeError("Unknown demo device.") }
        return device.raw
    }
    func control(_ identity: String, payload: [String: JSONValue]) async throws {
        guard let index = fixtures.firstIndex(where: { $0.id == identity }) else { throw MirAIeError("Unknown demo device.") }
        writes += 1
        fixtures[index].apply(payload)
    }
    func signOut() async {}
}
