import SwiftUI

struct NotificationSection<Content: View>: View {
    let title: String
    var icon: String = "sparkle.magnifyingglass"
    var accent: SemanticColor = .info
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            HStack(spacing: Spacing.sm) {
                Image(systemName: icon)
                    .font(.caption.weight(.bold))
                    .foregroundStyle(accent.color)
                    .frame(width: IconSize.small, height: IconSize.small)
                    .background(accent.color.opacity(0.1))
                    .clipShape(Circle())

                Text(title)
                    .font(Typography.sectionHeader)
                    .foregroundStyle(.secondary)
            }
            VStack(alignment: .leading, spacing: Spacing.md) {
                content
            }
            .sectionCardStyle()
        }
    }
}

struct NotificationHero: View {
    @ObservedObject var store: ChusennoteStore

    var body: some View {
        HStack(spacing: Spacing.md + Spacing.xs) {
            Image(systemName: "bell.badge.fill")
                .font(.title2)
                .foregroundStyle(SemanticColor.info.color)
                .frame(width: IconSize.large, height: IconSize.large)
                .background(SemanticColor.info.color.opacity(0.12))
                .clipShape(Circle())

            VStack(alignment: .leading, spacing: Spacing.xs) {
                Text("Ticket Alerts")
                    .font(Typography.heroTitle)
                HStack(spacing: Spacing.xs) {
                    Circle()
                        .fill(statusColor.color)
                        .frame(width: 8, height: 8)
                    Text(statusText)
                        .font(.caption.weight(.medium))
                        .foregroundStyle(.secondary)
                }
            }

            Spacer(minLength: 0)
        }
        .heroCardStyle()
    }

    private var statusText: String {
        if let error = store.errorMessage, !error.isEmpty {
            return "Server needs attention"
        }
        if let health = store.health {
            return "Server \(health.status)"
        }
        return "Checking server"
    }

    private var statusColor: SemanticColor {
        if store.errorMessage != nil {
            return .danger
        }
        return store.health == nil ? .neutral : .success
    }
}

struct NotificationPermissionCard: View {
    @ObservedObject var permission: NotificationPermission
    var compact = false

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.md) {
            HStack(spacing: Spacing.md) {
                let tint: SemanticColor = permission.isEnabled ? .success : .info
                Image(systemName: permission.isEnabled ? "bell.badge.fill" : "bell.slash")
                    .font(.title3)
                    .foregroundStyle(tint.color)
                    .frame(width: IconSize.standard, height: IconSize.standard)
                    .background(tint.color.opacity(0.12))
                    .clipShape(Circle())

                VStack(alignment: .leading, spacing: 3) {
                    Text(permission.title)
                        .font(Typography.rowTitle)
                    Text(permission.detail)
                        .font(Typography.rowSubtitle)
                        .foregroundStyle(.secondary)
                        .lineLimit(compact ? 2 : 3)
                }

                Spacer(minLength: 0)
            }

            if permission.canAct {
                Button {
                    permission.performAction()
                } label: {
                    Label(permission.actionTitle, systemImage: permission.actionIcon)
                        .font(.headline)
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
            }
        }
        .sectionCardStyle()
    }
}

struct MetricTile: View {
    let title: String
    let value: String
    let systemImage: String
    var tint: SemanticColor = .info

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Image(systemName: systemImage)
                .font(Typography.sectionHeader)
                .foregroundStyle(tint.color)
                .frame(width: IconSize.small, height: IconSize.small)
            Text(value)
                .font(Typography.metricValue)
            Text(title)
                .font(Typography.metricLabel)
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .minimumScaleFactor(0.8)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(Spacing.md)
        .sectionCardStyle()
    }
}

struct FeaturedDeadlineCard: View {
    let item: UpcomingItem
    @Environment(\.openURL) private var openURL

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.md) {
            HStack(alignment: .top, spacing: Spacing.md) {
                Image(systemName: "clock.badge.exclamationmark")
                    .font(.headline.weight(.bold))
                    .foregroundStyle(.white)
                    .frame(width: IconSize.large - 2, height: IconSize.large - 2)
                    .background(SemanticColor.warning.color)
                    .clipShape(Circle())

                VStack(alignment: .leading, spacing: 5) {
                    Text(item.eventTitle ?? "Untitled event")
                        .font(.headline.weight(.semibold))
                        .lineLimit(2)
                    Text(upcomingStatusText(item))
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }

                Spacer(minLength: 0)
            }

            HStack(spacing: Spacing.sm) {
                DashboardChip(
                    title: item.relevantDate ?? "Date TBA",
                    systemImage: "calendar",
                    tint: .warning
                )
                DashboardChip(
                    title: item.platform ?? "Ticket",
                    systemImage: "ticket",
                    tint: .info
                )
            }

            if let url = chusennoteWebURL(item.url) {
                IconActionButton(title: "Open", systemImage: "arrow.up.right", prominent: true) {
                    openURL(url)
                }
            }
        }
        .padding(Spacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(SemanticColor.warning.color.opacity(0.1))
        .clipShape(RoundedRectangle(cornerRadius: Radius.large, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: Radius.large, style: .continuous)
                .stroke(SemanticColor.warning.color.opacity(0.22), lineWidth: 1)
        )
    }
}

struct DashboardChip: View {
    let title: String
    let systemImage: String
    let tint: SemanticColor

    var body: some View {
        Label(title, systemImage: systemImage)
            .chipStyle(tint: tint)
    }
}

struct EventSummaryPanel<Actions: View>: View {
    let event: EventSummary
    @ViewBuilder var actions: Actions

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.md) {
            RowContent(
                title: event.title ?? "Untitled event",
                subtitle: event.status ?? "watching",
                systemImage: "ticket"
            )

            HStack(spacing: Spacing.sm) {
                EventInfoChip(
                    title: event.eventDates?.first ?? "Date TBA",
                    systemImage: "calendar",
                    tint: .info
                )
                EventInfoChip(
                    title: event.venueLabel?.isEmpty == false ? event.venueLabel! : "Venue TBA",
                    systemImage: "mappin.and.ellipse",
                    tint: .success
                )
            }

            actions
        }
    }
}

struct EventInfoChip: View {
    let title: String
    let systemImage: String
    let tint: SemanticColor

    var body: some View {
        Label(title, systemImage: systemImage)
            .chipStyle(tint: tint)
    }
}
