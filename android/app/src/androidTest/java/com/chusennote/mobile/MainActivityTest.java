package com.chusennote.mobile;

import static androidx.test.espresso.Espresso.onView;
import static androidx.test.espresso.action.ViewActions.click;
import static androidx.test.espresso.action.ViewActions.closeSoftKeyboard;
import static androidx.test.espresso.action.ViewActions.replaceText;
import static androidx.test.espresso.action.ViewActions.scrollTo;
import static androidx.test.espresso.assertion.ViewAssertions.matches;
import static androidx.test.espresso.matcher.ViewMatchers.isDisplayed;
import static androidx.test.espresso.matcher.ViewMatchers.withId;
import static androidx.test.espresso.matcher.ViewMatchers.withText;
import static org.hamcrest.Matchers.containsString;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationManager;
import android.content.Context;
import android.os.SystemClock;

import androidx.test.core.app.ActivityScenario;
import androidx.test.core.app.ApplicationProvider;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.filters.SdkSuppress;
import androidx.test.rule.GrantPermissionRule;

import org.junit.After;
import org.junit.Before;
import org.junit.Rule;
import org.junit.Test;
import org.junit.runner.RunWith;

import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

import okhttp3.mockwebserver.Dispatcher;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;

@RunWith(AndroidJUnit4.class)
@SdkSuppress(minSdkVersion = 33)
public class MainActivityTest {
    private static final String PREFS_NAME = "chusennote";
    private static final String PREF_BASE_URL = "base_url";

    @Rule
    public final GrantPermissionRule notificationPermission =
            GrantPermissionRule.grant(Manifest.permission.POST_NOTIFICATIONS);

    private MockWebServer server;
    private TestDispatcher dispatcher;
    private ActivityScenario<MainActivity> scenario;

    @Before
    public void setUp() throws Exception {
        Context context = ApplicationProvider.getApplicationContext();
        SecureTokenStore.setApiToken(context, "");
        SecureTokenStore.setPushToken(context, "");
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE).edit().clear().commit();

        dispatcher = new TestDispatcher();
        server = new MockWebServer();
        server.setDispatcher(dispatcher);
        server.start();
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .edit()
                .putString(PREF_BASE_URL, server.url("/").toString())
                .commit();

        scenario = ActivityScenario.launch(MainActivity.class);
        assertTrue("Initial refresh did not finish", dispatcher.initialRefresh.await(10, TimeUnit.SECONDS));
        drainMainThread();
    }

    @After
    public void tearDown() throws Exception {
        if (scenario != null) {
            scenario.close();
        }
        if (server != null) {
            server.shutdown();
        }
    }

    @Test
    public void fullFormCanScrollToRegisteredDevices() {
        onView(withId(R.id.registered_devices_heading))
                .perform(scrollTo())
                .check(matches(isDisplayed()));
    }

    @Test
    public void serverReleaseIdentityIsVisible() {
        assertEventuallyDisplayed("Server ok - v0.1.0 (1) - schema 16 - 0 artists, 0 events, 0 alerts.");
    }

    @Test
    public void addEventPostsFormAndRendersRefreshedWatch() throws Exception {
        onView(withId(R.id.event_keyword_input))
                .perform(scrollTo(), replaceText("Runtime Musical"), closeSoftKeyboard());
        onView(withId(R.id.add_event_button)).perform(scrollTo(), click());

        assertTrue("Add Event request was not received", dispatcher.addRequest.await(10, TimeUnit.SECONDS));
        assertTrue("Post-add refresh did not finish", dispatcher.secondRefresh.await(10, TimeUnit.SECONDS));
        drainMainThread();

        Map<String, String> form = parseForm(dispatcher.addBody.get());
        assertEquals("Runtime Musical", form.get("keyword"));
        assertEquals("event", form.get("kind"));
        assertEventuallyDisplayed("Runtime Musical");
    }

    @Test
    public void exactEventSearchAndAddUseSelectedResult() throws Exception {
        onView(withId(R.id.exact_event_input))
                .perform(scrollTo(), replaceText("Runtime Musical"), closeSoftKeyboard());
        onView(withId(R.id.exact_event_search_button)).perform(scrollTo(), click());

        assertTrue("Exact-event search was not received", dispatcher.exactSearch.await(10, TimeUnit.SECONDS));
        assertEventuallyDisplayed("Official Runtime Musical");
        onView(withText("Add Exact Event")).perform(scrollTo(), click());

        assertTrue("Exact-event add was not received", dispatcher.exactAdd.await(10, TimeUnit.SECONDS));
        Map<String, String> form = parseForm(dispatcher.exactAddBody.get());
        assertEquals("Runtime Musical", form.get("keyword"));
        assertEquals("Official Runtime Musical", form.get("title"));
        assertEquals("https://tickets.example/runtime", form.get("url"));
        assertEquals("Official ticket page", form.get("snippet"));
        assertEventuallyDisplayed("Exact event added.");
    }

    @Test
    public void publicCleartextServerCannotReceiveAccountCredentials() {
        onView(withId(R.id.base_url_input)).perform(replaceText("http://tickets.example"));
        onView(withId(R.id.email_input))
                .perform(scrollTo(), replaceText("person@example.com"), closeSoftKeyboard());
        onView(withId(R.id.password_input))
                .perform(scrollTo(), replaceText("not-sent"), closeSoftKeyboard());
        onView(withId(R.id.register_button)).perform(scrollTo(), click());

        onView(withText(BackendUrlPolicy.CREDENTIAL_TRANSPORT_MESSAGE))
                .perform(scrollTo())
                .check(matches(isDisplayed()));
        assertEquals(0, dispatcher.accountRequests.get());
    }

    @Test
    public void accountCredentialsAreNotForwardedThroughRedirects() throws Exception {
        dispatcher.redirectAccount.set(true);
        onView(withId(R.id.email_input))
                .perform(scrollTo(), replaceText("person@example.com"), closeSoftKeyboard());
        onView(withId(R.id.password_input))
                .perform(scrollTo(), replaceText("not-forwarded"), closeSoftKeyboard());
        onView(withId(R.id.register_button)).perform(scrollTo(), click());

        assertTrue("Account request was not received", dispatcher.accountRequest.await(10, TimeUnit.SECONDS));
        assertEventuallyDisplayed("Could not register: HTTP 302");
        assertEquals(0, dispatcher.redirectedCredentialRequests.get());
    }

    @Test
    public void foregroundReminderHasChannelAndAppLaunchIntent() {
        Context context = ApplicationProvider.getApplicationContext();
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        assertNotNull(manager);
        manager.cancelAll();

        Notification notification = ChusennoteMessagingService.showNotification(
                context,
                "Lottery deadline",
                "Apply by 18:00");

        assertNotNull(notification);
        assertEquals(ChusennoteMessagingService.CHANNEL_ID, notification.getChannelId());
        assertEquals("Lottery deadline", notification.extras.getString(Notification.EXTRA_TITLE));
        assertEquals("Apply by 18:00", notification.extras.getString(Notification.EXTRA_TEXT));
        assertNotNull(notification.contentIntent);
        assertEquals(context.getPackageName(), notification.contentIntent.getCreatorPackage());
        assertTrue((notification.flags & Notification.FLAG_AUTO_CANCEL) != 0);
        manager.cancelAll();
    }

    @Test
    public void legacyPushTokenMigratesIntoSecureStorage() {
        Context context = ApplicationProvider.getApplicationContext();
        SecureTokenStore.setPushToken(context, "");
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .edit()
                .putString("push_token", "legacy-fcm-token")
                .commit();

        assertEquals("legacy-fcm-token", ChusennoteMessagingService.savedToken(context));
        assertEquals("legacy-fcm-token", SecureTokenStore.pushToken(context));
        assertFalse(context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .contains("push_token"));
    }

    @Test
    public void eventDetailsShowOrganizerCastAndOfficialResaleSchedule() {
        dispatcher.eventsResponse.set("[{"
                + "\"watch_id\":41,"
                + "\"title\":\"Runtime Stage\","
                + "\"status\":\"lottery_found\","
                + "\"organizers\":[\"Example Productions\"],"
                + "\"lineup\":[\"Example Lead\",\"Example Guest\"],"
                + "\"related_events\":[{"
                + "\"id\":42,"
                + "\"title\":\"Related Stage\","
                + "\"official_url\":\"https://official.example/related\","
                + "\"recommendation_reasons\":[\"Shared venue: Example Hall\"]"
                + "}],"
                + "\"rounds\":[{"
                + "\"name\":\"Official resale\","
                + "\"schedule_label\":\"Resale 2026-07-01 – 2026-07-03\""
                + "}]}]");
        scenario.close();
        scenario = ActivityScenario.launch(MainActivity.class);
        drainMainThread();

        assertEventuallyDisplayed("Example Productions");
        assertEventuallyDisplayed("Example Lead");
        assertEventuallyDisplayed("Related Stage");
        assertEventuallyDisplayed("Shared venue: Example Hall");
        assertEventuallyDisplayed("Ticket rounds found");
        assertEventuallyDisplayed("Resale 2026-07-01 – 2026-07-03");
    }

    @Test
    public void alertHistoryUsesReadableLegacyType() {
        dispatcher.alertsResponse.set("[{"
                + "\"type\":\"trade_opened\","
                + "\"event\":\"Runtime Stage\""
                + "}]");
        scenario.close();
        scenario = ActivityScenario.launch(MainActivity.class);
        drainMainThread();

        assertEventuallyDisplayed("Official resale opened");
    }

    private void drainMainThread() {
        scenario.onActivity(activity -> {
            // ActivityScenario runs this after already-queued main-thread work.
        });
    }

    /**
     * Espresso waits for the main looper, but the app deliberately performs
     * HTTP work on its own executor. Poll the final user-visible state rather
     * than racing the response-to-main-thread handoff.
     */
    private void assertEventuallyDisplayed(String text) {
        long deadline = SystemClock.elapsedRealtime() + 5_000;
        Throwable lastFailure = null;
        while (SystemClock.elapsedRealtime() < deadline) {
            try {
                onView(withText(containsString(text)))
                        .perform(scrollTo())
                        .check(matches(isDisplayed()));
                return;
            } catch (RuntimeException | AssertionError failure) {
                lastFailure = failure;
                SystemClock.sleep(100);
            }
        }
        fail("Timed out waiting for visible text '" + text + "': " + lastFailure);
    }

    private static Map<String, String> parseForm(String body) throws Exception {
        Map<String, String> values = new HashMap<>();
        for (String pair : body.split("&")) {
            String[] parts = pair.split("=", 2);
            values.put(
                    URLDecoder.decode(parts[0], StandardCharsets.UTF_8.name()),
                    URLDecoder.decode(parts.length == 2 ? parts[1] : "", StandardCharsets.UTF_8.name()));
        }
        return values;
    }

    private static final class TestDispatcher extends Dispatcher {
        final CountDownLatch initialRefresh = new CountDownLatch(1);
        final CountDownLatch addRequest = new CountDownLatch(1);
        final CountDownLatch secondRefresh = new CountDownLatch(1);
        final CountDownLatch exactSearch = new CountDownLatch(1);
        final CountDownLatch exactAdd = new CountDownLatch(1);
        final AtomicReference<String> addBody = new AtomicReference<>("");
        final AtomicReference<String> exactAddBody = new AtomicReference<>("");
        final AtomicInteger accountRequests = new AtomicInteger();
        final CountDownLatch accountRequest = new CountDownLatch(1);
        final AtomicBoolean redirectAccount = new AtomicBoolean();
        final AtomicInteger redirectedCredentialRequests = new AtomicInteger();
        final AtomicReference<String> eventsResponse = new AtomicReference<>("[]");
        final AtomicReference<String> alertsResponse = new AtomicReference<>("[]");
        private final AtomicBoolean watchAdded = new AtomicBoolean();
        private final AtomicInteger healthRequests = new AtomicInteger();

        @Override
        public MockResponse dispatch(RecordedRequest request) {
            String path = request.getPath();
            if ("POST".equals(request.getMethod()) && "/api/watchlist".equals(path)) {
                addBody.set(request.getBody().readUtf8());
                watchAdded.set(true);
                addRequest.countDown();
                return json("{\"id\":41,\"keyword\":\"Runtime Musical\",\"kind\":\"event\"}");
            }
            if (path != null && path.startsWith("/api/event/search?")) {
                exactSearch.countDown();
                return json("[{\"title\":\"Official Runtime Musical\","
                        + "\"url\":\"https://tickets.example/runtime\","
                        + "\"snippet\":\"Official ticket page\"}]");
            }
            if ("POST".equals(request.getMethod()) && "/api/event/add".equals(path)) {
                exactAddBody.set(request.getBody().readUtf8());
                exactAdd.countDown();
                return json("{\"id\":42,\"title\":\"Official Runtime Musical\"}");
            }
            if (path != null && path.startsWith("/api/auth/")) {
                accountRequests.incrementAndGet();
                accountRequest.countDown();
                if (redirectAccount.get()) {
                    return new MockResponse()
                            .setResponseCode(302)
                            .setHeader("Location", "/credential-sink");
                }
                return json("{\"error\":\"credentials must not reach the server\"}")
                        .setResponseCode(500);
            }
            if ("/credential-sink".equals(path)) {
                redirectedCredentialRequests.incrementAndGet();
                return json("{\"error\":\"credential redirect followed\"}");
            }
            if (path != null && path.startsWith("/api/watchlist?")) {
                return json(watchAdded.get()
                        ? "[{\"id\":41,\"keyword\":\"Runtime Musical\",\"kind\":\"event\",\"muted\":false}]"
                        : "[]");
            }
            if ("/api/health".equals(path)) {
                int count = healthRequests.incrementAndGet();
                if (count == 1) {
                    initialRefresh.countDown();
                } else if (count == 2) {
                    secondRefresh.countDown();
                }
                return json("{\"status\":\"ok\",\"version\":\"0.1.0\",\"build\":1,"
                        + "\"schema_version\":16,\"tracked_artists\":0,"
                        + "\"tracked_events\":0,\"alerts\":0}");
            }
            if (path != null && path.startsWith("/api/events")) {
                return json(eventsResponse.get());
            }
            if (path != null && path.startsWith("/api/alerts")) {
                return json(alertsResponse.get());
            }
            if (path != null && (path.startsWith("/api/upcoming")
                    || path.startsWith("/api/notifications")
                    || path.startsWith("/api/subscriptions")
                    || path.startsWith("/api/devices")
                    || path.startsWith("/api/sources"))) {
                return json("[]");
            }
            return new MockResponse().setResponseCode(404);
        }

        private MockResponse json(String body) {
            return new MockResponse()
                    .setResponseCode(200)
                    .setHeader("Content-Type", "application/json")
                    .setBody(body);
        }
    }
}
