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
        "trade_opened",
        "trade_closing_soon",
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
    let reminders = alertCount(watch.alertPreferences ?? "")
    return [
        watch.preferredRegions,
        watch.preferredVenues,
        reminders == 1 ? "1 reminder" : "\(reminders) reminders",
        watch.lastCheckedAt.map { "checked \($0)" } ?? "not checked yet"
    ]
        .compactMap { $0 }
        .filter { !$0.isEmpty }
        .joined(separator: " · ")
}

func eventStatusText(statusLabel: String?, status: String?) -> String {
    if let statusLabel, !statusLabel.isEmpty {
        return statusLabel
    }
    let labels = [
        "watching": "Watching",
        "official_found": "Official page found",
        "ticket_links_found": "Ticket links found",
        "lottery_found": "Ticket rounds found",
        "lottery_open": "Ticket window open"
    ]
    return labels[status ?? ""] ?? "Watching"
}

func eventStatusText(_ event: EventSummary) -> String {
    eventStatusText(statusLabel: event.statusLabel, status: event.status)
}

func eventRowText(_ event: EventSummary) -> String {
    let rounds = event.rounds.count == 1 ? "1 round" : "\(event.rounds.count) rounds"
    var lines = ["\(eventStatusText(event)) · \(rounds)"]
    let details = [event.eventDates?.first, event.venueLabel]
        .compactMap { $0 }
        .filter { !$0.isEmpty }
        .joined(separator: " · ")
    if !details.isEmpty {
        lines.append(details)
    }
    if let round = sortTicketRoundsNewestFirst(event.rounds).first,
       let date = ticketRoundDateItems(round).first {
        lines.append("\(ticketRoundTitle(round)): \(date.label.lowercased()) \(date.value)")
    }
    return lines.joined(separator: "\n")
}

func alertTypeText(typeLabel: String?, type: String) -> String {
    if let typeLabel, !typeLabel.isEmpty {
        return typeLabel
    }
    let labels = [
        "new_official_page": "Official page found",
        "new_ticket_link": "Ticket link found",
        "new_lottery_round": "New ticket round",
        "ticket_field_changed": "Ticket details changed",
        "lottery_opened": "Lottery opened",
        "lottery_closing_soon": "Lottery closing soon",
        "results_today": "Results today",
        "payment_due_soon": "Payment due soon",
        "general_sale_soon": "General sale soon",
        "trade_opened": "Official resale opened",
        "trade_closing_soon": "Official resale closing soon",
        "watch_failed": "Watch check failed",
        "watch_filtered": "Watch filtered"
    ]
    if let label = labels[type] {
        return label
    }
    return type.replacingOccurrences(of: "_", with: " ").capitalized
}

func alertTypeText(_ alert: AlertPayload) -> String {
    alertTypeText(typeLabel: alert.typeLabel, type: alert.type)
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
        round.paymentEndAt,
        round.tradeStartAt,
        round.tradeEndAt
    ]
        .compactMap { $0 }
        .filter { !$0.isEmpty }
}

func ticketRoundDateItems(_ round: TicketRound) -> [TicketRoundDateItem] {
    [
        ticketRoundDateItem("Apply opens", round.applicationStartAt),
        ticketRoundDateItem("Apply closes", round.applicationEndAt),
        ticketRoundDateItem("Results", round.resultsDate),
        ticketRoundDateItem("Payment due", round.paymentEndAt),
        ticketRoundDateItem("General sale", round.generalSaleDate),
        ticketRoundDateItem("Resale opens", round.tradeStartAt),
        ticketRoundDateItem("Resale closes", round.tradeEndAt)
    ]
        .compactMap { $0 }
}

func ticketRoundDateItem(_ label: String, _ value: String?) -> TicketRoundDateItem? {
    guard let value, !value.isEmpty else { return nil }
    return TicketRoundDateItem(label: label, value: value)
}

func ticketRoundScheduleText(_ round: TicketRound) -> String {
    guard let schedule = round.scheduleLabel, !schedule.isEmpty else { return "" }
    return schedule
}

func labeledRoundDate(_ label: String, _ value: String?) -> String? {
    guard let value, !value.isEmpty else { return nil }
    return "\(label): \(value)"
}

func sourceMode(_ source: WatchSource) -> String {
    source.privateNote ? "private note" : source.platform
}

func sourceText(_ source: WatchSource) -> String {
    "Watch #\(source.watchId) · \(sourceMode(source))\n\(source.url)"
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

func relatedEventText(_ related: RelatedEventSummary) -> String {
    let detail = [related.eventDate, related.venueLabel]
        .compactMap { $0 }
        .filter { !$0.isEmpty }
        .joined(separator: " · ")
    let reasons = (related.recommendationReasons ?? []).prefix(3).joined(separator: "; ")
    return [detail, reasons].filter { !$0.isEmpty }.joined(separator: "\n")
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
