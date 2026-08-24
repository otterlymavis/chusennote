import SwiftUI

struct EventDetailView: View {
    @ObservedObject var store: ChusennoteStore
    let event: EventSummary
    @Environment(\.openURL) private var openURL
    @State private var showsLinks = false
    @State private var showsRules = false
    @State private var showsPrices = false
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
