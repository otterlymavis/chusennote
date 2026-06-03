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
    let rounds: [TicketRound]

    enum CodingKeys: String, CodingKey {
        case id
        case watchId = "watch_id"
        case watchKind = "watch_kind"
        case title
        case status
        case officialUrl = "official_url"
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
