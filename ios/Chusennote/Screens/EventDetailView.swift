import SwiftUI

struct EventDetailView: View {
    let store: ChusennoteStore
    let event: EventSummary
    @Environment(\.openURL) private var openURL

    var body: some View {
        List {
            ErrorSection(store: store)

            Section {
                VStack(alignment: .leading, spacing: Spacing.xxs) {
                    Text(event.title ?? "Untitled event")
                        .font(.title3.weight(.semibold))
                    Text(eventStatusText(event))
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                LabeledContent("Date", value: event.eventDates?.first ?? "TBA")
                LabeledContent("Venue", value: event.venueLabel?.isEmpty == false ? event.venueLabel! : "TBA")

                if let url = chusennoteWebURL(event.officialUrl) {
                    Button("Open Official Page") {
                        openURL(url)
                    }
                }
                Button("Notify Me for All Rounds") {
                    Task { await store.addSubscription(watch: "\(event.watchId)", scope: "event_all") }
                }
            }

            if event.rounds.isEmpty {
                Section("Ticket Rounds") {
                    EmptyRow("No ticket rounds yet. Check now to collect lottery rounds.")
                }
            } else {
                ForEach(ticketRoundGroups(event.rounds)) { group in
                    Section(group.website) {
                        ForEach(group.rounds) { round in
                            TicketRoundRow(round: round) { round in
                                subscribe(to: round)
                            }
                        }
                    }
                }
            }

            if let locations = event.eventLocations, !locations.isEmpty {
                Section("Notify Me by Location") {
                    DisclosureGroup("Choose a location") {
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

            Section("Details") {
                if let links = event.ticketLinks, !links.isEmpty {
                    DisclosureGroup("Ticket links (\(links.count))") {
                        ForEach(links) { link in
                            LinkRow(
                                title: link.label?.isEmpty == false ? link.label! : (link.platform ?? "Ticket link"),
                                subtitle: ticketLinkText(link),
                                url: chusennoteWebURL(link.url)
                            )
                        }
                    }
                }
                DetailDisclosure(title: "Cast & lineup", items: event.lineup ?? [])
                DetailDisclosure(title: "Organizers", items: event.organizers ?? [])
                DetailDisclosure(title: "Ticket rules", items: event.ticketRules ?? [])
                DetailDisclosure(title: "Ticket prices", items: event.ticketPrices ?? [])
                DetailDisclosure(title: "Why this matched", items: event.matchReasons ?? [])
                if let sources = event.manualSources, !sources.isEmpty {
                    DisclosureGroup("Manual sources (\(sources.count))") {
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
                    DisclosureGroup("Related saved events (\(relatedEvents.count))") {
                        ForEach(relatedEvents) { related in
                            LinkRow(
                                title: related.title ?? "Related event",
                                subtitle: relatedEventText(related),
                                url: chusennoteWebURL(related.officialUrl)
                            )
                        }
                    }
                }
                LabeledContent("Updated", value: event.updatedAt?.isEmpty == false ? event.updatedAt! : "Never")
            }
        }
        .navigationTitle("Event")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func subscribe(to round: TicketRound) {
        guard let roundKey = round.roundKey, !roundKey.isEmpty else { return }
        Task {
            await store.addSubscription(watch: "\(event.watchId)", scope: "round", roundKey: roundKey)
        }
    }
}
