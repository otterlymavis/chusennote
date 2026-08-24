import Foundation

func trimmed(_ value: String) -> String {
    value.trimmingCharacters(in: .whitespacesAndNewlines)
}

func alertKeys(_ value: String) -> Set<String> {
    Set(value.split(separator: ",").map { trimmed(String($0)) }.filter { !$0.isEmpty })
}

func orderedAlertKeys(_ keys: Set<String>) -> [String] {
    defaultAlertKeyOrder().filter { keys.contains($0) }
}

func defaultAlertKeyOrder() -> [String] {
    [
        "new_official_page",
        "new_ticket_link",
        "new_lottery_round",
        "ticket_field_changed",
        "lottery_opened",
        "lottery_closing_soon",
        "results_today",
        "payment_due_soon",
        "general_sale_soon",
        "watch_failed"
    ]
}

func defaultAlertPreferenceText() -> String {
    defaultAlertKeyOrder().joined(separator: ",")
}

func alertCount(_ value: String) -> Int {
    alertKeys(value).count
}

func chusennoteWatchText(_ watch: Watch) -> String {
    [
        emptyFallback(watch.preferredRegions),
        emptyFallback(watch.preferredVenues),
        readableAlertText(watch.alertPreferences),
        watch.lastCheckedAt ?? "never"
    ].joined(separator: "\n")
}

func readableAlertText(_ value: String?) -> String {
    let labels = [
        "new_official_page": "official page",
        "new_ticket_link": "ticket link",
        "new_lottery_round": "new rounds",
        "ticket_field_changed": "ticket changes",
        "lottery_opened": "lottery opened",
        "lottery_closing_soon": "closing soon",
        "results_today": "results today",
        "payment_due_soon": "payment due",
        "general_sale_soon": "general sale",
        "watch_failed": "watch failed"
    ]
    let keys = orderedAlertKeys(alertKeys(value ?? ""))
    guard !keys.isEmpty else { return "none" }
    return keys.map { labels[$0] ?? $0 }.joined(separator: ", ")
}

func ticketRoundTitle(_ round: TicketRound) -> String {
    round.name?.isEmpty == false ? round.name! : "Ticket round"
}

func ticketRoundStatusText(_ round: TicketRound) -> String {
    [
        round.platform?.isEmpty == false ? round.platform : "unknown",
        round.statusLabel?.isEmpty == false ? round.statusLabel : round.status
    ]
        .compactMap { $0 }
        .filter { !$0.isEmpty }
        .joined(separator: " - ")
}

func ticketRoundGroups(_ rounds: [TicketRound]) -> [TicketRoundGroup] {
    let grouped = Dictionary(grouping: rounds, by: ticketRoundWebsite)
    return grouped.map { website, rounds in
        TicketRoundGroup(website: website, rounds: sortTicketRoundsNewestFirst(rounds))
    }
    .sorted { first, second in
        let firstDate = first.rounds.map(ticketRoundSortKey).max() ?? ""
        let secondDate = second.rounds.map(ticketRoundSortKey).max() ?? ""
        if firstDate == secondDate {
            return first.website.localizedCaseInsensitiveCompare(second.website) == .orderedAscending
        }
        return firstDate > secondDate
    }
}

func limitedTicketRounds(_ rounds: [TicketRound], max: Int?) -> [TicketRound] {
    guard let max else { return rounds }
    return Array(rounds.prefix(max))
}

func sortTicketRoundsNewestFirst(_ rounds: [TicketRound]) -> [TicketRound] {
    rounds.sorted { first, second in
        let firstDate = ticketRoundSortKey(first)
        let secondDate = ticketRoundSortKey(second)
        if firstDate == secondDate {
            return ticketRoundTitle(first).localizedCaseInsensitiveCompare(ticketRoundTitle(second)) == .orderedAscending
        }
        return firstDate > secondDate
    }
}

func ticketRoundWebsite(_ round: TicketRound) -> String {
    if let platform = round.platform, !platform.isEmpty {
        return platform
    }
    if let url = chusennoteWebURL(round.url), let host = url.host, !host.isEmpty {
        return host.replacingOccurrences(of: "www.", with: "")
    }
    return "Unknown website"
}

func ticketRoundSortKey(_ round: TicketRound) -> String {
    ticketRoundDateValues(round).max() ?? ""
}

func ticketRoundDateValues(_ round: TicketRound) -> [String] {
    [
        round.applicationStartAt,
        round.applicationEndAt,
        round.resultsDate,
        round.generalSaleDate,
        round.paymentEndAt
    ]
        .compactMap { $0 }
        .filter { !$0.isEmpty }
}

func ticketRoundDateItems(_ round: TicketRound) -> [TicketRoundDateItem] {
    [
        ticketRoundDateItem("Apply opens", round.applicationStartAt, kind: .apply),
        ticketRoundDateItem("Apply closes", round.applicationEndAt, kind: .apply),
        ticketRoundDateItem("Results", round.resultsDate, kind: .result),
        ticketRoundDateItem("Payment due", round.paymentEndAt, kind: .payment),
        ticketRoundDateItem("General sale", round.generalSaleDate, kind: .sale)
    ]
        .compactMap { $0 }
}

func ticketRoundDateItem(_ label: String, _ value: String?, kind: TicketRoundDateKind) -> TicketRoundDateItem? {
    guard let value, !value.isEmpty else { return nil }
    return TicketRoundDateItem(label: label, value: value, kind: kind)
}

func ticketRoundAccent(_ round: TicketRound) -> SemanticColor {
    if round.paymentEndAt?.isEmpty == false {
        return .warning
    }
    if round.resultsDate?.isEmpty == false {
        return .highlight
    }
    if round.generalSaleDate?.isEmpty == false {
        return .success
    }
    return .info
}

func ticketDateColor(_ kind: TicketRoundDateKind) -> SemanticColor {
    switch kind {
    case .apply:
        return .info
    case .result:
        return .highlight
    case .payment:
        return .warning
    case .sale:
        return .success
    }
}

func ticketRoundScheduleText(_ round: TicketRound) -> String {
    guard let schedule = round.scheduleLabel, !schedule.isEmpty else { return "" }
    return schedule
}

func labeledRoundDate(_ label: String, _ value: String?) -> String? {
    guard let value, !value.isEmpty else { return nil }
    return "\(label): \(value)"
}

func emptyFallback(_ value: String?) -> String {
    guard let value, !value.isEmpty else { return "none" }
    return value
}

func sourceMode(_ source: WatchSource) -> String {
    source.privateNote ? "private note" : source.platform
}

func chusennoteAlertText(_ alert: AlertPayload) -> String {
    var parts: [String] = []
    for value in [alert.event, alert.eventTitle, alert.keyword, alert.round].compactMap({ $0 }).filter({ !$0.isEmpty }) {
        if !parts.contains(value) {
            parts.append(value)
        }
    }
    if let eventId = alert.eventId {
        parts.append("Event #\(eventId)")
    }
    if let watchKeyword = alert.watchKeyword, !watchKeyword.isEmpty {
        let kind = alert.watchKind ?? "watch"
        let muted = alert.watchMuted == true ? " muted" : ""
        parts.append("\(kind) \(watchKeyword)\(muted)")
    } else if let watchId = alert.watchId {
        parts.append("Watch #\(watchId)")
    }
    return parts.joined(separator: " ")
}

func upcomingStatusText(_ item: UpcomingItem) -> String {
    [
        item.statusLabel?.isEmpty == false ? item.statusLabel : item.status,
        item.roundName,
        item.relevantDate
    ]
        .compactMap { $0 }
        .filter { !$0.isEmpty }
        .joined(separator: " - ")
}

func watchForSubscription(_ subscription: NotificationSubscription, in watches: [Watch]) -> Watch? {
    watches.first { $0.id == subscription.watchId }
}

func subscriptionTitle(_ subscription: NotificationSubscription, watch: Watch?) -> String {
    let keyword = watch?.keyword ?? "Watch #\(subscription.watchId)"
    return "\(subscriptionScopeLabel(subscription.scope)) - \(keyword)"
}

func subscriptionText(_ subscription: NotificationSubscription) -> String {
    [
        subscription.location.isEmpty ? nil : "Location: \(subscription.location)",
        subscription.roundKey.isEmpty ? nil : "Round: \(subscription.roundKey)",
        "Channels: \(subscription.channels)",
        "Lead days: \(subscription.leadDays)",
        subscription.enabled ? "Enabled" : "Disabled"
    ]
        .compactMap { $0 }
        .joined(separator: "\n")
}

func subscriptionScopeLabel(_ scope: String) -> String {
    switch scope {
    case "artist_all":
        return "Artist"
    case "event_all":
        return "Event"
    case "event_location":
        return "Location"
    case "round":
        return "Round"
    default:
        return scope
    }
}

func notificationFeedText(_ item: NotificationFeedItem) -> String {
    [
        item.body,
        item.eventTitle,
        item.location?.isEmpty == false ? "Location: \(item.location!)" : nil,
        item.date?.isEmpty == false ? "Date: \(item.date!)" : nil,
        item.channel?.isEmpty == false ? "Channel: \(item.channel!)" : nil,
        item.createdAt
    ]
        .compactMap { $0 }
        .filter { !$0.isEmpty }
        .joined(separator: "\n")
}

func eventLocationTitle(_ location: EventLocation) -> String {
    if !location.city.isEmpty, !location.venue.isEmpty, location.city != location.venue {
        return "\(location.city) - \(location.venue)"
    }
    if !location.location.isEmpty {
        return location.location
    }
    return location.venue.isEmpty ? "Location" : location.venue
}

func ticketLinkText(_ link: TicketLink) -> String {
    [
        link.platform?.isEmpty == false ? "Platform: \(link.platform!)" : nil,
        link.confidence.map { "Confidence: \($0)" },
        link.provenance?.isEmpty == false ? "Source: \(link.provenance!)" : nil,
        link.url
    ]
        .compactMap { $0 }
        .joined(separator: "\n")
}

func maskedToken(_ token: String) -> String {
    guard token.count > 12 else { return token }
    return "\(token.prefix(6))...\(token.suffix(6))"
}

func chusennoteWebURL(_ value: String?) -> URL? {
    guard let value, let url = URL(string: value) else { return nil }
    guard url.scheme == "http" || url.scheme == "https" else { return nil }
    return url
}
