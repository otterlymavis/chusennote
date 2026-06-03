import Foundation

@MainActor
final class ChusennoteStore: ObservableObject {
    @Published var baseURL = "http://127.0.0.1:8765"
    @Published var watches: [Watch] = []
    @Published var events: [EventSummary] = []
    @Published var errorMessage: String?

    var trackedArtists: [Watch] {
        watches.filter { !$0.muted && ($0.kind ?? "event") == "artist" }
    }

    var trackedEvents: [Watch] {
        watches.filter { !$0.muted && ($0.kind ?? "event") == "event" }
    }

    func refresh() async {
        do {
            async let fetchedWatches: [Watch] = fetch("/api/watchlist")
            async let fetchedEvents: [EventSummary] = fetch("/api/events")
            watches = try await fetchedWatches
            events = try await fetchedEvents
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func addWatch(keyword: String, kind: String) async {
        do {
            var fields = URLComponents()
            fields.queryItems = [
                URLQueryItem(name: "keyword", value: keyword),
                URLQueryItem(name: "kind", value: kind)
            ]
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
