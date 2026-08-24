import SwiftUI

struct TicketRoundGroup: Identifiable {
    var id: String { website }
    let website: String
    let rounds: [TicketRound]
}

struct TicketRoundGroupList: View {
    let rounds: [TicketRound]
    var showsActions: Bool = true
    var isCompact = false
    var maxRoundsPerGroup: Int? = nil
    var subscribeAction: ((TicketRound) -> Void)? = nil

    var body: some View {
        LazyVStack(alignment: .leading, spacing: isCompact ? Spacing.sm : Spacing.md) {
            ForEach(ticketRoundGroups(rounds)) { group in
                VStack(alignment: .leading, spacing: Spacing.xs) {
                    Text(group.website)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                    let visibleRounds = limitedTicketRounds(group.rounds, max: maxRoundsPerGroup)
                    ForEach(visibleRounds) { round in
                        TicketRoundTile(
                            round: round,
                            showsActions: showsActions,
                            isCompact: isCompact,
                            subscribeAction: subscribeAction
                        )
                    }
                    if let maxRoundsPerGroup, group.rounds.count > maxRoundsPerGroup {
                        Label("+\(group.rounds.count - maxRoundsPerGroup)", systemImage: "ellipsis")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.secondary)
                            .padding(.horizontal, 10)
                            .padding(.vertical, Spacing.xs)
                            .background(Color(.tertiarySystemGroupedBackground))
                            .clipShape(Capsule())
                    }
                }
            }
        }
    }
}

struct TicketRoundTile: View {
    let round: TicketRound
    var showsActions: Bool = true
    var isCompact = false
    var subscribeAction: ((TicketRound) -> Void)? = nil
    @Environment(\.openURL) private var openURL

    var body: some View {
        VStack(alignment: .leading, spacing: isCompact ? Spacing.sm : Spacing.md) {
            HStack(alignment: .top, spacing: Spacing.sm + Spacing.xxs) {
                Image(systemName: "ticket.fill")
                    .font(.caption.weight(.bold))
                    .foregroundStyle(.white)
                    .frame(width: isCompact ? 26 : IconSize.standard, height: isCompact ? 26 : IconSize.standard)
                    .background(ticketRoundAccent(round).color)
                    .clipShape(Circle())

                VStack(alignment: .leading, spacing: 5) {
                    Text(ticketRoundTitle(round))
                        .font((isCompact ? Font.caption : Font.subheadline).weight(.semibold))
                        .foregroundStyle(.primary)
                        .lineLimit(isCompact ? 1 : 2)

                    Text(ticketRoundStatusText(round))
                        .font((isCompact ? Font.caption2 : Font.caption).weight(.medium))
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }

                Spacer(minLength: 0)
            }

            TicketRoundDatePanel(round: round, isCompact: isCompact)

            if !isCompact, let membership = round.membershipLabel, !membership.isEmpty {
                Label(membership, systemImage: "person.badge.key")
                    .font(.caption2.weight(.medium))
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }

            if !isCompact, let evidence = round.evidence, !evidence.isEmpty {
                Text("Evidence: \(evidence)")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(3)
            }

            if showsActions {
                HStack(spacing: Spacing.sm) {
                    if let url = chusennoteWebURL(round.url) {
                        IconActionButton(title: "Source", systemImage: "arrow.up.right") {
                            openURL(url)
                        }
                    }
                    if let subscribeAction, round.roundKey?.isEmpty == false {
                        IconActionButton(title: "Notify", systemImage: "bell.badge", prominent: true) {
                            subscribeAction(round)
                        }
                    }
                }
            }
        }
        .tileCardStyle(accent: ticketRoundAccent(round), prominent: !isCompact)
    }
}

struct TicketRoundDatePanel: View {
    let round: TicketRound
    var isCompact = false

    var body: some View {
        let dates = ticketRoundDateItems(round)
        if dates.isEmpty {
            Text(ticketRoundScheduleText(round).isEmpty ? "No dates found yet" : ticketRoundScheduleText(round))
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
                .padding(.horizontal, 12)
                .padding(.vertical, isCompact ? 7 : 9)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color(.secondarySystemGroupedBackground))
                .clipShape(Capsule())
        } else {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: isCompact ? 112 : 132), spacing: Spacing.sm)], alignment: .leading, spacing: Spacing.sm) {
                ForEach(dates) { date in
                    TicketRoundDateChip(date: date, isCompact: isCompact)
                }
            }
        }
    }
}

struct TicketRoundDateChip: View {
    let date: TicketRoundDateItem
    var isCompact = false

    var body: some View {
        VStack(alignment: .leading, spacing: isCompact ? 2 : 3) {
            Text(date.label)
                .font(Font.caption2.weight(.bold))
                .textCase(.uppercase)
                .foregroundStyle(ticketDateColor(date.kind).color)
                .lineLimit(1)
                .minimumScaleFactor(0.8)

            Text(date.value)
                .font((isCompact ? Font.caption2 : Font.caption).weight(.bold))
                .foregroundStyle(.primary)
                .lineLimit(isCompact ? 1 : 2)
                .minimumScaleFactor(0.85)
        }
        .padding(.horizontal, isCompact ? 9 : 11)
        .padding(.vertical, isCompact ? 7 : 9)
        .frame(maxWidth: .infinity, minHeight: isCompact ? 46 : 58, alignment: .leading)
        .background(ticketDateColor(date.kind).color.opacity(0.12))
        .clipShape(Capsule())
        .overlay(
            Capsule()
                .stroke(ticketDateColor(date.kind).color.opacity(0.22), lineWidth: 1)
        )
    }
}

struct TicketRoundDateItem: Identifiable {
    let label: String
    let value: String
    let kind: TicketRoundDateKind

    var id: String { "\(label)-\(value)" }
}

enum TicketRoundDateKind {
    case apply
    case result
    case payment
    case sale
}
