import Foundation

struct Watch: Codable, Identifiable {
    let id: Int
    let keyword: String
    let kind: String?
    let tags: String?
    let preferredRegions: String?
    let preferredVenues: String?
    let alertPreferences: String?
    let muted: Bool
    let lastCheckedAt: String?

    enum CodingKeys: String, CodingKey {
        case id
        case keyword
        case kind
        case tags
        case preferredRegions = "preferred_regions"
        case preferredVenues = "preferred_venues"
        case alertPreferences = "alert_preferences"
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
    let matchReasons: [String]?
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
        case matchReasons = "match_reasons"
        case rounds
    }
}

struct UpcomingItem: Codable, Identifiable {
    var id: String { "\(eventId ?? 0)-\(platform ?? "")-\(roundName ?? "")-\(relevantDate ?? "")" }
    let eventId: Int?
    let eventTitle: String?
    let watchId: Int?
    let watchKind: String?
    let platform: String?
    let roundName: String?
    let status: String?
    let relevantDate: String?
    let url: String?
    let matchReasons: [String]?

    enum CodingKeys: String, CodingKey {
        case eventId = "event_id"
        case eventTitle = "event_title"
        case watchId = "watch_id"
        case watchKind = "watch_kind"
        case platform
        case roundName = "round_name"
        case status
        case relevantDate = "relevant_date"
        case url
        case matchReasons = "match_reasons"
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
    let evidence: String?

    enum CodingKeys: String, CodingKey {
        case name
        case platform
        case url
        case status
        case confidence
        case roundType = "round_type"
        case membershipRequired = "membership_required"
        case evidence
    }
}

struct AlertPayload: Codable, Identifiable {
    var id: String { alertId.map(String.init) ?? "\(type)-\(event ?? keyword ?? "")-\(round ?? "")" }
    let alertId: Int?
    let eventId: Int?
    let type: String
    let event: String?
    let keyword: String?
    let round: String?
    let createdAt: String?

    enum CodingKeys: String, CodingKey {
        case alertId = "alert_id"
        case eventId = "event_id"
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
    let muted: Bool

    enum CodingKeys: String, CodingKey {
        case id
        case watchId = "watch_id"
        case url
        case label
        case platform
        case privateNote = "private_note"
        case muted
    }
}

struct RemoveResponse: Codable {
    let removed: Bool
}

struct UnmuteResponse: Codable {
    let unmuted: Bool
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
