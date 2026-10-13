import XCTest
@testable import MirAIeKit

func fixture(online: Bool = true, temperature: Double = 24) -> Device {
    var device = Device(id: "fixture-ac-1", name: "Test AC", detail: [:])
    device.apply(["onlineStatus": .string(online ? "true" : "false"), "ps": .string("on"),
        "actmp": .number(temperature), "rmtmp": .string("27.5"), "acmd": .string("cool"),
        "acfs": .string("auto"), "acvs": .number(0), "acdc": .string("off")])
    return device
}

final class DeviceTests: XCTestCase {
    func testJSONKeepsBooleansAndNumbersDistinct() throws {
        let value = try JSONDecoder().decode(JSONValue.self, from: Data("{\"flag\":true,\"number\":1}".utf8))
        XCTAssertEqual(value.object["flag"], .bool(true))
        XCTAssertEqual(value.object["number"], .number(1))
        XCTAssertNil(value.object["flag"]?.double)
        XCTAssertEqual(try JSONDecoder().decode(JSONValue.self, from: JSONEncoder().encode(value)), value)
    }
    func testOfflinePowerIsUnknownEvenWhenCachedOn() {
        let device = fixture(online: false)
        XCTAssertNil(device.power)
        XCTAssertEqual(device.raw["ps"], .string("on"))
        XCTAssertEqual(device.temperature, 24)
    }
    func testTelemetryWithoutAvailabilityCannotPromoteOfflineDevice() {
        var device = fixture(online: false)
        device.apply(["ps": .string("on"), "actmp": .number(26)])
        XCTAssertFalse(device.online)
        XCTAssertNil(device.power)
        device.apply(["onlineStatus": .string("true")])
        XCTAssertTrue(device.online)
    }
    func testExplicitFalseStringsAndNestedStatus() {
        var device = fixture()
        device.apply(["data": .object(["onlineStatus": .string("false"), "actmp": .string("25")])])
        XCTAssertFalse(device.online)
        XCTAssertEqual(device.temperature, 25)
    }
    func testFirmwareRoomTemperatureDecoding() {
        var device = fixture()
        device.detail["firmwareVersion"] = .string("3.2.1")
        device.apply(["rmtmp": .string("64.28")])
        XCTAssertEqual(device.roomTemperature, 28.25)
        device.detail["firmwareVersion"] = .string("2.1.0")
        XCTAssertEqual(device.roomTemperature, 64.28)
        device.apply(["rmtmp": .string("999")])
        XCTAssertNil(device.roomTemperature)
    }
    func testPowerAndDisplayHaveSeparateFieldsAndIndices() throws {
        let device = fixture()
        let power = try device.payload(.power, value: .bool(false))
        let display = try device.payload(.display, value: .bool(true))
        XCTAssertEqual(power["ki"], .number(1))
        XCTAssertEqual(power["ps"], .string("off"))
        XCTAssertNil(power["acdc"])
        XCTAssertEqual(display["ki"], .number(8))
        XCTAssertEqual(display["acdc"], .string("on"))
        XCTAssertNil(display["ps"])
    }
    func testInvalidBooleanAndTemperaturePayloadsAreRejected() {
        let device = fixture()
        for value in [JSONValue.number(1), .string("on"), .null] {
            XCTAssertThrowsError(try device.payload(.power, value: value))
        }
        for value in [15.0, 31, 24.5, .infinity, .nan] {
            XCTAssertThrowsError(try device.payload(.temperature, value: .number(value)))
        }
        XCTAssertThrowsError(try device.payload(.temperature, value: .bool(true)))
    }
    func testOfflineAndStaleWritesAreRejected() {
        XCTAssertThrowsError(try fixture(online: false).payload(.power, value: .bool(true)))
        var device = fixture()
        device.updatedAt = Date().addingTimeInterval(-181)
        XCTAssertThrowsError(try device.payload(.temperature, value: .number(25)))
    }
    func testModelLimitsAndNanoeAreEnforced() throws {
        var device = fixture()
        device.detail["_model"] = .object(["controlCapabilities": .array([
            .object(["capability": .string("NanoeG"), "attributes": .array([.object(["key": .string("acng")])])]),
            .object(["attributes": .array([.object(["key": .string("actmp"), "range": .object([
                "min": .array([.object(["unit": .string("celcius"), "value": .number(18)])]),
                "max": .array([.object(["unit": .string("celcius"), "value": .number(28)])]),
                "increment": .array([.object(["unit": .string("celcius"), "value": .number(0.5)])])])])])])
        ])])
        device.apply(["acng": .string("on")])
        XCTAssertTrue(device.features.contains(.nanoe))
        XCTAssertEqual(device.temperatureRange.min, 18)
        XCTAssertEqual(device.temperatureRange.max, 28)
        XCTAssertEqual(device.temperatureRange.step, 0.5)
        XCTAssertNoThrow(try device.payload(.temperature, value: .number(24.5)))
        XCTAssertThrowsError(try device.payload(.temperature, value: .number(17)))
        XCTAssertEqual(try device.payload(.nanoe, value: .bool(false))["ki"], .number(7))
        XCTAssertEqual(device.options[.mode], [])
    }
    func testUnsupportedCapacityIsNeverGuessedFromState() {
        var device = fixture()
        device.apply(["cnv": .number(80)])
        XCTAssertEqual(device.options[.capacity], [])
        XCTAssertThrowsError(try device.payload(.capacity, value: .string("80")))
        device.detail["supportedCapacity"] = .array([.number(0), .number(80), .number(100)])
        device.configureCapabilities()
        let payload = try? device.payload(.capacity, value: .string("80"), sequence: 42)
        XCTAssertEqual(payload?["cnv"], .number(80))
        XCTAssertEqual(payload?["sid"], .string("42"))
        XCTAssertEqual(payload?["acem"], .string("off"))
    }
    func testHeatRequiresModelOrObservedSupport() {
        var device = fixture()
        XCTAssertFalse(device.options[.mode, default: []].contains("heat"))
        device.apply(["acmd": .string("heat")])
        XCTAssertTrue(device.options[.mode, default: []].contains("heat"))
    }
    func testPhoneNormalization() throws {
        XCTAssertEqual(try CloudAPI.normalizedUsername("  test@example.invalid  "), "test@example.invalid")
        XCTAssertEqual(try CloudAPI.normalizedUsername("90000 00000"), "+919000000000")
        XCTAssertEqual(try CloudAPI.normalizedUsername("+1 (202) 555-0100"), "+12025550100")
        XCTAssertThrowsError(try CloudAPI.normalizedUsername("not-a-number"))
    }
}
