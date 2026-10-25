package com.chusennote.mobile;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public class FriendlyDateTest {
    private static final int TODAY = 2026;

    @Test
    public void dropsCurrentYearAndKeepsTime() {
        assertEquals("Oct 26, 23:59", FriendlyDate.format("2026-10-26T23:59", TODAY));
    }

    @Test
    public void showsYearOutsideCurrentYear() {
        assertEquals("Jan 16, 2027", FriendlyDate.format("2027-01-16", TODAY));
    }

    @Test
    public void passesFreeTextThrough() {
        assertEquals("10月下旬", FriendlyDate.format("10月下旬", TODAY));
        assertEquals("", FriendlyDate.format(null, TODAY));
    }

    @Test
    public void metaSkipsEmptyParts() {
        assertEquals("Open now · e+", FriendlyDate.meta("Open now", "", null, "—", "e+"));
    }
}
