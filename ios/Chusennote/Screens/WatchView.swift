import SwiftUI

struct WatchView: View {
    let store: ChusennoteStore
    // Kept here rather than in the sheet so an unsaved new watch survives
    // closing and reopening the editor.
    @State private var draft = WatchDraft()
    @State private var showsEditor = false

    var body: some View {
        List {
            ErrorSection(store: store)

            if store.ticketEvents.isEmpty && store.trackedEvents.isEmpty {
                ContentUnavailableView(
                    "Watch an event",
                    systemImage: "ticket",
                    description: Text("Tap Add to follow a keyword or find a specific event page.")
                )
            } else {
                Section("Saved Events") {
                    if store.ticketEvents.isEmpty {
                        EmptyRow("No events found yet. Tap Check Now to search your watches.")
                    } else {
                    ForEach(store.ticketEvents) { event in
                        NavigationLink {
                            EventDetailView(store: store, event: event)
                        } label: {
                            EventRow(event: event)
                        }
                    }
                    }
                }

                if !store.trackedEvents.isEmpty {
                    Section("Watches") {
                    ForEach(store.trackedEvents) { watch in
                        MenuRow(title: watch.keyword, subtitle: chusennoteWatchText(watch)) {
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
            }

            if !store.mutedEventWatches.isEmpty {
                Section {
                    DisclosureGroup("Removed Watches (\(store.mutedEventWatches.count))") {
                    ForEach(store.mutedEventWatches) { watch in
                        ActionRow(title: watch.keyword, subtitle: chusennoteWatchText(watch), actionTitle: "Restore") {
                            Task { await store.restoreWatch(id: watch.id) }
                        }
                    }
                    }
                }
            }
        }
        .navigationTitle("Watch")
        .refreshable {
            await store.refresh()
        }
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                RunChecksButton(isRunning: store.isRunningChecks) {
                    Task { await store.runEventWatches() }
                }
            }
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    showsEditor = true
                } label: {
                    Label("Add", systemImage: "plus")
                }
                .accessibilityLabel("Add Watch")
            }
        }
        .sheet(isPresented: $showsEditor, onDismiss: {
            // Edits of an existing watch are discarded; a new draft is kept.
            if draft.isEditing {
                draft = WatchDraft()
            }
        }) {
            NavigationStack {
                WatchEditor(store: store, draft: $draft, isPresented: $showsEditor)
            }
        }
    }
}

private struct WatchEditor: View {
    let store: ChusennoteStore
    @Binding var draft: WatchDraft
    @Binding var isPresented: Bool
    @State private var entryMethod = EntryMethod.keyword

    private enum EntryMethod: String, CaseIterable, Identifiable {
        case keyword = "Keyword"
        case officialPage = "Official Page"

        var id: String { rawValue }
    }

    var body: some View {
        Form {
            if !draft.isEditing {
                Section {
                    Picker("Watch by", selection: $entryMethod) {
                        ForEach(EntryMethod.allCases) { method in
                            Text(method.rawValue).tag(method)
                        }
                    }
                    .pickerStyle(.segmented)
                }
            }

            if draft.isEditing || entryMethod == .keyword {
                Section {
                    WatchFields(keywordTitle: "Event keyword", draft: $draft)
                } footer: {
                    if !draft.isEditing {
                        Text("Follow an event name or phrase to discover matching ticket pages.")
                    }
                }

                Section {
                Button(draft.isEditing ? "Save Changes" : "Add Watch") {
                    let submitted = draft
                    draft = WatchDraft()
                    isPresented = false
                    Task { await store.addWatch(submitted, kind: "event") }
                }
                .disabled(!draft.canSubmit)

                if !draft.isEditing {
                    DisclosureGroup("Try a Sample") {
                        Button("Add a Sample Watch") {
                            let sample = RandomEventWatch.sample().draft
                            draft = sample
                            isPresented = false
                            Task { await store.addWatch(sample, kind: "event") }
                        }
                    }
                }
                }
            } else {
                ExactEventSearchSection(store: store)
            }
        }
        .navigationTitle(draft.isEditing ? "Edit Watch" : "Add Watch")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                Button("Cancel") {
                    isPresented = false
                }
            }
        }
    }
}

/// Finds an official event page by name and adds it directly.
private struct ExactEventSearchSection: View {
    let store: ChusennoteStore
    @Environment(\.openURL) private var openURL
    @State private var keyword = ""
    @State private var phase = Phase.idle
    @State private var isAdding = false
    @State private var note: Note?

    private enum Phase {
        case idle
        case searching
        case finished(keyword: String, results: [SearchResult])
    }

    private struct Note {
        let text: String
        let isError: Bool
    }

    private var isSearching: Bool {
        if case .searching = phase { return true }
        return false
    }

    var body: some View {
        Section {
            AppTextField("Exact event name", text: $keyword)
                .onChange(of: keyword) { _, _ in
                    phase = .idle
                    note = nil
                }
            Button {
                search()
            } label: {
                ProgressLabel(title: isSearching ? "Searching" : "Search", isLoading: isSearching)
            }
            .disabled(trimmed(keyword).isEmpty || isSearching || isAdding)

            if let note {
                StatusMessage(text: note.text, isError: note.isError)
            }

            if case let .finished(searchedKeyword, results) = phase {
                if results.isEmpty {
                    if note == nil {
                        EmptyRow("No matching pages found. Try a more specific event name.")
                    }
                } else {
                    ForEach(results) { result in
                        resultRow(result, keyword: searchedKeyword)
                    }
                }
            }
        } header: {
            Text("Find an official page")
        } footer: {
            Text("Search for a specific event and watch its official page directly.")
        }
    }

    private func resultRow(_ result: SearchResult, keyword: String) -> some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            TextRow(
                title: result.title.isEmpty ? result.url : result.title,
                subtitle: [result.url, result.snippet].filter { !$0.isEmpty }.joined(separator: "\n")
            )
            HStack(spacing: Spacing.lg) {
                Button("Add") {
                    add(result, keyword: keyword)
                }
                .disabled(isAdding)
                if let url = chusennoteWebURL(result.url) {
                    Button("Open") {
                        openURL(url)
                    }
                }
            }
            .buttonStyle(.borderless)
            .font(Typography.rowSubtitle)
        }
    }

    private func search() {
        let query = trimmed(keyword)
        guard !query.isEmpty else { return }
        phase = .searching
        note = nil
        Task {
            let results = await store.searchExactEvents(keyword: query)
            // The field changed while the request was in flight; its onChange
            // already reset the phase, so drop these stale results.
            guard trimmed(keyword) == query else { return }
            phase = .finished(keyword: query, results: results)
            if let error = store.errorMessage {
                note = Note(text: error, isError: true)
            }
        }
    }

    private func add(_ result: SearchResult, keyword: String) {
        guard !isAdding else { return }
        isAdding = true
        note = nil
        Task {
            defer { isAdding = false }
            let added = await store.addExactEvent(
                keyword: keyword,
                title: result.title,
                url: result.url,
                snippet: result.snippet
            )
            if added {
                phase = .idle
                note = store.errorMessage.map {
                    Note(text: "Page added, but the list could not refresh: \($0)", isError: true)
                } ?? Note(text: "Page added. Verify its source before relying on ticket dates.", isError: false)
            } else {
                note = Note(text: store.errorMessage ?? "Could not add this event page.", isError: true)
            }
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

    var draft: WatchDraft {
        var draft = WatchDraft()
        draft.keyword = keyword
        draft.tags = tags
        draft.regions = regions
        draft.venues = venues
        draft.alerts = alerts
        return draft
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
