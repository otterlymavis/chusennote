import SwiftUI
import UserNotifications
#if canImport(FirebaseCore) && canImport(FirebaseMessaging)
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
        #if canImport(FirebaseCore) && canImport(FirebaseMessaging)
        if Bundle.main.url(forResource: "GoogleService-Info", withExtension: "plist") != nil {
            FirebaseApp.configure()
            Messaging.messaging().delegate = PushRegistrar.shared
        }
        #endif
        UNUserNotificationCenter.current().delegate = self
        PushNotifications.registerIfAuthorized(application: application)
        return true
    }

    func application(
        _ application: UIApplication,
        didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data
    ) {
        #if canImport(FirebaseCore) && canImport(FirebaseMessaging)
        if DeviceRegistration.firebaseMessagingConfigured {
            Messaging.messaging().apnsToken = deviceToken
        }
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

#if canImport(FirebaseCore) && canImport(FirebaseMessaging)
final class PushRegistrar: NSObject, MessagingDelegate {
    static let shared = PushRegistrar()

    func messaging(_ messaging: Messaging, didReceiveRegistrationToken fcmToken: String?) {
        guard let token = fcmToken else { return }
        Task { await DeviceRegistration.shared.saveAndRegister(token: token) }
    }
}
#endif

enum PushNotifications {
    @MainActor
    static func requestAuthorization(completion: (() -> Void)? = nil) {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]) { granted, _ in
            DispatchQueue.main.async {
                if granted {
                    if DeviceRegistration.firebaseMessagingConfigured {
                        UIApplication.shared.registerForRemoteNotifications()
                    }
                }
                completion?()
            }
        }
    }

    static func registerIfAuthorized(application: UIApplication) {
        guard DeviceRegistration.firebaseMessagingConfigured else { return }
        UNUserNotificationCenter.current().getNotificationSettings { settings in
            guard settings.authorizationStatus == .authorized || settings.authorizationStatus == .provisional else {
                return
            }
            DispatchQueue.main.async {
                application.registerForRemoteNotifications()
            }
        }
    }
}

/// Serializes FCM registration around account transitions. Logout pauses new
/// registrations and waits for already-created requests before asking the
/// backend to detach this device, preventing a late request from reattaching it.
actor DeviceRegistration {
    static let shared = DeviceRegistration()

    private var isPaused = false
    private var pendingRegistration: Task<Void, Never>?

    static var firebaseMessagingConfigured: Bool {
        #if canImport(FirebaseCore) && canImport(FirebaseMessaging)
        guard Bundle.main.url(forResource: "GoogleService-Info", withExtension: "plist") != nil else {
            return false
        }
        return FirebaseApp.app() != nil
        #else
        return false
        #endif
    }

    func saveAndRegister(token: String) {
        let cleanToken = token.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleanToken.isEmpty else { return }
        ChusennoteSettings.pushToken = cleanToken
        guard !isPaused else { return }
        enqueue(token: cleanToken)
    }

    func registerSavedTokenIfPossible() {
        guard Self.firebaseMessagingConfigured, !isPaused else { return }
        let token = ChusennoteSettings.pushToken
        guard !token.isEmpty else { return }
        enqueue(token: token)
    }

    func pauseForAccountTransition() async {
        isPaused = true
        let pending = pendingRegistration
        await pending?.value
    }

    func resumeAfterAccountTransition() {
        isPaused = false
        registerSavedTokenIfPossible()
    }

    private func enqueue(token: String) {
        let previous = pendingRegistration
        let baseURL = ChusennoteSettings.baseURL
        let apiToken = ChusennoteSettings.apiToken
        pendingRegistration = Task.detached {
            await previous?.value
            await Self.post(token: token, baseURL: baseURL, apiToken: apiToken)
        }
    }

    private static func post(token: String, baseURL: String, apiToken: String) async {
        guard BackendURLPolicy.permitsCredentialTransport(baseURL) else { return }
        let base = baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/ "))
        guard let url = URL(string: base + "/api/devices") else { return }
        var fields = URLComponents()
        fields.queryItems = [
            URLQueryItem(name: "token", value: token),
            URLQueryItem(name: "platform", value: "ios")
        ]
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        let cleanAPIToken = apiToken.trimmingCharacters(in: .whitespacesAndNewlines)
        if !cleanAPIToken.isEmpty {
            request.setValue("Bearer \(cleanAPIToken)", forHTTPHeaderField: "Authorization")
        }
        request.httpBody = fields.percentEncodedQuery?.data(using: .utf8)
        do {
            let (_, response) = try await BackendSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse, 200..<300 ~= http.statusCode else { return }
        } catch {
            return
        }
    }
}
