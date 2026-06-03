import Foundation

struct Watch: Codable, Identifiable {
    let id: Int
    let keyword: String
    let kind: String?
    let tags: String?
    let muted: Bool
    let lastCheckedAt: String?

    enum CodingKeys: String, CodingKey {
        case id
        case keyword
        case kind
        case tags
        case muted
        case lastCheckedAt = "last_checked_at"
    }
}

struct EventSummary: Codable, Identifiable {
    let id: Int
    let watchId: Int
    let watchKind: String?
    let title: String?
    let status: String?
    let officialUrl: String?
    let eventDates: [String]?
    let venues: [String]?
    let rounds: [TicketRound]

    enum CodingKeys: String, CodingKey {
        case id
        case watchId = "watch_id"
        case watchKind = "watch_kind"
        case title
        case status
        case officialUrl = "official_url"
        case eventDates = "event_dates"
        case venues
        case rounds
    }
}

struct TicketRound: Codable, Identifiable {
    var id: String { "\(url ?? "")-\(name ?? "")" }
    let name: String?
    let platform: String?
    let url: String?
    let status: String?
    let confidence: Int?
    let roundType: String?
    let membershipRequired: String?

    enum CodingKeys: String, CodingKey {
        case name
        case platform
        case url
        case status
        case confidence
        case roundType = "round_type"
        case membershipRequired = "membership_required"
    }
}

struct AlertPayload: Codable, Identifiable {
    var id: String { "\(type)-\(event ?? keyword ?? "")-\(round ?? "")" }
    let type: String
    let event: String?
    let keyword: String?
    let round: String?
    let createdAt: String?

    enum CodingKeys: String, CodingKey {
        case type
        case event
        case keyword
        case round
        case createdAt = "created_at"
    }
}

struct WatchSource: Codable, Identifiable {
    let id: Int
    let watchId: Int
    let url: String
    let label: String
    let platform: String
    let privateNote: Bool

    enum CodingKeys: String, CodingKey {
        case id
        case watchId = "watch_id"
        case url
        case label
        case platform
        case privateNote = "private_note"
    }
}

struct RemoveResponse: Codable {
    let removed: Bool
}

struct HealthSummary: Codable {
    let app: String
    let status: String
    let schemaVersion: Int
    let trackedArtists: Int
    let trackedEvents: Int
    let savedEvents: Int
    let manualSources: Int
    let alerts: Int

    enum CodingKeys: String, CodingKey {
        case app
        case status
        case schemaVersion = "schema_version"
        case trackedArtists = "tracked_artists"
        case trackedEvents = "tracked_events"
        case savedEvents = "saved_events"
        case manualSources = "manual_sources"
        case alerts
    }
}
