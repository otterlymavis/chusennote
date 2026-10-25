import SwiftUI

/// The Home tab: ticket deadlines that need action, then recent updates.
struct NotificationsView: View {
    let store: ChusennoteStore
    let notificationPermission: NotificationPermission
    @Binding var selectedTab: AppTab

    var body: some View {
        List {
            ErrorSection(store: store)

            if !notificationPermission.isEnabled {
                Section {
                    NotificationPermissionRows(permission: notificationPermission)
                }
            }

            Section("Upcoming") {
                if store.upcoming.isEmpty {
                    if store.trackedEvents.isEmpty {
                        EmptyRow("Watch an event to see its ticket deadlines here.")
                        Button("Watch an Event") {
                            selectedTab = .watch
                        }
                    } else {
                        EmptyRow("Nothing needs your attention right now.")
                    }
                } else {
                    ForEach(store.upcoming.prefix(8)) { item in
                        if let event = store.ticketEvents.first(where: { $0.id == item.eventId }) {
                            NavigationLink {
                                EventDetailView(store: store, event: event)
                            } label: {
                                TextRow(title: item.eventTitle ?? "Untitled event", subtitle: upcomingStatusText(item))
                            }
                        } else {
                            LinkRow(
                                title: item.eventTitle ?? "Untitled event",
                                subtitle: upcomingStatusText(item),
                                url: chusennoteWebURL(item.url)
                            )
                        }
                    }
                }
            }

            if !store.alerts.isEmpty {
                Section("Recent Updates") {
                    ForEach(store.alerts.prefix(10)) { alert in
                        TextRow(title: alertTypeText(alert), subtitle: chusennoteAlertText(alert))
                    }
                }
            }
        }
        .navigationTitle("Home")
        .refreshable {
            await store.refresh()
        }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                RunChecksButton(isRunning: store.isRunningChecks) {
                    Task { await store.runEventWatches() }
                }
            }
        }
    }
}
