import SwiftUI

struct TicketRoundGroup: Identifiable {
    var id: String { website }
    let website: String
    let rounds: [TicketRound]
}

struct TicketRoundRow: View {
    let round: TicketRound
    var subscribeAction: ((TicketRound) -> Void)? = nil
    @Environment(\.openURL) private var openURL

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            TextRow(title: ticketRoundTitle(round), subtitle: ticketRoundStatusText(round))

            let dates = ticketRoundDateItems(round)
            if dates.isEmpty {
                EmptyRow(ticketRoundScheduleText(round).isEmpty ? "No dates found yet" : ticketRoundScheduleText(round))
            } else {
                VStack(alignment: .leading, spacing: Spacing.xxs) {
                    ForEach(dates) { date in
                        HStack(alignment: .firstTextBaseline) {
                            Text(date.label)
                                .foregroundStyle(.secondary)
                            Spacer(minLength: Spacing.sm)
                            Text(date.value)
                                .multilineTextAlignment(.trailing)
                        }
                        .font(Typography.rowSubtitle)
                    }
                }
            }

            if let membership = round.membershipLabel, !membership.isEmpty {
                EmptyRow(membership)
            }

            if let evidence = round.evidence, !evidence.isEmpty {
                Text("Evidence: \(evidence)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(3)
            }

            HStack(spacing: Spacing.lg) {
                if let subscribeAction, round.roundKey?.isEmpty == false {
                    Button("Notify Me") {
                        subscribeAction(round)
                    }
                }
                if let url = chusennoteWebURL(round.url) {
                    Button("Open Source") {
                        openURL(url)
                    }
                }
            }
            .buttonStyle(.borderless)
            .font(Typography.rowSubtitle)
        }
        .padding(.vertical, Spacing.xxs)
    }
}

struct TicketRoundDateItem: Identifiable {
    let label: String
    let value: String

    var id: String { "\(label)-\(value)" }
}
