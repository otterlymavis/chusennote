import SwiftUI

struct TicketRoundRow: View {
    let round: TicketRound
    var subscribeAction: ((TicketRound) -> Void)? = nil
    @Environment(\.openURL) private var openURL

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            TextRow(title: ticketRoundTitle(round), subtitle: ticketRoundStatusText(round))

            // Only dates the source published are listed; a general-sale round
            // often has just one, and many lotteries never publish results.
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

            HStack(spacing: Spacing.lg) {
                if let subscribeAction, round.roundKey?.isEmpty == false {
                    Button("Notify Me") {
                        subscribeAction(round)
                    }
                }
                if let url = chusennoteWebURL(round.url) {
                    Button("Source") {
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
