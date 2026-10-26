import SwiftUI

enum Spacing {
    static let xxs: CGFloat = 4
    static let sm: CGFloat = 8
    static let lg: CGFloat = 16
}

enum Typography {
    static let rowTitle: Font = .body
    static let rowSubtitle: Font = .footnote
}

/// Color is reserved for status; everything else uses the system tint and label colors.
enum StatusColor {
    static let success: Color = .green
    static let danger: Color = .red
}
