package com.chusennote.mobile;

import java.util.Calendar;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Short dates shared with the web and iOS apps: {@code 2026-10-26T23:59} reads as {@code Oct 26, 23:59}. */
final class FriendlyDate {
    private static final Pattern ISO = Pattern.compile(
            "(\\d{4})-(\\d{2})-(\\d{2})(?:[T ](\\d{2}):(\\d{2})(?::\\d{2})?(?:\\.\\d+)?(?:[+-]\\d{2}:?\\d{2}|Z)?)?");

    private static final String[] MONTHS = {
            "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"};

    private FriendlyDate() {
    }

    /** The year appears only when it is not this year; free-text clues such as {@code 10月下旬} pass through. */
    static String format(String value, int currentYear) {
        String text = value == null ? "" : value.trim();
        Matcher match = ISO.matcher(text);
        if (!match.matches()) {
            return text;
        }
        int year = Integer.parseInt(match.group(1));
        int month = Integer.parseInt(match.group(2));
        int day = Integer.parseInt(match.group(3));
        if (month < 1 || month > 12 || day < 1 || day > 31) {
            return text;
        }
        StringBuilder label = new StringBuilder().append(MONTHS[month - 1]).append(' ').append(day);
        if (year != currentYear) {
            label.append(", ").append(year);
        }
        if (match.group(4) != null) {
            label.append(", ").append(match.group(4)).append(':').append(match.group(5));
        }
        return label.toString();
    }

    static String format(String value) {
        return format(value, Calendar.getInstance().get(Calendar.YEAR));
    }

    /** Only the date part, for server timestamps recorded in UTC. */
    static String day(String value) {
        String text = value == null ? "" : value.trim();
        return format(text.length() > 10 ? text.substring(0, 10) : text);
    }

    static String range(String start, String end) {
        String from = format(start);
        String to = format(end);
        if (!from.isEmpty() && !to.isEmpty()) {
            return from + " – " + to;
        }
        return from.isEmpty() ? (to.isEmpty() ? "" : "until " + to) : from;
    }

    /** Joins the non-empty parts with middots, the separator every client uses for row details. */
    static String meta(String... parts) {
        StringBuilder joined = new StringBuilder();
        for (String part : parts) {
            if (part == null || part.trim().isEmpty() || "—".equals(part.trim())) {
                continue;
            }
            if (joined.length() > 0) {
                joined.append(" · ");
            }
            joined.append(part.trim());
        }
        return joined.toString();
    }
}
