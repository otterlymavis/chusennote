import SwiftUI

struct ContentView: View {
    @StateObject private var store = ChusennoteStore()
    @Environment(\.openURL) private var openURL
    @State private var artistKeyword = ""
    @State private var artistTags = ""
    @State private var artistRegions = ""
    @State private var artistVenues = ""
    @State private var eventKeyword = ""
    @State private var eventTags = ""
    @State private var eventRegions = ""
    @State private var eventVenues = ""
    @State private var eventAlerts = ""
    @State private var sourceWatch = ""
    @State private var sourceURL = ""
    @State private var sourceLabel = ""

    var body: some View {
        NavigationStack {
            List {
                Section("API") {
                    TextField("Base URL", text: $store.baseURL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    Button("Refresh") {
                        Task { await store.refresh() }
                    }
                    if let calendarURL = store.calendarFeedURL {
                        Button("Open Calendar Feed") {
                            openURL(calendarURL)
                        }
                    }
                    if let error = store.errorMessage {
                        Text(error)
                            .foregroundStyle(.red)
                    }
                    if let health = store.health {
                        Text("Server \(health.status): \(health.trackedArtists) artists, \(health.trackedEvents) events, \(health.alerts) alerts")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }

                Section("Tracked Artists") {
                    TextField("Artist keyword", text: $artistKeyword)
                        .textInputAutocapitalization(.never)
                    TextField("Tags", text: $artistTags)
                        .textInputAutocapitalization(.never)
                    TextField("Preferred regions", text: $artistRegions)
                        .textInputAutocapitalization(.never)
                    TextField("Preferred venues", text: $artistVenues)
                        .textInputAutocapitalization(.never)
                    Button("Add Artist") {
                        let keyword = artistKeyword.trimmingCharacters(in: .whitespacesAndNewlines)
                        guard !keyword.isEmpty else { return }
                        let tags = artistTags.trimmingCharacters(in: .whitespacesAndNewlines)
                        let regions = artistRegions.trimmingCharacters(in: .whitespacesAndNewlines)
                        let venues = artistVenues.trimmingCharacters(in: .whitespacesAndNewlines)
                        artistKeyword = ""
                        artistTags = ""
                        artistRegions = ""
                        artistVenues = ""
                        Task { await store.addWatch(keyword: keyword, kind: "artist", tags: tags, regions: regions, venues: venues) }
                    }
                    if store.trackedArtists.isEmpty {
                        Text("No tracked artists yet.")
                    }
                    ForEach(store.trackedArtists) { watch in
                        HStack {
                            VStack(alignment: .leading) {
                                Text(watch.keyword).font(.headline)
                                Text("Last checked: \(watch.lastCheckedAt ?? "never")")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            Spacer()
                            Button("Remove") {
                                Task { await store.removeWatch(id: watch.id) }
                            }
                        }
                    }
                }

                Section("Tracked Events") {
                    TextField("Event keyword", text: $eventKeyword)
                        .textInputAutocapitalization(.never)
                    TextField("Tags", text: $eventTags)
                        .textInputAutocapitalization(.never)
                    TextField("Preferred regions", text: $eventRegions)
                        .textInputAutocapitalization(.never)
                    TextField("Preferred venues", text: $eventVenues)
                        .textInputAutocapitalization(.never)
                    TextField("Alert types", text: $eventAlerts)
                        .textInputAutocapitalization(.never)
                    Button("Add Event") {
                        let keyword = eventKeyword.trimmingCharacters(in: .whitespacesAndNewlines)
                        guard !keyword.isEmpty else { return }
                        let tags = eventTags.trimmingCharacters(in: .whitespacesAndNewlines)
                        let regions = eventRegions.trimmingCharacters(in: .whitespacesAndNewlines)
                        let venues = eventVenues.trimmingCharacters(in: .whitespacesAndNewlines)
                        let alerts = eventAlerts.trimmingCharacters(in: .whitespacesAndNewlines)
                        eventKeyword = ""
                        eventTags = ""
                        eventRegions = ""
                        eventVenues = ""
                        eventAlerts = ""
                        Task { await store.addWatch(keyword: keyword, kind: "event", tags: tags, regions: regions, venues: venues, alerts: alerts) }
                    }
                    Button("Run Event Watches") {
                        Task { await store.runEventWatches() }
                    }
                    if store.trackedEvents.isEmpty {
                        Text("No tracked events yet.")
                    }
                    ForEach(store.trackedEvents) { watch in
                        HStack {
                            VStack(alignment: .leading) {
                                Text(watch.keyword).font(.headline)
                                Text("Watch #\(watch.id)")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            Spacer()
                            Button("Remove") {
                                Task { await store.removeWatch(id: watch.id) }
                            }
                        }
                    }
                }

                Section("Muted Watches") {
                    if store.mutedWatches.isEmpty {
                        Text("No muted watches.")
                    }
                    ForEach(store.mutedWatches) { watch in
                        HStack {
                            VStack(alignment: .leading) {
                                Text(watch.keyword).font(.headline)
                                Text("Watch #\(watch.id) - \(watch.kind ?? "event")")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                Text("Last checked: \(watch.lastCheckedAt ?? "never")")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                            Spacer()
                            Button("Restore") {
                                Task { await store.restoreWatch(id: watch.id) }
                            }
                        }
                    }
                }

                Section("Ticket Timelines") {
                    let ticketEvents = store.events.filter { ($0.watchKind ?? "event") == "event" }
                    if ticketEvents.isEmpty {
                        Text("No ticket timelines saved yet.")
                    }
                    ForEach(ticketEvents) { event in
                        NavigationLink {
                            EventDetailView(event: event)
                        } label: {
                            VStack(alignment: .leading) {
                                Text(event.title ?? "Untitled event").font(.headline)
                                Text("\(event.status ?? "watching") - \(event.rounds.count) rounds")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }

                Section("Needs Attention") {
                    if store.upcoming.isEmpty {
                        Text("No urgent ticket dates saved yet.")
                    }
                    ForEach(store.upcoming.prefix(8)) { item in
                        VStack(alignment: .leading) {
                            Text(item.eventTitle ?? "Untitled event").font(.headline)
                            Text([item.status, item.platform, item.roundName, item.relevantDate].compactMap { $0 }.joined(separator: " - "))
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }

                Section("Artist Event Info") {
                    let artistEvents = store.events.filter { ($0.watchKind ?? "event") == "artist" }
                    if artistEvents.isEmpty {
                        Text("No artist event info saved yet.")
                    }
                    ForEach(artistEvents) { event in
                        VStack(alignment: .leading) {
                            Text(event.title ?? "Untitled event").font(.headline)
                            Text([event.status, event.eventDates?.prefix(2).joined(separator: "; "), event.venues?.prefix(2).joined(separator: "; ")].compactMap { $0 }.joined(separator: " - "))
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }

                Section("Manual Event Source") {
                    TextField("Watch id or keyword", text: $sourceWatch)
                        .textInputAutocapitalization(.never)
                    TextField("Ticket or source URL", text: $sourceURL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    TextField("Label", text: $sourceLabel)
                    Button("Add Source") {
                        let watch = sourceWatch.trimmingCharacters(in: .whitespacesAndNewlines)
                        let url = sourceURL.trimmingCharacters(in: .whitespacesAndNewlines)
                        let label = sourceLabel.trimmingCharacters(in: .whitespacesAndNewlines)
                        guard !watch.isEmpty, !url.isEmpty else { return }
                        sourceWatch = ""
                        sourceURL = ""
                        sourceLabel = ""
                        Task { await store.addSource(watch: watch, url: url, label: label) }
                    }
                    if store.activeSources.isEmpty {
                        Text("No manual sources.")
                    }
                    ForEach(store.activeSources) { source in
                        HStack {
                            VStack(alignment: .leading) {
                                Text(source.label).font(.headline)
                                Text("Watch #\(source.watchId) - \(source.platform)")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                Text(source.url)
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                            Spacer()
                            Button("Remove") {
                                Task { await store.removeSource(id: source.id) }
                            }
                        }
                    }
                }

                Section("Muted Sources") {
                    if store.mutedSources.isEmpty {
                        Text("No muted sources.")
                    }
                    ForEach(store.mutedSources) { source in
                        HStack {
                            VStack(alignment: .leading) {
                                Text(source.label).font(.headline)
                                Text("Watch #\(source.watchId) - \(source.platform)")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                Text(source.url)
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                            Spacer()
                            Button("Restore") {
                                Task { await store.restoreSource(id: source.id) }
                            }
                        }
                    }
                }

                Section("Recent Alerts") {
                    if store.alerts.isEmpty {
                        Text("No recent alerts.")
                    }
                    ForEach(store.alerts.prefix(10)) { alert in
                        VStack(alignment: .leading) {
                            Text(alert.type).font(.headline)
                            Text([alert.event, alert.keyword, alert.round].compactMap { $0 }.joined(separator: " "))
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }
            .navigationTitle("chusennote")
            .task {
                await store.refresh()
            }
        }
    }
}

struct EventDetailView: View {
    let event: EventSummary

    var body: some View {
        List {
            Section("Event") {
                Text(event.title ?? "Untitled event")
                Text(event.status ?? "watching")
                if let dates = event.eventDates, !dates.isEmpty {
                    Text("Dates: \(dates.prefix(2).joined(separator: "; "))")
                }
                if let venues = event.venues, !venues.isEmpty {
                    Text("Venues: \(venues.prefix(2).joined(separator: "; "))")
                }
                if let reasons = event.matchReasons, !reasons.isEmpty {
                    Text("Why: \(reasons.prefix(3).joined(separator: "; "))")
                }
            }
            Section("Ticket Rounds") {
                if event.rounds.isEmpty {
                    Text("No ticket rounds saved yet.")
                }
                ForEach(event.rounds) { round in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(round.name ?? "Ticket round").font(.headline)
                        Text("\(round.platform ?? "unknown") - \(round.status ?? "unknown")")
                        Text("Type: \(round.roundType ?? "unknown") - membership: \(round.membershipRequired ?? "unknown")")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        if let evidence = round.evidence, !evidence.isEmpty {
                            Text("Evidence: \(evidence)")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }
        }
        .navigationTitle("Event")
    }
}
