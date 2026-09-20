package com.chusennote.mobile;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public class EventStatusTextTest {
    @Test
    public void prefersServerProvidedLabel() {
        assertEquals("Server wording", EventStatusText.label("Server wording", "lottery_open"));
    }

    @Test
    public void mapsLegacyLifecycleCodes() {
        assertEquals("Official page found", EventStatusText.label(null, "official_found"));
        assertEquals("Ticket links found", EventStatusText.label("", "ticket_links_found"));
        assertEquals("Ticket rounds found", EventStatusText.label(null, "lottery_found"));
        assertEquals("Ticket window open", EventStatusText.label(null, "lottery_open"));
        assertEquals("Watching", EventStatusText.label(null, "watching"));
    }

    @Test
    public void defaultsMissingOrUnknownStateToWatching() {
        assertEquals("Watching", EventStatusText.label(null, null));
        assertEquals("Watching", EventStatusText.label(null, "future_state"));
    }
}
