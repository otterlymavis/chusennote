import SwiftUI

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

            Section("Needs Attention") {
                if store.upcoming.isEmpty {
                    if store.trackedEvents.isEmpty {
                        EmptyRow("Add a watch to keep track of ticket deadlines.")
                        Button("Browse Watches") {
                            selectedTab = .watch
                        }
                    } else {
                        EmptyRow("You're all caught up. New ticket deadlines will appear here.")
                    }
                } else {
                    ForEach(store.upcoming.prefix(6)) { item in
                        LinkRow(
                            title: item.eventTitle ?? "Untitled event",
                            subtitle: upcomingStatusText(item),
                            url: chusennoteWebURL(item.url)
                        )
                    }
                }
            }

            Section("Recent Alerts") {
                if store.alerts.isEmpty {
                    EmptyRow("Updates from your watches will appear here.")
                } else {
                    ForEach(store.alerts.prefix(10)) { alert in
                        TextRow(title: alertTypeText(alert), subtitle: chusennoteAlertText(alert))
                    }
                }
            }
        }
        .navigationTitle("Alerts")
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
