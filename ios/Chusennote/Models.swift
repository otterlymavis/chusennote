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
    let keyword: String?
    let watchKind: String?
    let title: String?
    let status: String?
    let officialUrl: String?
    let summary: String?
    let eventDates: [String]?
    let venues: [String]?
    let eventLocations: [EventLocation]?
    let ticketRules: [String]?
    let ticketPrices: [String]?
    let updatedAt: String?
    // Honest venue text from the backend: real venues, "Multiple cities" for a
    // tour, or a dash. Prefer this over `venues`, which is empty for tours.
    let venueLabel: String?
    let matchReasons: [String]?
    let ticketLinks: [TicketLink]?
    let manualSources: [WatchSource]?
    let rounds: [TicketRound]

    enum CodingKeys: String, CodingKey {
        case id
        case watchId = "watch_id"
        case keyword
        case watchKind = "watch_kind"
        case title
        case status
        case officialUrl = "official_url"
        case summary
        case eventDates = "event_dates"
        case venues
        case eventLocations = "event_locations"
        case ticketRules = "ticket_rules"
        case ticketPrices = "ticket_prices"
        case updatedAt = "updated_at"
        case venueLabel = "venue_label"
        case matchReasons = "match_reasons"
        case ticketLinks = "ticket_links"
        case manualSources = "manual_sources"
        case rounds
    }
}

struct EventLocation: Codable, Identifiable {
    var id: String { "\(location)-\(venue)-\(date)" }
    let location: String
    let city: String
    let venue: String
    let date: String
}

struct TicketLink: Codable, Identifiable {
    var id: String { url }
    let label: String?
    let url: String
    let platform: String?
    let confidence: Int?
    let provenance: String?
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
    let statusLabel: String?
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
        case statusLabel = "status_label"
        case relevantDate = "relevant_date"
        case url
        case matchReasons = "match_reasons"
    }
}

struct TicketRound: Codable, Identifiable {
    var id: String { roundKey ?? "\(url ?? "")-\(name ?? "")" }
    let name: String?
    let platform: String?
    let url: String?
    let status: String?
    let statusLabel: String?
    let applicationStartAt: String?
    let applicationEndAt: String?
    let resultsDate: String?
    let generalSaleDate: String?
    let paymentEndAt: String?
    // Compact "when do I act" line from the backend (apply window, results,
    // payment, sale), so the app shows the dates that matter for a lottery.
    let scheduleLabel: String?
    let confidence: Int?
    let roundType: String?
    let roundTypeLabel: String?
    let membershipRequired: String?
    let membershipLabel: String?
    let evidence: String?
    let roundKey: String?

    enum CodingKeys: String, CodingKey {
        case name
        case platform
        case url
        case status
        case statusLabel = "status_label"
        case applicationStartAt = "application_start_at"
        case applicationEndAt = "application_end_at"
        case resultsDate = "results_date"
        case generalSaleDate = "general_sale_date"
        case paymentEndAt = "payment_end_at"
        case scheduleLabel = "schedule_label"
        case confidence
        case roundType = "round_type"
        case roundTypeLabel = "round_type_label"
        case membershipRequired = "membership_required"
        case membershipLabel = "membership_label"
        case evidence
        case roundKey = "round_key"
    }
}

struct AlertPayload: Codable, Identifiable {
    var id: String { alertId.map(String.init) ?? "\(type)-\(event ?? keyword ?? "")-\(round ?? "")" }
    let alertId: Int?
    let eventId: Int?
    let eventTitle: String?
    let watchId: Int?
    let watchKeyword: String?
    let watchKind: String?
    let watchMuted: Bool?
    let type: String
    let event: String?
    let keyword: String?
    let round: String?
    let createdAt: String?

    enum CodingKeys: String, CodingKey {
        case alertId = "alert_id"
        case eventId = "event_id"
        case eventTitle = "event_title"
        case watchId = "watch_id"
        case watchKeyword = "watch_keyword"
        case watchKind = "watch_kind"
        case watchMuted = "watch_muted"
        case type
        case event
        case keyword
        case round
        case createdAt = "created_at"
    }
}

struct NotificationFeedItem: Codable, Identifiable {
    var id: String { "\(eventId ?? 0)-\(title ?? label ?? "reminder")-\(date ?? "")-\(createdAt ?? "")" }
    let title: String?
    let body: String?
    let eventId: Int?
    let eventTitle: String?
    let subject: String?
    let location: String?
    let label: String?
    let field: String?
    let date: String?
    let leadDays: Int?
    let url: String?
    let channel: String?
    let createdAt: String?

    enum CodingKeys: String, CodingKey {
        case title
        case body
        case eventId = "event_id"
        case eventTitle = "event_title"
        case subject
        case location
        case label
        case field
        case date
        case leadDays = "lead_days"
        case url
        case channel
        case createdAt = "created_at"
    }
}

struct NotificationSubscription: Codable, Identifiable {
    let id: Int
    let watchId: Int
    let scope: String
    let location: String
    let roundKey: String
    let channels: String
    let leadDays: String
    let enabled: Bool

    enum CodingKeys: String, CodingKey {
        case id
        case watchId = "watch_id"
        case scope
        case location
        case roundKey = "round_key"
        case channels
        case leadDays = "lead_days"
        case enabled
    }
}

struct DeviceToken: Codable, Identifiable {
    let id: Int
    let token: String
    let platform: String
    let label: String
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

struct AddedEventResponse: Codable {
    let added: Bool
    let alerts: [AlertPayload]?
}

struct SearchResult: Codable, Identifiable {
    var id: String { url }
    let title: String
    let url: String
    let snippet: String
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
