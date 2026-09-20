import SwiftUI

struct RowContent: View {
    let title: String
    let subtitle: String
    let systemImage: String
    var tint: SemanticColor = .info

    var body: some View {
        HStack(alignment: .top, spacing: Spacing.md) {
            Image(systemName: systemImage)
                .font(Typography.rowTitle)
                .frame(width: IconSize.standard, height: IconSize.standard)
                .foregroundStyle(tint.color)
                .background(tint.color.opacity(0.1))
                .clipShape(Circle())
            VStack(alignment: .leading, spacing: Spacing.xxs) {
                Text(title)
                    .font(Typography.rowTitle)
                    .foregroundStyle(.primary)
                    .lineLimit(1)
                if !subtitle.isEmpty {
                    Text(subtitle)
                        .font(Typography.rowSubtitle)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }
            }
            Spacer(minLength: 0)
        }
    }
}

struct EmptyStateRow: View {
    let title: String
    let detail: String
    var systemImage: String = "tray"

    var body: some View {
        HStack(alignment: .top, spacing: Spacing.sm + Spacing.xxs) {
            Image(systemName: systemImage)
                .font(Typography.rowTitle)
                .foregroundStyle(.secondary)
                .frame(width: IconSize.standard, height: IconSize.standard)
                .background(Color(.tertiarySystemGroupedBackground))
                .clipShape(Circle())

            VStack(alignment: .leading, spacing: Spacing.xs / 2) {
                Text(title)
                    .font(Typography.rowTitle)
                    .lineLimit(1)
                Text(detail)
                    .font(Typography.rowSubtitle)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .minimumScaleFactor(0.85)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct WatchRow: View {
    let watch: Watch
    let actionTitle: String
    var actionIcon: String = "trash"
    let action: () -> Void
    var secondaryActionTitle: String? = nil
    var secondaryActionIcon: String = "bell.badge"
    var secondaryAction: (() -> Void)? = nil
    var tertiaryActionTitle: String? = nil
    var tertiaryActionIcon: String = "slider.horizontal.3"
    var tertiaryAction: (() -> Void)? = nil

    var body: some View {
        HStack(alignment: .top, spacing: Spacing.md) {
            RowContent(
                title: watch.keyword,
                subtitle: chusennoteWatchText(watch),
                systemImage: "ticket"
            )
            VStack(spacing: Spacing.sm) {
                if let secondaryActionTitle, let secondaryAction {
                    IconActionButton(
                        title: secondaryActionTitle,
                        systemImage: secondaryActionIcon,
                        prominent: true,
                        action: secondaryAction
                    )
                }
                if let tertiaryActionTitle, let tertiaryAction {
                    IconActionButton(
                        title: tertiaryActionTitle,
                        systemImage: tertiaryActionIcon,
                        action: tertiaryAction
                    )
                }
                IconActionButton(title: actionTitle, systemImage: actionIcon, action: action)
            }
        }
    }
}

struct SourceRow: View {
    let source: WatchSource
    let actionTitle: String
    var actionIcon: String = "trash"
    let action: () -> Void
    @Environment(\.openURL) private var openURL

    var body: some View {
        HStack(alignment: .top, spacing: Spacing.md) {
            RowContent(
                title: source.label.isEmpty ? source.url : source.label,
                subtitle: "Watch #\(source.watchId) - \(sourceMode(source))\n\(source.url)",
                systemImage: "link"
            )
            VStack(spacing: Spacing.sm) {
                if let url = chusennoteWebURL(source.url) {
                    IconActionButton(title: "Open", systemImage: "arrow.up.right") {
                        openURL(url)
                    }
                }
                if !actionTitle.isEmpty {
                    IconActionButton(title: actionTitle, systemImage: actionIcon, action: action)
                }
            }
        }
    }
}

struct SearchResultRow: View {
    let result: SearchResult
    let action: () -> Void
    @Environment(\.openURL) private var openURL

    var body: some View {
        HStack(alignment: .top, spacing: Spacing.md) {
            RowContent(
                title: result.title.isEmpty ? result.url : result.title,
                subtitle: [result.url, result.snippet].filter { !$0.isEmpty }.joined(separator: "\n"),
                systemImage: "magnifyingglass"
            )
            VStack(spacing: Spacing.sm) {
                if let url = chusennoteWebURL(result.url) {
                    IconActionButton(title: "Open", systemImage: "arrow.up.right") {
                        openURL(url)
                    }
                }
                IconActionButton(title: "Add", systemImage: "plus", prominent: true, action: action)
            }
        }
    }
}

struct TicketLinkRow: View {
    let link: TicketLink
    @Environment(\.openURL) private var openURL

    var body: some View {
        HStack(alignment: .top, spacing: Spacing.md) {
            RowContent(
                title: link.label?.isEmpty == false ? link.label! : (link.platform ?? "Ticket link"),
                subtitle: ticketLinkText(link),
                systemImage: "ticket"
            )
            if let url = chusennoteWebURL(link.url) {
                IconActionButton(title: "Open", systemImage: "arrow.up.right") {
                    openURL(url)
                }
            }
        }
    }
}

struct InfoTextRow: View {
    let text: String
    let systemImage: String

    var body: some View {
        RowContent(title: text, subtitle: "", systemImage: systemImage, tint: .neutral)
    }
}

struct LinkRow: View {
    let title: String
    let subtitle: String
    let systemImage: String
    let url: URL?
    @Environment(\.openURL) private var openURL

    var body: some View {
        HStack(alignment: .top, spacing: Spacing.md) {
            RowContent(title: title, subtitle: subtitle, systemImage: systemImage)
            if let url {
                IconActionButton(title: "Open", systemImage: "arrow.up.right") {
                    openURL(url)
                }
            }
        }
    }
}

struct SubscriptionRow: View {
    let subscription: NotificationSubscription
    let watch: Watch?
    let action: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: Spacing.md) {
            RowContent(
                title: subscriptionTitle(subscription, watch: watch),
                subtitle: subscriptionText(subscription),
                systemImage: "bell.badge"
            )
            IconActionButton(title: "Remove", systemImage: "trash", action: action)
        }
    }
}

struct DeviceRow: View {
    let device: DeviceToken

    var body: some View {
        RowContent(
            title: device.label.isEmpty ? "\(device.platform) device" : device.label,
            subtitle: "Device #\(device.id)\n\(maskedToken(device.token))",
            systemImage: "iphone"
        )
    }
}

struct ArtistEventRow: View {
    let event: EventSummary

    var body: some View {
        HStack(alignment: .top, spacing: Spacing.md) {
            RowContent(
                title: event.title ?? "Untitled event",
                subtitle: [eventStatusText(event), event.eventDates?.prefix(2).joined(separator: "; "), event.venueLabel].compactMap { $0 }.joined(separator: " - "),
                systemImage: "music.mic"
            )
            Image(systemName: "chevron.right")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.tertiary)
        }
    }
}

struct UpcomingDeadlineRow: View {
    let item: UpcomingItem
    @Environment(\.openURL) private var openURL

    var body: some View {
        HStack(alignment: .top, spacing: Spacing.md) {
            RowContent(
                title: item.eventTitle ?? "Untitled event",
                subtitle: upcomingStatusText(item),
                systemImage: "calendar.badge.clock"
            )
            if let url = chusennoteWebURL(item.url) {
                IconActionButton(title: "Open", systemImage: "arrow.up.right") {
                    openURL(url)
                }
            }
        }
    }
}

struct EventTimelineRow: View {
    let event: EventSummary

    var body: some View {
        HStack(alignment: .top, spacing: Spacing.md) {
            Image(systemName: "ticket")
                .font(Typography.rowTitle)
                .frame(width: IconSize.standard, height: IconSize.standard)
                .foregroundStyle(SemanticColor.info.color)
                .background(SemanticColor.info.color.opacity(0.1))
                .clipShape(Circle())

            VStack(alignment: .leading, spacing: Spacing.sm) {
                Text(event.title ?? "Untitled event")
                    .font(Typography.rowTitle)
                    .foregroundStyle(.primary)
                    .lineLimit(2)

                Text("\(eventStatusText(event)) - \(event.rounds.count) rounds")
                    .font(Typography.rowSubtitle)
                    .foregroundStyle(.secondary)

                if event.rounds.isEmpty {
                    Text("No lottery rounds yet")
                        .font(Typography.rowSubtitle)
                        .foregroundStyle(.secondary)
                } else {
                    TicketRoundGroupList(
                        rounds: event.rounds,
                        showsActions: false,
                        isCompact: true,
                        maxRoundsPerGroup: 2
                    )
                }
            }
            Spacer(minLength: 0)
            Image(systemName: "chevron.right")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.tertiary)
        }
    }
}
