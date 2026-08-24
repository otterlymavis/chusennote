import SwiftUI

extension View {
    /// Container-tier card: standard section/row backgrounds (NotificationSection, MetricTile, permission cards).
    func sectionCardStyle() -> some View {
        modifier(SectionCardModifier())
    }

    /// Hero-tier card: the top status banner.
    func heroCardStyle() -> some View {
        modifier(HeroCardModifier())
    }

    /// Hero/featured-tier card: TicketRoundTile, FeaturedDeadlineCard.
    func tileCardStyle(accent: SemanticColor, prominent: Bool) -> some View {
        modifier(TileCardModifier(accent: accent, prominent: prominent))
    }

    func chipStyle(tint: SemanticColor) -> some View {
        modifier(ChipModifier(tint: tint))
    }
}

private struct SectionCardModifier: ViewModifier {
    func body(content: Content) -> some View {
        content
            .padding(Spacing.md)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(.secondarySystemGroupedBackground))
            .clipShape(RoundedRectangle(cornerRadius: Radius.medium, style: .continuous))
    }
}

private struct HeroCardModifier: ViewModifier {
    func body(content: Content) -> some View {
        content
            .padding(Spacing.lg)
            .background(.regularMaterial)
            .clipShape(RoundedRectangle(cornerRadius: Radius.large, style: .continuous))
    }
}

private struct TileCardModifier: ViewModifier {
    let accent: SemanticColor
    let prominent: Bool

    func body(content: Content) -> some View {
        content
            .padding(prominent ? Spacing.md : Spacing.sm)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: Radius.large, style: .continuous)
                    .fill(Color(.tertiarySystemGroupedBackground))
            )
            .overlay(
                RoundedRectangle(cornerRadius: Radius.large, style: .continuous)
                    .stroke(accent.color.opacity(0.25), lineWidth: 1)
            )
            .shadow(color: Color.black.opacity(prominent ? 0.04 : 0), radius: prominent ? 8 : 0, x: 0, y: 3)
    }
}

private struct ChipModifier: ViewModifier {
    let tint: SemanticColor

    func body(content: Content) -> some View {
        content
            .font(Typography.chip)
            .lineLimit(1)
            .minimumScaleFactor(0.8)
            .foregroundStyle(tint.color)
            .padding(.horizontal, 10)
            .padding(.vertical, Spacing.sm)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(tint.color.opacity(0.12))
            .clipShape(Capsule())
    }
}
