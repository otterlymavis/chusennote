import Foundation
import Observation

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
@Observable
final class ChusennoteStore {
    @ObservationIgnored private let session: URLSession

    init(session: URLSession = BackendSession.shared) {
        self.session = session
    }

    var baseURL = ChusennoteSettings.baseURL {
        didSet {
            ChusennoteSettings.baseURL = baseURL
            if baseURL != oldValue {
                calendarToken = ""
            }
        }
    }
    var apiToken = ChusennoteSettings.apiToken {
        didSet {
            ChusennoteSettings.apiToken = apiToken
            // The cached calendar token belongs to whichever account was
            // signed in when it was minted; drop it so a stale one from a
            // previous account/server is never reused after switching.
            calendarToken = ""
        }
    }
    var calendarToken = ChusennoteSettings.calendarToken {
        didSet {
            ChusennoteSettings.calendarToken = calendarToken
        }
    }
    var watches: [Watch] = [] {
        didSet { partitionWatches() }
    }
    var events: [EventSummary] = [] {
        didSet { partitionEvents() }
    }
    var upcoming: [UpcomingItem] = []
    var alerts: [AlertPayload] = []
    var notificationFeed: [NotificationFeedItem] = []
    var subscriptions: [NotificationSubscription] = []
    var devices: [DeviceToken] = []
    var sources: [WatchSource] = [] {
        didSet { partitionSources() }
    }
    var health: HealthSummary?
    var errorMessage: String?
    var signedInEmail = ""
    var isRefreshing = false
    var isRunningChecks = false
    var isRunningNotifications = false
    var isAccountTransitioning = false

    var isSignedIn: Bool {
        !apiToken.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    // Filtered views of the fetched lists, computed once per refresh rather
    // than on every view render.
    private(set) var trackedArtists: [Watch] = []
    private(set) var trackedEvents: [Watch] = []
    private(set) var mutedEventWatches: [Watch] = []
    private(set) var mutedOtherWatches: [Watch] = []
    private(set) var ticketEvents: [EventSummary] = []
    private(set) var artistEvents: [EventSummary] = []
    private(set) var activeSources: [WatchSource] = []
    private(set) var mutedSources: [WatchSource] = []

    private func partitionWatches() {
        let active = watches.filter { !$0.muted }
        let muted = watches.filter { $0.muted }
        trackedArtists = active.filter { ($0.kind ?? "event") == "artist" }
        trackedEvents = active.filter { ($0.kind ?? "event") == "event" }
        mutedEventWatches = muted.filter { ($0.kind ?? "event") == "event" }
        mutedOtherWatches = muted.filter { ($0.kind ?? "event") != "event" }
    }

    private func partitionEvents() {
        ticketEvents = events.filter { ($0.watchKind ?? "event") == "event" }
        artistEvents = events.filter { ($0.watchKind ?? "event") == "artist" }
    }

    private func partitionSources() {
        activeSources = sources.filter { !$0.muted }
        mutedSources = sources.filter { $0.muted }
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

    /// One server collection the app mirrors. Actions refresh only the
    /// collections they can change instead of refetching everything.
    enum Resource: CaseIterable {
        case watches, events, upcoming, alerts, notificationFeed, subscriptions, devices, sources, health

        /// What adding, removing, or restoring a watch can change: muted watches
        /// drop out of events, deadlines, and alert context.
        static let watchChanges: Set<Resource> = [.watches, .events, .upcoming, .alerts, .subscriptions, .health]
        /// Manual sources also appear inside event details.
        static let sourceChanges: Set<Resource> = [.sources, .events]
        static let checkResults: Set<Resource> = [.watches, .events, .upcoming, .alerts, .health]
    }

    /// Refetches `resources` concurrently. The default refreshes everything and
    /// re-registers this device for push.
    func refresh(_ resources: Set<Resource> = Set(Resource.allCases)) async {
        isRefreshing = true
        defer { isRefreshing = false }
        do {
            try await withThrowingTaskGroup(of: Void.self) { group in
                for resource in resources {
                    group.addTask { try await self.load(resource) }
                }
                try await group.waitForAll()
            }
            if resources.count == Resource.allCases.count {
                await DeviceRegistration.shared.registerSavedTokenIfPossible()
            }
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func load(_ resource: Resource) async throws {
        switch resource {
        case .watches: watches = try await fetch("/api/watchlist?include_muted=1")
        case .events: events = try await fetch("/api/events")
        case .upcoming: upcoming = try await fetch("/api/upcoming")
        case .alerts: alerts = try await fetch("/api/alerts")
        case .notificationFeed: notificationFeed = try await fetch("/api/notifications?limit=100")
        case .subscriptions: subscriptions = try await fetch("/api/subscriptions")
        case .devices: devices = try await fetch("/api/devices")
        case .sources: sources = try await fetch("/api/sources?include_muted=1")
        case .health: health = try await fetch("/api/health")
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
            await refresh(Resource.watchChanges)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func addWatch(_ draft: WatchDraft, kind: String) async {
        await addWatch(
            keyword: trimmed(draft.keyword),
            kind: kind,
            tags: trimmed(draft.tags),
            regions: trimmed(draft.regions),
            venues: trimmed(draft.venues),
            alerts: trimmed(draft.alerts)
        )
    }

    func runWatches(kind: String) async {
        isRunningChecks = true
        defer { isRunningChecks = false }
        do {
            let _: [AlertPayload] = try await post("/api/run", body: formBody([URLQueryItem(name: "kind", value: kind)]))
            await refresh(Resource.checkResults)
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
            await refresh([.notificationFeed])
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
            await refresh(Resource.sourceChanges)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func removeWatch(id: Int) async {
        do {
            let _: RemoveResponse = try await post("/api/watchlist/remove", body: formBody([URLQueryItem(name: "identifier", value: "\(id)")]))
            await refresh(Resource.watchChanges)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func restoreWatch(id: Int) async {
        do {
            let _: UnmuteResponse = try await post("/api/watchlist/unmute", body: formBody([URLQueryItem(name: "identifier", value: "\(id)")]))
            await refresh(Resource.watchChanges)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func removeSource(id: Int) async {
        do {
            let _: RemoveResponse = try await post("/api/sources/remove", body: formBody([URLQueryItem(name: "identifier", value: "\(id)")]))
            await refresh(Resource.sourceChanges)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func restoreSource(id: Int) async {
        do {
            let _: UnmuteResponse = try await post("/api/sources/unmute", body: formBody([URLQueryItem(name: "identifier", value: "\(id)")]))
            await refresh(Resource.sourceChanges)
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
            await refresh([.subscriptions])
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func removeSubscription(id: Int) async {
        do {
            let _: RemoveResponse = try await post("/api/subscriptions/remove", body: formBody([URLQueryItem(name: "identifier", value: "\(id)")]))
            await refresh([.subscriptions])
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

    func addExactEvent(keyword: String, title: String, url: String, snippet: String) async -> Bool {
        do {
            let response: AddedEventResponse = try await post(
                "/api/event/add",
                body: formBody([
                    URLQueryItem(name: "keyword", value: keyword),
                    URLQueryItem(name: "title", value: title),
                    URLQueryItem(name: "url", value: url),
                    URLQueryItem(name: "snippet", value: snippet)
                ])
            )
            guard response.added else {
                errorMessage = "The server did not add this event page."
                return false
            }
            await refresh(Resource.watchChanges)
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }

    private func fetch<T: Decodable>(_ path: String) async throws -> T {
        guard let url = URL(string: baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + path) else {
            throw APIClientError.invalidBaseURL
        }
        let (data, response) = try await session.data(for: try request(url: url))
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
        let (data, response) = try await session.data(for: request)
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
