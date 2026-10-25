import SwiftUI
import UIKit
import UserNotifications

enum AppTab {
    case notifications
    case watch
    case settings
}

struct ContentView: View {
    @State private var store = ChusennoteStore()
    @State private var notificationPermission = NotificationPermission()
    @State private var selectedTab: AppTab = .notifications

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
                WatchView(store: store)
            }
            .tabItem {
                Label("Watch", systemImage: "ticket")
            }
            .tag(AppTab.watch)

            NavigationStack {
                SettingsView(store: store, notificationPermission: notificationPermission)
            }
            .tabItem {
                Label("Settings", systemImage: "gearshape")
            }
            .tag(AppTab.settings)
        }
        .task {
            await store.refreshAccountStatus()
            await store.refresh()
            notificationPermission.refresh()
        }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.didBecomeActiveNotification)) { _ in
            notificationPermission.refresh()
        }
    }
}
