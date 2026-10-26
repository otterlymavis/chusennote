import SwiftUI

struct TextRow: View {
    let title: String
    var subtitle: String = ""

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.xxs) {
            Text(title)
                .font(Typography.rowTitle)
                .foregroundStyle(.primary)
                .lineLimit(2)
            if !subtitle.isEmpty {
                Text(subtitle)
                    .font(Typography.rowSubtitle)
                    .foregroundStyle(.secondary)
                    .lineLimit(4)
            }
        }
    }
}

struct EmptyRow: View {
    let text: String

    init(_ text: String) {
        self.text = text
    }

    var body: some View {
        Text(text)
            .font(Typography.rowSubtitle)
            .foregroundStyle(.secondary)
    }
}

/// A row that opens `url` when tapped. Falls back to plain text when there is no usable URL.
struct LinkRow: View {
    let title: String
    var subtitle: String = ""
    let url: URL?
    @Environment(\.openURL) private var openURL

    var body: some View {
        if let url {
            Button {
                openURL(url)
            } label: {
                HStack(spacing: Spacing.sm) {
                    TextRow(title: title, subtitle: subtitle)
                    Spacer(minLength: 0)
                    Image(systemName: "arrow.up.right")
                        .font(.footnote)
                        .foregroundStyle(.tertiary)
                }
            }
            .accessibilityHint("Opens in browser")
        } else {
            TextRow(title: title, subtitle: subtitle)
        }
    }
}

/// A row with a trailing menu for its actions.
struct MenuRow<Actions: View>: View {
    let title: String
    var subtitle: String = ""
    @ViewBuilder var actions: Actions

    var body: some View {
        HStack(spacing: Spacing.sm) {
            TextRow(title: title, subtitle: subtitle)
            Spacer(minLength: 0)
            Menu {
                actions
            } label: {
                Image(systemName: "ellipsis.circle")
                    .imageScale(.large)
                    .frame(minWidth: 44, minHeight: 44, alignment: .trailing)
                    .contentShape(Rectangle())
            }
            .accessibilityLabel("Actions for \(title)")
        }
    }
}

/// A row with a single trailing text action.
struct ActionRow: View {
    let title: String
    var subtitle: String = ""
    let actionTitle: String
    var role: ButtonRole? = nil
    let action: () -> Void

    var body: some View {
        HStack(spacing: Spacing.sm) {
            TextRow(title: title, subtitle: subtitle)
            Spacer(minLength: 0)
            Button(actionTitle, role: role, action: action)
                .buttonStyle(.borderless)
        }
    }
}

struct EventRow: View {
    let event: EventSummary

    var body: some View {
        TextRow(title: event.title ?? "Untitled event", subtitle: eventRowText(event))
    }
}

struct SourceRow<Actions: View>: View {
    let source: WatchSource
    @ViewBuilder var actions: Actions
    @Environment(\.openURL) private var openURL

    var body: some View {
        MenuRow(title: source.label.isEmpty ? source.url : source.label, subtitle: sourceText(source)) {
            if let url = chusennoteWebURL(source.url) {
                Button {
                    openURL(url)
                } label: {
                    Label("Open", systemImage: "arrow.up.right")
                }
            }
            actions
        }
    }
}
