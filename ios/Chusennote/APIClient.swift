import Foundation

@MainActor
final class ChusennoteStore: ObservableObject {
    @Published var baseURL = UserDefaults.standard.string(forKey: "baseURL") ?? "http://127.0.0.1:8765" {
        didSet {
            UserDefaults.standard.set(baseURL, forKey: "baseURL")
        }
    }
    @Published var watches: [Watch] = []
    @Published var events: [EventSummary] = []
    @Published var upcoming: [UpcomingItem] = []
    @Published var alerts: [AlertPayload] = []
    @Published var sources: [WatchSource] = []
    @Published var health: HealthSummary?
    @Published var errorMessage: String?

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

    var calendarFeedURL: URL? {
        URL(string: baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + "/calendar.ics")
    }

    func refresh() async {
        do {
            async let fetchedWatches: [Watch] = fetch("/api/watchlist")
            async let fetchedEvents: [EventSummary] = fetch("/api/events")
            async let fetchedUpcoming: [UpcomingItem] = fetch("/api/upcoming")
            async let fetchedAlerts: [AlertPayload] = fetch("/api/alerts")
            async let fetchedSources: [WatchSource] = fetch("/api/sources")
            async let fetchedHealth: HealthSummary = fetch("/api/health")
            watches = try await fetchedWatches
            events = try await fetchedEvents
            upcoming = try await fetchedUpcoming
            alerts = try await fetchedAlerts
            sources = try await fetchedSources
            health = try await fetchedHealth
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
                URLQueryItem(name: "venues", value: venues)
            ]
            if !alerts.isEmpty {
                fields.queryItems?.append(URLQueryItem(name: "alerts", value: alerts))
            }
            let _: Watch = try await post("/api/watchlist", body: fields.percentEncodedQuery ?? "")
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func runEventWatches() async {
        do {
            let _: [AlertPayload] = try await post("/api/run", body: "kind=event")
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func addSource(watch: String, url: String, label: String) async {
        do {
            var fields = URLComponents()
            fields.queryItems = [
                URLQueryItem(name: "watch", value: watch),
                URLQueryItem(name: "url", value: url),
                URLQueryItem(name: "label", value: label)
            ]
            let _: WatchSource = try await post("/api/sources", body: fields.percentEncodedQuery ?? "")
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func removeWatch(id: Int) async {
        do {
            let _: RemoveResponse = try await post("/api/watchlist/remove", body: "identifier=\(id)")
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func restoreWatch(id: Int) async {
        do {
            let _: UnmuteResponse = try await post("/api/watchlist/unmute", body: "identifier=\(id)")
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func removeSource(id: Int) async {
        do {
            let _: RemoveResponse = try await post("/api/sources/remove", body: "identifier=\(id)")
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func restoreSource(id: Int) async {
        do {
            let _: UnmuteResponse = try await post("/api/sources/unmute", body: "identifier=\(id)")
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func fetch<T: Decodable>(_ path: String) async throws -> T {
        guard let url = URL(string: baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + path) else {
            throw URLError(.badURL)
        }
        let (data, response) = try await URLSession.shared.data(from: url)
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
        request.httpBody = body.data(using: .utf8)
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, 200..<300 ~= http.statusCode else {
            throw URLError(.badServerResponse)
        }
        return try JSONDecoder().decode(T.self, from: data)
    }
}
