import SwiftUI
import UserNotifications
#if canImport(FirebaseCore)
import FirebaseCore
import FirebaseMessaging
#endif

@main
struct ChusennoteApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    var body: some Scene {
        WindowGroup {
            ContentView()
        }
    }
}

/// Registers the device for push and forwards its token to the chusennote
/// backend so the server can deliver ticket-date reminders.
final class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        #if canImport(FirebaseCore)
        FirebaseApp.configure()
        Messaging.messaging().delegate = PushRegistrar.shared
        #endif
        UNUserNotificationCenter.current().delegate = self
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]) { granted, _ in
            guard granted else { return }
            DispatchQueue.main.async { application.registerForRemoteNotifications() }
        }
        return true
    }

    func application(
        _ application: UIApplication,
        didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data
    ) {
        #if canImport(FirebaseCore)
        Messaging.messaging().apnsToken = deviceToken
        #else
        // Without Firebase, register the raw APNs token (hex) so the backend can
        // be pointed at APNs instead of FCM if desired.
        let token = deviceToken.map { String(format: "%02x", $0) }.joined()
        DeviceRegistration.register(token: token)
        #endif
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        completionHandler([.banner, .sound])
    }
}

#if canImport(FirebaseCore)
final class PushRegistrar: NSObject, MessagingDelegate {
    static let shared = PushRegistrar()

    func messaging(_ messaging: Messaging, didReceiveRegistrationToken fcmToken: String?) {
        guard let token = fcmToken else { return }
        DeviceRegistration.register(token: token)
    }
}
#endif

/// Posts a push token to POST /api/devices using the saved base URL.
enum DeviceRegistration {
    static func register(token: String) {
        let base = (UserDefaults.standard.string(forKey: "baseURL") ?? "")
            .trimmingCharacters(in: CharacterSet(charactersIn: "/ "))
        guard !base.isEmpty, let url = URL(string: base + "/api/devices") else { return }
        let encoded = token.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? token
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        request.httpBody = "token=\(encoded)&platform=ios".data(using: .utf8)
        URLSession.shared.dataTask(with: request).resume()
    }
}
