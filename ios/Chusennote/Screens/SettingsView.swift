import SwiftUI

struct SettingsView: View {
    @ObservedObject var store: ChusennoteStore
    @ObservedObject var notificationPermission: NotificationPermission
    @Environment(\.openURL) private var openURL
    @State private var artistKeyword = ""
    @State private var showsNotificationFeed = false
    @State private var showsSubscriptions = false
    @State private var showsManualSources = false
    @State private var showsArtists = false
    @State private var showsOtherWatches = false
    @State private var showsArtistEvents = false
    @State private var showsMutedSources = false
    @Binding var sourceWatch: String
    @Binding var sourceURL: String
    @Binding var sourceLabel: String
    @Binding var sourcePrivateNote: Bool

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: Spacing.lg) {
                NotificationSection(title: "Server", icon: "server.rack", accent: .info) {
                    AppTextField("Base URL", text: $store.baseURL)
                    AppTextField("API token", text: $store.apiToken)
                    Button {
                        Task { await store.refresh() }
                    } label: {
                        ProgressLabel(
                            title: store.isRefreshing ? "Refreshing Server" : "Refresh Server",
                            systemImage: "arrow.clockwise",
                            isLoading: store.isRefreshing
                        )
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(store.isRefreshing)

                    if let error = store.errorMessage {
                        Label(error, systemImage: "exclamationmark.triangle")
                            .font(.footnote)
                            .foregroundStyle(SemanticColor.danger.color)
                    }

                    if let health = store.health {
                        Text("Server \(health.status): \(health.trackedEvents) watched events, \(health.alerts) alerts")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }

                NotificationSection(title: "Notifications", icon: "bell.and.waves.left.and.right", accent: .info) {
                    NotificationPermissionCard(permission: notificationPermission, compact: true)

                    Button {
                        Task { await store.runNotifications() }
                    } label: {
                        ProgressLabel(
                            title: store.isRunningNotifications ? "Sending Due Reminders" : "Run Due Reminders",
                            systemImage: "bell.and.waves.left.and.right",
                            isLoading: store.isRunningNotifications
                        )
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                    .disabled(store.isRunningNotifications)

                    if let calendarURL = store.calendarFeedURL {
                        Button {
                            openURL(calendarURL)
                        } label: {
                            Label("Open Calendar Feed", systemImage: "calendar")
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }

                    if store.devices.isEmpty {
                        EmptyStateRow(title: "No push devices registered", detail: "Enable notifications and refresh after the app receives a push token.")
                    } else {
                        ForEach(store.devices) { device in
                            DeviceRow(device: device)
                        }
                    }
                }

                NotificationSection(title: "Notification Feed", icon: "bell.badge", accent: .info) {
                    DisclosureGroup(isExpanded: $showsNotificationFeed) {
                        VStack(alignment: .leading, spacing: Spacing.md) {
                            if store.notificationFeed.isEmpty {
                                EmptyStateRow(title: "No delivered reminders", detail: "Due reminders appear here.")
                            } else {
                                ForEach(store.notificationFeed.prefix(10)) { item in
                                    LinkRow(
                                        title: item.title ?? item.label ?? "Reminder",
                                        subtitle: notificationFeedText(item),
                                        systemImage: "bell.badge",
                                        url: chusennoteWebURL(item.url)
                                    )
                                }
                            }
                        }
                        .padding(.top, Spacing.sm)
                    } label: {
                        Label("\(store.notificationFeed.count)", systemImage: "bell.badge")
                            .font(Typography.sectionHeader)
                    }
                }

                NotificationSection(title: "Subscriptions", icon: "checkmark.circle", accent: .success) {
                    DisclosureGroup(isExpanded: $showsSubscriptions) {
                        VStack(alignment: .leading, spacing: Spacing.md) {
                            if store.subscriptions.isEmpty {
                                EmptyStateRow(title: "No subscriptions", detail: "Subscribe from event details.")
                            } else {
                                ForEach(store.subscriptions) { subscription in
                                    SubscriptionRow(subscription: subscription, watch: watchForSubscription(subscription, in: store.watches)) {
                                        Task { await store.removeSubscription(id: subscription.id) }
                                    }
                                }
                            }
                        }
                        .padding(.top, Spacing.sm)
                    } label: {
                        Label("\(store.subscriptions.count)", systemImage: "checkmark.circle")
                            .font(Typography.sectionHeader)
                    }
                }

                NotificationSection(title: "Manual Sources", icon: "link", accent: .info) {
                    DisclosureGroup(isExpanded: $showsManualSources) {
                        VStack(alignment: .leading, spacing: Spacing.md) {
                            AppTextField("Watch id or keyword", text: $sourceWatch)
                            AppTextField("Ticket or source URL", text: $sourceURL)
                            AppTextField("Label", text: $sourceLabel)
                            Toggle("Private note", isOn: $sourcePrivateNote)

                            Button {
                                addSource()
                            } label: {
                                Label("Add Source", systemImage: "plus.circle.fill")
                                    .frame(maxWidth: .infinity)
                            }
                            .buttonStyle(.bordered)

                            if store.activeSources.isEmpty {
                                EmptyStateRow(title: "No manual sources", detail: "Add links only when needed.")
                            } else {
                                ForEach(store.activeSources) { source in
                                    SourceRow(source: source, actionTitle: "Remove") {
                                        Task { await store.removeSource(id: source.id) }
                                    }
                                }
                            }
                        }
                        .padding(.top, Spacing.sm)
                    } label: {
                        Label("\(store.activeSources.count)", systemImage: "link")
                            .font(Typography.sectionHeader)
                    }
                }

                NotificationSection(title: "Tracked Artists", icon: "music.mic", accent: .info) {
                    DisclosureGroup(isExpanded: $showsArtists) {
                        VStack(alignment: .leading, spacing: Spacing.md) {
                            AppTextField("Artist", text: $artistKeyword)
                            HStack(spacing: Spacing.sm) {
                                Button {
                                    addArtistWatch()
                                } label: {
                                    Label("Add Artist", systemImage: "plus.circle.fill")
                                        .frame(maxWidth: .infinity)
                                }
                                .buttonStyle(.borderedProminent)

                                Button {
                                    Task { await store.runArtistWatches() }
                                } label: {
                                    ProgressLabel(
                                        title: store.isRunningChecks ? "Checking" : "Run Artists",
                                        systemImage: "play.circle.fill",
                                        isLoading: store.isRunningChecks
                                    )
                                        .frame(maxWidth: .infinity)
                                }
                                .buttonStyle(.bordered)
                                .disabled(store.isRunningChecks)
                            }

                            if store.trackedArtists.isEmpty {
                                EmptyStateRow(title: "No tracked artists", detail: "Artist watches discover shows.")
                            } else {
                                ForEach(store.trackedArtists) { watch in
                                    WatchRow(
                                        watch: watch,
                                        actionTitle: "Remove",
                                        actionIcon: "trash",
                                        action: {
                                            Task { await store.removeWatch(id: watch.id) }
                                        },
                                        secondaryActionTitle: "Notify",
                                        secondaryActionIcon: "bell.badge",
                                        secondaryAction: {
                                            Task { await store.addSubscription(watch: "\(watch.id)", scope: "artist_all") }
                                        }
                                    )
                                }
                            }
                        }
                        .padding(.top, Spacing.sm)
                    } label: {
                        Label("\(store.trackedArtists.count)", systemImage: "music.mic")
                            .font(Typography.sectionHeader)
                    }
                }

                NotificationSection(title: "Other Watches", icon: "eye.slash", accent: .neutral) {
                    let mutedOtherWatches = store.mutedWatches.filter { ($0.kind ?? "event") != "event" }
                    DisclosureGroup(isExpanded: $showsOtherWatches) {
                        VStack(alignment: .leading, spacing: Spacing.md) {
                            if mutedOtherWatches.isEmpty {
                                EmptyStateRow(title: "No other watches", detail: "Ticket watches live on Watch.")
                            } else {
                                ForEach(mutedOtherWatches) { watch in
                                    WatchRow(watch: watch, actionTitle: "Restore", actionIcon: "arrow.uturn.backward") {
                                        Task { await store.restoreWatch(id: watch.id) }
                                    }
                                }
                            }
                        }
                        .padding(.top, Spacing.sm)
                    } label: {
                        Label("\(mutedOtherWatches.count)", systemImage: "eye.slash")
                            .font(Typography.sectionHeader)
                    }
                }

                NotificationSection(title: "Artist Event Info", icon: "music.mic", accent: .info) {
                    let artistEvents = store.events.filter { ($0.watchKind ?? "event") == "artist" }
                    DisclosureGroup(isExpanded: $showsArtistEvents) {
                        VStack(alignment: .leading, spacing: Spacing.md) {
                            if artistEvents.isEmpty {
                                EmptyStateRow(title: "No artist event info", detail: "Run checks to collect events.")
                            } else {
                                ForEach(artistEvents) { event in
                                    NavigationLink {
                                        EventDetailView(store: store, event: event)
                                    } label: {
                                        ArtistEventRow(event: event)
                                    }
                                    .buttonStyle(.plain)
                                }
                            }
                        }
                        .padding(.top, Spacing.sm)
                    } label: {
                        Label("\(artistEvents.count)", systemImage: "music.mic")
                            .font(Typography.sectionHeader)
                    }
                }

                NotificationSection(title: "Muted Sources", icon: "link.badge.plus", accent: .neutral) {
                    DisclosureGroup(isExpanded: $showsMutedSources) {
                        VStack(alignment: .leading, spacing: Spacing.md) {
                            if store.mutedSources.isEmpty {
                                EmptyStateRow(title: "No muted sources", detail: "Removed sources appear here.")
                            } else {
                                ForEach(store.mutedSources) { source in
                                    SourceRow(source: source, actionTitle: "Restore", actionIcon: "arrow.uturn.backward") {
                                        Task { await store.restoreSource(id: source.id) }
                                    }
                                }
                            }
                        }
                        .padding(.top, Spacing.sm)
                    } label: {
                        Label("\(store.mutedSources.count)", systemImage: "link.badge.plus")
                            .font(Typography.sectionHeader)
                    }
                }
            }
            .padding(Spacing.xl)
            .padding(.bottom, Spacing.scrollBottomInset)
        }
        .background(Color(.systemGroupedBackground))
        .navigationTitle("Settings")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func addSource() {
        let watch = trimmed(sourceWatch)
        let url = trimmed(sourceURL)
        let label = trimmed(sourceLabel)
        let privateNote = sourcePrivateNote
        guard !watch.isEmpty, !url.isEmpty else { return }
        sourceWatch = ""
        sourceURL = ""
        sourceLabel = ""
        sourcePrivateNote = false
        Task { await store.addSource(watch: watch, url: url, label: label, privateNote: privateNote) }
    }

    private func addArtistWatch() {
        let keyword = trimmed(artistKeyword)
        guard !keyword.isEmpty else { return }
        artistKeyword = ""
        Task { await store.addWatch(keyword: keyword, kind: "artist") }
    }
}
