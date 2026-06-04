package com.chusennote.mobile;

import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
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
import java.io.OutputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.net.URLEncoder;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class MainActivity extends Activity {
    private static final String PREFS_NAME = "chusennote";
    private static final String PREF_BASE_URL = "base_url";
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private EditText baseUrlInput;
    private EditText artistInput;
    private EditText artistTagsInput;
    private EditText artistRegionsInput;
    private EditText artistVenuesInput;
    private EditText eventInput;
    private EditText eventTagsInput;
    private EditText eventRegionsInput;
    private EditText eventVenuesInput;
    private EditText eventAlertsInput;
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
    private TextView statusText;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(buildLayout());
        refresh();
    }

    private View buildLayout() {
        ScrollView scrollView = new ScrollView(this);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(32, 32, 32, 32);
        scrollView.addView(root);

        TextView title = heading("chusennote");
        root.addView(title);

        statusText = body("Connect to the local chusennote server.");
        root.addView(statusText);

        baseUrlInput = new EditText(this);
        baseUrlInput.setSingleLine(true);
        baseUrlInput.setText(preferences().getString(PREF_BASE_URL, "http://10.0.2.2:8765"));
        baseUrlInput.setHint("API base URL");
        root.addView(baseUrlInput);

        Button refresh = new Button(this);
        refresh.setText("Refresh");
        refresh.setOnClickListener(view -> {
            saveBaseUrl();
            refresh();
        });
        root.addView(refresh);

        Button calendar = new Button(this);
        calendar.setText("Open Calendar Feed");
        calendar.setOnClickListener(view -> openCalendarFeed());
        root.addView(calendar);

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
        addArtist.setText("Add Artist");
        addArtist.setOnClickListener(view -> addWatch("artist", artistInput, artistTagsInput, artistRegionsInput, artistVenuesInput, null));
        root.addView(addArtist);
        artistList = new LinearLayout(this);
        artistList.setOrientation(LinearLayout.VERTICAL);
        root.addView(artistList);

        root.addView(section("Tracked Events"));
        eventInput = new EditText(this);
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
        addEvent.setText("Add Event");
        addEvent.setOnClickListener(view -> addWatch("event", eventInput, eventTagsInput, eventRegionsInput, eventVenuesInput, eventAlertsInput));
        root.addView(addEvent);
        Button runEvents = new Button(this);
        runEvents.setText("Run Event Watches");
        runEvents.setOnClickListener(view -> runEventWatches());
        root.addView(runEvents);
        eventList = new LinearLayout(this);
        eventList.setOrientation(LinearLayout.VERTICAL);
        root.addView(eventList);

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
        sourcePrivateNoteInput.setText("Private note");
        root.addView(sourcePrivateNoteInput);
        Button addSource = new Button(this);
        addSource.setText("Add Source");
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

        return scrollView;
    }

    private void refresh() {
        saveBaseUrl();
        statusText.setText("Loading...");
        executor.execute(() -> {
            try {
                JSONArray watches = getJsonArray("/api/watchlist?include_muted=1");
                JSONArray events = getJsonArray("/api/events");
                JSONArray upcoming = getJsonArray("/api/upcoming");
                JSONArray alerts = getJsonArray("/api/alerts");
                JSONArray sources = getJsonArray("/api/sources?include_muted=1");
                JSONObject health = getJsonObject("/api/health");
                mainHandler.post(() -> render(watches, events, upcoming, alerts, sources, health));
            } catch (Exception error) {
                mainHandler.post(() -> statusText.setText("Could not load chusennote: " + error.getMessage()));
            }
        });
    }

    private SharedPreferences preferences() {
        return getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
    }

    private void saveBaseUrl() {
        preferences().edit().putString(PREF_BASE_URL, baseUrlInput.getText().toString().trim()).apply();
    }

    private void openCalendarFeed() {
        saveBaseUrl();
        String baseUrl = baseUrlInput.getText().toString().trim().replaceAll("/+$", "");
        if (baseUrl.isEmpty()) {
            statusText.setText("Enter the API base URL first.");
            return;
        }
        Intent intent = new Intent(Intent.ACTION_VIEW, Uri.parse(baseUrl + "/calendar.ics"));
        startActivity(intent);
    }

    private void openUrl(String url) {
        if (!isWebUrl(url)) {
            statusText.setText("No web URL available.");
            return;
        }
        startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url.trim())));
    }

    private boolean isWebUrl(String url) {
        if (url == null) {
            return false;
        }
        String normalized = url.trim().toLowerCase();
        return normalized.startsWith("https://") || normalized.startsWith("http://");
    }

    private void addWatch(String kind, EditText input, EditText tagsInput, EditText regionsInput, EditText venuesInput, EditText alertsInput) {
        String keyword = input.getText().toString().trim();
        if (keyword.isEmpty()) {
            statusText.setText("Enter a keyword first.");
            return;
        }
        String tags = tagsInput == null ? "" : tagsInput.getText().toString().trim();
        String regions = regionsInput == null ? "" : regionsInput.getText().toString().trim();
        String venues = venuesInput == null ? "" : venuesInput.getText().toString().trim();
        String alerts = alertsInput == null ? "" : alertsInput.getText().toString().trim();
        statusText.setText("Adding " + kind + "...");
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
                mainHandler.post(() -> {
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
                mainHandler.post(() -> statusText.setText("Could not add watch: " + error.getMessage()));
            }
        });
    }

    private void runEventWatches() {
        statusText.setText("Running tracked events...");
        executor.execute(() -> {
            try {
                JSONArray alerts = postJsonArray("/api/run", "kind=event");
                mainHandler.post(() -> {
                    statusText.setText("Run complete: " + alerts.length() + " alerts.");
                    refresh();
                });
            } catch (Exception error) {
                mainHandler.post(() -> statusText.setText("Could not run watches: " + error.getMessage()));
            }
        });
    }

    private void addSource() {
        String watch = sourceWatchInput.getText().toString().trim();
        String url = sourceUrlInput.getText().toString().trim();
        String label = sourceLabelInput.getText().toString().trim();
        boolean privateNote = sourcePrivateNoteInput.isChecked();
        if (watch.isEmpty() || url.isEmpty()) {
            statusText.setText("Enter a watch id/keyword and source URL first.");
            return;
        }
        statusText.setText("Adding source...");
        executor.execute(() -> {
            try {
                postForm(
                    "/api/sources",
                    "watch=" + encode(watch)
                        + "&url=" + encode(url)
                        + "&label=" + encode(label)
                        + (privateNote ? "&private_note=1" : "")
                );
                mainHandler.post(() -> {
                    sourceWatchInput.setText("");
                    sourceUrlInput.setText("");
                    sourceLabelInput.setText("");
                    sourcePrivateNoteInput.setChecked(false);
                    refresh();
                });
            } catch (Exception error) {
                mainHandler.post(() -> statusText.setText("Could not add source: " + error.getMessage()));
            }
        });
    }

    private void removeWatch(int id) {
        statusText.setText("Removing watch...");
        executor.execute(() -> {
            try {
                postForm("/api/watchlist/remove", "identifier=" + encode(String.valueOf(id)));
                mainHandler.post(this::refresh);
            } catch (Exception error) {
                mainHandler.post(() -> statusText.setText("Could not remove watch: " + error.getMessage()));
            }
        });
    }

    private void restoreWatch(int id) {
        statusText.setText("Restoring watch...");
        executor.execute(() -> {
            try {
                postForm("/api/watchlist/unmute", "identifier=" + encode(String.valueOf(id)));
                mainHandler.post(this::refresh);
            } catch (Exception error) {
                mainHandler.post(() -> statusText.setText("Could not restore watch: " + error.getMessage()));
            }
        });
    }

    private void removeSource(int id) {
        statusText.setText("Removing source...");
        executor.execute(() -> {
            try {
                postForm("/api/sources/remove", "identifier=" + encode(String.valueOf(id)));
                mainHandler.post(this::refresh);
            } catch (Exception error) {
                mainHandler.post(() -> statusText.setText("Could not remove source: " + error.getMessage()));
            }
        });
    }

    private void restoreSource(int id) {
        statusText.setText("Restoring source...");
        executor.execute(() -> {
            try {
                postForm("/api/sources/unmute", "identifier=" + encode(String.valueOf(id)));
                mainHandler.post(this::refresh);
            } catch (Exception error) {
                mainHandler.post(() -> statusText.setText("Could not restore source: " + error.getMessage()));
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
        URL url = new URL(baseUrlInput.getText().toString().replaceAll("/+$", "") + path);
        HttpURLConnection connection = (HttpURLConnection) url.openConnection();
        connection.setRequestMethod("GET");
        connection.setConnectTimeout(5000);
        connection.setReadTimeout(5000);
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(connection.getInputStream(), StandardCharsets.UTF_8))) {
            StringBuilder body = new StringBuilder();
            String line;
            while ((line = reader.readLine()) != null) {
                body.append(line);
            }
            return body.toString();
        }
    }

    private String postForm(String path, String body) throws Exception {
        byte[] data = body.getBytes(StandardCharsets.UTF_8);
        URL url = new URL(baseUrlInput.getText().toString().replaceAll("/+$", "") + path);
        HttpURLConnection connection = (HttpURLConnection) url.openConnection();
        connection.setRequestMethod("POST");
        connection.setDoOutput(true);
        connection.setConnectTimeout(5000);
        connection.setReadTimeout(30000);
        connection.setRequestProperty("Content-Type", "application/x-www-form-urlencoded");
        connection.setRequestProperty("Content-Length", String.valueOf(data.length));
        try (OutputStream output = connection.getOutputStream()) {
            output.write(data);
        }
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(connection.getInputStream(), StandardCharsets.UTF_8))) {
            StringBuilder response = new StringBuilder();
            String line;
            while ((line = reader.readLine()) != null) {
                response.append(line);
            }
            return response.toString();
        }
    }

    private JSONArray postJsonArray(String path, String body) throws Exception {
        return new JSONArray(postForm(path, body));
    }

    private String encode(String value) throws Exception {
        return URLEncoder.encode(value, StandardCharsets.UTF_8.name());
    }

    private void render(JSONArray watches, JSONArray events, JSONArray upcoming, JSONArray alerts, JSONArray sources, JSONObject health) {
        artistList.removeAllViews();
        eventList.removeAllViews();
        mutedWatchList.removeAllViews();
        needsAttentionList.removeAllViews();
        sourceList.removeAllViews();
        mutedSourceList.removeAllViews();
        alertList.removeAllViews();
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
                artistList.addView(removableCard(watch.optString("keyword"), watchPreferences(watch, false), () -> removeWatch(watch.optInt("id"))));
                artistCount++;
            } else {
                eventList.addView(removableCard(watch.optString("keyword"), "Watch #" + watch.optInt("id") + "\n" + watchPreferences(watch, true), () -> removeWatch(watch.optInt("id"))));
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
                String detail = event.optString("status", "watching");
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
            String detail = event.optString("status", "watching") + " - " + (rounds == null ? 0 : rounds.length()) + " ticket rounds";
            if (!clues.isEmpty()) {
                detail = detail + "\n" + clues;
            }
            String evidence = firstRoundEvidence(rounds);
            if (!evidence.isEmpty()) {
                detail = detail + "\nEvidence: " + evidence;
            }
            String reasons = joinFirst(event.optJSONArray("match_reasons"));
            if (!reasons.isEmpty()) {
                detail = detail + "\nWhy: " + reasons;
            }
            addEventCard(eventList, event, detail);
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
            String detail = item.optString("status", "unknown")
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
                mutedSourceList.addView(twoActionCard(source.optString("label", "Source"), detail, "Open", () -> openUrl(sourceUrl), "Restore", () -> restoreSource(source.optInt("id"))));
                mutedSourceCount++;
                continue;
            }
            sourceList.addView(twoActionCard(source.optString("label", "Source"), detail, "Open", () -> openUrl(sourceUrl), "Remove", () -> removeSource(source.optInt("id"))));
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
            String title = alert.optString("type", "alert");
            String detail = alert.optString("event", alert.optString("keyword", "")) + " " + alert.optString("round", "");
            if (alert.has("event_id")) {
                detail = detail.trim() + "\nEvent #" + alert.optInt("event_id");
            }
            alertList.addView(card(title, detail.trim()));
        }
        if (alertList.getChildCount() == 0) {
            alertList.addView(body("No recent alerts."));
        }
        statusText.setText(
            "Server ok - "
                + health.optInt("tracked_artists", artistCount)
                + " artists, "
                + health.optInt("tracked_events", eventCount)
                + " events, "
                + health.optInt("alerts", 0)
                + " alerts."
        );
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
            appendPreference(detail, "Alerts", watch.optString("alert_preferences", ""));
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
        String venues = joinFirst(event.optJSONArray("venues"));
        if (!dates.isEmpty()) {
            detail.append("Dates: ").append(dates);
        }
        if (!venues.isEmpty()) {
            if (detail.length() > 0) {
                detail.append("\n");
            }
            detail.append("Venues: ").append(venues);
        }
        return detail.toString();
    }

    private String firstRoundEvidence(JSONArray rounds) {
        if (rounds == null || rounds.length() == 0) {
            return "";
        }
        for (int i = 0; i < rounds.length(); i++) {
            JSONObject round = rounds.optJSONObject(i);
            if (round != null && !round.optString("evidence", "").isEmpty()) {
                return round.optString("evidence");
            }
        }
        return "";
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

    private LinearLayout removableCard(String title, String detail, Runnable removeAction) {
        return actionCard(title, detail, "Remove", removeAction);
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
}
