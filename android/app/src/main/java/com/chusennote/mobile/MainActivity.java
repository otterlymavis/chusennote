package com.chusennote.mobile;

import android.Manifest;
import android.app.Activity;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.Editable;
import android.text.InputType;
import android.text.TextWatcher;
import android.text.method.PasswordTransformationMethod;

import com.google.firebase.messaging.FirebaseMessaging;
import com.google.android.gms.tasks.Tasks;
import android.view.View;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.OutputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.net.URLEncoder;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

public class MainActivity extends Activity {
    private static final String PREFS_NAME = "chusennote";
    private static final String PREF_BASE_URL = "base_url";
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private volatile String apiBaseUrl = "";
    private volatile boolean destroyed;
    private EditText baseUrlInput;
    private TextView accountStatusText;
    private EditText emailInput;
    private EditText passwordInput;
    private Button registerButton;
    private Button loginButton;
    private Button logoutButton;
    private EditText artistInput;
    private EditText artistTagsInput;
    private EditText artistRegionsInput;
    private EditText artistVenuesInput;
    private EditText eventInput;
    private EditText eventTagsInput;
    private EditText eventRegionsInput;
    private EditText eventVenuesInput;
    private EditText eventAlertsInput;
    private EditText exactEventInput;
    private Button exactEventSearchButton;
    private LinearLayout exactEventResultList;
    private String exactEventSearchKeyword = "";
    private boolean exactEventActionInFlight;
    private EditText sourceWatchInput;
    private EditText sourceUrlInput;
    private EditText sourceLabelInput;
    private CheckBox sourcePrivateNoteInput;
    private LinearLayout artistList;
    private LinearLayout eventList;
    private LinearLayout mutedWatchList;
    private LinearLayout needsAttentionList;
    private LinearLayout sourceList;
    private LinearLayout mutedSourceList;
    private LinearLayout alertList;
    private LinearLayout notificationFeedList;
    private LinearLayout subscriptionList;
    private LinearLayout deviceList;
    private TextView statusText;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(buildLayout());
        ensureNotificationChannel();
        requestNotificationPermissionIfNeeded();
        setSignedInControls(!SecureTokenStore.apiToken(getApplicationContext()).isEmpty());
        registerPushToken();
        refreshAccountStatus();
        refresh();
    }

    @Override
    protected void onDestroy() {
        destroyed = true;
        mainHandler.removeCallbacksAndMessages(null);
        executor.shutdownNow();
        super.onDestroy();
    }

    private void postToMain(Runnable action) {
        if (destroyed) {
            return;
        }
        mainHandler.post(() -> {
            if (!destroyed) {
                action.run();
            }
        });
    }

    private void ensureNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationManager manager = getSystemService(NotificationManager.class);
            if (manager != null) {
                NotificationChannel channel = new NotificationChannel(
                        ChusennoteMessagingService.CHANNEL_ID,
                        "Ticket reminders",
                        NotificationManager.IMPORTANCE_HIGH);
                channel.setDescription("Lottery application, results, payment, and sale reminders.");
                manager.createNotificationChannel(channel);
            }
        }
    }

    private void requestNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 1001);
        }
    }

    private void registerPushToken() {
        saveBaseUrl();
        try {
            FirebaseMessaging.getInstance().getToken().addOnCompleteListener(task -> {
                if (task.isSuccessful() && task.getResult() != null) {
                    ChusennoteMessagingService.registerToken(getApplicationContext(), task.getResult());
                }
            });
        } catch (Throwable error) {
            // Firebase is not configured (no google-services.json) — push stays off.
            statusText.setText(R.string.push_disabled);
        }
    }

    private View buildLayout() {
        ScrollView scrollView = new ScrollView(this);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(32, 32, 32, 32);
        scrollView.addView(
                root,
                new ScrollView.LayoutParams(
                        ScrollView.LayoutParams.MATCH_PARENT,
                        ScrollView.LayoutParams.WRAP_CONTENT));

        TextView title = heading("chusennote");
        root.addView(title);

        statusText = body("Connect to the local chusennote server.");
        statusText.setId(R.id.status_text);
        root.addView(statusText);

        baseUrlInput = new EditText(this);
        baseUrlInput.setId(R.id.base_url_input);
        baseUrlInput.setSingleLine(true);
        apiBaseUrl = normalizeBaseUrl(preferences().getString(PREF_BASE_URL, "http://10.0.2.2:8877"));
        baseUrlInput.setText(apiBaseUrl);
        baseUrlInput.setHint("API base URL");
        baseUrlInput.addTextChangedListener(new TextWatcher() {
            @Override
            public void beforeTextChanged(CharSequence text, int start, int count, int after) {}

            @Override
            public void onTextChanged(CharSequence text, int start, int before, int count) {}

            @Override
            public void afterTextChanged(Editable text) {
                apiBaseUrl = normalizeBaseUrl(text.toString());
                preferences().edit().putString(PREF_BASE_URL, apiBaseUrl).apply();
            }
        });
        root.addView(baseUrlInput);

        Button refresh = new Button(this);
        refresh.setText(R.string.refresh);
        refresh.setOnClickListener(view -> {
            saveBaseUrl();
            refresh();
        });
        root.addView(refresh);

        Button calendar = new Button(this);
        calendar.setText(R.string.open_calendar_feed);
        calendar.setOnClickListener(view -> openCalendarFeed());
        root.addView(calendar);

        root.addView(section("Account"));
        accountStatusText = body("Not signed in.");
        root.addView(accountStatusText);
        emailInput = new EditText(this);
        emailInput.setId(R.id.email_input);
        emailInput.setSingleLine(true);
        emailInput.setHint("Email");
        emailInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_EMAIL_ADDRESS);
        root.addView(emailInput);
        passwordInput = new EditText(this);
        passwordInput.setId(R.id.password_input);
        passwordInput.setSingleLine(true);
        passwordInput.setHint("Password");
        passwordInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        passwordInput.setTransformationMethod(PasswordTransformationMethod.getInstance());
        root.addView(passwordInput);
        LinearLayout accountButtons = new LinearLayout(this);
        accountButtons.setOrientation(LinearLayout.HORIZONTAL);
        registerButton = new Button(this);
        registerButton.setId(R.id.register_button);
        registerButton.setText(R.string.register);
        registerButton.setOnClickListener(view -> registerAccount());
        accountButtons.addView(registerButton);
        loginButton = new Button(this);
        loginButton.setText(R.string.log_in);
        loginButton.setOnClickListener(view -> loginAccount());
        accountButtons.addView(loginButton);
        logoutButton = new Button(this);
        logoutButton.setText(R.string.log_out);
        logoutButton.setOnClickListener(view -> logoutAccount());
        accountButtons.addView(logoutButton);
        root.addView(accountButtons);

        root.addView(section("Tracked Artists"));
        artistInput = new EditText(this);
        artistInput.setSingleLine(true);
        artistInput.setHint("Artist keyword");
        root.addView(artistInput);
        artistTagsInput = singleLineInput("Tags");
        root.addView(artistTagsInput);
        artistRegionsInput = singleLineInput("Preferred regions");
        root.addView(artistRegionsInput);
        artistVenuesInput = singleLineInput("Preferred venues");
        root.addView(artistVenuesInput);
        Button addArtist = new Button(this);
        addArtist.setText(R.string.add_artist);
        addArtist.setOnClickListener(view -> addWatch("artist", artistInput, artistTagsInput, artistRegionsInput, artistVenuesInput, null));
        root.addView(addArtist);
        Button runArtists = new Button(this);
        runArtists.setText(R.string.run_artist_watches);
        runArtists.setOnClickListener(view -> runWatches("artist"));
        root.addView(runArtists);
        artistList = new LinearLayout(this);
        artistList.setOrientation(LinearLayout.VERTICAL);
        root.addView(artistList);

        root.addView(section("Tracked Events"));
        eventInput = new EditText(this);
        eventInput.setId(R.id.event_keyword_input);
        eventInput.setSingleLine(true);
        eventInput.setHint("Event keyword");
        root.addView(eventInput);
        eventTagsInput = singleLineInput("Tags");
        root.addView(eventTagsInput);
        eventRegionsInput = singleLineInput("Preferred regions");
        root.addView(eventRegionsInput);
        eventVenuesInput = singleLineInput("Preferred venues");
        root.addView(eventVenuesInput);
        eventAlertsInput = singleLineInput("Alert types");
        root.addView(eventAlertsInput);
        Button addEvent = new Button(this);
        addEvent.setId(R.id.add_event_button);
        addEvent.setText(R.string.add_event);
        addEvent.setOnClickListener(view -> addWatch("event", eventInput, eventTagsInput, eventRegionsInput, eventVenuesInput, eventAlertsInput));
        root.addView(addEvent);
        Button runEvents = new Button(this);
        runEvents.setText(R.string.run_event_watches);
        runEvents.setOnClickListener(view -> runWatches("event"));
        root.addView(runEvents);
        eventList = new LinearLayout(this);
        eventList.setId(R.id.event_list);
        eventList.setOrientation(LinearLayout.VERTICAL);
        root.addView(eventList);

        root.addView(section("Find Exact Event"));
        exactEventInput = singleLineInput("Event name");
        exactEventInput.setId(R.id.exact_event_input);
        root.addView(exactEventInput);
        exactEventSearchButton = new Button(this);
        exactEventSearchButton.setId(R.id.exact_event_search_button);
        exactEventSearchButton.setText(R.string.search_events);
        exactEventSearchButton.setOnClickListener(view -> searchExactEvents());
        root.addView(exactEventSearchButton);
        exactEventResultList = new LinearLayout(this);
        exactEventResultList.setId(R.id.exact_event_result_list);
        exactEventResultList.setOrientation(LinearLayout.VERTICAL);
        exactEventResultList.addView(body("Search for an official event page."));
        root.addView(exactEventResultList);

        root.addView(section("Muted Watches"));
        mutedWatchList = new LinearLayout(this);
        mutedWatchList.setOrientation(LinearLayout.VERTICAL);
        root.addView(mutedWatchList);

        root.addView(section("Needs Attention"));
        needsAttentionList = new LinearLayout(this);
        needsAttentionList.setOrientation(LinearLayout.VERTICAL);
        root.addView(needsAttentionList);

        root.addView(section("Manual Event Source"));
        sourceWatchInput = new EditText(this);
        sourceWatchInput.setSingleLine(true);
        sourceWatchInput.setHint("Tracked event watch id or keyword");
        root.addView(sourceWatchInput);
        sourceUrlInput = new EditText(this);
        sourceUrlInput.setSingleLine(true);
        sourceUrlInput.setHint("Ticket or source URL");
        root.addView(sourceUrlInput);
        sourceLabelInput = new EditText(this);
        sourceLabelInput.setSingleLine(true);
        sourceLabelInput.setHint("Label");
        root.addView(sourceLabelInput);
        sourcePrivateNoteInput = new CheckBox(this);
        sourcePrivateNoteInput.setText(R.string.private_note);
        root.addView(sourcePrivateNoteInput);
        Button addSource = new Button(this);
        addSource.setText(R.string.add_source);
        addSource.setOnClickListener(view -> addSource());
        root.addView(addSource);
        sourceList = new LinearLayout(this);
        sourceList.setOrientation(LinearLayout.VERTICAL);
        root.addView(sourceList);
        root.addView(section("Muted Sources"));
        mutedSourceList = new LinearLayout(this);
        mutedSourceList.setOrientation(LinearLayout.VERTICAL);
        root.addView(mutedSourceList);

        root.addView(section("Recent Alerts"));
        alertList = new LinearLayout(this);
        alertList.setOrientation(LinearLayout.VERTICAL);
        root.addView(alertList);

        root.addView(section("Due Reminders"));
        Button runNotifications = new Button(this);
        runNotifications.setText(R.string.run_due_reminders);
        runNotifications.setOnClickListener(view -> runNotifications());
        root.addView(runNotifications);
        notificationFeedList = new LinearLayout(this);
        notificationFeedList.setOrientation(LinearLayout.VERTICAL);
        root.addView(notificationFeedList);

        root.addView(section("Notification Subscriptions"));
        subscriptionList = new LinearLayout(this);
        subscriptionList.setOrientation(LinearLayout.VERTICAL);
        root.addView(subscriptionList);

        TextView registeredDevicesHeading = section("Registered Devices");
        registeredDevicesHeading.setId(R.id.registered_devices_heading);
        root.addView(registeredDevicesHeading);
        deviceList = new LinearLayout(this);
        deviceList.setOrientation(LinearLayout.VERTICAL);
        root.addView(deviceList);

        return scrollView;
    }

    private void refresh() {
        saveBaseUrl();
        statusText.setText(R.string.loading);
        executor.execute(() -> {
            try {
                JSONArray watches = getJsonArray("/api/watchlist?include_muted=1");
                JSONArray events = getJsonArray("/api/events");
                JSONArray upcoming = getJsonArray("/api/upcoming");
                JSONArray alerts = getJsonArray("/api/alerts");
                JSONArray notificationFeed = getJsonArray("/api/notifications?limit=100");
                JSONArray subscriptions = getJsonArray("/api/subscriptions");
                JSONArray devices = getJsonArray("/api/devices");
                JSONArray sources = getJsonArray("/api/sources?include_muted=1");
                JSONObject health = getJsonObject("/api/health");
                postToMain(() -> render(
                        watches,
                        events,
                        upcoming,
                        alerts,
                        notificationFeed,
                        subscriptions,
                        devices,
                        sources,
                        health));
            } catch (Exception error) {
                postToMain(() -> statusText.setText(getString(R.string.could_not_load, error.getMessage())));
            }
        });
    }

    private SharedPreferences preferences() {
        return getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
    }

    private void saveBaseUrl() {
        apiBaseUrl = normalizeBaseUrl(baseUrlInput.getText().toString());
        preferences().edit().putString(PREF_BASE_URL, apiBaseUrl).apply();
    }

    private String normalizeBaseUrl(String value) {
        return value == null ? "" : value.trim().replaceAll("/+$", "");
    }

    private void openCalendarFeed() {
        saveBaseUrl();
        String baseUrl = apiBaseUrl;
        if (baseUrl.isEmpty()) {
            statusText.setText(R.string.enter_api_base_url);
            return;
        }
        if (SecureTokenStore.apiToken(getApplicationContext()).isEmpty()) {
            openUrl(baseUrl + "/calendar.ics");
            return;
        }
        // Signed-in: authorize the feed with a calendar-only token (minted
        // separately from, and unusable as, the account's full API token) so
        // this shareable URL can't be replayed to mutate the account.
        executor.execute(() -> {
            try {
                String calendarToken = SecureTokenStore.calendarToken(getApplicationContext());
                if (calendarToken.isEmpty()) {
                    calendarToken = new JSONObject(postForm("/api/calendar/token", "")).getString("token");
                    SecureTokenStore.setCalendarToken(getApplicationContext(), calendarToken);
                }
                Uri uri = Uri.parse(baseUrl + "/calendar.ics").buildUpon()
                        .appendQueryParameter("token", calendarToken)
                        .build();
                postToMain(() -> openUrl(uri.toString()));
            } catch (Exception error) {
                postToMain(() -> statusText.setText(getString(R.string.could_not_open_calendar, error.getMessage())));
            }
        });
    }

    private void openUrl(String url) {
        if (!isWebUrl(url)) {
            statusText.setText(R.string.no_web_url);
            return;
        }
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url.trim())));
        } catch (RuntimeException error) {
            statusText.setText(getString(R.string.could_not_open_link, error.getMessage()));
        }
    }

    private boolean isWebUrl(String url) {
        if (url == null) {
            return false;
        }
        String normalized = url.trim().toLowerCase(Locale.ROOT);
        return normalized.startsWith("https://") || normalized.startsWith("http://");
    }

    private void addWatch(String kind, EditText input, EditText tagsInput, EditText regionsInput, EditText venuesInput, EditText alertsInput) {
        String keyword = input.getText().toString().trim();
        if (keyword.isEmpty()) {
            statusText.setText(R.string.enter_keyword);
            return;
        }
        String tags = tagsInput == null ? "" : tagsInput.getText().toString().trim();
        String regions = regionsInput == null ? "" : regionsInput.getText().toString().trim();
        String venues = venuesInput == null ? "" : venuesInput.getText().toString().trim();
        String alerts = alertsInput == null ? "" : alertsInput.getText().toString().trim();
        statusText.setText(getString(R.string.adding_watch, kind));
        executor.execute(() -> {
            try {
                String body = "keyword=" + encode(keyword)
                    + "&kind=" + encode(kind)
                    + "&tags=" + encode(tags)
                    + "&regions=" + encode(regions)
                    + "&venues=" + encode(venues);
                if (!alerts.isEmpty()) {
                    body = body + "&alerts=" + encode(alerts);
                }
                postForm("/api/watchlist", body);
                postToMain(() -> {
                    input.setText("");
                    if (tagsInput != null) {
                        tagsInput.setText("");
                    }
                    if (regionsInput != null) {
                        regionsInput.setText("");
                    }
                    if (venuesInput != null) {
                        venuesInput.setText("");
                    }
                    if (alertsInput != null) {
                        alertsInput.setText("");
                    }
                    refresh();
                });
            } catch (Exception error) {
                postToMain(() -> statusText.setText(getString(R.string.could_not_add_watch, error.getMessage())));
            }
        });
    }

    private void runWatches(String kind) {
        statusText.setText(getString(R.string.running_watches, kind));
        executor.execute(() -> {
            try {
                JSONArray alerts = postJsonArray("/api/run", "kind=" + encode(kind));
                postToMain(() -> {
                    statusText.setText(getResources().getQuantityString(
                            R.plurals.run_complete_alerts,
                            alerts.length(),
                            alerts.length()));
                    refresh();
                });
            } catch (Exception error) {
                postToMain(() -> statusText.setText(getString(R.string.could_not_run_watches, error.getMessage())));
            }
        });
    }

    private void runNotifications() {
        statusText.setText(R.string.sending_due_reminders);
        executor.execute(() -> {
            try {
                JSONArray delivered = postJsonArray("/api/notifications/run", "");
                postToMain(() -> {
                    statusText.setText(getResources().getQuantityString(
                            R.plurals.reminders_delivered,
                            delivered.length(),
                            delivered.length()));
                    refresh();
                });
            } catch (Exception error) {
                postToMain(() -> statusText.setText(
                        getString(R.string.could_not_send_reminders, error.getMessage())));
            }
        });
    }

    private void addSubscription(int watchId, String scope) {
        addSubscription(watchId, scope, "", "");
    }

    private void addSubscription(int watchId, String scope, String location, String roundKey) {
        statusText.setText(R.string.adding_subscription);
        executor.execute(() -> {
            try {
                postForm(
                        "/api/subscriptions",
                        "watch=" + encode(String.valueOf(watchId))
                                + "&scope=" + encode(scope)
                                + "&location=" + encode(location)
                                + "&round_key=" + encode(roundKey)
                                + "&channels=" + encode("feed,push")
                                + "&lead_days=" + encode("7,1,0"));
                postToMain(this::refresh);
            } catch (Exception error) {
                postToMain(() -> statusText.setText(
                        getString(R.string.could_not_add_subscription, error.getMessage())));
            }
        });
    }

    private void removeSubscription(int id) {
        statusText.setText(R.string.removing_subscription);
        executor.execute(() -> {
            try {
                postForm("/api/subscriptions/remove", "identifier=" + encode(String.valueOf(id)));
                postToMain(this::refresh);
            } catch (Exception error) {
                postToMain(() -> statusText.setText(
                        getString(R.string.could_not_remove_subscription, error.getMessage())));
            }
        });
    }

    private void searchExactEvents() {
        String keyword = exactEventInput.getText().toString().trim();
        if (keyword.isEmpty()) {
            statusText.setText(R.string.enter_event_name);
            return;
        }
        if (exactEventActionInFlight) {
            statusText.setText(R.string.wait_for_event_action);
            return;
        }
        exactEventActionInFlight = true;
        exactEventSearchButton.setEnabled(false);
        statusText.setText(R.string.searching_exact_events);
        executor.execute(() -> {
            try {
                JSONArray results = getJsonArray(
                        "/api/event/search?keyword=" + encode(keyword) + "&limit=6");
                postToMain(() -> {
                    exactEventSearchKeyword = keyword;
                    renderExactEventResults(results);
                    exactEventActionInFlight = false;
                    exactEventSearchButton.setEnabled(true);
                    statusText.setText(results.length() == 0
                            ? getString(R.string.no_matching_event_pages)
                            : getResources().getQuantityString(
                                    R.plurals.found_event_pages,
                                    results.length(),
                                    results.length()));
                });
            } catch (Exception error) {
                postToMain(() -> {
                    exactEventActionInFlight = false;
                    exactEventSearchButton.setEnabled(true);
                    statusText.setText(getString(R.string.could_not_search_events, error.getMessage()));
                });
            }
        });
    }

    private void renderExactEventResults(JSONArray results) {
        exactEventResultList.removeAllViews();
        if (results.length() == 0) {
            exactEventResultList.addView(body("No matching official pages found."));
            return;
        }
        for (int i = 0; i < results.length(); i++) {
            JSONObject result = results.optJSONObject(i);
            if (result == null) {
                continue;
            }
            String title = result.optString("title", "").trim();
            String url = result.optString("url", "").trim();
            String snippet = result.optString("snippet", "").trim();
            String displayTitle = title.isEmpty() ? url : title;
            String detail = url + (snippet.isEmpty() ? "" : "\n" + snippet);
            if (isWebUrl(url)) {
                exactEventResultList.addView(twoActionCard(
                        displayTitle,
                        detail,
                        "Open",
                        () -> openUrl(url),
                        "Add Exact Event",
                        () -> addExactEvent(exactEventSearchKeyword, title, url, snippet)));
            } else {
                exactEventResultList.addView(card(displayTitle, detail));
            }
        }
        if (exactEventResultList.getChildCount() == 0) {
            exactEventResultList.addView(body("No valid event pages found."));
        }
    }

    private void addExactEvent(String keyword, String title, String url, String snippet) {
        if (keyword.isEmpty() || !isWebUrl(url)) {
            statusText.setText(R.string.pick_valid_event_page);
            return;
        }
        if (exactEventActionInFlight) {
            statusText.setText(R.string.wait_for_event_action);
            return;
        }
        exactEventActionInFlight = true;
        exactEventSearchButton.setEnabled(false);
        statusText.setText(R.string.adding_exact_event);
        executor.execute(() -> {
            try {
                postForm(
                        "/api/event/add",
                        "keyword=" + encode(keyword)
                                + "&title=" + encode(title)
                                + "&url=" + encode(url)
                                + "&snippet=" + encode(snippet));
                postToMain(() -> {
                    exactEventInput.setText("");
                    exactEventSearchKeyword = "";
                    exactEventActionInFlight = false;
                    exactEventSearchButton.setEnabled(true);
                    exactEventResultList.removeAllViews();
                    exactEventResultList.addView(body("Exact event added."));
                    refresh();
                });
            } catch (Exception error) {
                postToMain(() -> {
                    exactEventActionInFlight = false;
                    exactEventSearchButton.setEnabled(true);
                    statusText.setText(getString(R.string.could_not_add_exact_event, error.getMessage()));
                });
            }
        });
    }

    private void addSource() {
        String watch = sourceWatchInput.getText().toString().trim();
        String url = sourceUrlInput.getText().toString().trim();
        String label = sourceLabelInput.getText().toString().trim();
        boolean privateNote = sourcePrivateNoteInput.isChecked();
        if (watch.isEmpty() || url.isEmpty()) {
            statusText.setText(R.string.enter_watch_and_source);
            return;
        }
        statusText.setText(R.string.adding_source);
        executor.execute(() -> {
            try {
                postForm(
                    "/api/sources",
                    "watch=" + encode(watch)
                        + "&url=" + encode(url)
                        + "&label=" + encode(label)
                        + (privateNote ? "&private_note=1" : "")
                );
                postToMain(() -> {
                    sourceWatchInput.setText("");
                    sourceUrlInput.setText("");
                    sourceLabelInput.setText("");
                    sourcePrivateNoteInput.setChecked(false);
                    refresh();
                });
            } catch (Exception error) {
                postToMain(() -> statusText.setText(getString(R.string.could_not_add_source, error.getMessage())));
            }
        });
    }

    private void removeWatch(int id) {
        statusText.setText(R.string.removing_watch);
        executor.execute(() -> {
            try {
                postForm("/api/watchlist/remove", "identifier=" + encode(String.valueOf(id)));
                postToMain(this::refresh);
            } catch (Exception error) {
                postToMain(() -> statusText.setText(getString(R.string.could_not_remove_watch, error.getMessage())));
            }
        });
    }

    private void restoreWatch(int id) {
        statusText.setText(R.string.restoring_watch);
        executor.execute(() -> {
            try {
                postForm("/api/watchlist/unmute", "identifier=" + encode(String.valueOf(id)));
                postToMain(this::refresh);
            } catch (Exception error) {
                postToMain(() -> statusText.setText(getString(R.string.could_not_restore_watch, error.getMessage())));
            }
        });
    }

    private void removeSource(int id) {
        statusText.setText(R.string.removing_source);
        executor.execute(() -> {
            try {
                postForm("/api/sources/remove", "identifier=" + encode(String.valueOf(id)));
                postToMain(this::refresh);
            } catch (Exception error) {
                postToMain(() -> statusText.setText(getString(R.string.could_not_remove_source, error.getMessage())));
            }
        });
    }

    private void restoreSource(int id) {
        statusText.setText(R.string.restoring_source);
        executor.execute(() -> {
            try {
                postForm("/api/sources/unmute", "identifier=" + encode(String.valueOf(id)));
                postToMain(this::refresh);
            } catch (Exception error) {
                postToMain(() -> statusText.setText(getString(R.string.could_not_restore_source, error.getMessage())));
            }
        });
    }

    private JSONArray getJsonArray(String path) throws Exception {
        return new JSONArray(getText(path));
    }

    private JSONObject getJsonObject(String path) throws Exception {
        return new JSONObject(getText(path));
    }

    private String getText(String path) throws Exception {
        URL url = new URL(apiBaseUrl + path);
        HttpURLConnection connection = (HttpURLConnection) url.openConnection();
        try {
            connection.setRequestMethod("GET");
            connection.setInstanceFollowRedirects(false);
            connection.setConnectTimeout(5000);
            connection.setReadTimeout(5000);
            applyAuthorization(connection);
            int responseCode = connection.getResponseCode();
            if (responseCode < 200 || responseCode >= 300) {
                throw new HttpStatusException(responseCode);
            }
            try (BufferedReader reader = new BufferedReader(new InputStreamReader(
                    connection.getInputStream(), StandardCharsets.UTF_8))) {
                StringBuilder body = new StringBuilder();
                String line;
                while ((line = reader.readLine()) != null) {
                    body.append(line);
                }
                return body.toString();
            }
        } finally {
            connection.disconnect();
        }
    }

    private static final class HttpStatusException extends IOException {
        final int statusCode;

        HttpStatusException(int statusCode) {
            super("HTTP " + statusCode);
            this.statusCode = statusCode;
        }
    }

    private String postForm(String path, String body) throws Exception {
        byte[] data = body.getBytes(StandardCharsets.UTF_8);
        URL url = new URL(apiBaseUrl + path);
        HttpURLConnection connection = (HttpURLConnection) url.openConnection();
        try {
            connection.setRequestMethod("POST");
            connection.setInstanceFollowRedirects(false);
            connection.setDoOutput(true);
            connection.setConnectTimeout(5000);
            connection.setReadTimeout(30000);
            connection.setRequestProperty("Content-Type", "application/x-www-form-urlencoded");
            connection.setRequestProperty("Content-Length", String.valueOf(data.length));
            applyAuthorization(connection);
            try (OutputStream output = connection.getOutputStream()) {
                output.write(data);
            }
            int responseCode = connection.getResponseCode();
            if (responseCode < 200 || responseCode >= 300) {
                throw new HttpStatusException(responseCode);
            }
            try (BufferedReader reader = new BufferedReader(new InputStreamReader(
                    connection.getInputStream(), StandardCharsets.UTF_8))) {
                StringBuilder response = new StringBuilder();
                String line;
                while ((line = reader.readLine()) != null) {
                    response.append(line);
                }
                return response.toString();
            }
        } finally {
            connection.disconnect();
        }
    }

    /** Attaches the signed-in account's bearer token, if any, to a request. */
    private void applyAuthorization(HttpURLConnection connection) {
        String token = SecureTokenStore.apiToken(getApplicationContext());
        if (!token.isEmpty()) {
            if (!BackendUrlPolicy.permitsCredentialTransport(apiBaseUrl)) {
                throw new IllegalStateException(BackendUrlPolicy.CREDENTIAL_TRANSPORT_MESSAGE);
            }
            connection.setRequestProperty("Authorization", "Bearer " + token);
        }
    }

    private JSONArray postJsonArray(String path, String body) throws Exception {
        return new JSONArray(postForm(path, body));
    }

    private String encode(String value) throws Exception {
        return URLEncoder.encode(value, StandardCharsets.UTF_8.name());
    }

    private void registerAccount() {
        submitAccountForm(R.string.registering, "/api/auth/register", R.string.could_not_register);
    }

    private void loginAccount() {
        submitAccountForm(R.string.logging_in, "/api/auth/login", R.string.could_not_log_in);
    }

    private void submitAccountForm(int progressMessage, String path, int errorMessage) {
        if (!SecureTokenStore.apiToken(getApplicationContext()).isEmpty()) {
            accountStatusText.setText(R.string.log_out_before_changing_account);
            return;
        }
        String email = emailInput.getText().toString().trim();
        String password = passwordInput.getText().toString();
        if (email.isEmpty() || password.isEmpty()) {
            statusText.setText(R.string.enter_email_and_password);
            return;
        }
        if (!BackendUrlPolicy.permitsCredentialTransport(apiBaseUrl)) {
            statusText.setText(BackendUrlPolicy.CREDENTIAL_TRANSPORT_MESSAGE);
            return;
        }
        setAccountTransitionControls();
        accountStatusText.setText(progressMessage);
        executor.execute(() -> {
            try {
                String body = "email=" + encode(email) + "&password=" + encode(password);
                JSONObject response = new JSONObject(postForm(path, body));
                String token = response.getString("token");
                String signedInEmail = response.getJSONObject("user").getString("email");
                SecureTokenStore.setApiToken(getApplicationContext(), token);
                postToMain(() -> {
                    passwordInput.setText("");
                    accountStatusText.setText(getString(R.string.signed_in_as, signedInEmail));
                    setSignedInControls(true);
                    registerPushToken();
                    refresh();
                });
            } catch (Exception error) {
                postToMain(() -> {
                    accountStatusText.setText(getString(errorMessage, error.getMessage()));
                    setSignedInControls(false);
                });
            }
        });
    }

    private void logoutAccount() {
        boolean wasSignedIn = !SecureTokenStore.apiToken(getApplicationContext()).isEmpty();
        if (!wasSignedIn) {
            accountStatusText.setText(R.string.not_signed_in);
            return;
        }
        setAccountTransitionControls();
        accountStatusText.setText(R.string.signing_out);
        executor.execute(() -> {
            try {
                synchronized (ChusennoteMessagingService.REGISTRATION_LOCK) {
                    String pushToken = ChusennoteMessagingService.savedToken(getApplicationContext());
                    if (pushToken.isEmpty()) {
                        // Upgrades may predate our persisted FCM token. Resolve
                        // it before logout rather than leave that registration.
                        FirebaseMessaging messaging = null;
                        try {
                            messaging = FirebaseMessaging.getInstance();
                        } catch (IllegalStateException notConfigured) {
                            // Builds without Firebase have no push registration.
                        }
                        if (messaging != null) {
                            pushToken = Tasks.await(messaging.getToken(), 10, TimeUnit.SECONDS);
                        }
                    }
                    JSONObject response = new JSONObject(postForm(
                            "/api/auth/logout", "device_token=" + encode(pushToken)));
                    if (!pushToken.isEmpty() && !response.optBoolean("device_detached")) {
                        throw new IOException("Update the server to detach this push device before signing out.");
                    }
                    SecureTokenStore.setApiToken(getApplicationContext(), "");
                }
                postToMain(() -> {
                    accountStatusText.setText(R.string.not_signed_in);
                    setSignedInControls(false);
                    refresh();
                });
            } catch (Exception error) {
                postToMain(() -> {
                    accountStatusText.setText(
                            getString(R.string.could_not_finish_signing_out, error.getMessage()));
                    setSignedInControls(true);
                });
            }
        });
    }

    private void refreshAccountStatus() {
        if (SecureTokenStore.apiToken(getApplicationContext()).isEmpty()) {
            accountStatusText.setText(R.string.not_signed_in);
            setSignedInControls(false);
            return;
        }
        setAccountTransitionControls();
        executor.execute(() -> {
            try {
                String email = new JSONObject(getText("/api/auth/me")).getString("email");
                postToMain(() -> {
                    accountStatusText.setText(getString(R.string.signed_in_as, email));
                    setSignedInControls(true);
                });
            } catch (Exception error) {
                if (error instanceof HttpStatusException && ((HttpStatusException) error).statusCode == 401) {
                    SecureTokenStore.setApiToken(getApplicationContext(), "");
                    postToMain(() -> {
                        accountStatusText.setText(R.string.not_signed_in);
                        setSignedInControls(false);
                    });
                } else {
                    postToMain(() -> {
                        accountStatusText.setText(R.string.could_not_verify_account);
                        setSignedInControls(true);
                    });
                }
            }
        });
    }

    /**
     * Keep the configured server and account transition atomic from the user's
     * perspective. A signed-in device must complete the logout endpoint's FCM
     * detachment before it can point at another server or claim another
     * account, otherwise the old registration can continue receiving alerts.
     */
    private void setSignedInControls(boolean signedIn) {
        baseUrlInput.setEnabled(!signedIn);
        emailInput.setEnabled(!signedIn);
        passwordInput.setEnabled(!signedIn);
        registerButton.setEnabled(!signedIn);
        loginButton.setEnabled(!signedIn);
        logoutButton.setEnabled(signedIn);
    }

    /** Prevent duplicate login/logout submissions while one is in flight. */
    private void setAccountTransitionControls() {
        baseUrlInput.setEnabled(false);
        emailInput.setEnabled(false);
        passwordInput.setEnabled(false);
        registerButton.setEnabled(false);
        loginButton.setEnabled(false);
        logoutButton.setEnabled(false);
    }

    private void render(
            JSONArray watches,
            JSONArray events,
            JSONArray upcoming,
            JSONArray alerts,
            JSONArray notificationFeed,
            JSONArray subscriptions,
            JSONArray devices,
            JSONArray sources,
            JSONObject health) {
        artistList.removeAllViews();
        eventList.removeAllViews();
        mutedWatchList.removeAllViews();
        needsAttentionList.removeAllViews();
        sourceList.removeAllViews();
        mutedSourceList.removeAllViews();
        alertList.removeAllViews();
        notificationFeedList.removeAllViews();
        subscriptionList.removeAllViews();
        deviceList.removeAllViews();
        int artistCount = 0;
        int eventCount = 0;
        int mutedCount = 0;
        int mutedSourceCount = 0;

        for (int i = 0; i < watches.length(); i++) {
            JSONObject watch = watches.optJSONObject(i);
            if (watch == null) {
                continue;
            }
            String kind = watch.optString("kind", "event");
            if (watch.optBoolean("muted")) {
                String detail = "Watch #" + watch.optInt("id")
                    + " - " + kind
                    + "\n" + watchPreferences(watch, "event".equals(kind));
                mutedWatchList.addView(actionCard(watch.optString("keyword"), detail, "Restore", () -> restoreWatch(watch.optInt("id"))));
                mutedCount++;
                continue;
            }
            if ("artist".equals(kind)) {
                artistList.addView(twoActionCard(
                        watch.optString("keyword"),
                        watchPreferences(watch, false),
                        "Remove",
                        () -> removeWatch(watch.optInt("id")),
                        "Notify",
                        () -> addSubscription(watch.optInt("id"), "artist_all")));
                artistCount++;
            } else {
                eventList.addView(twoActionCard(
                        watch.optString("keyword"),
                        "Watch #" + watch.optInt("id") + "\n" + watchPreferences(watch, true),
                        "Remove",
                        () -> removeWatch(watch.optInt("id")),
                        "Notify",
                        () -> addSubscription(watch.optInt("id"), "event_all")));
                eventCount++;
            }
        }

        for (int i = 0; i < events.length(); i++) {
            JSONObject event = events.optJSONObject(i);
            if (event == null) {
                continue;
            }
            String clues = eventClues(event);
            if ("artist".equals(event.optString("watch_kind"))) {
                String detail = EventStatusText.label(
                    event.optString("status_label", ""),
                    event.optString("status", "watching")
                );
                if (!clues.isEmpty()) {
                    detail = detail + "\n" + clues;
                }
                String reasons = joinFirst(event.optJSONArray("match_reasons"));
                if (!reasons.isEmpty()) {
                    detail = detail + "\nWhy: " + reasons;
                }
                addEventCard(artistList, event, detail);
                continue;
            }
            JSONArray rounds = event.optJSONArray("rounds");
            String detail = EventStatusText.label(
                event.optString("status_label", ""),
                event.optString("status", "watching")
            ) + " - " + (rounds == null ? 0 : rounds.length()) + " ticket rounds";
            if (!clues.isEmpty()) {
                detail = detail + "\n" + clues;
            }
            String reasons = joinFirst(event.optJSONArray("match_reasons"));
            if (!reasons.isEmpty()) {
                detail = detail + "\nWhy: " + reasons;
            }
            addTrackedEventDetails(eventList, event, detail);
        }

        if (artistCount == 0) {
            artistList.addView(body("No tracked artists yet."));
        }
        if (eventCount == 0 && eventList.getChildCount() == 0) {
            eventList.addView(body("No tracked events yet."));
        }
        if (mutedCount == 0) {
            mutedWatchList.addView(body("No muted watches."));
        }
        for (int i = 0; i < Math.min(upcoming.length(), 8); i++) {
            JSONObject item = upcoming.optJSONObject(i);
            if (item == null) {
                continue;
            }
            String statusText = item.optString("status_label", "");
            if (statusText.isEmpty()) {
                statusText = item.optString("status", "unknown");
            }
            String detail = statusText
                + " - " + item.optString("platform", "unknown")
                + " - " + item.optString("round_name", "Ticket round")
                + " - " + item.optString("relevant_date", "date unknown");
            String reasons = joinFirst(item.optJSONArray("match_reasons"));
            if (!reasons.isEmpty()) {
                detail = detail + "\nWhy: " + reasons;
            }
            String url = item.optString("url", "");
            if (!isWebUrl(url)) {
                needsAttentionList.addView(card(item.optString("event_title", "Untitled event"), detail));
            } else {
                needsAttentionList.addView(actionCard(item.optString("event_title", "Untitled event"), detail, "Open", () -> openUrl(url)));
            }
        }
        if (needsAttentionList.getChildCount() == 0) {
            needsAttentionList.addView(body("No urgent ticket dates saved yet."));
        }
        for (int i = 0; i < sources.length(); i++) {
            JSONObject source = sources.optJSONObject(i);
            if (source == null) {
                continue;
            }
            String mode = source.optBoolean("private_note") ? "private note" : source.optString("platform", "manual");
            String detail = "Watch #" + source.optInt("watch_id") + " - " + mode + "\n" + source.optString("url", "");
            String sourceUrl = source.optString("url", "");
            if (source.optBoolean("muted")) {
                mutedSourceList.addView(sourceActionCard(source.optString("label", "Source"), detail, sourceUrl, "Restore", () -> restoreSource(source.optInt("id"))));
                mutedSourceCount++;
                continue;
            }
            sourceList.addView(sourceActionCard(source.optString("label", "Source"), detail, sourceUrl, "Remove", () -> removeSource(source.optInt("id"))));
        }
        if (sourceList.getChildCount() == 0) {
            sourceList.addView(body("No manual sources."));
        }
        if (mutedSourceCount == 0) {
            mutedSourceList.addView(body("No muted sources."));
        }
        for (int i = 0; i < Math.min(alerts.length(), 10); i++) {
            JSONObject alert = alerts.optJSONObject(i);
            if (alert == null) {
                continue;
            }
            String title = AlertTypeText.label(
                alert.optString("type_label", ""),
                alert.optString("type", "alert")
            );
            String detail = alert.optString("event", alert.optString("event_title", alert.optString("keyword", ""))) + " " + alert.optString("round", "");
            if (alert.has("event_id")) {
                detail = detail.trim() + "\nEvent #" + alert.optInt("event_id");
            }
            String watchKeyword = alert.optString("watch_keyword", "");
            if (!watchKeyword.isEmpty()) {
                String muted = alert.optBoolean("watch_muted") ? " muted" : "";
                detail = detail.trim() + "\n" + alert.optString("watch_kind", "watch") + " " + watchKeyword + muted;
            } else if (alert.has("watch_id")) {
                detail = detail.trim() + "\nWatch #" + alert.optInt("watch_id");
            }
            alertList.addView(card(title, detail.trim()));
        }
        if (alertList.getChildCount() == 0) {
            alertList.addView(body("No recent alerts."));
        }
        renderNotificationFeed(notificationFeed);
        renderSubscriptions(subscriptions, watches);
        renderDevices(devices);
        String version = health.optString("version", "").trim();
        int build = health.optInt("build", -1);
        int schemaVersion = health.optInt("schema_version", -1);
        String release = version.isEmpty() || build < 0
                ? ""
                : " - v" + version + " (" + build + ")";
        String schema = schemaVersion < 0 ? "" : " - schema " + schemaVersion;
        int trackedArtists = health.optInt("tracked_artists", artistCount);
        int trackedEvents = health.optInt("tracked_events", eventCount);
        int alertCount = health.optInt("alerts", 0);
        String artistSummary = getResources().getQuantityString(
                R.plurals.artist_count, trackedArtists, trackedArtists);
        String eventSummary = getResources().getQuantityString(
                R.plurals.event_count, trackedEvents, trackedEvents);
        String alertSummary = getResources().getQuantityString(
                R.plurals.alert_count, alertCount, alertCount);
        statusText.setText(getString(
                R.string.server_status,
                health.optString("status", "unknown"),
                release,
                schema,
                artistSummary,
                eventSummary,
                alertSummary));
    }

    private void renderNotificationFeed(JSONArray notificationFeed) {
        for (int i = 0; i < Math.min(notificationFeed.length(), 10); i++) {
            JSONObject item = notificationFeed.optJSONObject(i);
            if (item == null) {
                continue;
            }
            String title = item.optString("title", item.optString("label", "Reminder"));
            StringBuilder detail = new StringBuilder();
            appendNonEmptyLine(detail, item.optString("body", ""));
            appendNonEmptyLine(detail, item.optString("event_title", ""));
            appendLabeledLine(detail, "Location", item.optString("location", ""));
            appendLabeledLine(detail, "Date", item.optString("date", ""));
            appendLabeledLine(detail, "Channel", item.optString("channel", ""));
            appendNonEmptyLine(detail, item.optString("created_at", ""));
            String url = item.optString("url", "");
            if (isWebUrl(url)) {
                notificationFeedList.addView(actionCard(title, detail.toString(), "Open", () -> openUrl(url)));
            } else {
                notificationFeedList.addView(card(title, detail.toString()));
            }
        }
        if (notificationFeedList.getChildCount() == 0) {
            notificationFeedList.addView(body("No delivered reminders."));
        }
    }

    private void renderSubscriptions(JSONArray subscriptions, JSONArray watches) {
        for (int i = 0; i < subscriptions.length(); i++) {
            JSONObject subscription = subscriptions.optJSONObject(i);
            if (subscription == null) {
                continue;
            }
            int watchId = subscription.optInt("watch_id");
            String title = watchKeyword(watches, watchId);
            if (title.isEmpty()) {
                title = "Watch #" + watchId;
            }
            StringBuilder detail = new StringBuilder();
            appendLabeledLine(detail, "Scope", subscription.optString("scope", ""));
            appendLabeledLine(detail, "Location", subscription.optString("location", ""));
            appendLabeledLine(detail, "Round", subscription.optString("round_key", ""));
            appendLabeledLine(detail, "Channels", subscription.optString("channels", ""));
            appendLabeledLine(detail, "Lead days", subscription.optString("lead_days", ""));
            subscriptionList.addView(actionCard(
                    title,
                    detail.toString(),
                    "Remove",
                    () -> removeSubscription(subscription.optInt("id"))));
        }
        if (subscriptionList.getChildCount() == 0) {
            subscriptionList.addView(body("No notification subscriptions."));
        }
    }

    private void renderDevices(JSONArray devices) {
        for (int i = 0; i < devices.length(); i++) {
            JSONObject device = devices.optJSONObject(i);
            if (device == null) {
                continue;
            }
            String title = device.optString("label", "").trim();
            if (title.isEmpty()) {
                title = device.optString("platform", "Device");
            }
            String detail = device.optString("platform", "unknown")
                    + "\nToken: " + abbreviatedToken(device.optString("token", ""));
            deviceList.addView(card(title, detail));
        }
        if (deviceList.getChildCount() == 0) {
            deviceList.addView(body("No push devices registered."));
        }
    }

    private String abbreviatedToken(String token) {
        if (token == null || token.isEmpty()) {
            return "unavailable";
        }
        if (token.length() <= 12) {
            return token;
        }
        return token.substring(0, 6) + "…" + token.substring(token.length() - 4);
    }

    private String watchKeyword(JSONArray watches, int watchId) {
        for (int i = 0; i < watches.length(); i++) {
            JSONObject watch = watches.optJSONObject(i);
            if (watch != null && watch.optInt("id") == watchId) {
                return watch.optString("keyword", "");
            }
        }
        return "";
    }

    private void appendNonEmptyLine(StringBuilder detail, String value) {
        if (value == null || value.trim().isEmpty()) {
            return;
        }
        if (detail.length() > 0) {
            detail.append("\n");
        }
        detail.append(value.trim());
    }

    private void appendLabeledLine(StringBuilder detail, String label, String value) {
        if (value == null || value.trim().isEmpty()) {
            return;
        }
        appendNonEmptyLine(detail, label + ": " + value.trim());
    }

    private TextView heading(String text) {
        TextView view = body(text);
        view.setTextSize(28);
        return view;
    }

    private TextView section(String text) {
        TextView view = body(text);
        view.setTextSize(20);
        view.setPadding(0, 28, 0, 8);
        return view;
    }

    private TextView body(String text) {
        TextView view = new TextView(this);
        view.setText(text);
        view.setTextSize(16);
        view.setPadding(0, 6, 0, 6);
        return view;
    }

    private EditText singleLineInput(String hint) {
        EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setHint(hint);
        return input;
    }

    private TextView card(String title, String detail) {
        TextView view = body(title + "\n" + detail);
        view.setPadding(18, 18, 18, 18);
        return view;
    }

    private String watchPreferences(JSONObject watch, boolean includeAlerts) {
        StringBuilder detail = new StringBuilder();
        appendPreference(detail, "Tags", watch.optString("tags", ""));
        appendPreference(detail, "Regions", watch.optString("preferred_regions", ""));
        appendPreference(detail, "Venues", watch.optString("preferred_venues", ""));
        if (includeAlerts) {
            appendPreference(detail, "Alerts", AlertTypeText.labels(watch.optString("alert_preferences", "")));
        }
        appendPreference(detail, "Last checked", watch.optString("last_checked_at", "never"));
        return detail.toString();
    }

    private void appendPreference(StringBuilder detail, String label, String value) {
        if (detail.length() > 0) {
            detail.append("\n");
        }
        detail.append(label).append(": ").append(value == null || value.isEmpty() ? "none" : value);
    }

    private String eventClues(JSONObject event) {
        StringBuilder detail = new StringBuilder();
        String dates = joinFirst(event.optJSONArray("event_dates"));
        // Prefer the backend's honest venue_label (real venues, "Multiple cities"
        // for a tour, or a dash) over the raw venues array, which is empty for
        // tour listings and read as missing data.
        String venue = event.optString("venue_label", "");
        if (venue.isEmpty()) {
            venue = joinFirst(event.optJSONArray("venues"));
        }
        if (!dates.isEmpty()) {
            detail.append("Dates: ").append(dates);
        }
        if (!venue.isEmpty()) {
            if (detail.length() > 0) {
                detail.append("\n");
            }
            detail.append("Venue: ").append(venue);
        }
        return detail.toString();
    }

    private void addEventCard(LinearLayout list, JSONObject event, String detail) {
        String title = event.optString("title", "Untitled event");
        String officialUrl = event.optString("official_url", "");
        if (!isWebUrl(officialUrl)) {
            list.addView(card(title, detail));
        } else {
            list.addView(actionCard(title, detail, "Open", () -> openUrl(officialUrl)));
        }
    }

    private void addTrackedEventDetails(LinearLayout list, JSONObject event, String summaryDetail) {
        int watchId = event.optInt("watch_id");
        String title = event.optString("title", "Untitled event");
        StringBuilder overview = new StringBuilder(summaryDetail);
        appendLabeledText(event, "Summary", "summary", overview);
        appendLabeledText(event, "Updated", "updated_at", overview);
        String officialUrl = event.optString("official_url", "");
        if (isWebUrl(officialUrl)) {
            list.addView(twoActionCard(
                    title,
                    overview.toString(),
                    "Open Official",
                    () -> openUrl(officialUrl),
                    "Notify All Rounds",
                    () -> addSubscription(watchId, "event_all")));
        } else {
            list.addView(actionCard(
                    title,
                    overview.toString(),
                    "Notify All Rounds",
                    () -> addSubscription(watchId, "event_all")));
        }

        JSONArray rounds = event.optJSONArray("rounds");
        if (rounds != null) {
            for (int i = 0; i < rounds.length(); i++) {
                JSONObject round = rounds.optJSONObject(i);
                if (round != null) {
                    addTicketRoundCard(list, watchId, round);
                }
            }
        }

        JSONArray locations = event.optJSONArray("event_locations");
        if (locations != null) {
            for (int i = 0; i < locations.length(); i++) {
                JSONObject location = locations.optJSONObject(i);
                if (location != null) {
                    addEventLocationCard(list, watchId, location);
                }
            }
        }

        appendTicketLinks(list, event.optJSONArray("ticket_links"));
        appendTextCards(list, "Organizer", event.optJSONArray("organizers"));
        appendTextCards(list, "Cast / lineup", event.optJSONArray("lineup"));
        appendTextCards(list, "Ticket rule", event.optJSONArray("ticket_rules"));
        appendTextCards(list, "Ticket price", event.optJSONArray("ticket_prices"));
        appendRelatedEvents(list, event.optJSONArray("related_events"));
        appendManualSources(list, event.optJSONArray("manual_sources"));
    }

    private void addTicketRoundCard(LinearLayout list, int watchId, JSONObject round) {
        StringBuilder detail = new StringBuilder();
        appendLabeledText(round, "Status", "status_label", detail);
        appendLabeledText(round, "Platform", "platform", detail);
        appendLabeledText(round, "Type", "round_type_label", detail);
        appendLabeledText(round, "Access", "membership_label", detail);
        appendLabeledText(round, "Schedule", "schedule_label", detail);
        if (round.has("confidence")) {
            appendNonEmptyLine(detail, "Confidence: " + round.optInt("confidence") + "%");
        }
        appendLabeledText(round, "Evidence", "evidence", detail);
        String title = round.optString("name", "Ticket round");
        String url = round.optString("url", "");
        String roundKey = round.optString("round_key", "");
        if (isWebUrl(url) && !roundKey.isEmpty()) {
            list.addView(twoActionCard(
                    title,
                    detail.toString(),
                    "Open Ticket Page",
                    () -> openUrl(url),
                    "Notify This Round",
                    () -> addSubscription(watchId, "round", "", roundKey)));
        } else if (!roundKey.isEmpty()) {
            list.addView(actionCard(
                    title,
                    detail.toString(),
                    "Notify This Round",
                    () -> addSubscription(watchId, "round", "", roundKey)));
        } else if (isWebUrl(url)) {
            list.addView(actionCard(title, detail.toString(), "Open Ticket Page", () -> openUrl(url)));
        } else {
            list.addView(card(title, detail.toString()));
        }
    }

    private void addEventLocationCard(LinearLayout list, int watchId, JSONObject location) {
        String locationKey = location.optString("location", "").trim();
        if (locationKey.isEmpty()) {
            return;
        }
        StringBuilder detail = new StringBuilder();
        appendLabeledText(location, "City", "city", detail);
        appendLabeledText(location, "Venue", "venue", detail);
        appendLabeledText(location, "Date", "date", detail);
        list.addView(actionCard(
                "Location: " + locationKey,
                detail.toString(),
                "Notify This Location",
                () -> addSubscription(watchId, "event_location", locationKey, "")));
    }

    private void appendTicketLinks(LinearLayout list, JSONArray links) {
        if (links == null) {
            return;
        }
        for (int i = 0; i < links.length(); i++) {
            JSONObject link = links.optJSONObject(i);
            if (link == null) {
                continue;
            }
            String url = link.optString("url", "");
            String title = link.optString("label", "Ticket link");
            StringBuilder detail = new StringBuilder();
            appendLabeledText(link, "Platform", "platform", detail);
            appendLabeledText(link, "Source", "provenance", detail);
            if (link.has("confidence")) {
                appendNonEmptyLine(detail, "Confidence: " + link.optInt("confidence") + "%");
            }
            if (isWebUrl(url)) {
                list.addView(actionCard(title, detail.toString(), "Open Ticket Link", () -> openUrl(url)));
            } else {
                list.addView(card(title, detail.toString()));
            }
        }
    }

    private void appendTextCards(LinearLayout list, String title, JSONArray values) {
        if (values == null) {
            return;
        }
        for (int i = 0; i < values.length(); i++) {
            String value = values.optString(i, "").trim();
            if (!value.isEmpty()) {
                list.addView(card(title, value));
            }
        }
    }

    private void appendManualSources(LinearLayout list, JSONArray sources) {
        if (sources == null) {
            return;
        }
        for (int i = 0; i < sources.length(); i++) {
            JSONObject source = sources.optJSONObject(i);
            if (source == null) {
                continue;
            }
            String title = source.optString("label", "Manual source");
            String url = source.optString("url", "");
            String mode = source.optBoolean("private_note") ? "Private note" : source.optString("platform", "Manual");
            if (isWebUrl(url)) {
                list.addView(actionCard(title, mode, "Open Source", () -> openUrl(url)));
            } else {
                list.addView(card(title, mode));
            }
        }
    }

    private void appendRelatedEvents(LinearLayout list, JSONArray relatedEvents) {
        if (relatedEvents == null) {
            return;
        }
        for (int i = 0; i < relatedEvents.length(); i++) {
            JSONObject related = relatedEvents.optJSONObject(i);
            if (related == null) {
                continue;
            }
            String title = related.optString("title", "Related event");
            StringBuilder detail = new StringBuilder("Related saved event");
            appendLabeledText(related, "Date", "event_date", detail);
            appendLabeledText(related, "Venue", "venue_label", detail);
            String reasons = joinFirst(related.optJSONArray("recommendation_reasons"));
            appendLabeledLine(detail, "Why", reasons);
            String url = related.optString("official_url", "");
            if (isWebUrl(url)) {
                list.addView(actionCard(title, detail.toString(), "Open Official Page", () -> openUrl(url)));
            } else {
                list.addView(card(title, detail.toString()));
            }
        }
    }

    private void appendLabeledText(JSONObject object, String label, String key, StringBuilder detail) {
        appendLabeledLine(detail, label, object.optString(key, ""));
    }

    private String joinFirst(JSONArray values) {
        if (values == null || values.length() == 0) {
            return "";
        }
        StringBuilder joined = new StringBuilder();
        for (int i = 0; i < Math.min(values.length(), 2); i++) {
            if (i > 0) {
                joined.append("; ");
            }
            joined.append(values.optString(i));
        }
        return joined.toString();
    }

    private LinearLayout actionCard(String title, String detail, String buttonLabel, Runnable action) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.VERTICAL);
        row.setPadding(18, 18, 18, 18);
        row.addView(body(title + "\n" + detail));
        Button button = new Button(this);
        button.setText(buttonLabel);
        button.setOnClickListener(view -> action.run());
        row.addView(button);
        return row;
    }

    private LinearLayout twoActionCard(String title, String detail, String firstLabel, Runnable firstAction, String secondLabel, Runnable secondAction) {
        LinearLayout row = actionCard(title, detail, firstLabel, firstAction);
        Button second = new Button(this);
        second.setText(secondLabel);
        second.setOnClickListener(view -> secondAction.run());
        row.addView(second);
        return row;
    }

    private LinearLayout sourceActionCard(String title, String detail, String url, String actionLabel, Runnable action) {
        if (!isWebUrl(url)) {
            return actionCard(title, detail, actionLabel, action);
        }
        return twoActionCard(title, detail, "Open", () -> openUrl(url), actionLabel, action);
    }
}
