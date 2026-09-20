package com.chusennote.mobile;

import java.util.Locale;

/** Human-readable alert history text while stable alert keys remain unchanged. */
final class AlertTypeText {
    private AlertTypeText() {
    }

    static String label(String typeLabel, String type) {
        if (typeLabel != null && !typeLabel.trim().isEmpty()) {
            return typeLabel.trim();
        }
        String code = type == null ? "" : type.trim();
        switch (code) {
            case "new_official_page": return "Official page found";
            case "new_ticket_link": return "Ticket link found";
            case "new_lottery_round": return "New ticket round";
            case "ticket_field_changed": return "Ticket details changed";
            case "lottery_opened": return "Lottery opened";
            case "lottery_closing_soon": return "Lottery closing soon";
            case "results_today": return "Results today";
            case "payment_due_soon": return "Payment due soon";
            case "general_sale_soon": return "General sale soon";
            case "trade_opened": return "Official resale opened";
            case "trade_closing_soon": return "Official resale closing soon";
            case "watch_failed": return "Watch check failed";
            case "watch_filtered": return "Watch filtered";
            case "": return "Alert";
            default:
                String readable = code.replace('_', ' ');
                return readable.substring(0, 1).toUpperCase(Locale.ROOT) + readable.substring(1);
        }
    }

    static String labels(String types) {
        if (types == null || types.trim().isEmpty()) {
            return "none";
        }
        StringBuilder result = new StringBuilder();
        for (String type : types.split(",")) {
            if (type.trim().isEmpty()) {
                continue;
            }
            if (result.length() > 0) {
                result.append(", ");
            }
            result.append(label(null, type));
        }
        return result.length() == 0 ? "none" : result.toString();
    }
}
