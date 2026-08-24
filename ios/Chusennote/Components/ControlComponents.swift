import SwiftUI

struct ProgressLabel: View {
    let title: String
    let systemImage: String
    let isLoading: Bool

    var body: some View {
        HStack(spacing: Spacing.sm) {
            if isLoading {
                ProgressView()
            } else {
                Image(systemName: systemImage)
            }
            Text(title)
        }
    }
}

struct IconActionButton: View {
    let title: String
    let systemImage: String
    var prominent = false
    let action: () -> Void

    var body: some View {
        if prominent {
            Button(action: action) {
                icon
            }
            .buttonStyle(.borderedProminent)
            .accessibilityLabel(title)
        } else {
            Button(action: action) {
                icon
            }
            .buttonStyle(.bordered)
            .accessibilityLabel(title)
        }
    }

    private var icon: some View {
        Image(systemName: systemImage)
            .font(Typography.rowTitle)
            .frame(width: IconSize.standard, height: IconSize.standard)
    }
}

struct AppTextField: View {
    let title: String
    @Binding var text: String

    init(_ title: String, text: Binding<String>) {
        self.title = title
        self._text = text
    }

    var body: some View {
        TextField(title, text: $text)
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
            .padding(.horizontal, Spacing.md)
            .padding(.vertical, 10)
            .background(Color(.tertiarySystemGroupedBackground))
            .clipShape(RoundedRectangle(cornerRadius: Radius.small, style: .continuous))
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

struct CollapsibleTextSection: View {
    let title: String
    var icon: String = "sparkle.magnifyingglass"
    var accent: SemanticColor = .info
    @Binding var isExpanded: Bool
    let emptyTitle: String
    let emptyDetail: String
    let items: [String]
    let systemImage: String

    var body: some View {
        NotificationSection(title: title, icon: icon, accent: accent) {
            DisclosureGroup(isExpanded: $isExpanded) {
                VStack(alignment: .leading, spacing: Spacing.md) {
                    if items.isEmpty {
                        EmptyStateRow(title: emptyTitle, detail: emptyDetail)
                    } else {
                        ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                            InfoTextRow(text: item, systemImage: systemImage)
                        }
                    }
                }
                .padding(.top, Spacing.sm)
            } label: {
                Label("\(items.count)", systemImage: systemImage)
                    .font(Typography.sectionHeader)
            }
        }
    }
}

struct CollapsibleEventListSection<Item: Identifiable, Content: View>: View {
    let title: String
    var icon: String = "sparkle.magnifyingglass"
    var accent: SemanticColor = .info
    @Binding var isExpanded: Bool
    let emptyTitle: String
    let emptyDetail: String
    let items: [Item]
    @ViewBuilder var content: (Item) -> Content

    var body: some View {
        NotificationSection(title: title, icon: icon, accent: accent) {
            DisclosureGroup(isExpanded: $isExpanded) {
                VStack(alignment: .leading, spacing: Spacing.md) {
                    if items.isEmpty {
                        EmptyStateRow(title: emptyTitle, detail: emptyDetail)
                    } else {
                        ForEach(items) { item in
                            content(item)
                        }
                    }
                }
                .padding(.top, Spacing.sm)
            } label: {
                Label("\(items.count)", systemImage: icon)
                    .font(Typography.sectionHeader)
            }
        }
    }
}
