package com.chusennote.mobile;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class BackendUrlPolicyTest {
    @Test
    public void distributionDefaultUsesProductionHttps() {
        assertEquals("https://chusennote.onrender.com", MainActivity.DEFAULT_BASE_URL);
        assertTrue(BackendUrlPolicy.permitsCredentialTransport(MainActivity.DEFAULT_BASE_URL));
    }

    @Test
    public void permitsHttpsServers() {
        assertTrue(BackendUrlPolicy.permitsCredentialTransport("https://tickets.example/api"));
    }

    @Test
    public void permitsExplicitLocalDevelopmentHosts() {
        assertTrue(BackendUrlPolicy.permitsCredentialTransport("http://localhost:8877"));
        assertTrue(BackendUrlPolicy.permitsCredentialTransport("http://127.0.0.1:8877"));
        assertTrue(BackendUrlPolicy.permitsCredentialTransport("http://10.0.2.2:8877"));
        assertTrue(BackendUrlPolicy.permitsCredentialTransport("http://192.168.1.20:8877"));
        assertTrue(BackendUrlPolicy.permitsCredentialTransport("http://[::1]:8877"));
    }

    @Test
    public void rejectsPublicCleartextAndNonHttpUrls() {
        assertFalse(BackendUrlPolicy.permitsCredentialTransport("http://tickets.example"));
        assertFalse(BackendUrlPolicy.permitsCredentialTransport("http://8.8.8.8:8877"));
        assertFalse(BackendUrlPolicy.permitsCredentialTransport("ftp://192.168.1.20/data"));
    }

    @Test
    public void rejectsAmbiguousOrCredentialBearingUrls() {
        assertFalse(BackendUrlPolicy.permitsCredentialTransport("https://user:pass@tickets.example"));
        assertFalse(BackendUrlPolicy.permitsCredentialTransport("http://192.168.1.20.example"));
        assertFalse(BackendUrlPolicy.permitsCredentialTransport("not a url"));
        assertFalse(BackendUrlPolicy.permitsCredentialTransport(""));
        assertFalse(BackendUrlPolicy.permitsCredentialTransport(null));
    }
}
