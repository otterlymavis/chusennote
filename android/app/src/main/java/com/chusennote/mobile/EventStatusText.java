package com.chusennote.mobile;

/** Human-readable event lifecycle text with compatibility for older servers. */
final class EventStatusText {
    private EventStatusText() {
    }

    static String label(String statusLabel, String status) {
        if (statusLabel != null && !statusLabel.trim().isEmpty()) {
            return statusLabel.trim();
        }
        if (status == null) {
            return "Watching";
        }
        switch (status.trim()) {
            case "official_found":
                return "Official page found";
            case "ticket_links_found":
                return "Ticket links found";
            case "lottery_found":
                return "Ticket rounds found";
            case "lottery_open":
                return "Ticket window open";
            case "watching":
            case "":
            default:
                return "Watching";
        }
    }
}
