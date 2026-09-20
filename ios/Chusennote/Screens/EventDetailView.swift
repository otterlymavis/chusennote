import SwiftUI

struct EventDetailView: View {
    @ObservedObject var store: ChusennoteStore
    let event: EventSummary
    @Environment(\.openURL) private var openURL
    @State private var showsLinks = false
    @State private var showsRules = false
    @State private var showsPrices = false
    @State private var showsOrganizers = false
    @State private var showsLineup = false
    @State private var showsSources = false
    @State private var showsContext = false

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: Spacing.lg) {
                NotificationSection(title: "Event", icon: "ticket", accent: .info) {
                    EventSummaryPanel(event: event) {
                        HStack(spacing: Spacing.sm) {
                            if let url = chusennoteWebURL(event.officialUrl) {
                                IconActionButton(title: "Open Official Page", systemImage: "safari", prominent: true) {
                                    openURL(url)
                                }
                            }
                            IconActionButton(title: "Notify All Rounds", systemImage: "bell.badge") {
                                Task { await store.addSubscription(watch: "\(event.watchId)", scope: "event_all") }
                            }
                        }
                    }
                }

                NotificationSection(title: "Ticket Rounds", icon: "ticket", accent: .info) {
                    if event.rounds.isEmpty {
                        EmptyStateRow(title: "No ticket rounds", detail: "Run checks to collect lottery rounds.")
                    } else {
                        TicketRoundGroupList(rounds: event.rounds) { round in
                            if let roundKey = round.roundKey, !roundKey.isEmpty {
                                Task {
                                    await store.addSubscription(
                                        watch: "\(event.watchId)",
                                        scope: "round",
                                        roundKey: roundKey
                                    )
                                }
                            }
                        }
                    }
                }

                NotificationSection(title: "Context", icon: "info.circle", accent: .neutral) {
                    DisclosureGroup(isExpanded: $showsContext) {
                        VStack(alignment: .leading, spacing: Spacing.sm + Spacing.xxs) {
                            if let locations = event.eventLocations, !locations.isEmpty {
                                ForEach(locations) { location in
                                    Button {
                                        Task {
                                            await store.addSubscription(
                                                watch: "\(event.watchId)",
                                                scope: "event_location",
                                                location: location.location
                                            )
                                        }
                                    } label: {
                                        Label(eventLocationTitle(location), systemImage: "mappin.and.ellipse")
                                            .frame(maxWidth: .infinity)
                                    }
                                    .buttonStyle(.bordered)
                                }
                            }
                            if let reasons = event.matchReasons, !reasons.isEmpty {
                                InfoTextRow(text: reasons.prefix(3).joined(separator: "; "), systemImage: "checkmark.seal")
                            }
                            if let updatedAt = event.updatedAt, !updatedAt.isEmpty {
                                InfoTextRow(text: updatedAt, systemImage: "clock")
                            }
                        }
                        .padding(.top, Spacing.sm)
                    } label: {
                        Label("More event info", systemImage: "info.circle")
                            .font(Typography.sectionHeader)
                    }
                }

                if let relatedEvents = event.relatedEvents, !relatedEvents.isEmpty {
                    NotificationSection(title: "Related Saved Events", icon: "sparkles", accent: .highlight) {
                        VStack(alignment: .leading, spacing: Spacing.sm) {
                            ForEach(relatedEvents) { related in
                                VStack(alignment: .leading, spacing: Spacing.xs) {
                                    Text(related.title ?? "Related event")
                                        .font(Typography.sectionHeader)
                                    let detail = [related.eventDate, related.venueLabel]
                                        .compactMap({ value in value?.isEmpty == false ? value : nil })
                                        .joined(separator: " · ")
                                    if !detail.isEmpty {
                                        Text(detail)
                                            .font(Typography.rowSubtitle)
                                            .foregroundStyle(.secondary)
                                    }
                                    if let reasons = related.recommendationReasons, !reasons.isEmpty {
                                        Text(reasons.prefix(3).joined(separator: "; "))
                                            .font(Typography.rowSubtitle)
                                            .foregroundStyle(.secondary)
                                    }
                                    if let url = chusennoteWebURL(related.officialUrl) {
                                        Button {
                                            openURL(url)
                                        } label: {
                                            Label("Open Official Page", systemImage: "safari")
                                        }
                                        .buttonStyle(.bordered)
                                    }
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(Spacing.sm)
                                .background(.thinMaterial, in: RoundedRectangle(cornerRadius: Radius.medium))
                            }
                        }
                    }
                }

                CollapsibleEventListSection(
                    title: "Ticket Links",
                    icon: "ticket",
                    accent: .info,
                    isExpanded: $showsLinks,
                    emptyTitle: "No ticket links saved",
                    emptyDetail: "Links found on official pages appear here.",
                    items: event.ticketLinks ?? []
                ) { link in
                    TicketLinkRow(link: link)
                }

                CollapsibleTextSection(
                    title: "Organizers",
                    icon: "building.2",
                    accent: .neutral,
                    isExpanded: $showsOrganizers,
                    emptyTitle: "No organizers captured",
                    emptyDetail: "Organizer names from official pages appear here.",
                    items: event.organizers ?? [],
                    systemImage: "building.2"
                )

                CollapsibleTextSection(
                    title: "Cast & Lineup",
                    icon: "person.3",
                    accent: .highlight,
                    isExpanded: $showsLineup,
                    emptyTitle: "No cast or lineup captured",
                    emptyDetail: "Performer names from official pages appear here.",
                    items: event.lineup ?? [],
                    systemImage: "person.3"
                )

                CollapsibleTextSection(
                    title: "Ticket Rules",
                    icon: "checklist",
                    accent: .neutral,
                    isExpanded: $showsRules,
                    emptyTitle: "No ticket rules captured",
                    emptyDetail: "Rules from official pages appear here.",
                    items: event.ticketRules ?? [],
                    systemImage: "checklist"
                )

                CollapsibleTextSection(
                    title: "Ticket Price",
                    icon: "yensign.circle",
                    accent: .success,
                    isExpanded: $showsPrices,
                    emptyTitle: "No ticket prices captured",
                    emptyDetail: "Price notes from official pages appear here.",
                    items: event.ticketPrices ?? [],
                    systemImage: "yensign.circle"
                )

                CollapsibleEventListSection(
                    title: "Manual Sources",
                    icon: "link",
                    accent: .info,
                    isExpanded: $showsSources,
                    emptyTitle: "No manual sources",
                    emptyDetail: "Attached source links appear here.",
                    items: event.manualSources ?? []
                ) { source in
                    SourceRow(source: source, actionTitle: "") {}
                }
            }
            .padding(Spacing.xl)
            .padding(.bottom, Spacing.scrollBottomInset)
        }
        .background(Color(.systemGroupedBackground))
        .navigationTitle("Event")
        .navigationBarTitleDisplayMode(.inline)
    }
}
