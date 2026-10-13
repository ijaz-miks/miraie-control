import Foundation

public protocol ACAPI: Sendable {
    func signIn(_ credentials: Credentials) async throws
    func discover() async throws -> [Device]
    func status(_ identity: String) async throws -> [String: JSONValue]
    func control(_ identity: String, payload: [String: JSONValue]) async throws
    func signOut() async
}

public actor CloudAPI: ACAPI {
    private let session: URLSession
    private var credentials: Credentials?
    private var token: String?
    private var expiry = Date.distantPast
    private var generation = 0
    private static let base = "https://app.miraie.in/simplifi/v1"
    private static let auth = "https://auth.miraie.in/simplifi/v1/userManagement/login"
    // Shared public application configuration, not an account credential.
    private static let clientID = "PBcMcfG19njNCL8AOgvRzIC8AjQa"

    public init(session: URLSession? = nil) {
        if let session { self.session = session }
        else {
            let config = URLSessionConfiguration.ephemeral
            config.timeoutIntervalForRequest = 12
            config.timeoutIntervalForResource = 20
            config.urlCache = nil
            config.httpCookieStorage = nil
            self.session = URLSession(configuration: config)
        }
    }

    public static func normalizedUsername(_ input: String) throws -> String {
        var username = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !username.isEmpty else { throw MirAIeError("Enter your MirAIe email or mobile number.") }
        if !username.contains("@") {
            username = username.filter { !$0.isWhitespace && !"()-".contains($0) }
            if username.range(of: "^[0-9]{10}$", options: .regularExpression) != nil { username = "+91" + username }
            guard username.range(of: "^\\+[0-9]{8,15}$", options: .regularExpression) != nil else {
                throw MirAIeError("Use your mobile number with its country code, or your email.")
            }
        }
        return username
    }

    public func signIn(_ input: Credentials) async throws {
        generation += 1
        credentials = nil
        token = nil
        let normalized = try Self.normalizedUsername(input.username)
        guard !input.password.isEmpty else { throw MirAIeError("Enter your MirAIe password.") }
        let account = Credentials(username: normalized, password: input.password)
        try await authenticate(account, generation: generation)
    }

    private func authenticate(_ account: Credentials, generation expected: Int) async throws {
        let payload: [String: JSONValue] = ["clientId": .string(Self.clientID), "scope": .string("an_14214235325"),
            "password": .string(account.password), account.username.contains("@") ? "email" : "mobile": .string(account.username)]
        let response = try await request("POST", url: Self.auth, payload: payload, authenticated: false)
        guard generation == expected else { throw CancellationError() }
        guard let bearer = response.object["accessToken"]?.text, !bearer.isEmpty else {
            throw MirAIeError("MirAIe did not return a valid session.")
        }
        token = bearer
        credentials = account
        expiry = Date().addingTimeInterval(max(30, (response.object["expiresIn"]?.double ?? 3600) - 120))
    }

    private func ensureSession() async throws {
        guard let credentials else { throw MirAIeError("Sign in to your MirAIe account.") }
        if token == nil || expiry <= Date() { try await authenticate(credentials, generation: generation) }
    }

    private func request(_ method: String, url: String, payload: [String: JSONValue]? = nil,
                         authenticated: Bool = true) async throws -> JSONValue {
        if authenticated { try await ensureSession() }
        guard let address = URL(string: url), address.scheme == "https",
              ["app.miraie.in", "auth.miraie.in"].contains(address.host) else {
            throw MirAIeError("Invalid MirAIe endpoint.")
        }
        var request = URLRequest(url: address)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if authenticated, let token { request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization") }
        if let payload { request.httpBody = try JSONEncoder().encode(JSONValue.object(payload)) }
        let data: Data
        let response: URLResponse
        do { (data, response) = try await session.data(for: request) }
        catch is CancellationError { throw CancellationError() }
        catch { throw MirAIeError("Cannot reach MirAIe. Check your Internet connection.") }
        guard let response = response as? HTTPURLResponse else { throw MirAIeError("Unsupported cloud response.") }
        guard (200..<300).contains(response.statusCode) else {
            switch response.statusCode {
            case 401, 406:
                token = nil
                throw MirAIeError("Your MirAIe session expired. Refresh or reconnect your account.")
            case 429: throw MirAIeError("MirAIe is limiting requests. Please wait before refreshing.")
            case 400 where !authenticated, 403 where !authenticated:
                throw MirAIeError("MirAIe could not sign in. Check your account details.")
            default: throw MirAIeError("MirAIe returned HTTP \(response.statusCode).")
            }
        }
        guard data.count <= 2 * 1024 * 1024 else { throw MirAIeError("The cloud response was too large.") }
        if data.isEmpty { return .object([:]) }
        guard let value = try? JSONDecoder().decode(JSONValue.self, from: data) else {
            throw MirAIeError("MirAIe returned an unsupported response.")
        }
        return value
    }

    private static func component(_ text: String) -> String {
        let safe = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-._~"))
        return text.addingPercentEncoding(withAllowedCharacters: safe) ?? ""
    }

    public func discover() async throws -> [Device] {
        let result = try await request("GET", url: Self.base + "/homeManagement/homes")
        let homes: [JSONValue]
        if case .array(let list) = result { homes = list }
        else if result.object["homeId"] != nil { homes = [result] }
        else { throw MirAIeError("MirAIe did not return a list of homes.") }
        var devices: [Device] = []
        for home in homes {
            for room in home.object["spaces"]?.array ?? [] {
                for source in room.object["devices"]?.array ?? [] {
                    guard ["AC", "ODMIRB-AC"].contains(source.object["category"]?.text?.uppercased() ?? ""),
                          let id = source.object["deviceId"]?.text, !id.isEmpty,
                          !devices.contains(where: { $0.id == id }) else { continue }
                    devices.append(Device(id: id, name: source.object["deviceName"]?.text ?? "Air conditioner",
                        room: room.object["spaceName"]?.text ?? "", model: source.object["modelName"]?.text ?? "",
                        detail: Self.modelDetails(source.object)))
                }
            }
        }
        if !devices.isEmpty {
            let ids = devices.map { Self.component($0.id) }.joined(separator: ",")
            if let details = try? await request("GET", url: Self.base + "/deviceManagement/devices/deviceId/" + ids) {
                for detail in details.array {
                    if let index = devices.firstIndex(where: { $0.id == detail.object["deviceId"]?.text }) {
                        devices[index].detail.merge(Self.modelDetails(detail.object)) { _, new in new }
                    }
                }
            }
            var models: [String: JSONValue] = [:]
            for index in devices.indices {
                guard let modelID = devices[index].detail["modelId"]?.text else { continue }
                if models[modelID] == nil {
                    models[modelID] = (try? await request("GET", url: Self.base + "/deviceManagement/deviceModels/modelId/" + Self.component(modelID))) ?? .object([:])
                }
                devices[index].detail["_model"] = models[modelID]
            }
        }
        return devices
    }

    private static func modelDetails(_ detail: [String: JSONValue]) -> [String: JSONValue] {
        // The Mac client does not need home IDs, MQTT topics, MACs or UDP secrets.
        let fields = ["firmwareVersion", "modelId", "modelName", "modelNumber", "supportedModes",
                      "minTemperature", "maxTemperature", "temperatureStep", "supportedCapacity"]
        return detail.filter { fields.contains($0.key) }
    }

    public func status(_ identity: String) async throws -> [String: JSONValue] {
        let value = try await request("GET", url: Self.base + "/deviceManagement/devices/" + Self.component(identity) + "/mobile/status")
        guard case .object(let status) = value else { throw MirAIeError("Unsupported device status.") }
        return status
    }

    public func control(_ identity: String, payload: [String: JSONValue]) async throws {
        _ = try await request("POST", url: Self.base + "/deviceManagement/control",
            payload: ["deviceId": .string(identity), "control_params": .object(payload)])
    }

    public func signOut() async {
        generation += 1
        credentials = nil
        token = nil
        expiry = .distantPast
    }
}
