package com.chusennote.mobile;

import java.net.InetAddress;
import java.net.URI;
import java.util.Locale;

/** Transport rules for account credentials and device-addressing FCM tokens. */
final class BackendUrlPolicy {
    static final String CREDENTIAL_TRANSPORT_MESSAGE =
            "Use HTTPS, localhost, or a literal private-network IP before signing in.";

    private BackendUrlPolicy() {
    }

    /**
     * Public servers must use HTTPS. Cleartext HTTP remains available for the
     * local-first workflow only when the host is an explicit loopback or
     * private-network IP, avoiding DNS names that could resolve elsewhere.
     */
    static boolean permitsCredentialTransport(String baseUrl) {
        if (baseUrl == null) {
            return false;
        }
        try {
            URI uri = new URI(baseUrl.trim());
            if (uri.getUserInfo() != null || uri.getHost() == null) {
                return false;
            }
            String scheme = uri.getScheme();
            if ("https".equalsIgnoreCase(scheme)) {
                return true;
            }
            if (!"http".equalsIgnoreCase(scheme)) {
                return false;
            }
            String host = stripIpv6Brackets(uri.getHost()).toLowerCase(Locale.ROOT);
            if ("localhost".equals(host)) {
                return true;
            }
            if (!looksLikeIpLiteral(host)) {
                return false;
            }
            InetAddress address = InetAddress.getByName(host);
            return address.isLoopbackAddress()
                    || address.isSiteLocalAddress()
                    || address.isLinkLocalAddress();
        } catch (Exception ignored) {
            return false;
        }
    }

    private static boolean looksLikeIpLiteral(String host) {
        return host.indexOf(':') >= 0 || host.matches("[0-9.]+");
    }

    private static String stripIpv6Brackets(String host) {
        if (host.startsWith("[") && host.endsWith("]")) {
            return host.substring(1, host.length() - 1);
        }
        return host;
    }
}
