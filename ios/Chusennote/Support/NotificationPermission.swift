import SwiftUI
import UIKit
import UserNotifications

final class NotificationPermission: ObservableObject {
    @Published private(set) var status: UNAuthorizationStatus = .notDetermined

    private var pushIsConfigured: Bool {
        DeviceRegistration.firebaseMessagingConfigured
    }

    var isEnabled: Bool {
        guard pushIsConfigured else { return false }
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
        guard pushIsConfigured else { return "Firebase is not configured" }
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
        guard pushIsConfigured else {
            return "Add GoogleService-Info.plist to enable push registration."
        }
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
        guard pushIsConfigured else { return "Firebase setup required" }
        return status == .denied ? "Open iOS Settings" : "Enable Notifications"
    }

    var actionIcon: String {
        guard pushIsConfigured else { return "wrench.and.screwdriver" }
        return status == .denied ? "gearshape" : "bell.badge.fill"
    }

    var canAct: Bool {
        pushIsConfigured && !isEnabled
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
        guard pushIsConfigured else { return }
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
