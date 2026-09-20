import SwiftUI

struct NotificationsView: View {
    @ObservedObject var store: ChusennoteStore
    @ObservedObject var notificationPermission: NotificationPermission
    @Binding var selectedTab: AppTab
    @State private var showsRecentAlerts = false

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: Spacing.lg) {
                NotificationHero(store: store)

                NotificationPermissionCard(permission: notificationPermission)

                HStack(spacing: Spacing.sm) {
                    MetricTile(title: "Watching", value: "\(store.trackedEvents.count)", systemImage: "ticket", tint: .info)
                    MetricTile(title: "Due Soon", value: "\(store.upcoming.count)", systemImage: "clock.badge.exclamationmark", tint: .warning)
                    MetricTile(title: "Alerts", value: "\(store.alerts.count)", systemImage: "bell", tint: store.alerts.isEmpty ? .info : .danger)
                }

                NotificationSection(title: "Needs Attention", icon: "clock.badge.exclamationmark", accent: .warning) {
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

                HStack(spacing: Spacing.sm) {
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

                NotificationSection(title: "Recent Alerts", icon: "bell.badge", accent: .info) {
                    DisclosureGroup(isExpanded: $showsRecentAlerts) {
                        VStack(alignment: .leading, spacing: Spacing.md) {
                            if store.alerts.isEmpty {
                                EmptyStateRow(title: "No alert history yet", detail: "New reminders appear here.")
                            } else {
                                ForEach(store.alerts.prefix(10)) { alert in
                                    RowContent(
                                        title: alertTypeText(alert),
                                        subtitle: chusennoteAlertText(alert),
                                        systemImage: "bell.badge"
                                    )
                                }
                            }
                        }
                        .padding(.top, Spacing.sm)
                    } label: {
                        Label("\(store.alerts.count)", systemImage: "bell.badge")
                            .font(Typography.sectionHeader)
                    }
                }
            }
            .padding(Spacing.xl)
            .padding(.bottom, Spacing.scrollBottomInset)
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
