package com.chusennote.mobile;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public class AlertTypeTextTest {
    @Test
    public void prefersServerProvidedLabel() {
        assertEquals("Server wording", AlertTypeText.label("Server wording", "lottery_opened"));
    }

    @Test
    public void mapsLegacyAlertCodes() {
        assertEquals("Official page found", AlertTypeText.label(null, "new_official_page"));
        assertEquals("Lottery closing soon", AlertTypeText.label("", "lottery_closing_soon"));
        assertEquals("Official resale opened", AlertTypeText.label(null, "trade_opened"));
    }

    @Test
    public void humanizesUnknownCodesAndDefaultsMissingType() {
        assertEquals("Future alert", AlertTypeText.label(null, "future_alert"));
        assertEquals("Alert", AlertTypeText.label(null, null));
    }

    @Test
    public void rendersStoredPreferenceKeysWithoutChangingTheirOrder() {
        assertEquals(
                "Official page found, Official resale opened",
                AlertTypeText.labels("new_official_page,trade_opened"));
        assertEquals("none", AlertTypeText.labels(""));
    }
}
