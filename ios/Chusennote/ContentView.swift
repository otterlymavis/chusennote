import SwiftUI

struct ContentView: View {
    @StateObject private var store = ChusennoteStore()
    @State private var artistKeyword = ""
    @State private var eventKeyword = ""

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
                    if let error = store.errorMessage {
                        Text(error)
                            .foregroundStyle(.red)
                    }
                }

                Section("Tracked Artists") {
                    TextField("Artist keyword", text: $artistKeyword)
                        .textInputAutocapitalization(.never)
                    Button("Add Artist") {
                        let keyword = artistKeyword.trimmingCharacters(in: .whitespacesAndNewlines)
                        guard !keyword.isEmpty else { return }
                        artistKeyword = ""
                        Task { await store.addWatch(keyword: keyword, kind: "artist") }
                    }
                    if store.trackedArtists.isEmpty {
                        Text("No tracked artists yet.")
                    }
                    ForEach(store.trackedArtists) { watch in
                        VStack(alignment: .leading) {
                            Text(watch.keyword).font(.headline)
                            Text("Last checked: \(watch.lastCheckedAt ?? "never")")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }

                Section("Tracked Events") {
                    TextField("Event keyword", text: $eventKeyword)
                        .textInputAutocapitalization(.never)
                    Button("Add Event") {
                        let keyword = eventKeyword.trimmingCharacters(in: .whitespacesAndNewlines)
                        guard !keyword.isEmpty else { return }
                        eventKeyword = ""
                        Task { await store.addWatch(keyword: keyword, kind: "event") }
                    }
                    Button("Run Event Watches") {
                        Task { await store.runEventWatches() }
                    }
                    if store.trackedEvents.isEmpty {
                        Text("No tracked events yet.")
                    }
                    ForEach(store.trackedEvents) { watch in
                        VStack(alignment: .leading) {
                            Text(watch.keyword).font(.headline)
                            Text("Watch #\(watch.id)")
                                .font(.caption)
                                .foregroundStyle(.secondary)
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
                    }
                }
            }
        }
        .navigationTitle("Event")
    }
}
