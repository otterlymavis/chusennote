import SwiftUI

struct ProgressLabel: View {
    let title: String
    let isLoading: Bool

    var body: some View {
        HStack(spacing: Spacing.sm) {
            Text(title)
            if isLoading {
                Spacer(minLength: 0)
                ProgressView()
            }
        }
    }
}

struct RunChecksButton: View {
    let isRunning: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            if isRunning {
                ProgressView()
            } else {
                Text("Check Now")
            }
        }
        .disabled(isRunning)
        .accessibilityLabel(isRunning ? "Checking" : "Check Now")
    }
}

struct StatusMessage: View {
    let text: String
    var isError = true

    var body: some View {
        Label(text, systemImage: isError ? "exclamationmark.triangle" : "checkmark.circle")
            .font(Typography.rowSubtitle)
            .foregroundStyle(isError ? StatusColor.danger : StatusColor.success)
    }
}

/// The store's latest error, at the top of a list. Renders nothing when there is none.
struct ErrorSection: View {
    let store: ChusennoteStore

    var body: some View {
        if let error = store.errorMessage, !error.isEmpty {
            Section {
                StatusMessage(text: error)
            }
        }
    }
}

/// Notification status plus, when something can be done about it, the button that does it.
struct NotificationPermissionRows: View {
    let permission: NotificationPermission

    var body: some View {
        TextRow(title: permission.title, subtitle: permission.detail)
        if permission.canAct {
            Button(permission.actionTitle) {
                permission.performAction()
            }
        }
    }
}

struct AppTextField: View {
    let title: String
    @Binding var text: String
    var isSecure: Bool = false

    init(_ title: String, text: Binding<String>, isSecure: Bool = false) {
        self.title = title
        self._text = text
        self.isSecure = isSecure
    }

    var body: some View {
        Group {
            if isSecure {
                SecureField(title, text: $text)
            } else {
                TextField(title, text: $text)
            }
        }
        .textInputAutocapitalization(.never)
        .autocorrectionDisabled()
    }
}

/// The in-progress edit of a ticket or artist watch. Held as one value so a
/// screen owns a single piece of state instead of a field per input.
struct WatchDraft {
    var keyword = ""
    var tags = ""
    var regions = ""
    var venues = ""
    var alerts = defaultAlertPreferenceText()
    /// The watch being edited, or nil when adding a new one.
    var editingID: Int?

    init() {}

    init(editing watch: Watch) {
        keyword = watch.keyword
        tags = watch.tags ?? ""
        regions = watch.preferredRegions ?? ""
        venues = watch.preferredVenues ?? ""
        alerts = watch.alertPreferences ?? defaultAlertPreferenceText()
        editingID = watch.id
    }

    var isEditing: Bool { editingID != nil }
    var canSubmit: Bool { !trimmed(keyword).isEmpty }
}

/// The fields shared by ticket and artist watches. Emits list rows.
struct WatchFields: View {
    let keywordTitle: String
    @Binding var draft: WatchDraft
    @State private var showsFilters = false
    @State private var showsReminders = false

    private var filterCount: Int {
        [draft.tags, draft.regions, draft.venues].filter { !trimmed($0).isEmpty }.count
    }

    var body: some View {
        AppTextField(keywordTitle, text: $draft.keyword)
            .disabled(draft.isEditing)
        DisclosureGroup(filterCount == 0 ? "Filters (optional)" : "Filters (\(filterCount))", isExpanded: $showsFilters) {
            AppTextField("Tags", text: $draft.tags)
            AppTextField("Preferred regions", text: $draft.regions)
            AppTextField("Preferred venues", text: $draft.venues)
        }
        DisclosureGroup("\(alertCount(draft.alerts)) reminders", isExpanded: $showsReminders) {
            AlertPreferenceToggles(alerts: $draft.alerts)
        }
    }
}

struct AlertPresetToggle: View {
    let title: String
    let key: String
    @Binding var alerts: String

    var body: some View {
        Toggle(title, isOn: Binding(
            get: {
                alertKeys(alerts).contains(key)
            },
            set: { isOn in
                var keys = alertKeys(alerts)
                if isOn {
                    keys.insert(key)
                } else {
                    keys.remove(key)
                }
                alerts = orderedAlertKeys(keys).joined(separator: ",")
            }
        ))
    }
}

struct AlertPreferenceToggles: View {
    @Binding var alerts: String

    var body: some View {
        AlertPresetToggle(title: "Official page found", key: "new_official_page", alerts: $alerts)
        AlertPresetToggle(title: "Ticket link found", key: "new_ticket_link", alerts: $alerts)
        AlertPresetToggle(title: "New lottery rounds", key: "new_lottery_round", alerts: $alerts)
        AlertPresetToggle(title: "Ticket details changed", key: "ticket_field_changed", alerts: $alerts)
        AlertPresetToggle(title: "Lottery opened", key: "lottery_opened", alerts: $alerts)
        AlertPresetToggle(title: "Closing soon", key: "lottery_closing_soon", alerts: $alerts)
        AlertPresetToggle(title: "Results today", key: "results_today", alerts: $alerts)
        AlertPresetToggle(title: "Payment due soon", key: "payment_due_soon", alerts: $alerts)
        AlertPresetToggle(title: "General sale soon", key: "general_sale_soon", alerts: $alerts)
        AlertPresetToggle(title: "Official resale opened", key: "trade_opened", alerts: $alerts)
        AlertPresetToggle(title: "Official resale closing soon", key: "trade_closing_soon", alerts: $alerts)
        AlertPresetToggle(title: "Watch failed", key: "watch_failed", alerts: $alerts)
    }
}
