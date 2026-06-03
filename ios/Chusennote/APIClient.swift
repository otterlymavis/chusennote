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
}
