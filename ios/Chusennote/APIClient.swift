import Foundation

enum ChusennoteSettings {
    static let baseURLKey = "baseURL"
    static let apiTokenKey = "apiToken"
    static let calendarTokenKey = "calendarToken"
    static let defaultBaseURL = "http://127.0.0.1:8877"

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
            DeviceRegistration.registerSavedTokenIfPossible()
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
    @Published var isRefreshing = false
    @Published var isRunningChecks = false
    @Published var isRunningNotifications = false

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
            DeviceRegistration.registerSavedTokenIfPossible()
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
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
            throw URLError(.badURL)
        }
        let (data, response) = try await URLSession.shared.data(for: request(url: url))
        guard let http = response as? HTTPURLResponse, 200..<300 ~= http.statusCode else {
            throw URLError(.badServerResponse)
        }
        return try JSONDecoder().decode(T.self, from: data)
    }

    private func post<T: Decodable>(_ path: String, body: String) async throws -> T {
        guard let url = URL(string: baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + path) else {
            throw URLError(.badURL)
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        applyAuthorization(to: &request)
        request.httpBody = body.data(using: .utf8)
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, 200..<300 ~= http.statusCode else {
            throw URLError(.badServerResponse)
        }
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

    private func request(url: URL) -> URLRequest {
        var request = URLRequest(url: url)
        applyAuthorization(to: &request)
        return request
    }

    private func applyAuthorization(to request: inout URLRequest) {
        let token = apiToken.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !token.isEmpty else { return }
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
    }
}
