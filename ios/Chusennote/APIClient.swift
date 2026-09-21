import Foundation

enum ChusennoteSettings {
    static let baseURLKey = "baseURL"
    static let apiTokenKey = "apiToken"
    static let calendarTokenKey = "calendarToken"
    static let pushTokenKey = "iosPushToken"
    static let defaultBaseURL = "https://chusennote.onrender.com"

    static var baseURL: String {
        get {
            UserDefaults.standard.string(forKey: baseURLKey) ?? defaultBaseURL
        }
        set {
            UserDefaults.standard.set(newValue, forKey: baseURLKey)
        }
    }

    static var apiToken: String {
        get { migratedAPIToken() }
        set { KeychainStore.set(newValue, forKey: apiTokenKey) }
    }

    static var calendarToken: String {
        get { KeychainStore.string(forKey: calendarTokenKey) ?? "" }
        set { KeychainStore.set(newValue, forKey: calendarTokenKey) }
    }

    static var pushToken: String {
        get { migratedPushToken() }
        set { KeychainStore.set(newValue, forKey: pushTokenKey) }
    }

    /// One-time migration off the old UserDefaults-backed token (a full-access
    /// credential that plist storage isn't a safe place for) into the Keychain.
    private static func migratedAPIToken() -> String {
        if let existing = KeychainStore.string(forKey: apiTokenKey) {
            return existing
        }
        if let legacy = UserDefaults.standard.string(forKey: apiTokenKey), !legacy.isEmpty {
            KeychainStore.set(legacy, forKey: apiTokenKey)
            UserDefaults.standard.removeObject(forKey: apiTokenKey)
            return legacy
        }
        return ""
    }

    /// Earlier builds stored the device-addressing token in UserDefaults.
    /// Preserve it long enough to detach/re-register the device, but remove
    /// the plaintext copy immediately after migrating it to the Keychain.
    private static func migratedPushToken() -> String {
        if let existing = KeychainStore.string(forKey: pushTokenKey) {
            UserDefaults.standard.removeObject(forKey: pushTokenKey)
            return existing
        }
        if let legacy = UserDefaults.standard.string(forKey: pushTokenKey), !legacy.isEmpty {
            KeychainStore.set(legacy, forKey: pushTokenKey)
            UserDefaults.standard.removeObject(forKey: pushTokenKey)
            return legacy
        }
        UserDefaults.standard.removeObject(forKey: pushTokenKey)
        return ""
    }
}

enum APIClientError: LocalizedError {
    case invalidBaseURL
    case insecureCredentialTransport
    case http(status: Int, message: String)

    var errorDescription: String? {
        switch self {
        case .invalidBaseURL:
            return "Enter a valid backend URL."
        case .insecureCredentialTransport:
            return BackendURLPolicy.credentialTransportMessage
        case let .http(status, message):
            return message.isEmpty ? "Server returned HTTP \(status)." : message
        }
    }

    var statusCode: Int? {
        if case let .http(status, _) = self { return status }
        return nil
    }
}

@MainActor
final class ChusennoteStore: ObservableObject {
    @Published var baseURL = ChusennoteSettings.baseURL {
        didSet {
            ChusennoteSettings.baseURL = baseURL
            if baseURL != oldValue {
                calendarToken = ""
            }
        }
    }
    @Published var apiToken = ChusennoteSettings.apiToken {
        didSet {
            ChusennoteSettings.apiToken = apiToken
            // The cached calendar token belongs to whichever account was
            // signed in when it was minted; drop it so a stale one from a
            // previous account/server is never reused after switching.
            calendarToken = ""
        }
    }
    @Published var calendarToken = ChusennoteSettings.calendarToken {
        didSet {
            ChusennoteSettings.calendarToken = calendarToken
        }
    }
    @Published var watches: [Watch] = []
    @Published var events: [EventSummary] = []
    @Published var upcoming: [UpcomingItem] = []
    @Published var alerts: [AlertPayload] = []
    @Published var notificationFeed: [NotificationFeedItem] = []
    @Published var subscriptions: [NotificationSubscription] = []
    @Published var devices: [DeviceToken] = []
    @Published var sources: [WatchSource] = []
    @Published var health: HealthSummary?
    @Published var errorMessage: String?
    @Published var signedInEmail = ""
    @Published var isRefreshing = false
    @Published var isRunningChecks = false
    @Published var isRunningNotifications = false
    @Published var isAccountTransitioning = false

    var isSignedIn: Bool {
        !apiToken.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var trackedArtists: [Watch] {
        watches.filter { !$0.muted && ($0.kind ?? "event") == "artist" }
    }

    var trackedEvents: [Watch] {
        watches.filter { !$0.muted && ($0.kind ?? "event") == "event" }
    }

    var mutedWatches: [Watch] {
        watches.filter { $0.muted }
    }

    var activeSources: [WatchSource] {
        sources.filter { !$0.muted }
    }

    var mutedSources: [WatchSource] {
        sources.filter { $0.muted }
    }

    /// The calendar subscription URL, authorized with a calendar-only token
    /// (never the full-access API token) so a leaked/shared link can't be
    /// replayed to mutate the account. Mints one on first use via
    /// POST /api/calendar/token and caches it; call again after switching
    /// accounts or servers to pick up a fresh one.
    func calendarFeedURL() async -> URL? {
        let requestedBaseURL = baseURL
        let requestedAPIToken = apiToken
        guard var components = URLComponents(
            string: baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + "/calendar.ics"
        ) else {
            return nil
        }
        guard !apiToken.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return components.url
        }
        if calendarToken.isEmpty {
            do {
                let response: CalendarTokenResponse = try await post("/api/calendar/token", body: "")
                // Settings may change while the request is suspended. Never
                // cache a response or open a URL for a previous account/server.
                guard baseURL == requestedBaseURL, apiToken == requestedAPIToken else { return nil }
                guard !response.token.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                    throw URLError(.badServerResponse)
                }
                calendarToken = response.token
            } catch {
                errorMessage = "Could not authorize calendar feed: \(error.localizedDescription)"
                return nil
            }
        }
        components.queryItems = [URLQueryItem(name: "token", value: calendarToken)]
        errorMessage = nil
        return components.url
    }

    func refresh() async {
        isRefreshing = true
        defer { isRefreshing = false }
        do {
            async let fetchedWatches: [Watch] = fetch("/api/watchlist?include_muted=1")
            async let fetchedEvents: [EventSummary] = fetch("/api/events")
            async let fetchedUpcoming: [UpcomingItem] = fetch("/api/upcoming")
            async let fetchedAlerts: [AlertPayload] = fetch("/api/alerts")
            async let fetchedNotificationFeed: [NotificationFeedItem] = fetch("/api/notifications?limit=100")
            async let fetchedSubscriptions: [NotificationSubscription] = fetch("/api/subscriptions")
            async let fetchedDevices: [DeviceToken] = fetch("/api/devices")
            async let fetchedSources: [WatchSource] = fetch("/api/sources?include_muted=1")
            async let fetchedHealth: HealthSummary = fetch("/api/health")
            watches = try await fetchedWatches
            events = try await fetchedEvents
            upcoming = try await fetchedUpcoming
            alerts = try await fetchedAlerts
            notificationFeed = try await fetchedNotificationFeed
            subscriptions = try await fetchedSubscriptions
            devices = try await fetchedDevices
            sources = try await fetchedSources
            health = try await fetchedHealth
            await DeviceRegistration.shared.registerSavedTokenIfPossible()
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func refreshAccountStatus() async {
        guard isSignedIn else {
            signedInEmail = ""
            return
        }
        do {
            let user: UserAccount = try await fetch("/api/auth/me")
            signedInEmail = user.email
        } catch let error as APIClientError where error.statusCode == 401 {
            apiToken = ""
            signedInEmail = ""
            await DeviceRegistration.shared.registerSavedTokenIfPossible()
        } catch {
            // A temporary server failure must not destroy a valid login.
            errorMessage = "Could not verify account. Login saved; try again when the server is available."
        }
    }

    func registerAccount(email: String, password: String) async {
        await submitAccount(path: "/api/auth/register", email: email, password: password, action: "register")
    }

    func loginAccount(email: String, password: String) async {
        await submitAccount(path: "/api/auth/login", email: email, password: password, action: "log in")
    }

    private func submitAccount(path: String, email: String, password: String, action: String) async {
        guard !isSignedIn else {
            errorMessage = "Log out before changing accounts or servers."
            return
        }
        let cleanEmail = email.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleanEmail.isEmpty, !password.isEmpty else {
            errorMessage = "Enter an email and password first."
            return
        }
        guard BackendURLPolicy.permitsCredentialTransport(baseURL) else {
            errorMessage = BackendURLPolicy.credentialTransportMessage
            return
        }

        isAccountTransitioning = true
        await DeviceRegistration.shared.pauseForAccountTransition()
        do {
            let response: AuthResponse = try await post(
                path,
                body: formBody([
                    URLQueryItem(name: "email", value: cleanEmail),
                    URLQueryItem(name: "password", value: password)
                ]),
                requiresCredentialTransport: true
            )
            apiToken = response.token
            signedInEmail = response.user.email
            errorMessage = nil
            await DeviceRegistration.shared.resumeAfterAccountTransition()
            await refresh()
        } catch {
            await DeviceRegistration.shared.resumeAfterAccountTransition()
            errorMessage = "Could not \(action): \(error.localizedDescription)"
        }
        isAccountTransitioning = false
    }

    func logoutAccount() async {
        guard isSignedIn else {
            errorMessage = "Not signed in."
            return
        }
        guard BackendURLPolicy.permitsCredentialTransport(baseURL) else {
            errorMessage = BackendURLPolicy.credentialTransportMessage
            return
        }

        isAccountTransitioning = true
        await DeviceRegistration.shared.pauseForAccountTransition()
        let pushToken = ChusennoteSettings.pushToken
        do {
            let response: LogoutResponse = try await post(
                "/api/auth/logout",
                body: formBody([URLQueryItem(name: "device_token", value: pushToken)]),
                requiresCredentialTransport: true
            )
            guard response.revoked, pushToken.isEmpty || response.deviceDetached else {
                throw APIClientError.http(
                    status: 409,
                    message: "Update the server to detach this push device before signing out."
                )
            }
            apiToken = ""
            signedInEmail = ""
            errorMessage = nil
            await DeviceRegistration.shared.resumeAfterAccountTransition()
            await refresh()
        } catch {
            await DeviceRegistration.shared.resumeAfterAccountTransition()
            errorMessage = "Could not finish signing out. Reconnect and retry: \(error.localizedDescription)"
        }
        isAccountTransitioning = false
    }

    @discardableResult
    func deleteAccount(password: String) async -> Bool {
        guard isSignedIn else {
            errorMessage = "Not signed in."
            return false
        }
        guard !password.isEmpty else {
            errorMessage = "Enter your password to confirm account deletion."
            return false
        }
        guard BackendURLPolicy.permitsCredentialTransport(baseURL) else {
            errorMessage = BackendURLPolicy.credentialTransportMessage
            return false
        }

        isAccountTransitioning = true
        await DeviceRegistration.shared.pauseForAccountTransition()
        do {
            let response: DeleteAccountResponse = try await post(
                "/api/auth/delete",
                body: formBody([URLQueryItem(name: "password", value: password)]),
                requiresCredentialTransport: true
            )
            guard response.deleted else {
                throw APIClientError.http(status: 409, message: "The server did not confirm account deletion.")
            }
            apiToken = ""
            signedInEmail = ""
            errorMessage = nil
            await DeviceRegistration.shared.resumeAfterAccountDeletion()
            await refresh()
            isAccountTransitioning = false
            return true
        } catch {
            await DeviceRegistration.shared.resumeAfterAccountTransition()
            errorMessage = "Could not delete account: \(error.localizedDescription)"
            isAccountTransitioning = false
            return false
        }
    }

    func addWatch(
        keyword: String,
        kind: String,
        tags: String = "",
        regions: String = "",
        venues: String = "",
        alerts: String = ""
    ) async {
        do {
            var fields = URLComponents()
            fields.queryItems = [
                URLQueryItem(name: "keyword", value: keyword),
                URLQueryItem(name: "kind", value: kind),
                URLQueryItem(name: "tags", value: tags),
                URLQueryItem(name: "regions", value: regions),
                URLQueryItem(name: "venues", value: venues),
                URLQueryItem(name: "alerts", value: alerts)
            ]
            let _: Watch = try await post("/api/watchlist", body: formBody(fields))
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func runWatches(kind: String) async {
        isRunningChecks = true
        defer { isRunningChecks = false }
        do {
            let _: [AlertPayload] = try await post("/api/run", body: formBody([URLQueryItem(name: "kind", value: kind)]))
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func runEventWatches() async {
        await runWatches(kind: "event")
    }

    func runArtistWatches() async {
        await runWatches(kind: "artist")
    }

    func runNotifications() async {
        isRunningNotifications = true
        defer { isRunningNotifications = false }
        do {
            let _: [NotificationFeedItem] = try await post("/api/notifications/run", body: "")
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func addSource(watch: String, url: String, label: String, privateNote: Bool = false) async {
        do {
            var fields = URLComponents()
            fields.queryItems = [
                URLQueryItem(name: "watch", value: watch),
                URLQueryItem(name: "url", value: url),
                URLQueryItem(name: "label", value: label)
            ]
            if privateNote {
                fields.queryItems?.append(URLQueryItem(name: "private_note", value: "1"))
            }
            let _: WatchSource = try await post("/api/sources", body: formBody(fields))
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func removeWatch(id: Int) async {
        do {
            let _: RemoveResponse = try await post("/api/watchlist/remove", body: formBody([URLQueryItem(name: "identifier", value: "\(id)")]))
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func restoreWatch(id: Int) async {
        do {
            let _: UnmuteResponse = try await post("/api/watchlist/unmute", body: formBody([URLQueryItem(name: "identifier", value: "\(id)")]))
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func removeSource(id: Int) async {
        do {
            let _: RemoveResponse = try await post("/api/sources/remove", body: formBody([URLQueryItem(name: "identifier", value: "\(id)")]))
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func restoreSource(id: Int) async {
        do {
            let _: UnmuteResponse = try await post("/api/sources/unmute", body: formBody([URLQueryItem(name: "identifier", value: "\(id)")]))
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func addSubscription(
        watch: String,
        scope: String,
        location: String = "",
        roundKey: String = "",
        channels: String = "feed,push",
        leadDays: String = "7,1,0"
    ) async {
        do {
            let _: NotificationSubscription = try await post(
                "/api/subscriptions",
                body: formBody([
                    URLQueryItem(name: "watch", value: watch),
                    URLQueryItem(name: "scope", value: scope),
                    URLQueryItem(name: "location", value: location),
                    URLQueryItem(name: "round_key", value: roundKey),
                    URLQueryItem(name: "channels", value: channels),
                    URLQueryItem(name: "lead_days", value: leadDays)
                ])
            )
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func removeSubscription(id: Int) async {
        do {
            let _: RemoveResponse = try await post("/api/subscriptions/remove", body: formBody([URLQueryItem(name: "identifier", value: "\(id)")]))
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func searchExactEvents(keyword: String) async -> [SearchResult] {
        let query = formBody([URLQueryItem(name: "keyword", value: keyword), URLQueryItem(name: "limit", value: "6")])
        do {
            let results: [SearchResult] = try await fetch("/api/event/search?\(query)")
            errorMessage = nil
            return results
        } catch {
            errorMessage = error.localizedDescription
            return []
        }
    }

    func addExactEvent(keyword: String, title: String, url: String, snippet: String) async {
        do {
            let _: AddedEventResponse = try await post(
                "/api/event/add",
                body: formBody([
                    URLQueryItem(name: "keyword", value: keyword),
                    URLQueryItem(name: "title", value: title),
                    URLQueryItem(name: "url", value: url),
                    URLQueryItem(name: "snippet", value: snippet)
                ])
            )
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func fetch<T: Decodable>(_ path: String) async throws -> T {
        guard let url = URL(string: baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + path) else {
            throw APIClientError.invalidBaseURL
        }
        let (data, response) = try await BackendSession.shared.data(for: try request(url: url))
        try validate(response: response, data: data)
        return try JSONDecoder().decode(T.self, from: data)
    }

    private func post<T: Decodable>(
        _ path: String,
        body: String,
        requiresCredentialTransport: Bool = false
    ) async throws -> T {
        guard let url = URL(string: baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + path) else {
            throw APIClientError.invalidBaseURL
        }
        if requiresCredentialTransport && !BackendURLPolicy.permitsCredentialTransport(baseURL) {
            throw APIClientError.insecureCredentialTransport
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        try applyAuthorization(to: &request)
        request.httpBody = body.data(using: .utf8)
        let (data, response) = try await BackendSession.shared.data(for: request)
        try validate(response: response, data: data)
        return try JSONDecoder().decode(T.self, from: data)
    }

    private func formBody(_ queryItems: [URLQueryItem]) -> String {
        var fields = URLComponents()
        fields.queryItems = queryItems
        return formBody(fields)
    }

    private func formBody(_ fields: URLComponents) -> String {
        fields.percentEncodedQuery ?? ""
    }

    private func request(url: URL) throws -> URLRequest {
        var request = URLRequest(url: url)
        try applyAuthorization(to: &request)
        return request
    }

    private func applyAuthorization(to request: inout URLRequest) throws {
        let token = apiToken.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !token.isEmpty else { return }
        guard BackendURLPolicy.permitsCredentialTransport(baseURL) else {
            throw APIClientError.insecureCredentialTransport
        }
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
    }

    private func validate(response: URLResponse, data: Data) throws {
        guard let http = response as? HTTPURLResponse else {
            throw APIClientError.http(status: 0, message: "The backend returned an invalid response.")
        }
        guard 200..<300 ~= http.statusCode else {
            let serverMessage = (try? JSONDecoder().decode(APIErrorResponse.self, from: data).error) ?? ""
            throw APIClientError.http(status: http.statusCode, message: serverMessage)
        }
    }
}
