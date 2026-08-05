import SwiftUI
import UIKit
import UserNotifications

private let sectionCornerRadius: CGFloat = 14
private let compactCornerRadius: CGFloat = 10
private let cardPadding: CGFloat = 14
private let iconFrame: CGFloat = 32
private let actionIconFrame: CGFloat = 34

enum AppTab {
    case notifications
    case watch
    case settings
}

struct ContentView: View {
    @StateObject private var store = ChusennoteStore()
    @StateObject private var notificationPermission = NotificationPermission()
    @State private var selectedTab: AppTab = .notifications
    @State private var eventKeyword = ""
    @State private var eventTags = ""
    @State private var eventRegions = ""
    @State private var eventVenues = ""
    @State private var eventAlerts = defaultAlertPreferenceText()
    @State private var sourceWatch = ""
    @State private var sourceURL = ""
    @State private var sourceLabel = ""
    @State private var sourcePrivateNote = false

    var body: some View {
        TabView(selection: $selectedTab) {
            NavigationStack {
                NotificationsView(store: store, notificationPermission: notificationPermission, selectedTab: $selectedTab)
            }
            .tabItem {
                Label("Alerts", systemImage: "bell.badge")
            }
            .tag(AppTab.notifications)

            NavigationStack {
                WatchView(
                    store: store,
                    eventKeyword: $eventKeyword,
                    eventTags: $eventTags,
                    eventRegions: $eventRegions,
                    eventVenues: $eventVenues,
                    eventAlerts: $eventAlerts
                )
            }
            .tabItem {
                Label("Watch", systemImage: "ticket")
            }
            .tag(AppTab.watch)

            NavigationStack {
                SettingsView(
                    store: store,
                    notificationPermission: notificationPermission,
                    sourceWatch: $sourceWatch,
                    sourceURL: $sourceURL,
                    sourceLabel: $sourceLabel,
                    sourcePrivateNote: $sourcePrivateNote
                )
            }
            .tabItem {
                Label("Settings", systemImage: "gearshape")
            }
            .tag(AppTab.settings)
        }
        .task {
            await store.refresh()
            notificationPermission.refresh()
        }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.didBecomeActiveNotification)) { _ in
            notificationPermission.refresh()
        }
    }
}

struct NotificationsView: View {
    @ObservedObject var store: ChusennoteStore
    @ObservedObject var notificationPermission: NotificationPermission
    @Binding var selectedTab: AppTab
    @State private var showsRecentAlerts = false

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 16) {
                NotificationHero(store: store)

                NotificationPermissionCard(permission: notificationPermission)

                HStack(spacing: 10) {
                    MetricTile(title: "Watching", value: "\(store.trackedEvents.count)", systemImage: "ticket")
                    MetricTile(title: "Due Soon", value: "\(store.upcoming.count)", systemImage: "clock.badge.exclamationmark")
                    MetricTile(title: "Alerts", value: "\(store.alerts.count)", systemImage: "bell")
                }

                NotificationSection(title: "Needs Attention") {
                    if store.upcoming.isEmpty {
                        EmptyStateRow(title: "No deadlines need attention", detail: "Add a ticket watch, then run checks.")
                    } else {
                        if let first = store.upcoming.first {
                            FeaturedDeadlineCard(item: first)
                        }
                        ForEach(Array(store.upcoming.dropFirst().prefix(5))) { item in
                            UpcomingDeadlineRow(item: item)
                        }
                    }
                }

                HStack(spacing: 10) {
                    Button {
                        Task { await store.runEventWatches() }
                    } label: {
                        ProgressLabel(
                            title: store.isRunningChecks ? "Checking" : "Run Checks",
                            systemImage: "play.circle.fill",
                            isLoading: store.isRunningChecks
                        )
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(store.isRunningChecks)

                    Button {
                        selectedTab = .watch
                    } label: {
                        Label("Watches", systemImage: "ticket")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                }

                NotificationSection(title: "Recent Alerts") {
                    DisclosureGroup(isExpanded: $showsRecentAlerts) {
                        VStack(alignment: .leading, spacing: 12) {
                            if store.alerts.isEmpty {
                                EmptyStateRow(title: "No alert history yet", detail: "New reminders appear here.")
                            } else {
                                ForEach(store.alerts.prefix(10)) { alert in
                                    RowContent(
                                        title: alert.type,
                                        subtitle: chusennoteAlertText(alert),
                                        systemImage: "bell.badge"
                                    )
                                }
                            }
                        }
                        .padding(.top, 8)
                    } label: {
                        Label("\(store.alerts.count)", systemImage: "bell.badge")
                            .font(.subheadline.weight(.semibold))
                    }
                }
            }
            .padding(20)
            .padding(.bottom, 150)
        }
        .background(Color(.systemGroupedBackground))
        .navigationTitle("Notifications")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    Task { await store.refresh() }
                } label: {
                    ProgressLabel(
                        title: store.isRefreshing ? "Refreshing" : "Refresh",
                        systemImage: "arrow.clockwise",
                        isLoading: store.isRefreshing
                    )
                }
                .disabled(store.isRefreshing)
            }
        }
    }
}

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

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 16) {
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

                NotificationSection(title: "Ticket Timelines") {
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

                NotificationSection(title: "Active Watches") {
                    if store.trackedEvents.isEmpty {
                        EmptyStateRow(title: "No active ticket watches", detail: "Add a watch below.")
                    } else {
                        ForEach(store.trackedEvents) { watch in
                            WatchRow(watch: watch, actionTitle: "Remove") {
                                Task { await store.removeWatch(id: watch.id) }
                            }
                        }
                    }
                }

                NotificationSection(title: "Add") {
                    DisclosureGroup(isExpanded: $showsAddWatch) {
                        VStack(alignment: .leading, spacing: 12) {
                            AppTextField("Event keyword", text: $eventKeyword)
                            AppTextField("Tags", text: $eventTags)
                            AppTextField("Preferred regions", text: $eventRegions)
                            AppTextField("Preferred venues", text: $eventVenues)

                            DisclosureGroup(isExpanded: $showsReminderOptions) {
                                VStack(alignment: .leading, spacing: 8) {
                                    AlertPresetToggle(title: "Official page found", key: "new_official_page", alerts: $eventAlerts)
                                    AlertPresetToggle(title: "Ticket link found", key: "new_ticket_link", alerts: $eventAlerts)
                                    AlertPresetToggle(title: "New lottery rounds", key: "new_lottery_round", alerts: $eventAlerts)
                                    AlertPresetToggle(title: "Ticket details changed", key: "ticket_field_changed", alerts: $eventAlerts)
                                    AlertPresetToggle(title: "Lottery opened", key: "lottery_opened", alerts: $eventAlerts)
                                    AlertPresetToggle(title: "Closing soon", key: "lottery_closing_soon", alerts: $eventAlerts)
                                    AlertPresetToggle(title: "Results today", key: "results_today", alerts: $eventAlerts)
                                    AlertPresetToggle(title: "Payment due soon", key: "payment_due_soon", alerts: $eventAlerts)
                                    AlertPresetToggle(title: "General sale soon", key: "general_sale_soon", alerts: $eventAlerts)
                                    AlertPresetToggle(title: "Watch failed", key: "watch_failed", alerts: $eventAlerts)
                                }
                                .padding(.top, 6)
                            } label: {
                                Label("\(alertCount(eventAlerts)) reminders", systemImage: "bell.badge")
                                    .font(.subheadline.weight(.semibold))
                            }

                            HStack(spacing: 10) {
                                Button {
                                    addEventWatch()
                                } label: {
                                    Label("Add Watch", systemImage: "plus.circle.fill")
                                        .frame(maxWidth: .infinity)
                                }
                                .buttonStyle(.borderedProminent)

                                Button {
                                    addRandomEventWatch()
                                } label: {
                                    Label("Random", systemImage: "shuffle")
                                        .frame(maxWidth: .infinity)
                                }
                                .buttonStyle(.bordered)
                            }
                        }
                        .padding(.top, 8)
                    } label: {
                        Label("Ticket watch", systemImage: "plus.circle")
                            .font(.subheadline.weight(.semibold))
                    }
                }

                NotificationSection(title: "Find") {
                    DisclosureGroup(isExpanded: $showsExactSearch) {
                        VStack(alignment: .leading, spacing: 12) {
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
                        .padding(.top, 8)
                    } label: {
                        Label("Exact event", systemImage: "magnifyingglass")
                            .font(.subheadline.weight(.semibold))
                    }
                }

                NotificationSection(title: "Muted Watches") {
                    let mutedEvents = store.mutedWatches.filter { ($0.kind ?? "event") == "event" }
                    if mutedEvents.isEmpty {
                        EmptyStateRow(title: "No muted ticket watches", detail: "Removed watches can be restored here.")
                    } else {
                        ForEach(mutedEvents) { watch in
                            WatchRow(watch: watch, actionTitle: "Restore") {
                                Task { await store.restoreWatch(id: watch.id) }
                            }
                        }
                    }
                }
            }
            .padding(20)
            .padding(.bottom, 150)
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
        eventKeyword = ""
        eventTags = ""
        eventRegions = ""
        eventVenues = ""
        eventAlerts = defaultAlertPreferenceText()
        Task { await store.addWatch(keyword: keyword, kind: "event", tags: tags, regions: regions, venues: venues, alerts: alerts) }
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
            LazyVStack(alignment: .leading, spacing: 16) {
                NotificationSection(title: "Server") {
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
                            .foregroundStyle(.red)
                    }

                    if let health = store.health {
                        Text("Server \(health.status): \(health.trackedEvents) watched events, \(health.alerts) alerts")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }

                NotificationSection(title: "Notifications") {
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

                NotificationSection(title: "Notification Feed") {
                    DisclosureGroup(isExpanded: $showsNotificationFeed) {
                        VStack(alignment: .leading, spacing: 12) {
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
                        .padding(.top, 8)
                    } label: {
                        Label("\(store.notificationFeed.count)", systemImage: "bell.badge")
                            .font(.subheadline.weight(.semibold))
                    }
                }

                NotificationSection(title: "Subscriptions") {
                    DisclosureGroup(isExpanded: $showsSubscriptions) {
                        VStack(alignment: .leading, spacing: 12) {
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
                        .padding(.top, 8)
                    } label: {
                        Label("\(store.subscriptions.count)", systemImage: "checkmark.circle")
                            .font(.subheadline.weight(.semibold))
                    }
                }

                NotificationSection(title: "Manual Sources") {
                    DisclosureGroup(isExpanded: $showsManualSources) {
                        VStack(alignment: .leading, spacing: 12) {
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
                        .padding(.top, 8)
                    } label: {
                        Label("\(store.activeSources.count)", systemImage: "link")
                            .font(.subheadline.weight(.semibold))
                    }
                }

                NotificationSection(title: "Tracked Artists") {
                    DisclosureGroup(isExpanded: $showsArtists) {
                        VStack(alignment: .leading, spacing: 12) {
                            AppTextField("Artist", text: $artistKeyword)
                            HStack(spacing: 10) {
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
                                    WatchRow(watch: watch, actionTitle: "Remove") {
                                        Task { await store.removeWatch(id: watch.id) }
                                    } secondaryActionTitle: {
                                        "Notify"
                                    } secondaryAction: {
                                        Task { await store.addSubscription(watch: "\(watch.id)", scope: "artist_all") }
                                    }
                                }
                            }
                        }
                        .padding(.top, 8)
                    } label: {
                        Label("\(store.trackedArtists.count)", systemImage: "music.mic")
                            .font(.subheadline.weight(.semibold))
                    }
                }

                NotificationSection(title: "Other Watches") {
                    let mutedOtherWatches = store.mutedWatches.filter { ($0.kind ?? "event") != "event" }
                    DisclosureGroup(isExpanded: $showsOtherWatches) {
                        VStack(alignment: .leading, spacing: 12) {
                            if mutedOtherWatches.isEmpty {
                                EmptyStateRow(title: "No other watches", detail: "Ticket watches live on Watch.")
                            } else {
                                ForEach(mutedOtherWatches) { watch in
                                    WatchRow(watch: watch, actionTitle: "Restore") {
                                        Task { await store.restoreWatch(id: watch.id) }
                                    }
                                }
                            }
                        }
                        .padding(.top, 8)
                    } label: {
                        Label("\(mutedOtherWatches.count)", systemImage: "eye.slash")
                            .font(.subheadline.weight(.semibold))
                    }
                }

                NotificationSection(title: "Artist Event Info") {
                    let artistEvents = store.events.filter { ($0.watchKind ?? "event") == "artist" }
                    DisclosureGroup(isExpanded: $showsArtistEvents) {
                        VStack(alignment: .leading, spacing: 12) {
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
                        .padding(.top, 8)
                    } label: {
                        Label("\(artistEvents.count)", systemImage: "music.mic")
                            .font(.subheadline.weight(.semibold))
                    }
                }

                NotificationSection(title: "Muted Sources") {
                    DisclosureGroup(isExpanded: $showsMutedSources) {
                        VStack(alignment: .leading, spacing: 12) {
                            if store.mutedSources.isEmpty {
                                EmptyStateRow(title: "No muted sources", detail: "Removed sources appear here.")
                            } else {
                                ForEach(store.mutedSources) { source in
                                    SourceRow(source: source, actionTitle: "Restore") {
                                        Task { await store.restoreSource(id: source.id) }
                                    }
                                }
                            }
                        }
                        .padding(.top, 8)
                    } label: {
                        Label("\(store.mutedSources.count)", systemImage: "link.badge.plus")
                            .font(.subheadline.weight(.semibold))
                    }
                }
            }
            .padding(20)
            .padding(.bottom, 150)
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

final class NotificationPermission: ObservableObject {
    @Published private(set) var status: UNAuthorizationStatus = .notDetermined

    var isEnabled: Bool {
        switch status {
        case .authorized, .provisional, .ephemeral:
            return true
        case .denied, .notDetermined:
            return false
        @unknown default:
            return false
        }
    }

    var title: String {
        switch status {
        case .authorized:
            return "Notifications are on"
        case .provisional:
            return "Quiet notifications are on"
        case .ephemeral:
            return "Temporary notifications are on"
        case .denied:
            return "Notifications are off"
        case .notDetermined:
            return "Notifications are not enabled"
        @unknown default:
            return "Notification status unknown"
        }
    }

    var detail: String {
        switch status {
        case .authorized, .provisional, .ephemeral:
            return "Ticket reminders can appear on this iPhone."
        case .denied:
            return "Enable alerts in iOS Settings to receive reminders."
        case .notDetermined:
            return "Turn on alerts to receive lottery, result, payment, and sale reminders."
        @unknown default:
            return "Open Settings if reminders are not arriving."
        }
    }

    var actionTitle: String {
        status == .denied ? "Open iOS Settings" : "Enable Notifications"
    }

    var actionIcon: String {
        status == .denied ? "gearshape" : "bell.badge.fill"
    }

    var canAct: Bool {
        !isEnabled
    }

    func refresh() {
        UNUserNotificationCenter.current().getNotificationSettings { settings in
            DispatchQueue.main.async {
                self.status = settings.authorizationStatus
            }
        }
    }

    @MainActor
    func performAction() {
        if status == .denied {
            guard let url = URL(string: UIApplication.openSettingsURLString) else { return }
            UIApplication.shared.open(url)
            return
        }
        PushNotifications.requestAuthorization {
            self.refresh()
        }
    }
}

struct NotificationPermissionCard: View {
    @ObservedObject var permission: NotificationPermission
    var compact = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 12) {
                Image(systemName: permission.isEnabled ? "bell.badge.fill" : "bell.slash")
                    .font(.title3)
                    .foregroundStyle(permission.isEnabled ? .green : .blue)
                    .frame(width: iconFrame, height: iconFrame)
                    .background((permission.isEnabled ? Color.green : Color.blue).opacity(0.12))
                    .clipShape(Circle())

                VStack(alignment: .leading, spacing: 3) {
                    Text(permission.title)
                        .font(.subheadline.weight(.semibold))
                    Text(permission.detail)
                        .font(.caption)
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
        .padding(cardPadding)
        .background(Color(.secondarySystemGroupedBackground))
        .clipShape(RoundedRectangle(cornerRadius: sectionCornerRadius, style: .continuous))
    }
}

struct NotificationHero: View {
    @ObservedObject var store: ChusennoteStore

    var body: some View {
        HStack(spacing: 14) {
            Image(systemName: "bell.badge.fill")
                .font(.title2)
                .foregroundStyle(.blue)
                .frame(width: 46, height: 46)
                .background(Color.blue.opacity(0.12))
                .clipShape(Circle())

            VStack(alignment: .leading, spacing: 6) {
                Text("Ticket Alerts")
                    .font(.title3.weight(.semibold))
                HStack(spacing: 6) {
                    Circle()
                        .fill(statusColor)
                        .frame(width: 8, height: 8)
                    Text(statusText)
                        .font(.caption.weight(.medium))
                        .foregroundStyle(.secondary)
                }
            }

            Spacer(minLength: 0)
        }
        .padding(cardPadding)
        .background(.regularMaterial)
        .clipShape(RoundedRectangle(cornerRadius: sectionCornerRadius, style: .continuous))
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

    private var statusColor: Color {
        if store.errorMessage != nil {
            return .red
        }
        return store.health == nil ? .secondary : .green
    }
}

struct MetricTile: View {
    let title: String
    let value: String
    let systemImage: String

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Image(systemName: systemImage)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(.blue)
                .frame(width: 22, height: 22)
            Text(value)
                .font(.title3.weight(.semibold))
            Text(title)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .minimumScaleFactor(0.8)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(Color(.secondarySystemGroupedBackground))
        .clipShape(RoundedRectangle(cornerRadius: sectionCornerRadius, style: .continuous))
    }
}

struct ProgressLabel: View {
    let title: String
    let systemImage: String
    let isLoading: Bool

    var body: some View {
        HStack(spacing: 8) {
            if isLoading {
                ProgressView()
            } else {
                Image(systemName: systemImage)
            }
            Text(title)
        }
    }
}

struct NotificationSection<Content: View>: View {
    let title: String
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Image(systemName: sectionIcon(title))
                    .font(.caption.weight(.bold))
                    .foregroundStyle(.blue)
                    .frame(width: 24, height: 24)
                    .background(Color.blue.opacity(0.1))
                    .clipShape(Circle())

                Text(title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.secondary)
            }
            VStack(alignment: .leading, spacing: 12) {
                content
            }
            .padding(cardPadding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(.secondarySystemGroupedBackground))
            .clipShape(RoundedRectangle(cornerRadius: sectionCornerRadius, style: .continuous))
        }
    }
}

struct WatchRow: View {
    let watch: Watch
    let actionTitle: String
    let action: () -> Void
    var secondaryActionTitle: (() -> String)? = nil
    var secondaryAction: (() -> Void)? = nil

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            RowContent(
                title: watch.keyword,
                subtitle: chusennoteWatchText(watch),
                systemImage: "ticket"
            )
            VStack(spacing: 8) {
                if let secondaryActionTitle, let secondaryAction {
                    IconActionButton(
                        title: secondaryActionTitle(),
                        systemImage: actionIcon(secondaryActionTitle()),
                        prominent: true,
                        action: secondaryAction
                    )
                }
                IconActionButton(title: actionTitle, systemImage: actionIcon(actionTitle), action: action)
            }
        }
    }
}

struct SourceRow: View {
    let source: WatchSource
    let actionTitle: String
    let action: () -> Void
    @Environment(\.openURL) private var openURL

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            RowContent(
                title: source.label.isEmpty ? source.url : source.label,
                subtitle: "Watch #\(source.watchId) - \(sourceMode(source))\n\(source.url)",
                systemImage: "link"
            )
            VStack(spacing: 8) {
                if let url = chusennoteWebURL(source.url) {
                    IconActionButton(title: "Open", systemImage: "arrow.up.right") {
                        openURL(url)
                    }
                }
                if !actionTitle.isEmpty {
                    IconActionButton(title: actionTitle, systemImage: actionIcon(actionTitle), action: action)
                }
            }
        }
    }
}

struct SearchResultRow: View {
    let result: SearchResult
    let action: () -> Void
    @Environment(\.openURL) private var openURL

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            RowContent(
                title: result.title.isEmpty ? result.url : result.title,
                subtitle: [result.url, result.snippet].filter { !$0.isEmpty }.joined(separator: "\n"),
                systemImage: "magnifyingglass"
            )
            VStack(spacing: 8) {
                if let url = chusennoteWebURL(result.url) {
                    IconActionButton(title: "Open", systemImage: "arrow.up.right") {
                        openURL(url)
                    }
                }
                IconActionButton(title: "Add", systemImage: "plus", prominent: true, action: action)
            }
        }
    }
}

struct TicketLinkRow: View {
    let link: TicketLink
    @Environment(\.openURL) private var openURL

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            RowContent(
                title: link.label?.isEmpty == false ? link.label! : (link.platform ?? "Ticket link"),
                subtitle: ticketLinkText(link),
                systemImage: "ticket"
            )
            if let url = chusennoteWebURL(link.url) {
                IconActionButton(title: "Open", systemImage: "arrow.up.right") {
                    openURL(url)
                }
            }
        }
    }
}

struct InfoTextRow: View {
    let text: String
    let systemImage: String

    var body: some View {
        RowContent(title: text, subtitle: "", systemImage: systemImage)
    }
}

struct FeaturedDeadlineCard: View {
    let item: UpcomingItem
    @Environment(\.openURL) private var openURL

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: "clock.badge.exclamationmark")
                    .font(.headline.weight(.bold))
                    .foregroundStyle(.white)
                    .frame(width: 42, height: 42)
                    .background(Color.orange)
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

            HStack(spacing: 8) {
                DashboardChip(
                    title: item.relevantDate ?? "Date TBA",
                    systemImage: "calendar",
                    tint: .orange
                )
                DashboardChip(
                    title: item.platform ?? "Ticket",
                    systemImage: "ticket",
                    tint: .blue
                )
            }

            if let url = chusennoteWebURL(item.url) {
                IconActionButton(title: "Open", systemImage: "arrow.up.right", prominent: true) {
                    openURL(url)
                }
            }
        }
        .padding(cardPadding)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.orange.opacity(0.1))
        .clipShape(RoundedRectangle(cornerRadius: sectionCornerRadius, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: sectionCornerRadius, style: .continuous)
                .stroke(Color.orange.opacity(0.22), lineWidth: 1)
        )
    }
}

struct UpcomingDeadlineRow: View {
    let item: UpcomingItem
    @Environment(\.openURL) private var openURL

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            RowContent(
                title: item.eventTitle ?? "Untitled event",
                subtitle: upcomingStatusText(item),
                systemImage: "calendar.badge.clock"
            )
            if let url = chusennoteWebURL(item.url) {
                IconActionButton(title: "Open", systemImage: "arrow.up.right") {
                    openURL(url)
                }
            }
        }
    }
}

struct DashboardChip: View {
    let title: String
    let systemImage: String
    let tint: Color

    var body: some View {
        Label(title, systemImage: systemImage)
            .font(.caption.weight(.bold))
            .lineLimit(1)
            .minimumScaleFactor(0.8)
            .foregroundStyle(tint)
            .padding(.horizontal, 10)
            .padding(.vertical, 8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(tint.opacity(0.12))
            .clipShape(Capsule())
    }
}

struct EventTimelineRow: View {
    let event: EventSummary

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: "ticket")
                .font(.subheadline.weight(.semibold))
                .frame(width: iconFrame, height: iconFrame)
                .foregroundStyle(.blue)
                .background(Color.blue.opacity(0.1))
                .clipShape(Circle())

            VStack(alignment: .leading, spacing: 8) {
                Text(event.title ?? "Untitled event")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.primary)
                    .lineLimit(2)

                Text("\(event.status ?? "watching") - \(event.rounds.count) rounds")
                    .font(.caption)
                    .foregroundStyle(.secondary)

                if event.rounds.isEmpty {
                    Text("No lottery rounds yet")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    TicketRoundGroupList(
                        rounds: event.rounds,
                        showsActions: false,
                        isCompact: true,
                        maxRoundsPerGroup: 2
                    )
                }
            }
            Spacer(minLength: 0)
            Image(systemName: "chevron.right")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.tertiary)
        }
    }
}

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
        LazyVStack(alignment: .leading, spacing: isCompact ? 8 : 10) {
            ForEach(ticketRoundGroups(rounds)) { group in
                VStack(alignment: .leading, spacing: 6) {
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
                            .padding(.vertical, 6)
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
        VStack(alignment: .leading, spacing: isCompact ? 8 : 12) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "ticket.fill")
                    .font(.caption.weight(.bold))
                    .foregroundStyle(.white)
                    .frame(width: isCompact ? 26 : 32, height: isCompact ? 26 : 32)
                    .background(ticketRoundAccent(round))
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
                HStack(spacing: 8) {
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
        .padding(isCompact ? 10 : 14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: isCompact ? 18 : 26, style: .continuous)
                .fill(Color(.tertiarySystemGroupedBackground))
        )
        .overlay(
            RoundedRectangle(cornerRadius: isCompact ? 18 : 26, style: .continuous)
                .stroke(ticketRoundAccent(round).opacity(0.25), lineWidth: 1)
        )
        .shadow(color: Color.black.opacity(isCompact ? 0 : 0.04), radius: isCompact ? 0 : 8, x: 0, y: 3)
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
            LazyVGrid(columns: [GridItem(.adaptive(minimum: isCompact ? 112 : 132), spacing: 8)], alignment: .leading, spacing: 8) {
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
                .font((isCompact ? Font.caption2 : Font.caption2).weight(.bold))
                .textCase(.uppercase)
                .foregroundStyle(ticketDateColor(date.kind))
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
        .background(ticketDateColor(date.kind).opacity(0.12))
        .clipShape(Capsule())
        .overlay(
            Capsule()
                .stroke(ticketDateColor(date.kind).opacity(0.22), lineWidth: 1)
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

struct ArtistEventRow: View {
    let event: EventSummary

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            RowContent(
                title: event.title ?? "Untitled event",
                subtitle: [event.status, event.eventDates?.prefix(2).joined(separator: "; "), event.venueLabel].compactMap { $0 }.joined(separator: " - "),
                systemImage: "music.mic"
            )
            Image(systemName: "chevron.right")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.tertiary)
        }
    }
}

struct AlertPresetToggle: View {
    let title: String
    let key: String
    @Binding var alerts: String

    var body: some View {
        Toggle(title, isOn: Binding(
            get: {
                alertKeys(alerts).contains(key)
            },
            set: { isOn in
                var keys = alertKeys(alerts)
                if isOn {
                    keys.insert(key)
                } else {
                    keys.remove(key)
                }
                alerts = orderedAlertKeys(keys).joined(separator: ",")
            }
        ))
    }
}

struct LinkRow: View {
    let title: String
    let subtitle: String
    let systemImage: String
    let url: URL?
    @Environment(\.openURL) private var openURL

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            RowContent(title: title, subtitle: subtitle, systemImage: systemImage)
            if let url {
                IconActionButton(title: "Open", systemImage: "arrow.up.right") {
                    openURL(url)
                }
            }
        }
    }
}

struct SubscriptionRow: View {
    let subscription: NotificationSubscription
    let watch: Watch?
    let action: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            RowContent(
                title: subscriptionTitle(subscription, watch: watch),
                subtitle: subscriptionText(subscription),
                systemImage: "bell.badge"
            )
            IconActionButton(title: "Remove", systemImage: "trash", action: action)
        }
    }
}

struct DeviceRow: View {
    let device: DeviceToken

    var body: some View {
        RowContent(
            title: device.label.isEmpty ? "\(device.platform) device" : device.label,
            subtitle: "Device #\(device.id)\n\(maskedToken(device.token))",
            systemImage: "iphone"
        )
    }
}

struct RowContent: View {
    let title: String
    let subtitle: String
    let systemImage: String

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: systemImage)
                .font(.subheadline.weight(.semibold))
                .frame(width: iconFrame, height: iconFrame)
                .foregroundStyle(.blue)
                .background(Color.blue.opacity(0.1))
                .clipShape(Circle())
            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.primary)
                    .lineLimit(1)
                if !subtitle.isEmpty {
                    Text(subtitle)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }
            }
            Spacer(minLength: 0)
        }
    }
}

struct EmptyStateRow: View {
    let title: String
    let detail: String
    var systemImage: String = "tray"

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: systemImage)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(.secondary)
                .frame(width: iconFrame, height: iconFrame)
                .background(Color(.tertiarySystemGroupedBackground))
                .clipShape(Circle())

            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(.subheadline.weight(.semibold))
                    .lineLimit(1)
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .minimumScaleFactor(0.85)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct IconActionButton: View {
    let title: String
    let systemImage: String
    var prominent = false
    let action: () -> Void

    var body: some View {
        if prominent {
            Button(action: action) {
                icon
            }
            .buttonStyle(.borderedProminent)
            .accessibilityLabel(title)
        } else {
            Button(action: action) {
                icon
            }
            .buttonStyle(.bordered)
            .accessibilityLabel(title)
        }
    }

    private var icon: some View {
        Image(systemName: systemImage)
            .font(.subheadline.weight(.semibold))
            .frame(width: actionIconFrame, height: actionIconFrame)
    }
}

struct AppTextField: View {
    let title: String
    @Binding var text: String

    init(_ title: String, text: Binding<String>) {
        self.title = title
        self._text = text
    }

    var body: some View {
        TextField(title, text: $text)
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            .background(Color(.tertiarySystemGroupedBackground))
            .clipShape(RoundedRectangle(cornerRadius: compactCornerRadius, style: .continuous))
    }
}

struct EventSummaryPanel<Actions: View>: View {
    let event: EventSummary
    @ViewBuilder var actions: Actions

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            RowContent(
                title: event.title ?? "Untitled event",
                subtitle: event.status ?? "watching",
                systemImage: "ticket"
            )

            HStack(spacing: 8) {
                EventInfoChip(
                    title: event.eventDates?.first ?? "Date TBA",
                    systemImage: "calendar",
                    tint: .blue
                )
                EventInfoChip(
                    title: event.venueLabel?.isEmpty == false ? event.venueLabel! : "Venue TBA",
                    systemImage: "mappin.and.ellipse",
                    tint: .green
                )
            }

            actions
        }
    }
}

struct EventInfoChip: View {
    let title: String
    let systemImage: String
    let tint: Color

    var body: some View {
        Label(title, systemImage: systemImage)
            .font(.caption.weight(.semibold))
            .lineLimit(1)
            .minimumScaleFactor(0.8)
            .padding(.horizontal, 10)
            .padding(.vertical, 8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .foregroundStyle(tint)
            .background(tint.opacity(0.12))
            .clipShape(Capsule())
    }
}

struct CollapsibleTextSection: View {
    let title: String
    @Binding var isExpanded: Bool
    let emptyTitle: String
    let emptyDetail: String
    let items: [String]
    let systemImage: String

    var body: some View {
        NotificationSection(title: title) {
            DisclosureGroup(isExpanded: $isExpanded) {
                VStack(alignment: .leading, spacing: 12) {
                    if items.isEmpty {
                        EmptyStateRow(title: emptyTitle, detail: emptyDetail)
                    } else {
                        ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                            InfoTextRow(text: item, systemImage: systemImage)
                        }
                    }
                }
                .padding(.top, 8)
            } label: {
                Label("\(items.count)", systemImage: systemImage)
                    .font(.subheadline.weight(.semibold))
            }
        }
    }
}

struct CollapsibleEventListSection<Item: Identifiable, Content: View>: View {
    let title: String
    @Binding var isExpanded: Bool
    let emptyTitle: String
    let emptyDetail: String
    let items: [Item]
    @ViewBuilder var content: (Item) -> Content

    var body: some View {
        NotificationSection(title: title) {
            DisclosureGroup(isExpanded: $isExpanded) {
                VStack(alignment: .leading, spacing: 12) {
                    if items.isEmpty {
                        EmptyStateRow(title: emptyTitle, detail: emptyDetail)
                    } else {
                        ForEach(items) { item in
                            content(item)
                        }
                    }
                }
                .padding(.top, 8)
            } label: {
                Label("\(items.count)", systemImage: sectionIcon(title))
                    .font(.subheadline.weight(.semibold))
            }
        }
    }
}

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
            LazyVStack(alignment: .leading, spacing: 16) {
                NotificationSection(title: "Event") {
                    EventSummaryPanel(event: event) {
                        HStack(spacing: 8) {
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

                NotificationSection(title: "Ticket Rounds") {
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

                NotificationSection(title: "Context") {
                    DisclosureGroup(isExpanded: $showsContext) {
                        VStack(alignment: .leading, spacing: 10) {
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
                        .padding(.top, 8)
                    } label: {
                        Label("More event info", systemImage: "info.circle")
                            .font(.subheadline.weight(.semibold))
                    }
                }

                CollapsibleEventListSection(
                    title: "Ticket Links",
                    isExpanded: $showsLinks,
                    emptyTitle: "No ticket links saved",
                    emptyDetail: "Links found on official pages appear here.",
                    items: event.ticketLinks ?? []
                ) { link in
                    TicketLinkRow(link: link)
                }

                CollapsibleTextSection(
                    title: "Ticket Rules",
                    isExpanded: $showsRules,
                    emptyTitle: "No ticket rules captured",
                    emptyDetail: "Rules from official pages appear here.",
                    items: event.ticketRules ?? [],
                    systemImage: "checklist"
                )

                CollapsibleTextSection(
                    title: "Ticket Price",
                    isExpanded: $showsPrices,
                    emptyTitle: "No ticket prices captured",
                    emptyDetail: "Price notes from official pages appear here.",
                    items: event.ticketPrices ?? [],
                    systemImage: "yensign.circle"
                )

                CollapsibleEventListSection(
                    title: "Manual Sources",
                    isExpanded: $showsSources,
                    emptyTitle: "No manual sources",
                    emptyDetail: "Attached source links appear here.",
                    items: event.manualSources ?? []
                ) { source in
                    SourceRow(source: source, actionTitle: "") {}
                }
            }
            .padding(20)
            .padding(.bottom, 150)
        }
        .background(Color(.systemGroupedBackground))
        .navigationTitle("Event")
        .navigationBarTitleDisplayMode(.inline)
    }
}

private func trimmed(_ value: String) -> String {
    value.trimmingCharacters(in: .whitespacesAndNewlines)
}

private func alertKeys(_ value: String) -> Set<String> {
    Set(value.split(separator: ",").map { trimmed(String($0)) }.filter { !$0.isEmpty })
}

private func orderedAlertKeys(_ keys: Set<String>) -> [String] {
    defaultAlertKeyOrder().filter { keys.contains($0) }
}

private func defaultAlertKeyOrder() -> [String] {
    [
        "new_official_page",
        "new_ticket_link",
        "new_lottery_round",
        "ticket_field_changed",
        "lottery_opened",
        "lottery_closing_soon",
        "results_today",
        "payment_due_soon",
        "general_sale_soon",
        "watch_failed"
    ]
}

private func defaultAlertPreferenceText() -> String {
    defaultAlertKeyOrder().joined(separator: ",")
}

private func alertCount(_ value: String) -> Int {
    alertKeys(value).count
}

private func sectionIcon(_ title: String) -> String {
    let lowercased = title.lowercased()
    if lowercased.contains("server") {
        return "server.rack"
    }
    if lowercased == "add" {
        return "plus"
    }
    if lowercased == "find" || lowercased.contains("search") {
        return "magnifyingglass"
    }
    if lowercased.contains("notification") || lowercased.contains("alert") {
        return "bell.badge"
    }
    if lowercased.contains("ticket") {
        return "ticket"
    }
    if lowercased.contains("watch") {
        return "eye"
    }
    if lowercased.contains("source") || lowercased.contains("link") {
        return "link"
    }
    if lowercased.contains("artist") {
        return "music.mic"
    }
    if lowercased.contains("subscription") {
        return "checkmark.circle"
    }
    if lowercased.contains("price") {
        return "yensign.circle"
    }
    if lowercased.contains("rule") {
        return "checklist"
    }
    return "sparkle.magnifyingglass"
}

private func actionIcon(_ title: String) -> String {
    switch title.lowercased() {
    case let value where value.contains("remove"):
        return "trash"
    case let value where value.contains("restore"):
        return "arrow.uturn.backward"
    case let value where value.contains("notify"):
        return "bell.badge"
    case let value where value.contains("add"):
        return "plus"
    default:
        return "ellipsis"
    }
}

private func chusennoteWatchText(_ watch: Watch) -> String {
    [
        emptyFallback(watch.preferredRegions),
        emptyFallback(watch.preferredVenues),
        readableAlertText(watch.alertPreferences),
        watch.lastCheckedAt ?? "never"
    ].joined(separator: "\n")
}

private func readableAlertText(_ value: String?) -> String {
    let labels = [
        "new_official_page": "official page",
        "new_ticket_link": "ticket link",
        "new_lottery_round": "new rounds",
        "ticket_field_changed": "ticket changes",
        "lottery_opened": "lottery opened",
        "lottery_closing_soon": "closing soon",
        "results_today": "results today",
        "payment_due_soon": "payment due",
        "general_sale_soon": "general sale",
        "watch_failed": "watch failed"
    ]
    let keys = orderedAlertKeys(alertKeys(value ?? ""))
    guard !keys.isEmpty else { return "none" }
    return keys.map { labels[$0] ?? $0 }.joined(separator: ", ")
}

private func ticketRoundTitle(_ round: TicketRound) -> String {
    round.name?.isEmpty == false ? round.name! : "Ticket round"
}

private func ticketRoundStatusText(_ round: TicketRound) -> String {
    [
        round.platform?.isEmpty == false ? round.platform : "unknown",
        round.statusLabel?.isEmpty == false ? round.statusLabel : round.status
    ]
        .compactMap { $0 }
        .filter { !$0.isEmpty }
        .joined(separator: " - ")
}

private func ticketRoundGroups(_ rounds: [TicketRound]) -> [TicketRoundGroup] {
    let grouped = Dictionary(grouping: rounds, by: ticketRoundWebsite)
    return grouped.map { website, rounds in
        TicketRoundGroup(website: website, rounds: sortTicketRoundsNewestFirst(rounds))
    }
    .sorted { first, second in
        let firstDate = first.rounds.map(ticketRoundSortKey).max() ?? ""
        let secondDate = second.rounds.map(ticketRoundSortKey).max() ?? ""
        if firstDate == secondDate {
            return first.website.localizedCaseInsensitiveCompare(second.website) == .orderedAscending
        }
        return firstDate > secondDate
    }
}

private func limitedTicketRounds(_ rounds: [TicketRound], max: Int?) -> [TicketRound] {
    guard let max else { return rounds }
    return Array(rounds.prefix(max))
}

private func sortTicketRoundsNewestFirst(_ rounds: [TicketRound]) -> [TicketRound] {
    rounds.sorted { first, second in
        let firstDate = ticketRoundSortKey(first)
        let secondDate = ticketRoundSortKey(second)
        if firstDate == secondDate {
            return ticketRoundTitle(first).localizedCaseInsensitiveCompare(ticketRoundTitle(second)) == .orderedAscending
        }
        return firstDate > secondDate
    }
}

private func ticketRoundWebsite(_ round: TicketRound) -> String {
    if let platform = round.platform, !platform.isEmpty {
        return platform
    }
    if let url = chusennoteWebURL(round.url), let host = url.host, !host.isEmpty {
        return host.replacingOccurrences(of: "www.", with: "")
    }
    return "Unknown website"
}

private func ticketRoundSortKey(_ round: TicketRound) -> String {
    ticketRoundDateValues(round).max() ?? ""
}

private func ticketRoundDateValues(_ round: TicketRound) -> [String] {
    [
        round.applicationStartAt,
        round.applicationEndAt,
        round.resultsDate,
        round.generalSaleDate,
        round.paymentEndAt
    ]
        .compactMap { $0 }
        .filter { !$0.isEmpty }
}

private func ticketRoundDateItems(_ round: TicketRound) -> [TicketRoundDateItem] {
    [
        ticketRoundDateItem("Apply opens", round.applicationStartAt, kind: .apply),
        ticketRoundDateItem("Apply closes", round.applicationEndAt, kind: .apply),
        ticketRoundDateItem("Results", round.resultsDate, kind: .result),
        ticketRoundDateItem("Payment due", round.paymentEndAt, kind: .payment),
        ticketRoundDateItem("General sale", round.generalSaleDate, kind: .sale)
    ]
        .compactMap { $0 }
}

private func ticketRoundDateItem(_ label: String, _ value: String?, kind: TicketRoundDateKind) -> TicketRoundDateItem? {
    guard let value, !value.isEmpty else { return nil }
    return TicketRoundDateItem(label: label, value: value, kind: kind)
}

private func ticketRoundAccent(_ round: TicketRound) -> Color {
    if round.paymentEndAt?.isEmpty == false {
        return .orange
    }
    if round.resultsDate?.isEmpty == false {
        return .purple
    }
    if round.generalSaleDate?.isEmpty == false {
        return .green
    }
    return .blue
}

private func ticketDateColor(_ kind: TicketRoundDateKind) -> Color {
    switch kind {
    case .apply:
        return .blue
    case .result:
        return .purple
    case .payment:
        return .orange
    case .sale:
        return .green
    }
}

private func ticketRoundDateText(_ round: TicketRound) -> String {
    [
        labeledRoundDate("Apply opens", round.applicationStartAt),
        labeledRoundDate("Apply closes", round.applicationEndAt),
        labeledRoundDate("Results", round.resultsDate),
        labeledRoundDate("Payment due", round.paymentEndAt),
        labeledRoundDate("General sale", round.generalSaleDate)
    ]
        .compactMap { $0 }
        .joined(separator: "\n")
}

private func ticketRoundScheduleText(_ round: TicketRound) -> String {
    guard let schedule = round.scheduleLabel, !schedule.isEmpty else { return "" }
    return schedule
}

private func labeledRoundDate(_ label: String, _ value: String?) -> String? {
    guard let value, !value.isEmpty else { return nil }
    return "\(label): \(value)"
}

private func emptyFallback(_ value: String?) -> String {
    guard let value, !value.isEmpty else { return "none" }
    return value
}

private func sourceMode(_ source: WatchSource) -> String {
    source.privateNote ? "private note" : source.platform
}

private func chusennoteAlertText(_ alert: AlertPayload) -> String {
    var parts: [String] = []
    for value in [alert.event, alert.eventTitle, alert.keyword, alert.round].compactMap({ $0 }).filter({ !$0.isEmpty }) {
        if !parts.contains(value) {
            parts.append(value)
        }
    }
    if let eventId = alert.eventId {
        parts.append("Event #\(eventId)")
    }
    if let watchKeyword = alert.watchKeyword, !watchKeyword.isEmpty {
        let kind = alert.watchKind ?? "watch"
        let muted = alert.watchMuted == true ? " muted" : ""
        parts.append("\(kind) \(watchKeyword)\(muted)")
    } else if let watchId = alert.watchId {
        parts.append("Watch #\(watchId)")
    }
    return parts.joined(separator: " ")
}

private func upcomingStatusText(_ item: UpcomingItem) -> String {
    [
        item.statusLabel?.isEmpty == false ? item.statusLabel : item.status,
        item.roundName,
        item.relevantDate
    ]
        .compactMap { $0 }
        .filter { !$0.isEmpty }
        .joined(separator: " - ")
}

private func watchForSubscription(_ subscription: NotificationSubscription, in watches: [Watch]) -> Watch? {
    watches.first { $0.id == subscription.watchId }
}

private func subscriptionTitle(_ subscription: NotificationSubscription, watch: Watch?) -> String {
    let keyword = watch?.keyword ?? "Watch #\(subscription.watchId)"
    return "\(subscriptionScopeLabel(subscription.scope)) - \(keyword)"
}

private func subscriptionText(_ subscription: NotificationSubscription) -> String {
    [
        subscription.location.isEmpty ? nil : "Location: \(subscription.location)",
        subscription.roundKey.isEmpty ? nil : "Round: \(subscription.roundKey)",
        "Channels: \(subscription.channels)",
        "Lead days: \(subscription.leadDays)",
        subscription.enabled ? "Enabled" : "Disabled"
    ]
        .compactMap { $0 }
        .joined(separator: "\n")
}

private func subscriptionScopeLabel(_ scope: String) -> String {
    switch scope {
    case "artist_all":
        return "Artist"
    case "event_all":
        return "Event"
    case "event_location":
        return "Location"
    case "round":
        return "Round"
    default:
        return scope
    }
}

private func notificationFeedText(_ item: NotificationFeedItem) -> String {
    [
        item.body,
        item.eventTitle,
        item.location?.isEmpty == false ? "Location: \(item.location!)" : nil,
        item.date?.isEmpty == false ? "Date: \(item.date!)" : nil,
        item.channel?.isEmpty == false ? "Channel: \(item.channel!)" : nil,
        item.createdAt
    ]
        .compactMap { $0 }
        .filter { !$0.isEmpty }
        .joined(separator: "\n")
}

private func eventLocationTitle(_ location: EventLocation) -> String {
    if !location.city.isEmpty, !location.venue.isEmpty, location.city != location.venue {
        return "\(location.city) - \(location.venue)"
    }
    if !location.location.isEmpty {
        return location.location
    }
    return location.venue.isEmpty ? "Location" : location.venue
}

private func ticketLinkText(_ link: TicketLink) -> String {
    [
        link.platform?.isEmpty == false ? "Platform: \(link.platform!)" : nil,
        link.confidence.map { "Confidence: \($0)" },
        link.provenance?.isEmpty == false ? "Source: \(link.provenance!)" : nil,
        link.url
    ]
        .compactMap { $0 }
        .joined(separator: "\n")
}

private func maskedToken(_ token: String) -> String {
    guard token.count > 12 else { return token }
    return "\(token.prefix(6))...\(token.suffix(6))"
}

private func chusennoteWebURL(_ value: String?) -> URL? {
    guard let value, let url = URL(string: value) else { return nil }
    guard url.scheme == "http" || url.scheme == "https" else { return nil }
    return url
}
