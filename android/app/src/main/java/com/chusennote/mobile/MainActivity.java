package com.chusennote.mobile;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
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
import android.text.TextUtils;
import android.text.TextWatcher;
import android.text.method.PasswordTransformationMethod;

import com.google.firebase.messaging.FirebaseMessaging;
import com.google.android.gms.tasks.Tasks;
import android.view.View;
import android.view.WindowInsets;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

import androidx.swiperefreshlayout.widget.SwipeRefreshLayout;

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
import java.util.ArrayList;
import java.util.EnumMap;
import java.util.EnumSet;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Future;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

public class MainActivity extends Activity {
    static final String DEFAULT_BASE_URL = "https://chusennote.onrender.com";
    private static final String PREFS_NAME = "chusennote";
    private static final String PREF_BASE_URL = "base_url";
    private static final String STATE_SELECTED_TAB = "selected_tab";
    /** One server collection the app mirrors, and where to fetch it. */
    private enum Resource {
        WATCHES("/api/watchlist?include_muted=1"),
        EVENTS("/api/events"),
        UPCOMING("/api/upcoming"),
        ALERTS("/api/alerts"),
        NOTIFICATION_FEED("/api/notifications?limit=100"),
        SUBSCRIPTIONS("/api/subscriptions"),
        DEVICES("/api/devices"),
        SOURCES("/api/sources?include_muted=1"),
        HEALTH("/api/health");

        final String path;

        Resource(String path) {
            this.path = path;
        }
    }

    // Actions refetch only what they can change. Muting a watch drops it out of
    // events, deadlines, and alert context; manual sources also appear in events.
    private static final Set<Resource> WATCH_CHANGES = EnumSet.of(
            Resource.WATCHES, Resource.EVENTS, Resource.UPCOMING, Resource.ALERTS,
            Resource.SUBSCRIPTIONS, Resource.HEALTH);
    private static final Set<Resource> SOURCE_CHANGES = EnumSet.of(Resource.SOURCES, Resource.EVENTS);
    private static final Set<Resource> CHECK_RESULTS = EnumSet.of(
            Resource.WATCHES, Resource.EVENTS, Resource.UPCOMING, Resource.ALERTS, Resource.HEALTH);

    // Serializes actions so account transitions never interleave with other requests.
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    // Lets one refresh fetch its collections concurrently.
    private final ExecutorService fetchPool = Executors.newFixedThreadPool(4);
    // The last fetched copy of each collection, so a partial refresh can re-render everything.
    private final Map<Resource, Object> data = new EnumMap<>(Resource.class);
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
    private Button deleteAccountButton;
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
    private KeyedRowList artistList;
    private KeyedRowList eventList;
    private TextView mutedWatchHeading;
    private KeyedRowList mutedWatchList;
    private KeyedRowList needsAttentionList;
    private KeyedRowList sourceList;
    private TextView mutedSourceHeading;
    private KeyedRowList mutedSourceList;
    private KeyedRowList alertList;
    private KeyedRowList notificationFeedList;
    private KeyedRowList subscriptionList;
    private KeyedRowList deviceList;
    private TextView statusText;
    private TextView debugStatusText;
    private TextView notificationStatusText;
    private ProgressBar busyIndicator;
    private int busyCount;
    private SwipeRefreshLayout[] refreshLayouts;
    private final List<Button> checkButtons = new ArrayList<>();
    private View[] tabPages;
    private View detailPage;
    private LinearLayout detailContent;
    // The event shown on the detail page, by row key, or null when it is closed.
    private String openEventKey;
    private String openEventSignature;
    private final Map<String, JSONObject> eventsByKey = new HashMap<>();
    private LinearLayout addEventFlow;
    private LinearLayout exactEventForm;
    private LinearLayout keywordEventForm;
    private View eventPreferences;
    private Button eventPreferencesButton;
    private Button[] tabButtons;
    private int selectedTab;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        buildLayout();
        selectTab(savedInstanceState == null ? 0 : savedInstanceState.getInt(STATE_SELECTED_TAB, 0));
        ensureNotificationChannel();
        requestNotificationPermissionIfNeeded();
        setSignedInControls(!SecureTokenStore.apiToken(getApplicationContext()).isEmpty());
        updateNotificationStatus();
        registerPushToken();
        refreshAccountStatus();
        refresh();
    }

    @Override
    protected void onResume() {
        super.onResume();
        updateNotificationStatus();
    }

    private void updateNotificationStatus() {
        if (notificationStatusText != null) {
            NotificationManager manager = getSystemService(NotificationManager.class);
            notificationStatusText.setText(manager != null && manager.areNotificationsEnabled()
                    ? R.string.notifications_enabled : R.string.notifications_disabled);
        }
    }

    private void openNotificationSettings() {
        Intent intent = new Intent(Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? android.provider.Settings.ACTION_APP_NOTIFICATION_SETTINGS
                : android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            intent.putExtra(android.provider.Settings.EXTRA_APP_PACKAGE, getPackageName());
        } else {
            intent.setData(Uri.parse("package:" + getPackageName()));
        }
        startActivity(intent);
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        outState.putInt(STATE_SELECTED_TAB, selectedTab);
    }

    @Override
    protected void onDestroy() {
        destroyed = true;
        mainHandler.removeCallbacksAndMessages(null);
        executor.shutdownNow();
        fetchPool.shutdownNow();
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

    /** Runs network work on the action executor while the busy indicator shows. */
    private void runInBackground(Runnable work) {
        setBusy(1);
        executor.execute(() -> {
            try {
                work.run();
            } finally {
                postToMain(() -> setBusy(-1));
            }
        });
    }

    private void setBusy(int delta) {
        busyCount = Math.max(0, busyCount + delta);
        busyIndicator.setVisibility(busyCount > 0 ? View.VISIBLE : View.INVISIBLE);
        if (busyCount == 0) {
            for (SwipeRefreshLayout layout : refreshLayouts) {
                layout.setRefreshing(false);
            }
        }
    }

    private void setChecksRunning(boolean running) {
        for (Button button : checkButtons) {
            button.setEnabled(!running);
        }
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
            if (BuildConfig.DEBUG) {
                debugStatusText.setText(R.string.push_disabled);
            }
        }
    }

    private void buildLayout() {
        setContentView(R.layout.activity_main);
        View header = findViewById(R.id.header);
        View tabBar = findViewById(R.id.tab_bar);
        int headerTop = header.getPaddingTop();
        int tabBarBottom = tabBar.getPaddingBottom();
        // The header and tab bar stay fixed while pages scroll, so they are what
        // must clear the system bars.
        findViewById(R.id.root).setOnApplyWindowInsetsListener((view, insets) -> {
            int[] bars = systemBarInsets(insets);
            header.setPadding(
                    header.getPaddingLeft(),
                    headerTop + bars[0],
                    header.getPaddingRight(),
                    header.getPaddingBottom());
            tabBar.setPadding(
                    tabBar.getPaddingLeft(),
                    tabBar.getPaddingTop(),
                    tabBar.getPaddingRight(),
                    tabBarBottom + bars[1]);
            return insets;
        });

        statusText = findViewById(R.id.status_text);
        busyIndicator = findViewById(R.id.busy_indicator);
        LinearLayout headerActions = findViewById(R.id.header_actions);
        headerActions.addView(textButton(getString(R.string.refresh), this::refresh));

        buildAlertsPage(findViewById(R.id.alerts_content));
        buildWatchPage(findViewById(R.id.watch_content));
        buildSettingsPage(findViewById(R.id.settings_content));

        detailPage = findViewById(R.id.detail_page);
        detailContent = findViewById(R.id.detail_content);
        refreshLayouts = new SwipeRefreshLayout[]{
                findViewById(R.id.alerts_page),
                findViewById(R.id.watch_page),
                findViewById(R.id.settings_page)};
        tabPages = refreshLayouts;
        for (SwipeRefreshLayout layout : refreshLayouts) {
            layout.setColorSchemeColors(getColor(R.color.accent));
            layout.setProgressBackgroundColorSchemeColor(getColor(R.color.surface));
            layout.setOnRefreshListener(this::refresh);
        }
        tabButtons = new Button[]{
                findViewById(R.id.tab_alerts),
                findViewById(R.id.tab_watch),
                findViewById(R.id.tab_settings)};
        for (int i = 0; i < tabButtons.length; i++) {
            int index = i;
            tabButtons[i].setOnClickListener(view -> selectTab(index));
        }
    }

    /** {top, bottom} system bar insets. */
    @SuppressWarnings("deprecation")
    private static int[] systemBarInsets(WindowInsets insets) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars());
            return new int[]{bars.top, bars.bottom};
        }
        return new int[]{insets.getSystemWindowInsetTop(), insets.getSystemWindowInsetBottom()};
    }

    /** Each page keeps its own scroll position while hidden. */
    private void selectTab(int index) {
        openEventKey = null;
        detailPage.setVisibility(View.GONE);
        selectedTab = index >= 0 && index < tabPages.length ? index : 0;
        for (int i = 0; i < tabPages.length; i++) {
            tabPages[i].setVisibility(i == selectedTab ? View.VISIBLE : View.GONE);
            tabButtons[i].setSelected(i == selectedTab);
        }
    }

    @Override
    @SuppressWarnings("deprecation")
    public void onBackPressed() {
        if (openEventKey != null) {
            closeEventDetail();
        } else if (selectedTab != 0) {
            selectTab(0);
        } else {
            super.onBackPressed();
        }
    }

    private void showEventDetail(String key) {
        JSONObject event = eventsByKey.get(key);
        if (event == null) {
            closeEventDetail();
            return;
        }
        boolean alreadyShown = key.equals(openEventKey) && event.toString().equals(openEventSignature);
        openEventKey = key;
        openEventSignature = event.toString();
        if (!alreadyShown) {
            detailContent.removeAllViews();
            LinearLayout bar = actionBar();
            bar.addView(textButton(getString(R.string.back_to_events), this::closeEventDetail));
            detailContent.addView(bar);
            LinearLayout surface = list();
            surface.addView(trackedEventBlock(event, eventSummary(event)));
            detailContent.addView(surface);
            detailPage.scrollTo(0, 0);
        }
        tabPages[selectedTab].setVisibility(View.GONE);
        detailPage.setVisibility(View.VISIBLE);
    }

    private void closeEventDetail() {
        openEventKey = null;
        detailPage.setVisibility(View.GONE);
        tabPages[selectedTab].setVisibility(View.VISIBLE);
    }

    /** A button that shows and hides {@code form}, which starts hidden. */
    private Button formToggle(String label, View form) {
        form.setVisibility(View.GONE);
        Button toggle = textButton(getString(R.string.section_collapsed, label), () -> {});
        toggle.setContentDescription(getString(R.string.section_collapsed_description, label));
        toggle.setOnClickListener(view -> {
            boolean opening = form.getVisibility() != View.VISIBLE;
            form.setVisibility(opening ? View.VISIBLE : View.GONE);
            toggle.setText(getString(opening ? R.string.section_expanded : R.string.section_collapsed, label));
            toggle.setContentDescription(getString(opening
                    ? R.string.section_expanded_description
                    : R.string.section_collapsed_description, label));
        });
        return toggle;
    }

    private void buildAlertsPage(LinearLayout page) {
        page.addView(section("Upcoming"));
        needsAttentionList = rowList();
        page.addView(needsAttentionList.container());

        page.addView(section("Recent Updates"));
        alertList = rowList();
        page.addView(alertList.container());
    }

    private void buildWatchPage(LinearLayout page) {
        page.addView(body(getString(R.string.watch_intro)));
        LinearLayout addFlow = new LinearLayout(this);
        addEventFlow = addFlow;
        addFlow.setOrientation(LinearLayout.VERTICAL);
        Button addFlowButton = primaryButton(getString(R.string.add_event), () -> {
            boolean opening = addFlow.getVisibility() != View.VISIBLE;
            addFlow.setVisibility(opening ? View.VISIBLE : View.GONE);
        });
        addFlowButton.setId(R.id.add_event_flow_button);
        page.addView(addFlowButton);
        addFlow.setVisibility(View.GONE);
        page.addView(addFlow);

        exactEventForm = form();
        exactEventForm.addView(body(getString(R.string.exact_event_intro)));
        exactEventInput = singleLineInput("Event name");
        exactEventInput.setId(R.id.exact_event_input);
        exactEventForm.addView(exactEventInput);
        exactEventSearchButton = primaryButton(getString(R.string.search_events), this::searchExactEvents);
        exactEventSearchButton.setId(R.id.exact_event_search_button);
        exactEventForm.addView(exactEventSearchButton);
        exactEventResultList = list();
        exactEventResultList.setId(R.id.exact_event_result_list);
        exactEventForm.addView(exactEventResultList);

        LinearLayout eventForm = form();
        keywordEventForm = eventForm;
        eventForm.addView(body(getString(R.string.keyword_watch_intro)));
        eventInput = singleLineInput("Event keyword");
        eventInput.setId(R.id.event_keyword_input);
        eventForm.addView(eventInput);
        LinearLayout preferences = new LinearLayout(this);
        preferences.setOrientation(LinearLayout.VERTICAL);
        eventTagsInput = singleLineInput("Tags (optional)");
        eventTagsInput.setId(R.id.event_tags_input);
        preferences.addView(eventTagsInput);
        eventRegionsInput = singleLineInput("Preferred regions (optional)");
        preferences.addView(eventRegionsInput);
        eventVenuesInput = singleLineInput("Preferred venues (optional)");
        preferences.addView(eventVenuesInput);
        eventAlertsInput = singleLineInput("Alert types (optional)");
        preferences.addView(eventAlertsInput);
        Button preferencesButton = formToggle(getString(R.string.watch_preferences), preferences);
        preferencesButton.setId(R.id.event_preferences_button);
        eventPreferences = preferences;
        eventPreferencesButton = preferencesButton;
        eventForm.addView(preferencesButton);
        eventForm.addView(preferences);
        Button addEvent = primaryButton(getString(R.string.watch_keyword), () ->
                addWatch("event", eventInput, eventTagsInput, eventRegionsInput, eventVenuesInput, eventAlertsInput));
        addEvent.setId(R.id.add_event_button);
        eventForm.addView(addEvent);
        eventForm.setVisibility(View.GONE);

        LinearLayout modes = actionBar();
        Button exactMode = textButton(getString(R.string.find_event_page), () -> {
            exactEventForm.setVisibility(View.VISIBLE);
            eventForm.setVisibility(View.GONE);
        });
        exactMode.setId(R.id.exact_event_mode_button);
        Button keywordMode = textButton(getString(R.string.watch_keyword), () -> {
            exactEventForm.setVisibility(View.GONE);
            eventForm.setVisibility(View.VISIBLE);
        });
        keywordMode.setId(R.id.keyword_event_mode_button);
        modes.addView(exactMode);
        modes.addView(keywordMode);
        addFlow.addView(modes);
        addFlow.addView(exactEventForm);
        addFlow.addView(eventForm);

        page.addView(section("Events"));
        eventList = rowList();
        eventList.container().setId(R.id.event_list);
        page.addView(eventList.container());
        Button checkNow = textButton(getString(R.string.check_now), () -> runWatches("event"));
        checkButtons.add(checkNow);
        page.addView(checkNow);

        mutedWatchHeading = section("Removed");
        page.addView(mutedWatchHeading);
        mutedWatchList = rowList();
        page.addView(mutedWatchList.container());
    }

    private void buildSettingsPage(LinearLayout page) {
        page.addView(section("Account"));
        LinearLayout accountForm = form();
        // The saved endpoint remains bound to account transitions even when its
        // developer-only editor is not attached to the release UI.
        baseUrlInput = singleLineInput("API base URL");
        baseUrlInput.setId(R.id.base_url_input);
        apiBaseUrl = normalizeBaseUrl(preferences().getString(PREF_BASE_URL, DEFAULT_BASE_URL));
        baseUrlInput.setText(apiBaseUrl);
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
        accountStatusText = body(getString(R.string.not_signed_in));
        accountForm.addView(accountStatusText);
        emailInput = singleLineInput("Email");
        emailInput.setId(R.id.email_input);
        emailInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_EMAIL_ADDRESS);
        accountForm.addView(emailInput);
        passwordInput = singleLineInput("Password");
        passwordInput.setId(R.id.password_input);
        passwordInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        passwordInput.setTransformationMethod(PasswordTransformationMethod.getInstance());
        accountForm.addView(passwordInput);
        LinearLayout accountButtons = actionBar();
        loginButton = textButton(getString(R.string.log_in), this::loginAccount);
        accountButtons.addView(loginButton);
        registerButton = textButton(getString(R.string.register), this::registerAccount);
        registerButton.setId(R.id.register_button);
        accountButtons.addView(registerButton);
        logoutButton = textButton(getString(R.string.log_out), this::logoutAccount);
        accountButtons.addView(logoutButton);
        accountForm.addView(accountButtons);
        deleteAccountButton = textButton(getString(R.string.delete_account), this::showDeleteAccountDialog);
        deleteAccountButton.setId(R.id.delete_account_button);
        deleteAccountButton.setTextColor(getColorStateList(R.color.danger_text));
        accountForm.addView(deleteAccountButton);
        page.addView(accountForm);

        page.addView(section("Notifications & Calendar"));
        LinearLayout notificationControls = form();
        notificationStatusText = body("");
        notificationControls.addView(notificationStatusText);
        notificationControls.addView(textButton(getString(R.string.notification_settings), this::openNotificationSettings));
        notificationControls.addView(textButton(getString(R.string.open_calendar_feed), this::openCalendarFeed));
        page.addView(notificationControls);
        LinearLayout reminders = new LinearLayout(this);
        reminders.setOrientation(LinearLayout.VERTICAL);
        reminders.addView(section("Subscriptions"));
        subscriptionList = rowList();
        reminders.addView(subscriptionList.container());
        reminders.addView(section("Delivered Reminders"));
        notificationFeedList = rowList();
        reminders.addView(notificationFeedList.container());
        Button remindersToggle = formToggle(getString(R.string.manage_reminders), reminders);
        remindersToggle.setId(R.id.manage_reminders_button);
        page.addView(remindersToggle);
        page.addView(reminders);

        LinearLayout tracking = new LinearLayout(this);
        tracking.setOrientation(LinearLayout.VERTICAL);
        tracking.addView(body(getString(R.string.advanced_tracking_intro)));
        tracking.addView(section("Artists"));
        artistList = rowList();
        tracking.addView(artistList.container());
        LinearLayout artistForm = form();
        artistInput = singleLineInput("Artist keyword");
        artistForm.addView(artistInput);
        LinearLayout artistPreferences = new LinearLayout(this);
        artistPreferences.setOrientation(LinearLayout.VERTICAL);
        artistTagsInput = singleLineInput("Tags (optional)");
        artistPreferences.addView(artistTagsInput);
        artistRegionsInput = singleLineInput("Preferred regions (optional)");
        artistPreferences.addView(artistRegionsInput);
        artistVenuesInput = singleLineInput("Preferred venues (optional)");
        artistPreferences.addView(artistVenuesInput);
        artistForm.addView(formToggle(getString(R.string.watch_preferences), artistPreferences));
        artistForm.addView(artistPreferences);
        artistForm.addView(primaryButton(getString(R.string.add_artist), () ->
                addWatch("artist", artistInput, artistTagsInput, artistRegionsInput, artistVenuesInput, null)));
        tracking.addView(formToggle(getString(R.string.add_artist_ellipsis), artistForm));
        tracking.addView(artistForm);
        Button checkArtists = textButton(getString(R.string.run_artist_watches), () -> runWatches("artist"));
        checkButtons.add(checkArtists);
        tracking.addView(checkArtists);

        tracking.addView(section("Manual Sources"));
        sourceList = rowList();
        tracking.addView(sourceList.container());
        LinearLayout sourceForm = form();
        sourceWatchInput = singleLineInput("Tracked event watch id or keyword");
        sourceForm.addView(sourceWatchInput);
        sourceUrlInput = singleLineInput("Ticket or source URL");
        sourceForm.addView(sourceUrlInput);
        sourceLabelInput = singleLineInput("Label (optional)");
        sourceForm.addView(sourceLabelInput);
        sourcePrivateNoteInput = new CheckBox(this);
        sourcePrivateNoteInput.setText(R.string.private_note);
        sourceForm.addView(sourcePrivateNoteInput);
        sourceForm.addView(primaryButton(getString(R.string.add_source), this::addSource));
        tracking.addView(formToggle(getString(R.string.add_source_ellipsis), sourceForm));
        tracking.addView(sourceForm);
        mutedSourceHeading = section("Removed Sources");
        tracking.addView(mutedSourceHeading);
        mutedSourceList = rowList();
        tracking.addView(mutedSourceList.container());
        Button trackingToggle = formToggle(getString(R.string.advanced_tracking), tracking);
        trackingToggle.setId(R.id.advanced_tracking_button);
        page.addView(trackingToggle);
        page.addView(tracking);

        debugStatusText = body("");
        deviceList = rowList();
        if (BuildConfig.DEBUG) {
            LinearLayout developer = form();
            developer.addView(baseUrlInput);
            developer.addView(debugStatusText);
            developer.addView(textButton(getString(R.string.run_due_reminders), this::runNotifications));
            TextView devicesHeading = section("Registered Devices");
            devicesHeading.setId(R.id.registered_devices_heading);
            developer.addView(devicesHeading);
            developer.addView(deviceList.container());
            Button developerToggle = formToggle(getString(R.string.developer_tools), developer);
            developerToggle.setId(R.id.developer_tools_button);
            page.addView(developerToggle);
            page.addView(developer);
        }
    }

    private void refresh() {
        refresh(EnumSet.allOf(Resource.class));
    }

    /** Fetches {@code resources} concurrently, then re-renders with the cached rest. */
    private void refresh(Set<Resource> resources) {
        saveBaseUrl();
        statusText.setText(R.string.loading);
        runInBackground(() -> {
            Map<Resource, Future<Object>> pending = new EnumMap<>(Resource.class);
            try {
                for (Resource resource : resources) {
                    pending.put(resource, fetchPool.submit(() -> {
                        String body = getText(resource.path);
                        return resource == Resource.HEALTH ? new JSONObject(body) : new JSONArray(body);
                    }));
                }
                Map<Resource, Object> fetched = new EnumMap<>(Resource.class);
                for (Map.Entry<Resource, Future<Object>> entry : pending.entrySet()) {
                    fetched.put(entry.getKey(), entry.getValue().get());
                }
                postToMain(() -> {
                    data.putAll(fetched);
                    render(
                            array(Resource.WATCHES),
                            array(Resource.EVENTS),
                            array(Resource.UPCOMING),
                            array(Resource.ALERTS),
                            array(Resource.NOTIFICATION_FEED),
                            array(Resource.SUBSCRIPTIONS),
                            array(Resource.DEVICES),
                            array(Resource.SOURCES),
                            data.containsKey(Resource.HEALTH) ? (JSONObject) data.get(Resource.HEALTH) : new JSONObject());
                });
            } catch (Exception error) {
                for (Future<Object> future : pending.values()) {
                    future.cancel(true);
                }
                Throwable cause = error instanceof ExecutionException && error.getCause() != null
                        ? error.getCause()
                        : error;
                postToMain(() -> {
                    statusText.setText(getString(R.string.could_not_load, cause.getMessage()));
                    if (data.isEmpty()) {
                        String retry = getString(R.string.load_failed_retry);
                        showRows(needsAttentionList, new ArrayList<>(), retry);
                        showRows(alertList, new ArrayList<>(), retry);
                    }
                });
            }
        });
    }

    private JSONArray array(Resource resource) {
        Object value = data.get(resource);
        return value instanceof JSONArray ? (JSONArray) value : new JSONArray();
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
        runInBackground(() -> {
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
        runInBackground(() -> {
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
                    refresh(WATCH_CHANGES);
                });
            } catch (Exception error) {
                postToMain(() -> statusText.setText(getString(R.string.could_not_add_watch, error.getMessage())));
            }
        });
    }

    /** Opens the keyword form filled with {@code watch}; saving the same keyword updates its filters. */
    private void editWatch(JSONObject watch) {
        addEventFlow.setVisibility(View.VISIBLE);
        exactEventForm.setVisibility(View.GONE);
        keywordEventForm.setVisibility(View.VISIBLE);
        if (eventPreferences.getVisibility() != View.VISIBLE) {
            eventPreferencesButton.performClick();
        }
        eventInput.setText(text(watch, "keyword"));
        eventTagsInput.setText(text(watch, "tags"));
        eventRegionsInput.setText(text(watch, "preferred_regions"));
        eventVenuesInput.setText(text(watch, "preferred_venues"));
        eventAlertsInput.setText(text(watch, "alert_preferences"));
        eventInput.requestFocus();
        eventInput.getParent().requestChildFocus(eventInput, eventInput);
    }

    private void runWatches(String kind) {
        statusText.setText(getString(R.string.running_watches, kind));
        setChecksRunning(true);
        runInBackground(() -> {
            try {
                JSONArray alerts = postJsonArray("/api/run", "kind=" + encode(kind));
                postToMain(() -> {
                    setChecksRunning(false);
                    statusText.setText(getResources().getQuantityString(
                            R.plurals.run_complete_alerts,
                            alerts.length(),
                            alerts.length()));
                    refresh(CHECK_RESULTS);
                });
            } catch (Exception error) {
                postToMain(() -> {
                    setChecksRunning(false);
                    statusText.setText(getString(R.string.could_not_run_watches, error.getMessage()));
                });
            }
        });
    }

    private void runNotifications() {
        statusText.setText(R.string.sending_due_reminders);
        runInBackground(() -> {
            try {
                JSONArray delivered = postJsonArray("/api/notifications/run", "");
                postToMain(() -> {
                    statusText.setText(getResources().getQuantityString(
                            R.plurals.reminders_delivered,
                            delivered.length(),
                            delivered.length()));
                    refresh(EnumSet.of(Resource.NOTIFICATION_FEED));
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
        runInBackground(() -> {
            try {
                postForm(
                        "/api/subscriptions",
                        "watch=" + encode(String.valueOf(watchId))
                                + "&scope=" + encode(scope)
                                + "&location=" + encode(location)
                                + "&round_key=" + encode(roundKey)
                                + "&channels=" + encode("feed,push")
                                + "&lead_days=" + encode("7,1,0"));
                postToMain(() -> refresh(EnumSet.of(Resource.SUBSCRIPTIONS)));
            } catch (Exception error) {
                postToMain(() -> statusText.setText(
                        getString(R.string.could_not_add_subscription, error.getMessage())));
            }
        });
    }

    private void removeSubscription(int id) {
        statusText.setText(R.string.removing_subscription);
        runInBackground(() -> {
            try {
                postForm("/api/subscriptions/remove", "identifier=" + encode(String.valueOf(id)));
                postToMain(() -> refresh(EnumSet.of(Resource.SUBSCRIPTIONS)));
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
        runInBackground(() -> {
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
            exactEventResultList.addView(emptyRow("No matching official pages found."));
            return;
        }
        for (int i = 0; i < results.length(); i++) {
            JSONObject result = results.optJSONObject(i);
            if (result == null) {
                continue;
            }
            String title = text(result, "title").trim();
            String url = text(result, "url").trim();
            String snippet = text(result, "snippet").trim();
            String detail = url + (snippet.isEmpty() ? "" : "\n" + snippet);
            exactEventResultList.addView(row(
                    title.isEmpty() ? url : title,
                    detail,
                    isWebUrl(url)
                            ? new Action("Watch", () -> addExactEvent(exactEventSearchKeyword, title, url, snippet))
                            : null,
                    openAction("Open", url)));
        }
        if (exactEventResultList.getChildCount() == 0) {
            exactEventResultList.addView(emptyRow("No valid event pages found."));
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
        runInBackground(() -> {
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
                    exactEventResultList.addView(emptyRow("Exact event added."));
                    refresh(WATCH_CHANGES);
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
        runInBackground(() -> {
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
                    refresh(SOURCE_CHANGES);
                });
            } catch (Exception error) {
                postToMain(() -> statusText.setText(getString(R.string.could_not_add_source, error.getMessage())));
            }
        });
    }

    private void removeWatch(int id) {
        statusText.setText(R.string.removing_watch);
        runInBackground(() -> {
            try {
                postForm("/api/watchlist/remove", "identifier=" + encode(String.valueOf(id)));
                postToMain(() -> refresh(WATCH_CHANGES));
            } catch (Exception error) {
                postToMain(() -> statusText.setText(getString(R.string.could_not_remove_watch, error.getMessage())));
            }
        });
    }

    private void restoreWatch(int id) {
        statusText.setText(R.string.restoring_watch);
        runInBackground(() -> {
            try {
                postForm("/api/watchlist/unmute", "identifier=" + encode(String.valueOf(id)));
                postToMain(() -> refresh(WATCH_CHANGES));
            } catch (Exception error) {
                postToMain(() -> statusText.setText(getString(R.string.could_not_restore_watch, error.getMessage())));
            }
        });
    }

    private void removeSource(int id) {
        statusText.setText(R.string.removing_source);
        runInBackground(() -> {
            try {
                postForm("/api/sources/remove", "identifier=" + encode(String.valueOf(id)));
                postToMain(() -> refresh(SOURCE_CHANGES));
            } catch (Exception error) {
                postToMain(() -> statusText.setText(getString(R.string.could_not_remove_source, error.getMessage())));
            }
        });
    }

    private void restoreSource(int id) {
        statusText.setText(R.string.restoring_source);
        runInBackground(() -> {
            try {
                postForm("/api/sources/unmute", "identifier=" + encode(String.valueOf(id)));
                postToMain(() -> refresh(SOURCE_CHANGES));
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
        runInBackground(() -> {
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
        runInBackground(() -> {
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

    private void showDeleteAccountDialog() {
        if (SecureTokenStore.apiToken(getApplicationContext()).isEmpty()) {
            accountStatusText.setText(R.string.not_signed_in);
            return;
        }
        EditText confirmation = new EditText(this);
        confirmation.setSingleLine(true);
        confirmation.setHint(R.string.current_password);
        confirmation.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        confirmation.setTransformationMethod(PasswordTransformationMethod.getInstance());
        new AlertDialog.Builder(this)
                .setTitle(R.string.delete_account_confirmation_title)
                .setMessage(R.string.delete_account_confirmation_message)
                .setView(confirmation)
                .setNegativeButton(android.R.string.cancel, null)
                .setPositiveButton(R.string.delete_account, (dialog, which) ->
                        deleteAccount(confirmation.getText().toString()))
                .show();
    }

    private void deleteAccount(String password) {
        if (password.isEmpty()) {
            accountStatusText.setText(R.string.enter_password_to_delete_account);
            return;
        }
        if (!BackendUrlPolicy.permitsCredentialTransport(apiBaseUrl)) {
            accountStatusText.setText(BackendUrlPolicy.CREDENTIAL_TRANSPORT_MESSAGE);
            return;
        }
        setAccountTransitionControls();
        accountStatusText.setText(R.string.deleting_account);
        runInBackground(() -> {
            try {
                synchronized (ChusennoteMessagingService.REGISTRATION_LOCK) {
                    JSONObject response = new JSONObject(postForm(
                            "/api/auth/delete", "password=" + encode(password)));
                    if (!response.optBoolean("deleted")) {
                        throw new IOException("The server did not confirm account deletion.");
                    }
                    SecureTokenStore.setApiToken(getApplicationContext(), "");
                    SecureTokenStore.setPushToken(getApplicationContext(), "");
                    try {
                        Tasks.await(FirebaseMessaging.getInstance().deleteToken(), 10, TimeUnit.SECONDS);
                    } catch (Exception ignored) {
                        // The backend record is already deleted. Firebase token
                        // cleanup is best effort and must not resurrect it.
                    }
                }
                postToMain(() -> {
                    accountStatusText.setText(R.string.account_deleted);
                    setSignedInControls(false);
                    refresh();
                });
            } catch (Exception error) {
                postToMain(() -> {
                    accountStatusText.setText(
                            getString(R.string.could_not_delete_account, error.getMessage()));
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
        runInBackground(() -> {
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
        deleteAccountButton.setEnabled(signedIn);
        emailInput.setVisibility(signedIn ? View.GONE : View.VISIBLE);
        passwordInput.setVisibility(signedIn ? View.GONE : View.VISIBLE);
        registerButton.setVisibility(signedIn ? View.GONE : View.VISIBLE);
        loginButton.setVisibility(signedIn ? View.GONE : View.VISIBLE);
        logoutButton.setVisibility(signedIn ? View.VISIBLE : View.GONE);
        deleteAccountButton.setVisibility(signedIn ? View.VISIBLE : View.GONE);
    }

    /** Prevent duplicate login/logout submissions while one is in flight. */
    private void setAccountTransitionControls() {
        baseUrlInput.setEnabled(false);
        emailInput.setEnabled(false);
        passwordInput.setEnabled(false);
        registerButton.setEnabled(false);
        loginButton.setEnabled(false);
        logoutButton.setEnabled(false);
        deleteAccountButton.setEnabled(false);
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
        List<KeyedRowList.Row> artistRows = new ArrayList<>();
        List<KeyedRowList.Row> watchRows = new ArrayList<>();
        List<KeyedRowList.Row> mutedWatchRows = new ArrayList<>();
        int artistCount = 0;
        int eventCount = 0;
        Set<Integer> watchIdsWithEvents = new HashSet<>();
        Map<Integer, JSONObject> watchesById = new HashMap<>();
        for (int i = 0; i < watches.length(); i++) {
            JSONObject watch = watches.optJSONObject(i);
            if (watch != null) {
                watchesById.put(watch.optInt("id"), watch);
            }
        }
        for (int i = 0; i < events.length(); i++) {
            JSONObject event = events.optJSONObject(i);
            if (event != null && !"artist".equals(event.optString("watch_kind"))) {
                watchIdsWithEvents.add(event.optInt("watch_id"));
            }
        }

        for (int i = 0; i < watches.length(); i++) {
            JSONObject watch = watches.optJSONObject(i);
            if (watch == null) {
                continue;
            }
            int id = watch.optInt("id");
            String keyword = watch.optString("keyword");
            String kind = watch.optString("kind", "event");
            String key = "watch:" + id;
            if (watch.optBoolean("muted")) {
                mutedWatchRows.add(new KeyedRowList.Row(key, watch.toString(), () -> row(
                        keyword,
                        watchCheckedText(watch),
                        new Action("Restore", () -> restoreWatch(id)))));
                continue;
            }
            if ("artist".equals(kind)) {
                artistRows.add(new KeyedRowList.Row(key, watch.toString(), () -> row(
                        keyword,
                        watchCheckedText(watch),
                        new Action("Notify Me", () -> addSubscription(id, "artist_all")),
                        new Action("Remove", () -> removeWatch(id)))));
                artistCount++;
            } else {
                // A watch that found an event is shown as that event below.
                if (!watchIdsWithEvents.contains(id)) {
                    watchRows.add(new KeyedRowList.Row(key, watch.toString(), () -> row(
                            keyword,
                            watchCheckedText(watch),
                            new Action("Edit", () -> editWatch(watch)),
                            new Action("Remove", () -> removeWatch(id)))));
                }
                eventCount++;
            }
        }

        List<KeyedRowList.Row> eventRows = new ArrayList<>();
        eventsByKey.clear();
        for (int i = 0; i < events.length(); i++) {
            JSONObject event = events.optJSONObject(i);
            if (event == null) {
                continue;
            }
            String title = event.optString("title", "Untitled event");
            String officialUrl = text(event, "official_url");
            if ("artist".equals(event.optString("watch_kind"))) {
                artistRows.add(new KeyedRowList.Row(
                        "artist-event:" + event.optInt("id", i), event.toString(), () -> row(
                                title,
                                artistEventSummary(event),
                                openAction("Official Page", officialUrl))));
                continue;
            }
            String key = "event:" + event.optInt("id", -1) + ":" + event.optInt("watch_id", -1);
            if (eventsByKey.containsKey(key)) {
                key = key + "#" + i;
            }
            String eventKey = key;
            eventsByKey.put(eventKey, event);
            // The list shows a summary; everything else is on the detail page.
            int watchId = event.optInt("watch_id");
            JSONObject eventWatch = watchesById.get(watchId);
            // The watch is part of what the row shows through its Edit action.
            eventRows.add(new KeyedRowList.Row(eventKey, event + "\n" + eventWatch, () -> summaryRow(
                    title,
                    eventSummary(event),
                    new Action("Details", () -> showEventDetail(eventKey)),
                    eventWatch == null ? null : new Action("Edit", () -> editWatch(eventWatch)),
                    new Action("Remove", () -> removeWatch(watchId)))));
        }
        if (openEventKey != null) {
            showEventDetail(openEventKey);
        }
        showRows(artistList, artistRows, "No tracked artists yet.");
        eventRows.addAll(watchRows);
        showRows(eventList, eventRows, "Add an event to keep its ticket dates here.");
        showRows(mutedWatchList, mutedWatchRows, null);

        List<KeyedRowList.Row> needsAttentionRows = new ArrayList<>();
        for (int i = 0; i < Math.min(upcoming.length(), 8); i++) {
            JSONObject item = upcoming.optJSONObject(i);
            if (item == null) {
                continue;
            }
            needsAttentionRows.add(new KeyedRowList.Row("upcoming:" + i, item.toString(), () -> upcomingRow(item)));
        }
        showRows(needsAttentionList, needsAttentionRows, eventCount == 0
                ? "Watch an event to see its ticket deadlines here."
                : "Nothing needs your attention right now.");

        List<KeyedRowList.Row> sourceRows = new ArrayList<>();
        List<KeyedRowList.Row> mutedSourceRows = new ArrayList<>();
        for (int i = 0; i < sources.length(); i++) {
            JSONObject source = sources.optJSONObject(i);
            if (source == null) {
                continue;
            }
            int id = source.optInt("id");
            boolean muted = source.optBoolean("muted");
            (muted ? mutedSourceRows : sourceRows).add(new KeyedRowList.Row(
                    "source:" + id, source.toString(), () -> sourceRow(source, id, muted)));
        }
        showRows(sourceList, sourceRows, "No manual sources.");
        showRows(mutedSourceList, mutedSourceRows, null);
        setSectionVisible(mutedWatchHeading, mutedWatchList);
        setSectionVisible(mutedSourceHeading, mutedSourceList);

        List<KeyedRowList.Row> alertRows = new ArrayList<>();
        for (int i = 0; i < Math.min(alerts.length(), 10); i++) {
            JSONObject alert = alerts.optJSONObject(i);
            if (alert == null) {
                continue;
            }
            alertRows.add(new KeyedRowList.Row("alert:" + i, alert.toString(), () -> alertRow(alert)));
        }
        showRows(alertList, alertRows, "Ticket updates will appear here.");
        renderNotificationFeed(notificationFeed);
        renderSubscriptions(subscriptions, watches);
        renderDevices(devices);
        String version = text(health, "version").trim();
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
        statusText.setText("ok".equalsIgnoreCase(text(health, "status"))
                ? R.string.updated_just_now : R.string.updates_may_be_delayed);
        if (BuildConfig.DEBUG) {
            debugStatusText.setText(getString(
                R.string.server_status,
                health.optString("status", "unknown"),
                release,
                schema,
                artistSummary,
                eventSummary,
                alertSummary));
        }
    }

    /** Renders {@code rows}, or a single empty-state row when there are none and {@code emptyText} is set. */
    private void showRows(KeyedRowList list, List<KeyedRowList.Row> rows, String emptyText) {
        if (rows.isEmpty() && emptyText != null) {
            rows.add(new KeyedRowList.Row("empty", emptyText, () -> emptyRow(emptyText)));
        }
        list.render(rows);
    }

    /** Status, first date, and venue on one line, as on the web and iOS. */
    private String eventSummary(JSONObject event) {
        JSONArray dates = event.optJSONArray("event_dates");
        // Prefer the backend's honest venue_label (real venues, "Multiple cities"
        // for a tour, or a dash) over the raw venues array, which is empty for tours.
        String venue = text(event, "venue_label");
        if (venue.isEmpty()) {
            venue = joinFirst(event.optJSONArray("venues"));
        }
        return FriendlyDate.meta(
                EventStatusText.label(text(event, "status_label"), event.optString("status", "watching")),
                dates == null || dates.length() == 0 ? "" : FriendlyDate.format(dates.optString(0)),
                venue);
    }

    private String artistEventSummary(JSONObject event) {
        return eventSummary(event);
    }

    private String watchCheckedText(JSONObject watch) {
        String checked = watch.isNull("last_checked_at") ? "" : text(watch, "last_checked_at");
        return checked.isEmpty() ? "Not checked yet" : "Checked " + FriendlyDate.day(checked);
    }

    private View upcomingRow(JSONObject item) {
        StringBuilder detail = new StringBuilder(FriendlyDate.meta(
                text(item, "status_label"),
                FriendlyDate.format(text(item, "relevant_date")),
                text(item, "platform")));
        appendNonEmptyLine(detail, text(item, "round_name"));
        String eventKey = eventKeyForId(item.optInt("event_id", -1));
        return row(
                item.optString("event_title", "Untitled event"),
                detail.toString(),
                eventKey == null
                        ? openAction("Open", text(item, "url"))
                        : new Action("Details", () -> showEventDetail(eventKey)));
    }

    private String eventKeyForId(int eventId) {
        for (Map.Entry<String, JSONObject> entry : eventsByKey.entrySet()) {
            if (entry.getValue().optInt("id", -2) == eventId) {
                return entry.getKey();
            }
        }
        return null;
    }

    private View sourceRow(JSONObject source, int id, boolean muted) {
        String mode = source.optBoolean("private_note") ? "private note" : source.optString("platform", "manual");
        String detail = mode;
        return row(
                source.optString("label", "Source"),
                detail,
                openAction("Open", text(source, "url")),
                muted
                        ? new Action("Restore", () -> restoreSource(id))
                        : new Action("Remove", () -> removeSource(id)));
    }

    private View alertRow(JSONObject alert) {
        String title = AlertTypeText.label(
                text(alert, "type_label"),
                alert.optString("type", "alert"));
        String event = alert.optString("event_title", text(alert, "event"));
        if (event.isEmpty()) {
            event = alert.optString("watch_keyword", text(alert, "keyword"));
        }
        String round = text(alert, "round");
        if (round.isEmpty()) {
            // Ticket-link alerts carry the link's name instead of a round.
            round = text(alert, "label");
        }
        return row(title, FriendlyDate.meta(
                event,
                round.equals(event) ? "" : round,
                FriendlyDate.day(text(alert, "created_at"))));
    }

    private void renderNotificationFeed(JSONArray notificationFeed) {
        List<KeyedRowList.Row> rows = new ArrayList<>();
        for (int i = 0; i < Math.min(notificationFeed.length(), 10); i++) {
            JSONObject item = notificationFeed.optJSONObject(i);
            if (item == null) {
                continue;
            }
            rows.add(new KeyedRowList.Row("reminder:" + i, item.toString(), () -> {
                StringBuilder detail = new StringBuilder();
                appendNonEmptyLine(detail, text(item, "body"));
                appendNonEmptyLine(detail, FriendlyDate.meta(
                        text(item, "event_title"),
                        text(item, "location"),
                        FriendlyDate.format(text(item, "date")),
                        FriendlyDate.day(text(item, "created_at"))));
                return row(
                        item.optString("title", item.optString("label", "Reminder")),
                        detail.toString(),
                        openAction("Open", text(item, "url")));
            }));
        }
        showRows(notificationFeedList, rows, "No delivered reminders.");
    }

    private void renderSubscriptions(JSONArray subscriptions, JSONArray watches) {
        List<KeyedRowList.Row> rows = new ArrayList<>();
        for (int i = 0; i < subscriptions.length(); i++) {
            JSONObject subscription = subscriptions.optJSONObject(i);
            if (subscription == null) {
                continue;
            }
            int id = subscription.optInt("id");
            int watchId = subscription.optInt("watch_id");
            String keyword = watchKeyword(watches, watchId);
            String title = keyword.isEmpty() ? "Removed watch" : keyword;
            // The title comes from the watch list, so it is part of what this row shows.
            rows.add(new KeyedRowList.Row("subscription:" + id, title + "\n" + subscription, () -> {
                String leadDays = text(subscription, "lead_days");
                return row(
                        title,
                        FriendlyDate.meta(
                                subscriptionScopeLabel(text(subscription, "scope")),
                                text(subscription, "location"),
                                text(subscription, "channels").replace(",", ", "),
                                leadDays.isEmpty() ? "" : leadDays + " days before"),
                        new Action("Remove", () -> removeSubscription(id)));
            }));
        }
        showRows(subscriptionList, rows, "No notification subscriptions.");
    }

    private void renderDevices(JSONArray devices) {
        List<KeyedRowList.Row> rows = new ArrayList<>();
        for (int i = 0; i < devices.length(); i++) {
            JSONObject device = devices.optJSONObject(i);
            if (device == null) {
                continue;
            }
            rows.add(new KeyedRowList.Row("device:" + device.optInt("id", i), device.toString(), () -> {
                String title = text(device, "label").trim();
                if (title.isEmpty()) {
                    title = device.optString("platform", "Device");
                }
                String detail = device.optString("platform", "unknown")
                        + "\nToken: " + abbreviatedToken(text(device, "token"));
                return row(title, detail);
            }));
        }
        showRows(deviceList, rows, "No push devices registered.");
    }

    private static String subscriptionScopeLabel(String scope) {
        switch (scope) {
            case "artist_all":
                return "All shows";
            case "event_all":
                return "All rounds";
            case "event_location":
                return "One location";
            case "round":
                return "One round";
            default:
                return scope;
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
                return text(watch, "keyword");
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

    /** One event is one block in the list: a summary row followed by indented detail rows. */
    private View trackedEventBlock(JSONObject event, String summaryDetail) {
        int watchId = event.optInt("watch_id");
        LinearLayout block = new LinearLayout(this);
        block.setOrientation(LinearLayout.VERTICAL);
        block.addView(row(
                event.optString("title", "Untitled event"),
                summaryDetail,
                openAction("Official Page", text(event, "official_url")),
                new Action("Notify Me", () -> addSubscription(watchId, "event_all"))));

        JSONArray rounds = event.optJSONArray("rounds");
        if (rounds != null) {
            for (int i = 0; i < rounds.length(); i++) {
                JSONObject round = rounds.optJSONObject(i);
                if (round != null) {
                    addTicketRoundRow(block, watchId, round);
                }
            }
        }

        JSONArray locations = event.optJSONArray("event_locations");
        if (locations != null) {
            for (int i = 0; i < locations.length(); i++) {
                JSONObject location = locations.optJSONObject(i);
                if (location != null) {
                    addEventLocationRow(block, watchId, location);
                }
            }
        }

        appendTicketLinks(block, event.optJSONArray("ticket_links"));
        appendFacts(block, "Organizers", event.optJSONArray("organizers"));
        appendFacts(block, "Cast & lineup", event.optJSONArray("lineup"));
        appendFacts(block, "Ticket rules", event.optJSONArray("ticket_rules"));
        appendFacts(block, "Ticket prices", event.optJSONArray("ticket_prices"));
        appendRelatedEvents(block, event.optJSONArray("related_events"));
        appendManualSources(block, event.optJSONArray("manual_sources"));
        return block;
    }

    private void addTicketRoundRow(LinearLayout block, int watchId, JSONObject round) {
        StringBuilder detail = new StringBuilder(FriendlyDate.meta(
                text(round, "status_label"),
                text(round, "platform"),
                text(round, "membership_label")));
        // Only dates the source published; many lotteries never list results or payment.
        String schedule = FriendlyDate.meta(
                labeled("Apply", FriendlyDate.range(text(round, "application_start_at"), text(round, "application_end_at"))),
                labeled("Results", FriendlyDate.format(text(round, "results_date"))),
                labeled("Payment due", FriendlyDate.format(text(round, "payment_end_at"))),
                labeled("On sale", FriendlyDate.format(text(round, "general_sale_date"))),
                labeled("Resale", FriendlyDate.range(text(round, "trade_start_at"), text(round, "trade_end_at"))));
        appendNonEmptyLine(detail, schedule.isEmpty() ? text(round, "schedule_label") : schedule);
        String roundKey = text(round, "round_key");
        block.addView(subRow(
                round.optString("name", "Ticket round"),
                detail.toString(),
                roundKey.isEmpty()
                        ? null
                        : new Action("Notify Me", () -> addSubscription(watchId, "round", "", roundKey)),
                openAction("Source", text(round, "url"))));
    }

    /** {@code optString} turns a JSON null into the text "null"; this returns "" instead. */
    private static String text(JSONObject object, String key) {
        return object == null || object.isNull(key) ? "" : object.optString(key, "");
    }

    private static String labeled(String label, String value) {
        return value == null || value.isEmpty() ? "" : label + " " + value;
    }

    private void addEventLocationRow(LinearLayout block, int watchId, JSONObject location) {
        String locationKey = text(location, "location").trim();
        if (locationKey.isEmpty()) {
            return;
        }
        String venue = text(location, "venue");
        block.addView(subRow(
                locationKey,
                FriendlyDate.meta(venue.equals(locationKey) ? "" : venue, FriendlyDate.format(text(location, "date"))),
                new Action("Notify Me", () -> addSubscription(watchId, "event_location", locationKey, ""))));
    }

    private void appendTicketLinks(LinearLayout block, JSONArray links) {
        if (links == null) {
            return;
        }
        for (int i = 0; i < links.length(); i++) {
            JSONObject link = links.optJSONObject(i);
            if (link == null) {
                continue;
            }
            block.addView(subRow(
                    link.optString("label", "Ticket link"),
                    text(link, "platform"),
                    openAction("Open", text(link, "url"))));
        }
    }

    /** All values of one kind share a single row instead of one row each. */
    private void appendFacts(LinearLayout block, String title, JSONArray values) {
        if (values == null) {
            return;
        }
        StringBuilder detail = new StringBuilder();
        for (int i = 0; i < values.length(); i++) {
            appendNonEmptyLine(detail, values.optString(i, ""));
        }
        if (detail.length() > 0) {
            block.addView(subRow(title, detail.toString()));
        }
    }

    private void appendManualSources(LinearLayout block, JSONArray sources) {
        if (sources == null) {
            return;
        }
        for (int i = 0; i < sources.length(); i++) {
            JSONObject source = sources.optJSONObject(i);
            if (source == null) {
                continue;
            }
            String mode = source.optBoolean("private_note") ? "Private note" : source.optString("platform", "Manual");
            block.addView(subRow(
                    source.optString("label", "Manual source"),
                    mode,
                    openAction("Source", text(source, "url"))));
        }
    }

    private void appendRelatedEvents(LinearLayout block, JSONArray relatedEvents) {
        if (relatedEvents == null) {
            return;
        }
        for (int i = 0; i < relatedEvents.length(); i++) {
            JSONObject related = relatedEvents.optJSONObject(i);
            if (related == null) {
                continue;
            }
            block.addView(subRow(
                    related.optString("title", "Related event"),
                    FriendlyDate.meta(
                            FriendlyDate.format(text(related, "event_date")),
                            text(related, "venue_label"),
                            joinFirst(related.optJSONArray("recommendation_reasons"))),
                    openAction("Official Page", text(related, "official_url"))));
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

    // ---- View building. One row shape, one list shape, one form shape. ----

    private static final class Action {
        final String label;
        final Runnable run;

        Action(String label, Runnable run) {
            this.label = label;
            this.run = run;
        }
    }

    /** An action that opens {@code url}, or null (no button) when it is not a web URL. */
    private Action openAction(String label, String url) {
        return isWebUrl(url) ? new Action(label, () -> openUrl(url)) : null;
    }

    private int dimen(int resource) {
        return getResources().getDimensionPixelSize(resource);
    }

    /** A TextView whose look comes entirely from {@code style}. */
    private TextView styledText(String value, int style) {
        TextView view = new TextView(this, null, 0, style);
        view.setText(value);
        return view;
    }

    private TextView section(String value) {
        return styledText(value, R.style.SectionLabel);
    }

    private TextView body(String value) {
        return styledText(value, R.style.BodyText);
    }

    private TextView emptyRow(String value) {
        return styledText(value, R.style.EmptyRowText);
    }

    private EditText singleLineInput(String hint) {
        EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setHint(hint);
        return input;
    }

    /** A rounded surface whose rows are separated by hairlines. */
    private LinearLayout list() {
        LinearLayout list = new LinearLayout(this);
        list.setOrientation(LinearLayout.VERTICAL);
        list.setBackgroundResource(R.drawable.surface);
        list.setDividerDrawable(getDrawable(R.drawable.list_divider));
        list.setDividerPadding(dimen(R.dimen.row_inset));
        list.setShowDividers(LinearLayout.SHOW_DIVIDER_MIDDLE);
        return list;
    }

    private KeyedRowList rowList() {
        return new KeyedRowList(list());
    }

    /** A rounded surface holding inputs and their submit button. */
    private LinearLayout form() {
        LinearLayout form = new LinearLayout(this);
        form.setOrientation(LinearLayout.VERTICAL);
        form.setBackgroundResource(R.drawable.surface);
        int horizontal = dimen(R.dimen.row_vertical);
        int vertical = dimen(R.dimen.space_sm);
        form.setPadding(horizontal, vertical, horizontal, vertical);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT);
        params.topMargin = vertical;
        form.setLayoutParams(params);
        return form;
    }

    private LinearLayout actionBar() {
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        return bar;
    }

    private Button textButton(String label, Runnable action) {
        Button button = new Button(this, null, 0, R.style.TextButton);
        button.setText(label);
        button.setOnClickListener(view -> action.run());
        return button;
    }

    private Button primaryButton(String label, Runnable action) {
        Button button = new Button(this, null, 0, R.style.PrimaryButton);
        button.setText(label);
        button.setOnClickListener(view -> action.run());
        return button;
    }

    private LinearLayout row(String title, String detail, Action... actions) {
        return buildRow(title, detail, false, actions);
    }

    /** A row whose detail is cut to two lines, for lists whose rows open a full page. */
    private LinearLayout summaryRow(String title, String detail, Action... actions) {
        LinearLayout row = buildRow(title, detail, false, actions);
        View detailView = row.getChildAt(1);
        if (detailView instanceof TextView) {
            ((TextView) detailView).setMaxLines(2);
            ((TextView) detailView).setEllipsize(TextUtils.TruncateAt.END);
        }
        return row;
    }

    /** A row indented under the event it belongs to. */
    private LinearLayout subRow(String title, String detail, Action... actions) {
        return buildRow(title, detail, true, actions);
    }

    private LinearLayout buildRow(String title, String detail, boolean nested, Action... actions) {
        int start = dimen(nested ? R.dimen.row_inset_nested : R.dimen.row_inset);
        int end = dimen(R.dimen.row_inset);
        int vertical = dimen(nested ? R.dimen.row_vertical_nested : R.dimen.row_vertical);
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.VERTICAL);
        row.setPadding(0, vertical, 0, vertical);
        TextView titleView = styledText(title, nested ? R.style.RowTitle_Nested : R.style.RowTitle);
        titleView.setPadding(start, 0, end, 0);
        row.addView(titleView);
        if (detail != null && !detail.isEmpty()) {
            TextView detailView = styledText(detail, R.style.RowDetail);
            detailView.setPadding(start, dimen(R.dimen.space_xs), end, 0);
            row.addView(detailView);
        }
        LinearLayout buttons = null;
        for (Action action : actions) {
            if (action == null) {
                continue;
            }
            if (buttons == null) {
                buttons = actionBar();
                // Buttons carry their own inset, so pull the bar back to align labels with the text.
                int buttonInset = dimen(R.dimen.space_sm);
                buttons.setPadding(start - buttonInset, 0, buttonInset, 0);
                row.addView(buttons);
                row.setPadding(0, vertical, 0, 0);
            }
            buttons.addView(textButton(action.label, action.run));
        }
        return row;
    }

    private void setSectionVisible(TextView heading, KeyedRowList list) {
        int visibility = list.container().getChildCount() == 0 ? View.GONE : View.VISIBLE;
        heading.setVisibility(visibility);
        list.container().setVisibility(visibility);
    }
}
