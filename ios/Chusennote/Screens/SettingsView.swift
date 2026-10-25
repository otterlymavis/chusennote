import SwiftUI

struct SettingsView: View {
    let store: ChusennoteStore
    let notificationPermission: NotificationPermission
    // Owned by the tab root so an unsaved source survives leaving its screen.
    @State private var sourceDraft = SourceDraft()

    var body: some View {
        List {
            ErrorSection(store: store)

            Section {
                NavigationLink {
                    AccountScreen(store: store)
                } label: {
                    LabeledContent(
                        "Account",
                        value: store.isSignedIn
                            ? (store.signedInEmail.isEmpty ? "Signed in" : store.signedInEmail)
                            : "Not signed in"
                    )
                }
            }

            Section("Notifications") {
                NotificationPermissionRows(permission: notificationPermission)

                NavigationLink("Subscriptions") {
                    SubscriptionsScreen(store: store)
                }
                .badge(store.subscriptions.count)

                NavigationLink("Delivered Reminders") {
                    DeliveredRemindersScreen(store: store)
                }
                .badge(store.notificationFeed.count)
            }

            Section("Calendar") {
                CalendarFeedButton(store: store)
            }

            Section {
                NavigationLink {
                    TrackingOptionsScreen(store: store, sourceDraft: $sourceDraft)
                } label: {
                    Label("More Tracking Options", systemImage: "slider.horizontal.3")
                }
            }

            #if DEBUG
            Section("Development") {
                NavigationLink("Developer Tools") {
                    DeveloperToolsScreen(store: store)
                }
            }
            #endif
        }
        .navigationTitle("Settings")
        .refreshable {
            await store.refresh()
        }
    }
}

private struct CalendarFeedButton: View {
    let store: ChusennoteStore
    @Environment(\.openURL) private var openURL

    var body: some View {
        Button("Open Calendar Feed") {
            Task {
                if let calendarURL = await store.calendarFeedURL() {
                    openURL(calendarURL)
                }
            }
        }
    }
}

private struct TrackingOptionsScreen: View {
    let store: ChusennoteStore
    @Binding var sourceDraft: SourceDraft

    var body: some View {
        List {
            Section {
                NavigationLink("Artists") {
                    ArtistsScreen(store: store)
                }
                .badge(store.trackedArtists.count)

                NavigationLink("Manual Sources") {
                    SourcesScreen(store: store, draft: $sourceDraft)
                }
                .badge(store.activeSources.count)
            } footer: {
                Text("Discover shows by artist, or add a source that a watch has missed.")
            }
        }
        .navigationTitle("More Tracking")
        .navigationBarTitleDisplayMode(.inline)
    }
}

/// Optional account access, with the existing confirmation for permanent deletion.
private struct AccountScreen: View {
    let store: ChusennoteStore
    @State private var email = ""
    @State private var password = ""
    @State private var deletionPassword = ""
    @State private var showsDeleteConfirmation = false

    var body: some View {
        List {
            ErrorSection(store: store)

            if store.isSignedIn {
                Section("Account") {
                    TextRow(
                        title: store.signedInEmail.isEmpty ? "Signed in" : "Signed in as \(store.signedInEmail)",
                        subtitle: "Watches, sources, and reminders are scoped to this account."
                    )
                    Button(role: .destructive) {
                        Task { await store.logoutAccount() }
                    } label: {
                        ProgressLabel(
                            title: store.isAccountTransitioning ? "Signing Out" : "Log Out",
                            isLoading: store.isAccountTransitioning
                        )
                    }
                }
                .disabled(store.isAccountTransitioning)

                Section {
                    Button("Delete Account", role: .destructive) {
                        deletionPassword = ""
                        showsDeleteConfirmation = true
                    }
                }
                .disabled(store.isAccountTransitioning)
            } else {
                Section {
                    AppTextField("Email", text: $email)
                        .keyboardType(.emailAddress)
                        .textContentType(.username)
                    AppTextField("Password", text: $password, isSecure: true)
                        .textContentType(.password)
                    Button {
                        Task {
                            await store.loginAccount(email: email, password: password)
                            if store.isSignedIn { password = "" }
                        }
                    } label: {
                        ProgressLabel(
                            title: store.isAccountTransitioning ? "Signing In" : "Log In",
                            isLoading: store.isAccountTransitioning
                        )
                    }
                    Button("Register") {
                        Task {
                            await store.registerAccount(email: email, password: password)
                            if store.isSignedIn { password = "" }
                        }
                    }
                } header: {
                    Text("Account")
                } footer: {
                    Text("An account is optional. Signing in keeps your watches, sources, and reminders with you.")
                }
                .disabled(store.isAccountTransitioning)
            }
        }
        .navigationTitle("Account")
        .navigationBarTitleDisplayMode(.inline)
        .alert("Permanently Delete Account?", isPresented: $showsDeleteConfirmation) {
            SecureField("Current password", text: $deletionPassword)
            Button("Cancel", role: .cancel) {
                deletionPassword = ""
            }
            Button("Delete Account", role: .destructive) {
                let confirmation = deletionPassword
                deletionPassword = ""
                Task { await store.deleteAccount(password: confirmation) }
            }
        } message: {
            Text("This permanently deletes your account, watches, private sources, reminders, calendar tokens, and registered push devices. This cannot be undone.")
        }
    }
}

#if DEBUG
private struct DeveloperToolsScreen: View {
    @Bindable var store: ChusennoteStore

    var body: some View {
        List {
            ErrorSection(store: store)

            Section {
                AppTextField("Base URL", text: $store.baseURL)
                    .keyboardType(.URL)
                    .disabled(store.isSignedIn || store.isAccountTransitioning)
                if let health = store.health {
                    TextRow(
                        title: "Server \(health.status)",
                        subtitle: "\(health.releaseLabel) · schema \(health.schemaVersion) · \(health.trackedEvents) watched events, \(health.alerts) alerts"
                    )
                }
                Button {
                    Task { await store.refresh() }
                } label: {
                    ProgressLabel(title: store.isRefreshing ? "Refreshing" : "Refresh", isLoading: store.isRefreshing)
                }
                .disabled(store.isRefreshing || store.isAccountTransitioning)
            } header: {
                Text("Server")
            } footer: {
                if store.isSignedIn {
                    Text("Log out before changing servers.")
                }
            }

            Section("Notifications") {
                if !DeviceRegistration.firebaseMessagingConfigured {
                    TextRow(
                        title: "Push setup required",
                        subtitle: "Add GoogleService-Info.plist to enable Firebase push registration."
                    )
                }
                Button {
                    Task { await store.runNotifications() }
                } label: {
                    ProgressLabel(
                        title: store.isRunningNotifications ? "Sending Due Reminders" : "Send Due Reminders",
                        isLoading: store.isRunningNotifications
                    )
                }
                .disabled(store.isRunningNotifications)
            }

            Section("Push Devices") {
                if store.devices.isEmpty {
                    EmptyRow("No push devices registered. Enable notifications, then refresh.")
                } else {
                    ForEach(store.devices) { device in
                        TextRow(
                            title: device.label.isEmpty ? "\(device.platform) device" : device.label,
                            subtitle: "Device #\(device.id) · \(maskedToken(device.token))"
                        )
                    }
                }
            }
        }
        .navigationTitle("Developer Tools")
        .navigationBarTitleDisplayMode(.inline)
    }
}
#endif

private struct SubscriptionsScreen: View {
    let store: ChusennoteStore

    var body: some View {
        List {
            ErrorSection(store: store)

            Section {
                if store.subscriptions.isEmpty {
                    EmptyRow("No subscriptions. Tap Notify Me on an event to add one.")
                } else {
                    ForEach(store.subscriptions) { subscription in
                        ActionRow(
                            title: subscriptionTitle(subscription, watch: watchForSubscription(subscription, in: store.watches)),
                            subtitle: subscriptionText(subscription),
                            actionTitle: "Remove",
                            role: .destructive
                        ) {
                            Task { await store.removeSubscription(id: subscription.id) }
                        }
                    }
                }
            }
        }
        .navigationTitle("Subscriptions")
        .navigationBarTitleDisplayMode(.inline)
    }
}

private struct DeliveredRemindersScreen: View {
    let store: ChusennoteStore

    var body: some View {
        List {
            ErrorSection(store: store)

            if store.notificationFeed.isEmpty {
                EmptyRow("No delivered reminders yet.")
            } else {
                ForEach(store.notificationFeed.prefix(10)) { item in
                    LinkRow(
                        title: item.title ?? item.label ?? "Reminder",
                        subtitle: notificationFeedText(item),
                        url: chusennoteWebURL(item.url)
                    )
                }
            }
        }
        .navigationTitle("Delivered Reminders")
        .navigationBarTitleDisplayMode(.inline)
    }
}

private struct ArtistsScreen: View {
    let store: ChusennoteStore
    @State private var draft = WatchDraft()
    @State private var showsEditor = false

    var body: some View {
        List {
            ErrorSection(store: store)

            Section("Tracked Artists") {
                if store.trackedArtists.isEmpty {
                    EmptyRow("Tap Add to discover shows by an artist.")
                } else {
                    ForEach(store.trackedArtists) { watch in
                        MenuRow(title: watch.keyword, subtitle: chusennoteWatchText(watch)) {
                            Button {
                                Task { await store.addSubscription(watch: "\(watch.id)", scope: "artist_all") }
                            } label: {
                                Label("Notify Me", systemImage: "bell")
                            }
                            Button {
                                draft = WatchDraft(editing: watch)
                                showsEditor = true
                            } label: {
                                Label("Edit", systemImage: "pencil")
                            }
                            Button(role: .destructive) {
                                Task { await store.removeWatch(id: watch.id) }
                            } label: {
                                Label("Remove", systemImage: "trash")
                            }
                        }
                    }
                }
            }

            Section("Artist Events") {
                if store.artistEvents.isEmpty {
                    EmptyRow("No artist events yet. Check now to collect them.")
                } else {
                    ForEach(store.artistEvents) { event in
                        NavigationLink {
                            EventDetailView(store: store, event: event)
                        } label: {
                            EventRow(event: event)
                        }
                    }
                }
            }

            if !store.mutedOtherWatches.isEmpty {
                Section {
                    DisclosureGroup("Removed Watches (\(store.mutedOtherWatches.count))") {
                    ForEach(store.mutedOtherWatches) { watch in
                        ActionRow(title: watch.keyword, subtitle: chusennoteWatchText(watch), actionTitle: "Restore") {
                            Task { await store.restoreWatch(id: watch.id) }
                        }
                    }
                    }
                }
            }
        }
        .navigationTitle("Artists")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                RunChecksButton(isRunning: store.isRunningChecks) {
                    Task { await store.runArtistWatches() }
                }
            }
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    showsEditor = true
                } label: {
                    Label("Add", systemImage: "plus")
                }
                .accessibilityLabel("Add Artist")
            }
        }
        .sheet(isPresented: $showsEditor, onDismiss: {
            if draft.isEditing {
                draft = WatchDraft()
            }
        }) {
            NavigationStack {
                Form {
                    Section {
                        WatchFields(keywordTitle: "Artist", draft: $draft)
                    }
                    Section {
                        Button(draft.isEditing ? "Save Changes" : "Add Artist") {
                            let submitted = draft
                            draft = WatchDraft()
                            showsEditor = false
                            Task { await store.addWatch(submitted, kind: "artist") }
                        }
                        .disabled(!draft.canSubmit)
                    }
                }
                .navigationTitle(draft.isEditing ? "Edit Artist" : "Add Artist")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Cancel") {
                            showsEditor = false
                        }
                    }
                }
            }
        }
    }
}

private struct SourcesScreen: View {
    let store: ChusennoteStore
    @Binding var draft: SourceDraft
    @State private var showsEditor = false

    var body: some View {
        List {
            ErrorSection(store: store)

            Section("Sources") {
                if store.activeSources.isEmpty {
                    EmptyRow("Tap Add to link a page that a watch has missed.")
                } else {
                    ForEach(store.activeSources) { source in
                        SourceRow(source: source) {
                            Button(role: .destructive) {
                                Task { await store.removeSource(id: source.id) }
                            } label: {
                                Label("Remove", systemImage: "trash")
                            }
                        }
                    }
                }
            }

            if !store.mutedSources.isEmpty {
                Section {
                    DisclosureGroup("Removed Sources (\(store.mutedSources.count))") {
                    ForEach(store.mutedSources) { source in
                        SourceRow(source: source) {
                            Button {
                                Task { await store.restoreSource(id: source.id) }
                            } label: {
                                Label("Restore", systemImage: "arrow.uturn.backward")
                            }
                        }
                    }
                    }
                }
            }
        }
        .navigationTitle("Manual Sources")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    showsEditor = true
                } label: {
                    Label("Add", systemImage: "plus")
                }
                .accessibilityLabel("Add Source")
            }
        }
        .sheet(isPresented: $showsEditor) {
            NavigationStack {
                Form {
                    Section {
                        AppTextField("Watch id or keyword", text: $draft.watch)
                        AppTextField("Ticket or source URL", text: $draft.url)
                            .keyboardType(.URL)
                        DisclosureGroup("Details (optional)") {
                            AppTextField("Label", text: $draft.label)
                            Toggle("Private note", isOn: $draft.privateNote)
                        }
                    }
                    Section {
                        Button("Add Source") {
                            addSource()
                            showsEditor = false
                        }
                        .disabled(!draft.canSubmit)
                    }
                }
                .navigationTitle("Add Source")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Cancel") {
                            showsEditor = false
                        }
                    }
                }
            }
        }
    }

    private func addSource() {
        guard draft.canSubmit else { return }
        let submitted = draft
        draft = SourceDraft()
        Task {
            await store.addSource(
                watch: trimmed(submitted.watch),
                url: trimmed(submitted.url),
                label: trimmed(submitted.label),
                privateNote: submitted.privateNote
            )
        }
    }
}

struct SourceDraft {
    var watch = ""
    var url = ""
    var label = ""
    var privateNote = false

    var canSubmit: Bool { !trimmed(watch).isEmpty && !trimmed(url).isEmpty }
}
