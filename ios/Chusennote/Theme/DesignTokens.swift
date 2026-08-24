import SwiftUI

enum Radius {
    static let small: CGFloat = 10
    static let medium: CGFloat = 14
    static let large: CGFloat = 20
}

enum Spacing {
    static let xxs: CGFloat = 4
    static let xs: CGFloat = 6
    static let sm: CGFloat = 8
    static let md: CGFloat = 12
    static let lg: CGFloat = 16
    static let xl: CGFloat = 20
    static let scrollBottomInset: CGFloat = 120
}

enum IconSize {
    static let small: CGFloat = 24
    static let standard: CGFloat = 32
    static let large: CGFloat = 44
}

enum SemanticColor {
    case info
    case success
    case warning
    case danger
    case highlight
    case neutral

    var color: Color {
        switch self {
        case .info: return .blue
        case .success: return .green
        case .warning: return .orange
        case .danger: return .red
        case .highlight: return .purple
        case .neutral: return .secondary
        }
    }
}

enum Typography {
    static let heroTitle: Font = .title3.weight(.semibold)
    static let sectionHeader: Font = .subheadline.weight(.semibold)
    static let rowTitle: Font = .subheadline.weight(.semibold)
    static let rowSubtitle: Font = .caption
    static let metricValue: Font = .title3.weight(.semibold)
    static let metricLabel: Font = .caption
    static let chip: Font = .caption.weight(.semibold)
    static let footnote: Font = .footnote
}
