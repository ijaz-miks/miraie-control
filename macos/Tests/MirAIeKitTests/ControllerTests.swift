import XCTest
@testable import MirAIeKit

@MainActor final class MemoryStore: CredentialStore {
    var credentials: Credentials? = Credentials(username: "test@example.invalid", password: "synthetic-password")
    func load() throws -> Credentials? { credentials }
    func save(_ credentials: Credentials) throws { self.credentials = credentials }
    func clear() throws { credentials = nil }
}

actor MockAPI: ACAPI {
    var device = fixture()
    var responses: [[String: JSONValue]] = []
    var uncertainWrite = false
    var failingStatus = false
    var waitForControl = false
    var writes = 0
    var controlContinuation: CheckedContinuation<Void, Never>?
    func signIn(_ credentials: Credentials) async throws {}
    func discover() async throws -> [Device] { [device] }
    func status(_ identity: String) async throws -> [String: JSONValue] {
        if failingStatus { throw MirAIeError("Synthetic network failure.") }
        return responses.isEmpty ? device.raw : responses.removeFirst()
    }
    func control(_ identity: String, payload: [String: JSONValue]) async throws {
        writes += 1
        if uncertainWrite { throw MirAIeError("The write response was lost.") }
        if waitForControl { await withCheckedContinuation { controlContinuation = $0 } }
        if responses.isEmpty { device.apply(payload) }
    }
    func signOut() async {}
    func setResponses(_ values: [[String: JSONValue]]) { responses = values }
    func failWrites() { uncertainWrite = true }
    func failStatus() { failingStatus = true }
    func suspendWrites() { waitForControl = true }
    func releaseWrite() { controlContinuation?.resume(); controlContinuation = nil }
    func writeCount() -> Int { writes }
}

@MainActor final class ControllerTests: XCTestCase {
    private func model(_ api: MockAPI, store: MemoryStore? = nil) -> ACController {
        ACController(api: api, store: store ?? MemoryStore(), defaults: nil,
            confirmationAttempts: 2, pause: { _ in })
    }
    func testStartAndRefreshDoNotIssueCommands() async {
        let api = MockAPI()
        let controller = model(api)
        await controller.start()
        await controller.refresh()
        XCTAssertEqual(controller.connection, .ready)
        XCTAssertTrue(controller.canControl)
        let count = await api.writeCount()
        XCTAssertEqual(count, 0)
    }
    func testFreshStatusConfirmsAChange() async {
        let api = MockAPI()
        let controller = model(api)
        await controller.start()
        await controller.command(.temperature, value: .number(25))
        XCTAssertEqual(controller.selectedDevice?.temperature, 25)
        XCTAssertFalse(controller.isBusy)
        XCTAssertNil(controller.pendingField)
        XCTAssertEqual(controller.error, "")
        let count = await api.writeCount()
        XCTAssertEqual(count, 1)
    }
    func testPartialTelemetryCannotConfirmAnOldField() async {
        let api = MockAPI()
        let controller = model(api)
        await controller.start()
        await api.setResponses([["onlineStatus": .string("true"), "acfs": .string("low")],
            ["onlineStatus": .string("true"), "acfs": .string("low")]])
        await controller.command(.temperature, value: .number(24))
        XCTAssertTrue(controller.error.contains("did not confirm"))
        XCTAssertEqual(controller.selectedDevice?.temperature, 24)
        let count = await api.writeCount()
        XCTAssertEqual(count, 1)
    }
    func testUncertainWriteIsSentOnce() async {
        let api = MockAPI()
        let controller = model(api)
        await controller.start()
        await api.failWrites()
        await controller.command(.power, value: .bool(false))
        XCTAssertEqual(controller.selectedDevice?.power, true)
        XCTAssertFalse(controller.isBusy)
        XCTAssertFalse(controller.error.isEmpty)
        let count = await api.writeCount()
        XCTAssertEqual(count, 1)
    }
    func testNetworkFailureMakesCachedPowerUnavailable() async {
        let api = MockAPI()
        let controller = model(api)
        await controller.start()
        await api.failStatus()
        await controller.refresh()
        XCTAssertEqual(controller.connection, .offline)
        XCTAssertNil(controller.selectedDevice?.power)
        XCTAssertFalse(controller.canControl)
        await controller.command(.power, value: .bool(false))
        let count = await api.writeCount()
        XCTAssertEqual(count, 0)
    }
    func testInvalidValueNeverSendsRequest() async {
        let api = MockAPI()
        let controller = model(api)
        await controller.start()
        await controller.command(.temperature, value: .number(31))
        XCTAssertFalse(controller.error.isEmpty)
        let count = await api.writeCount()
        XCTAssertEqual(count, 0)
    }
    func testSignOutClearsLocalStateAndSavedAccount() async {
        let api = MockAPI()
        let store = MemoryStore()
        let controller = model(api, store: store)
        await controller.start()
        await controller.signOut()
        XCTAssertEqual(controller.connection, .signedOut)
        XCTAssertTrue(controller.devices.isEmpty)
        XCTAssertNil(store.credentials)
        XCTAssertEqual(controller.username, "")
    }
    func testLateCommandCannotRestoreSignedOutSession() async {
        let api = MockAPI()
        let controller = model(api)
        await controller.start()
        await api.suspendWrites()
        let command = Task { await controller.command(.temperature, value: .number(25)) }
        for _ in 0..<100 {
            if await api.writeCount() == 1 { break }
            await Task.yield()
        }
        await controller.signOut()
        await api.releaseWrite()
        await command.value
        XCTAssertEqual(controller.connection, .signedOut)
        XCTAssertTrue(controller.devices.isEmpty)
        XCTAssertFalse(controller.isBusy)
    }
}
