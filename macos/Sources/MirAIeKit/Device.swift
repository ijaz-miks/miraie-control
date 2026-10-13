import Foundation

public enum ControlField: String, CaseIterable, Hashable, Sendable {
    case power, temperature, mode, fan, verticalSwing, horizontalSwing, nanoe, display, capacity

    public var wire: String {
        switch self {
        case .power: return "ps"
        case .temperature: return "actmp"
        case .mode: return "acmd"
        case .fan: return "acfs"
        case .verticalSwing: return "acvs"
        case .horizontalSwing: return "achs"
        case .nanoe: return "acng"
        case .display: return "acdc"
        case .capacity: return "cnv"
        }
    }
    public var index: Int {
        switch self {
        case .mode: return 0
        case .power: return 1
        case .temperature: return 2
        case .fan: return 4
        case .verticalSwing: return 5
        case .horizontalSwing: return 6
        case .nanoe: return 7
        case .display: return 8
        case .capacity: return 28
        }
    }
    public var title: String {
        switch self {
        case .power: return "Power"
        case .temperature: return "Temperature"
        case .mode: return "Mode"
        case .fan: return "Fan speed"
        case .verticalSwing: return "Vertical swing"
        case .horizontalSwing: return "Horizontal swing"
        case .nanoe: return "nanoe"
        case .display: return "Display light"
        case .capacity: return "Capacity"
        }
    }
    public var isBoolean: Bool { [.power, .nanoe, .display].contains(self) }
}

public struct TemperatureRange: Equatable, Sendable {
    public var min = 16.0
    public var max = 30.0
    public var step = 1.0
    public func contains(_ value: Double) -> Bool {
        guard value.isFinite, value >= min, value <= max, step > 0 else { return false }
        let position = (value - min) / step
        return abs(position - position.rounded()) < 0.000001
    }
}

public struct Device: Identifiable, Equatable, Sendable {
    public let id: String
    public var name: String
    public var room: String
    public var model: String
    public var online = false
    public var raw: [String: JSONValue] = [:]
    public var detail: [String: JSONValue]
    public var updatedAt: Date?
    public var temperatureRange = TemperatureRange()
    public var options: [ControlField: [String]] = [:]
    public var features: Set<ControlField> = []

    public init(id: String, name: String, room: String = "", model: String = "", detail: [String: JSONValue] = [:]) {
        self.id = id
        self.name = name
        self.room = room
        self.model = model
        self.detail = detail
    }

    public var power: Bool? { online ? raw["ps"]?.flag : nil }
    public var temperature: Double? { raw["actmp"]?.double }
    public var roomTemperature: Double? {
        guard let value = raw["rmtmp"] else { return nil }
        let version = (detail["firmwareVersion"]?.text ?? "").split(whereSeparator: { !$0.isNumber }).compactMap { Int($0) }
        let components = (value.text ?? "").split(separator: ".", omittingEmptySubsequences: false)
        if version.count >= 2, version[0] > 3 || (version[0] == 3 && version[1] >= 2),
           components.count == 2, let prefix = Int(components[0]), let degrees = Int(components[1]),
           (0..<256).contains(prefix), (1..<80).contains(degrees) {
            return ((Double(degrees) + Double(prefix) / 256) * 100).rounded() / 100
        }
        return value.double.flatMap { (-20...80).contains($0) ? $0 : nil }
    }

    public func value(_ field: ControlField) -> JSONValue? {
        guard let value = raw[field.wire] else { return nil }
        if field == .power { return power.map(JSONValue.bool) }
        if field.isBoolean { return value.flag.map(JSONValue.bool) }
        if field == .temperature { return value.double.map(JSONValue.number) }
        return value.text.map { .string($0.lowercased()) }
    }

    public mutating func apply(_ response: [String: JSONValue], at date: Date = Date()) {
        let status = Self.unwrap(response)
        raw.merge(status) { _, new in new }
        // Only explicit REST availability can establish reachability.
        if let explicit = (status["onlineStatus"] ?? status["isOnline"])?.flag { online = explicit }
        updatedAt = date
        configureCapabilities()
    }

    public static func unwrap(_ response: [String: JSONValue]) -> [String: JSONValue] {
        for key in ["data", "status", "state", "params", "control_params", "deviceStatus"] {
            if case .object(let nested) = response[key] {
                return response.merging(unwrap(nested)) { _, new in new }
            }
        }
        return response
    }

    public mutating func configureCapabilities() {
        let declarations = detail["_model"]?.object["controlCapabilities"]?.array ?? []
        var attributes: [ControlField: [String: JSONValue]] = [:]
        var types: [ControlField: String] = [:]
        for declaration in declarations {
            guard let first = declaration.object["attributes"]?.array.first else { continue }
            for field in ControlField.allCases where first.object["key"]?.text == field.wire {
                attributes[field] = first.object
                types[field] = declaration.object["capability"]?.text
            }
        }
        var range = TemperatureRange()
        range.min = detail["minTemperature"]?.double ?? 16
        range.max = detail["maxTemperature"]?.double ?? 30
        range.step = detail["temperatureStep"]?.double ?? 1
        let limits = attributes[.temperature]?["range"]?.object ?? [:]
        for key in ["min", "max", "increment"] {
            let entry = limits[key]?.array.first { ["celcius", "celsius", "C"].contains($0.object["unit"]?.text ?? "") }
            guard let number = entry?.object["value"]?.double else { continue }
            if key == "min" { range.min = number }
            if key == "max" { range.max = number }
            if key == "increment" { range.step = number }
        }
        if !(10...40).contains(range.min) || !(10...40).contains(range.max) || range.min >= range.max {
            range.min = 16; range.max = 30
        }
        if ![0.5, 1].contains(range.step) { range.step = 1 }
        temperatureRange = range
        let swing = (0...5).map(String.init)
        var modes = ["auto", "cool", "dry", "fan"]
        if attributes[.mode]?["values"]?.array.contains(.string("heat")) == true || raw["acmd"]?.text == "heat" {
            modes.append("heat")
        }
        if let declared = detail["supportedModes"] {
            modes = declared.array.compactMap(\.text).filter { (modes + ["heat"]).contains($0) }
        }
        for (field, fallback) in [(ControlField.mode, modes), (.fan, ["auto", "low", "medium", "high", "quiet"]),
                                  (.verticalSwing, swing), (.horizontalSwing, swing)] {
            if value(field) == nil { options[field] = []; continue }
            if let declared = attributes[field]?["values"] ?? attributes[field]?["intValues"] {
                options[field] = declared.array.compactMap(\.text).map { $0.lowercased() }.filter(fallback.contains)
            } else { options[field] = declarations.isEmpty || attributes[field] != nil ? fallback : [] }
        }
        let capacity = attributes[.capacity]?["intValues"] ?? detail["supportedCapacity"]
        options[.capacity] = capacity?.array.compactMap(\.text).filter {
            ["0", "40", "50", "55", "60", "70", "80", "90", "100", "110"].contains($0)
        } ?? []
        features = []
        if value(.power) != nil || raw["ps"]?.flag != nil { features.insert(.power) }
        if value(.display) != nil && (declarations.isEmpty || attributes[.display] != nil) { features.insert(.display) }
        if value(.nanoe) != nil && types[.nanoe] == "NanoeG" { features.insert(.nanoe) }
        model = detail["modelName"]?.text ?? detail["modelNumber"]?.text ?? model
    }

    public func payload(_ field: ControlField, value: JSONValue, sequence: Int = 1, now: Date = Date()) throws -> [String: JSONValue] {
        guard online else { throw MirAIeError("This air conditioner is offline. Refresh before changing it.") }
        guard let updatedAt, now.timeIntervalSince(updatedAt) < 180 else {
            throw MirAIeError("The displayed status is stale. Refresh before changing the AC.")
        }
        guard self.value(field) != nil else { throw MirAIeError("This control is unavailable for the selected AC.") }
        let wireValue: JSONValue
        if field.isBoolean {
            guard case .bool(let flag) = value, features.contains(field) else {
                throw MirAIeError("This switch is unavailable for the selected AC.")
            }
            wireValue = .string(flag ? "on" : "off")
        } else if field == .temperature {
            guard case .number(let number) = value, temperatureRange.contains(number) else {
                throw MirAIeError("Choose a temperature within the supported range and step.")
            }
            wireValue = .string(JSONValue.number(number).text!)
        } else {
            guard case .string(let string) = value, options[field, default: []].contains(string) else {
                throw MirAIeError("This setting is unavailable for the selected AC.")
            }
            wireValue = [.verticalSwing, .horizontalSwing, .capacity].contains(field) ? .number(Double(string)!) : value
        }
        var result: [String: JSONValue] = ["ki": .number(Double(field.index)), "cnt": .string("an"),
            "sid": .string(String(sequence)), field.wire: wireValue]
        if field == .capacity { result["acem"] = .string("off"); result["acpm"] = .string("off") }
        return result
    }
}
