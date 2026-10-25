import SwiftUI

struct EventDetailView: View {
    let store: ChusennoteStore
    let event: EventSummary
    @Environment(\.openURL) private var openURL

    var body: some View {
        List {
            ErrorSection(store: store)

            Section {
                TextRow(title: event.title ?? "Untitled event", subtitle: eventRowText(event))
                if let url = chusennoteWebURL(event.officialUrl) {
                    Button("Official Page") {
                        openURL(url)
                    }
                }
                Button("Notify Me for All Rounds") {
                    Task { await store.addSubscription(watch: "\(event.watchId)", scope: "event_all") }
                }
                if let locations = event.eventLocations, locations.count > 1 {
                    Menu("Notify Me for One Location") {
                        ForEach(locations) { location in
                            Button(eventLocationTitle(location)) {
                                Task {
                                    await store.addSubscription(
                                        watch: "\(event.watchId)",
                                        scope: "event_location",
                                        location: location.location
                                    )
                                }
                            }
                        }
                    }
                }
            }

            Section("Ticket Rounds") {
                if event.rounds.isEmpty {
                    EmptyRow("No ticket rounds found yet.")
                } else {
                    ForEach(sortTicketRoundsNewestFirst(event.rounds)) { round in
                        TicketRoundRow(round: round) { round in
                            subscribe(to: round)
                        }
                    }
                }
            }

            let facts = detailFacts
            if !facts.isEmpty {
                Section("Details") {
                    ForEach(facts, id: \.label) { fact in
                        LabeledContent(fact.label) {
                            Text(fact.value)
                                .multilineTextAlignment(.trailing)
                        }
                    }
                }
            }

            if let links = event.ticketLinks, !links.isEmpty {
                Section("Ticket Links") {
                    ForEach(links) { link in
                        LinkRow(
                            title: link.label?.isEmpty == false ? link.label! : (link.platform ?? "Ticket link"),
                            subtitle: ticketLinkText(link),
                            url: chusennoteWebURL(link.url)
                        )
                    }
                }
            }

            if let sources = event.manualSources, !sources.isEmpty {
                Section("Your Sources") {
                    ForEach(sources) { source in
                        LinkRow(
                            title: source.label.isEmpty ? source.url : source.label,
                            subtitle: sourceText(source),
                            url: chusennoteWebURL(source.url)
                        )
                    }
                }
            }

            if let relatedEvents = event.relatedEvents, !relatedEvents.isEmpty {
                Section("Related Events") {
                    ForEach(relatedEvents) { related in
                        LinkRow(
                            title: related.title ?? "Related event",
                            subtitle: relatedEventText(related),
                            url: chusennoteWebURL(related.officialUrl)
                        )
                    }
                }
            }
        }
        .navigationTitle("Event")
        .navigationBarTitleDisplayMode(.inline)
    }

    /// Facts the event page actually published; unknown ones are left out.
    private var detailFacts: [(label: String, value: String)] {
        [
            ("Dates", (event.eventDates ?? []).prefix(3).map { friendlyDate($0) }),
            ("Venue", Array((event.venues ?? []).prefix(3))),
            ("Organizer", event.organizers ?? []),
            ("Cast & lineup", event.lineup ?? []),
            ("Prices", event.ticketPrices ?? []),
            ("Rules", event.ticketRules ?? [])
        ]
            .filter { !$0.1.isEmpty }
            .map { (label: $0.0, value: $0.1.joined(separator: "\n")) }
    }

    private func subscribe(to round: TicketRound) {
        guard let roundKey = round.roundKey, !roundKey.isEmpty else { return }
        Task {
            await store.addSubscription(watch: "\(event.watchId)", scope: "round", roundKey: roundKey)
        }
    }
}
