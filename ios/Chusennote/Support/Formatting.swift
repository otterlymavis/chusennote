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
    watch.lastCheckedAt.map { "Checked \(friendlyDate($0))" } ?? "Not checked yet"
}

/// Shows `2026-10-26T23:59` as `Oct 26, 23:59`, adding the year only when it
/// is not this year. Free-text date clues such as `10月下旬` pass through.
func friendlyDate(_ value: String?, today: Date = Date()) -> String {
    let text = trimmed(value ?? "")
    let parts = text.prefix(16).split(whereSeparator: { "-T :".contains($0) }).compactMap { Int($0) }
    guard text.range(of: #"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?([+-]\d{2}:?\d{2}|Z)?)?$"#, options: .regularExpression) != nil,
          parts.count >= 3,
          let date = Calendar(identifier: .gregorian).date(from: DateComponents(year: parts[0], month: parts[1], day: parts[2]))
    else { return text }
    let calendar = Calendar(identifier: .gregorian)
    let formatter = DateFormatter()
    formatter.locale = Locale(identifier: "en_US_POSIX")
    formatter.dateFormat = calendar.component(.year, from: date) == calendar.component(.year, from: today) ? "MMM d" : "MMM d, yyyy"
    var label = formatter.string(from: date)
    if parts.count >= 5 {
        label += String(format: ", %02d:%02d", parts[3], parts[4])
    }
    return label
}

func friendlyDateRange(_ start: String?, _ end: String?) -> String {
    let startText = friendlyDate(start)
    let endText = friendlyDate(end)
    if !startText.isEmpty, !endText.isEmpty {
        return "\(startText) – \(endText)"
    }
    return startText.isEmpty ? (endText.isEmpty ? "" : "until \(endText)") : startText
}

func joinedMeta(_ parts: [String?]) -> String {
    parts.compactMap { $0 }.filter { !$0.isEmpty && $0 != "—" }.joined(separator: " · ")
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
    joinedMeta([eventStatusText(event), friendlyDate(event.eventDates?.first), event.venueLabel])
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
    joinedMeta([round.statusLabel, ticketRoundWebsite(round), round.membershipLabel])
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
    return ""
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
        ticketRoundDateItem("Apply", friendlyDateRange(round.applicationStartAt, round.applicationEndAt)),
        ticketRoundDateItem("Results", friendlyDate(round.resultsDate)),
        ticketRoundDateItem("Payment due", friendlyDate(round.paymentEndAt)),
        ticketRoundDateItem("On sale", friendlyDate(round.generalSaleDate)),
        ticketRoundDateItem("Resale", friendlyDateRange(round.tradeStartAt, round.tradeEndAt))
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

func sourceMode(_ source: WatchSource) -> String {
    source.privateNote ? "private note" : source.platform
}

func sourceText(_ source: WatchSource) -> String {
    "Watch #\(source.watchId) · \(sourceMode(source))\n\(source.url)"
}

func chusennoteAlertText(_ alert: AlertPayload) -> String {
    var titles: [String] = []
    for value in [alert.eventTitle ?? alert.event, alert.round ?? alert.label].compactMap({ $0 }) where !value.isEmpty && !titles.contains(value) {
        titles.append(value)
    }
    if titles.isEmpty, let keyword = alert.watchKeyword ?? alert.keyword {
        titles.append(keyword)
    }
    return joinedMeta([titles.joined(separator: " · "), friendlyDate(alert.createdAt.map { String($0.prefix(10)) })])
}

func upcomingStatusText(_ item: UpcomingItem) -> String {
    let summary = joinedMeta([item.statusLabel, friendlyDate(item.relevantDate), item.platform])
    return [summary, item.roundName ?? ""].filter { !$0.isEmpty }.joined(separator: "\n")
}

func watchForSubscription(_ subscription: NotificationSubscription, in watches: [Watch]) -> Watch? {
    watches.first { $0.id == subscription.watchId }
}

func subscriptionTitle(_ subscription: NotificationSubscription, watch: Watch?) -> String {
    watch?.keyword ?? "Removed watch"
}

func subscriptionText(_ subscription: NotificationSubscription) -> String {
    let channels: String = subscription.channels.split(separator: ",").map { $0.capitalized }.joined(separator: ", ")
    let paused: String? = subscription.enabled ? nil : "Paused"
    let parts: [String?] = [
        subscriptionScopeLabel(subscription.scope),
        subscription.location,
        channels,
        "\(subscription.leadDays) days before",
        paused
    ]
    return joinedMeta(parts)
}

func subscriptionScopeLabel(_ scope: String) -> String {
    switch scope {
    case "artist_all":
        return "All shows"
    case "event_all":
        return "All rounds"
    case "event_location":
        return "One location"
    case "round":
        return "One round"
    default:
        return scope
    }
}

func notificationFeedText(_ item: NotificationFeedItem) -> String {
    [
        item.body ?? "",
        joinedMeta([item.eventTitle, item.location, friendlyDate(item.date), friendlyDate(item.createdAt.map { String($0.prefix(10)) })])
    ]
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
    var parts: [String?] = [friendlyDate(related.eventDate), related.venueLabel]
    parts.append(contentsOf: (related.recommendationReasons ?? []).prefix(2).map { Optional($0) })
    return joinedMeta(parts)
}

func ticketLinkText(_ link: TicketLink) -> String {
    link.platform ?? ""
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
