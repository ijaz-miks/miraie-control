import Foundation

/// Typed protocol values keep booleans distinct from temperature and enum values.
public enum JSONValue: Codable, Equatable, Sendable {
    case object([String: JSONValue]), array([JSONValue]), string(String)
    case number(Double), bool(Bool), null

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() { self = .null }
        else if let value = try? container.decode(Bool.self) { self = .bool(value) }
        else if let value = try? container.decode(Double.self) { self = .number(value) }
        else if let value = try? container.decode(String.self) { self = .string(value) }
        else if let value = try? container.decode([String: JSONValue].self) { self = .object(value) }
        else { self = .array(try container.decode([JSONValue].self)) }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .object(let value): try container.encode(value)
        case .array(let value): try container.encode(value)
        case .string(let value): try container.encode(value)
        case .number(let value): try container.encode(value)
        case .bool(let value): try container.encode(value)
        case .null: try container.encodeNil()
        }
    }

    public var object: [String: JSONValue] {
        if case .object(let value) = self { return value }
        return [:]
    }
    public var array: [JSONValue] {
        if case .array(let value) = self { return value }
        return []
    }
    public var text: String? {
        switch self {
        case .string(let value): return value
        case .number(let value) where value.isFinite:
            return value.rounded() == value ? String(format: "%.0f", value) : String(value)
        default: return nil
        }
    }
    public var double: Double? {
        let value: Double?
        switch self {
        case .number(let number): value = number
        case .string(let string): value = Double(string)
        default: value = nil
        }
        return value.flatMap { $0.isFinite ? $0 : nil }
    }
    public var flag: Bool? {
        switch self {
        case .bool(let value): return value
        case .number(1): return true
        case .number(0): return false
        case .string(let value):
            if ["on", "online", "true", "1"].contains(value.lowercased()) { return true }
            if ["off", "offline", "false", "0"].contains(value.lowercased()) { return false }
            return nil
        default: return nil
        }
    }
}

public struct MirAIeError: LocalizedError, Equatable, Sendable {
    public let message: String
    public init(_ message: String) { self.message = message }
    public var errorDescription: String? { message }
}

public struct Credentials: Codable, Equatable, Sendable {
    public let username: String
    public let password: String
    public init(username: String, password: String) {
        self.username = username
        self.password = password
    }
}
