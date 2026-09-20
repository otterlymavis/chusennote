import SwiftUI

struct WatchView: View {
    @ObservedObject var store: ChusennoteStore
    @Binding var eventKeyword: String
    @Binding var eventTags: String
    @Binding var eventRegions: String
    @Binding var eventVenues: String
    @Binding var eventAlerts: String
    @State private var exactEventKeyword = ""
    @State private var exactEventResults: [SearchResult] = []
    @State private var isSearchingEvents = false
    @State private var showsAddWatch = false
    @State private var showsExactSearch = false
    @State private var showsReminderOptions = false
    @State private var editingWatchID: Int?

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: Spacing.lg) {
                Button {
                    Task { await store.runEventWatches() }
                } label: {
                    ProgressLabel(
                        title: store.isRunningChecks ? "Checking" : "Run Checks Now",
                        systemImage: "play.circle.fill",
                        isLoading: store.isRunningChecks
                    )
                        .font(.headline)
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .disabled(store.isRunningChecks)

                NotificationSection(title: "Ticket Timelines", icon: "ticket", accent: .info) {
                    let ticketEvents = store.events.filter { ($0.watchKind ?? "event") == "event" }
                    if ticketEvents.isEmpty {
                        EmptyStateRow(title: "No ticket timelines", detail: "Run checks after adding a watch.")
                    } else {
                        ForEach(ticketEvents) { event in
                            NavigationLink {
                                EventDetailView(store: store, event: event)
                            } label: {
                                EventTimelineRow(event: event)
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }

                NotificationSection(title: "Active Watches", icon: "eye", accent: .info) {
                    if store.trackedEvents.isEmpty {
                        EmptyStateRow(title: "No active ticket watches", detail: "Add a watch below.")
                    } else {
                        ForEach(store.trackedEvents) { watch in
                            WatchRow(
                                watch: watch,
                                actionTitle: "Remove",
                                actionIcon: "trash",
                                action: { Task { await store.removeWatch(id: watch.id) } },
                                secondaryActionTitle: "Edit",
                                secondaryActionIcon: "slider.horizontal.3",
                                secondaryAction: { editEventWatch(watch) }
                            )
                        }
                    }
                }

                NotificationSection(title: "Add", icon: "plus.circle", accent: .success) {
                    DisclosureGroup(isExpanded: $showsAddWatch) {
                        VStack(alignment: .leading, spacing: Spacing.md) {
                            AppTextField("Event keyword", text: $eventKeyword)
                                .disabled(editingWatchID != nil)
                            AppTextField("Tags", text: $eventTags)
                            AppTextField("Preferred regions", text: $eventRegions)
                            AppTextField("Preferred venues", text: $eventVenues)

                            DisclosureGroup(isExpanded: $showsReminderOptions) {
                                AlertPreferenceToggles(alerts: $eventAlerts)
                                    .padding(.top, Spacing.xs)
                            } label: {
                                Label("\(alertCount(eventAlerts)) reminders", systemImage: "bell.badge")
                                    .font(Typography.sectionHeader)
                            }

                            HStack(spacing: Spacing.sm) {
                                Button {
                                    addEventWatch()
                                } label: {
                                    Label(
                                        editingWatchID == nil ? "Add Watch" : "Save Changes",
                                        systemImage: editingWatchID == nil ? "plus.circle.fill" : "checkmark.circle.fill"
                                    )
                                        .frame(maxWidth: .infinity)
                                }
                                .buttonStyle(.borderedProminent)

                                if editingWatchID == nil {
                                    Button {
                                        addRandomEventWatch()
                                    } label: {
                                        Label("Random", systemImage: "shuffle")
                                            .frame(maxWidth: .infinity)
                                    }
                                    .buttonStyle(.bordered)
                                } else {
                                    Button {
                                        clearEventEditor()
                                    } label: {
                                        Label("Cancel", systemImage: "xmark.circle")
                                            .frame(maxWidth: .infinity)
                                    }
                                    .buttonStyle(.bordered)
                                }
                            }
                        }
                        .padding(.top, Spacing.sm)
                    } label: {
                        Label("Ticket watch", systemImage: "plus.circle")
                            .font(Typography.sectionHeader)
                    }
                }

                NotificationSection(title: "Find", icon: "magnifyingglass", accent: .info) {
                    DisclosureGroup(isExpanded: $showsExactSearch) {
                        VStack(alignment: .leading, spacing: Spacing.md) {
                            AppTextField("Search exact event", text: $exactEventKeyword)
                            Button {
                                searchExactEvents()
                            } label: {
                                ProgressLabel(
                                    title: isSearchingEvents ? "Searching" : "Search Events",
                                    systemImage: "magnifyingglass",
                                    isLoading: isSearchingEvents
                                )
                                    .frame(maxWidth: .infinity)
                            }
                            .buttonStyle(.bordered)
                            .disabled(isSearchingEvents)

                            if exactEventResults.isEmpty {
                                EmptyStateRow(title: "No results loaded", detail: "Search for an official page.")
                            } else {
                                ForEach(exactEventResults) { result in
                                    SearchResultRow(result: result) {
                                        Task {
                                            await store.addExactEvent(
                                                keyword: trimmed(exactEventKeyword),
                                                title: result.title,
                                                url: result.url,
                                                snippet: result.snippet
                                            )
                                            exactEventResults = []
                                        }
                                    }
                                }
                            }
                        }
                        .padding(.top, Spacing.sm)
                    } label: {
                        Label("Exact event", systemImage: "magnifyingglass")
                            .font(Typography.sectionHeader)
                    }
                }

                NotificationSection(title: "Muted Watches", icon: "eye.slash", accent: .neutral) {
                    let mutedEvents = store.mutedWatches.filter { ($0.kind ?? "event") == "event" }
                    if mutedEvents.isEmpty {
                        EmptyStateRow(title: "No muted ticket watches", detail: "Removed watches can be restored here.")
                    } else {
                        ForEach(mutedEvents) { watch in
                            WatchRow(watch: watch, actionTitle: "Restore", actionIcon: "arrow.uturn.backward") {
                                Task { await store.restoreWatch(id: watch.id) }
                            }
                        }
                    }
                }
            }
            .padding(Spacing.xl)
            .padding(.bottom, Spacing.scrollBottomInset)
        }
        .background(Color(.systemGroupedBackground))
        .navigationTitle("Watch")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func addEventWatch() {
        let keyword = trimmed(eventKeyword)
        guard !keyword.isEmpty else { return }
        let tags = trimmed(eventTags)
        let regions = trimmed(eventRegions)
        let venues = trimmed(eventVenues)
        let alerts = trimmed(eventAlerts)
        clearEventEditor()
        Task { await store.addWatch(keyword: keyword, kind: "event", tags: tags, regions: regions, venues: venues, alerts: alerts) }
    }

    private func editEventWatch(_ watch: Watch) {
        editingWatchID = watch.id
        eventKeyword = watch.keyword
        eventTags = watch.tags ?? ""
        eventRegions = watch.preferredRegions ?? ""
        eventVenues = watch.preferredVenues ?? ""
        eventAlerts = watch.alertPreferences ?? defaultAlertPreferenceText()
        showsAddWatch = true
    }

    private func clearEventEditor() {
        editingWatchID = nil
        eventKeyword = ""
        eventTags = ""
        eventRegions = ""
        eventVenues = ""
        eventAlerts = defaultAlertPreferenceText()
    }

    private func addRandomEventWatch() {
        let event = RandomEventWatch.sample()
        eventKeyword = event.keyword
        eventTags = event.tags
        eventRegions = event.regions
        eventVenues = event.venues
        eventAlerts = event.alerts
        Task {
            await store.addWatch(
                keyword: event.keyword,
                kind: "event",
                tags: event.tags,
                regions: event.regions,
                venues: event.venues,
                alerts: event.alerts
            )
        }
    }

    private func searchExactEvents() {
        let keyword = trimmed(exactEventKeyword)
        guard !keyword.isEmpty else { return }
        isSearchingEvents = true
        Task {
            exactEventResults = await store.searchExactEvents(keyword: keyword)
            isSearchingEvents = false
        }
    }
}

private struct RandomEventWatch {
    let keyword: String
    let tags: String
    let regions: String
    let venues: String
    let alerts: String

    static func sample() -> RandomEventWatch {
        samples.randomElement() ?? samples[0]
    }

    private static let samples: [RandomEventWatch] = [
        RandomEventWatch(
            keyword: "YOASOBI live",
            tags: "concert,jpop",
            regions: "Tokyo,Kanagawa",
            venues: "Tokyo Dome,Ariake Arena",
            alerts: defaultAlertPreferenceText()
        ),
        RandomEventWatch(
            keyword: "King Gnu tour",
            tags: "concert,rock",
            regions: "Osaka,Hyogo",
            venues: "Kyocera Dome Osaka,Osaka-jo Hall",
            alerts: defaultAlertPreferenceText()
        ),
        RandomEventWatch(
            keyword: "Ghibli concert",
            tags: "orchestra,anime",
            regions: "Tokyo,Saitama",
            venues: "Tokyo International Forum,Saitama Super Arena",
            alerts: defaultAlertPreferenceText()
        ),
        RandomEventWatch(
            keyword: "Fuji Rock",
            tags: "festival",
            regions: "Niigata",
            venues: "Naeba Ski Resort",
            alerts: defaultAlertPreferenceText()
        )
    ]
}
