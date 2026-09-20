import SwiftUI
import UIKit
import UserNotifications

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
            await store.refreshAccountStatus()
            await store.refresh()
            notificationPermission.refresh()
        }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.didBecomeActiveNotification)) { _ in
            notificationPermission.refresh()
        }
    }
}
