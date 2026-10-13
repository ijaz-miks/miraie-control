import Combine
import Foundation

public enum ConnectionState: String, Sendable { case signedOut, connecting, ready, offline }

@MainActor public final class ACController: ObservableObject {
    @Published public private(set) var devices: [Device] = []
    @Published public private(set) var connection = ConnectionState.signedOut
    @Published public private(set) var isBusy = false
    @Published public private(set) var pendingField: ControlField?
    @Published public private(set) var error = ""
    @Published public private(set) var username = ""
    @Published public var selectedID: String {
        didSet { defaults?.set(selectedID, forKey: "selected-device") }
    }

    private let api: any ACAPI
    private let store: any CredentialStore
    private let defaults: UserDefaults?
    private let pause: @Sendable (Double) async throws -> Void
    private let confirmationAttempts: Int
    private var generation = 0
    private var sequence = 0

    public init(api: any ACAPI, store: any CredentialStore, defaults: UserDefaults? = .standard,
                confirmationAttempts: Int = 8,
                pause: @escaping @Sendable (Double) async throws -> Void = {
                    try await Task.sleep(nanoseconds: UInt64($0 * 1_000_000_000))
                }) {
        self.api = api
        self.store = store
        self.defaults = defaults
        self.pause = pause
        self.confirmationAttempts = confirmationAttempts
        self.selectedID = defaults?.string(forKey: "selected-device") ?? ""
    }

    public var selectedDevice: Device? { devices.first { $0.id == selectedID } ?? devices.first }
    public var canControl: Bool { connection == .ready && selectedDevice?.online == true && !isBusy }

    public func start() async {
        do {
            if let saved = try store.load() { await connect(saved, remember: false) }
        } catch { self.error = message(error) }
    }

    public func signIn(username: String, password: String) async {
        await connect(Credentials(username: username, password: password), remember: true)
    }

    private func connect(_ account: Credentials, remember: Bool) async {
        guard !isBusy else { return }
        generation += 1
        let current = generation
        isBusy = true
        connection = .connecting
        error = ""
        devices = []
        username = ""
        do {
            try await api.signIn(account)
            let discovered = try await api.discover()
            guard current == generation else { return }
            devices = discovered
            if !devices.contains(where: { $0.id == selectedID }) { selectedID = devices.first?.id ?? "" }
            username = try CloudAPI.normalizedUsername(account.username)
            await refreshDevices(generation: current)
            guard current == generation else { return }
            if remember { try store.save(account) }
        } catch {
            guard current == generation else { return }
            self.error = message(error)
            connection = .offline
        }
        if current == generation { isBusy = false }
    }

    public func refresh() async {
        guard !isBusy, !username.isEmpty else { return }
        isBusy = true
        error = ""
        let current = generation
        await refreshDevices(generation: current)
        if current == generation { isBusy = false }
    }

    private func refreshDevices(generation current: Int) async {
        var successes = 0
        for id in devices.map(\.id) {
            do {
                let response = try await api.status(id)
                guard current == generation else { return }
                if let index = devices.firstIndex(where: { $0.id == id }) { devices[index].apply(response) }
                successes += 1
            } catch {
                guard current == generation else { return }
                if let index = devices.firstIndex(where: { $0.id == id }) { devices[index].online = false }
                self.error = message(error)
            }
        }
        connection = devices.isEmpty || successes > 0 ? .ready : .offline
        if successes > 0 && successes < devices.count { error = "Some air conditioners could not be refreshed." }
    }

    public func command(_ field: ControlField, value: JSONValue) async {
        guard !isBusy else { return }
        let current = generation
        do {
            guard connection == .ready, let device = selectedDevice else {
                throw MirAIeError("The air conditioner is unavailable. Refresh before changing it.")
            }
            sequence += 1
            let payload = try device.payload(field, value: value, sequence: sequence)
            isBusy = true
            pendingField = field
            error = ""
            // Send once. A lost response is never retried automatically.
            try await api.control(device.id, payload: payload)
            guard current == generation else { return }
            let deadline = Date().addingTimeInterval(12)
            var confirmed = false
            for attempt in 0..<confirmationAttempts {
                if attempt > 0 { try await pause(1) }
                guard current == generation else { return }
                if Date() >= deadline { break }
                let response = try await api.status(device.id)
                guard current == generation else { return }
                guard let index = devices.firstIndex(where: { $0.id == device.id }) else { return }
                devices[index].apply(response)
                let present = Device.unwrap(response)[field.wire] != nil
                let actual = devices[index].value(field)
                let matches: Bool
                if field == .temperature, let actual = actual?.double, let expected = value.double {
                    matches = abs(actual - expected) < 0.01
                } else { matches = actual == value }
                if present && devices[index].online && matches { confirmed = true; break }
            }
            if !confirmed { throw MirAIeError("The AC did not confirm this setting. Refresh to check its current state.") }
        } catch {
            guard current == generation else { return }
            self.error = message(error)
        }
        if current == generation { isBusy = false; pendingField = nil }
    }

    public func signOut() async {
        do { try store.clear() }
        catch { self.error = message(error); return }
        generation += 1
        devices = []
        username = ""
        selectedID = ""
        connection = .signedOut
        isBusy = false
        pendingField = nil
        error = ""
        await api.signOut()
    }

    private func message(_ error: Error) -> String {
        (error as? MirAIeError)?.message ?? "The controller could not complete this request. Please try again."
    }
}
