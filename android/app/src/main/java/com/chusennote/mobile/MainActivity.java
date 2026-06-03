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
    private EditText eventInput;
    private EditText sourceWatchInput;
    private EditText sourceUrlInput;
    private EditText sourceLabelInput;
    private LinearLayout artistList;
    private LinearLayout eventList;
    private LinearLayout sourceList;
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
        Button addArtist = new Button(this);
        addArtist.setText("Add Artist");
        addArtist.setOnClickListener(view -> addWatch("artist", artistInput));
        root.addView(addArtist);
        artistList = new LinearLayout(this);
        artistList.setOrientation(LinearLayout.VERTICAL);
        root.addView(artistList);

        root.addView(section("Tracked Events"));
        eventInput = new EditText(this);
        eventInput.setSingleLine(true);
        eventInput.setHint("Event keyword");
        root.addView(eventInput);
        Button addEvent = new Button(this);
        addEvent.setText("Add Event");
        addEvent.setOnClickListener(view -> addWatch("event", eventInput));
        root.addView(addEvent);
        Button runEvents = new Button(this);
        runEvents.setText("Run Event Watches");
        runEvents.setOnClickListener(view -> runEventWatches());
        root.addView(runEvents);
        eventList = new LinearLayout(this);
        eventList.setOrientation(LinearLayout.VERTICAL);
        root.addView(eventList);

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
        Button addSource = new Button(this);
        addSource.setText("Add Source");
        addSource.setOnClickListener(view -> addSource());
        root.addView(addSource);
        sourceList = new LinearLayout(this);
        sourceList.setOrientation(LinearLayout.VERTICAL);
        root.addView(sourceList);

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
                JSONArray watches = getJsonArray("/api/watchlist");
                JSONArray events = getJsonArray("/api/events");
                JSONArray alerts = getJsonArray("/api/alerts");
                JSONArray sources = getJsonArray("/api/sources");
                JSONObject health = getJsonObject("/api/health");
                mainHandler.post(() -> render(watches, events, alerts, sources, health));
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

    private void addWatch(String kind, EditText input) {
        String keyword = input.getText().toString().trim();
        if (keyword.isEmpty()) {
            statusText.setText("Enter a keyword first.");
            return;
        }
        statusText.setText("Adding " + kind + "...");
        executor.execute(() -> {
            try {
                postForm("/api/watchlist", "keyword=" + encode(keyword) + "&kind=" + encode(kind));
                mainHandler.post(() -> {
                    input.setText("");
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
        if (watch.isEmpty() || url.isEmpty()) {
            statusText.setText("Enter a watch id/keyword and source URL first.");
            return;
        }
        statusText.setText("Adding source...");
        executor.execute(() -> {
            try {
                postForm(
                    "/api/sources",
                    "watch=" + encode(watch) + "&url=" + encode(url) + "&label=" + encode(label)
                );
                mainHandler.post(() -> {
                    sourceWatchInput.setText("");
                    sourceUrlInput.setText("");
                    sourceLabelInput.setText("");
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

    private void render(JSONArray watches, JSONArray events, JSONArray alerts, JSONArray sources, JSONObject health) {
        artistList.removeAllViews();
        eventList.removeAllViews();
        sourceList.removeAllViews();
        alertList.removeAllViews();
        int artistCount = 0;
        int eventCount = 0;

        for (int i = 0; i < watches.length(); i++) {
            JSONObject watch = watches.optJSONObject(i);
            if (watch == null || watch.optBoolean("muted")) {
                continue;
            }
            String kind = watch.optString("kind", "event");
            if ("artist".equals(kind)) {
                artistList.addView(removableCard(watch.optString("keyword"), "Last checked: " + watch.optString("last_checked_at", "never"), () -> removeWatch(watch.optInt("id"))));
                artistCount++;
            } else {
                eventList.addView(removableCard(watch.optString("keyword"), "Watch #" + watch.optInt("id"), () -> removeWatch(watch.optInt("id"))));
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
                artistList.addView(card(event.optString("title", "Untitled event"), detail));
                continue;
            }
            JSONArray rounds = event.optJSONArray("rounds");
            String detail = event.optString("status", "watching") + " - " + (rounds == null ? 0 : rounds.length()) + " ticket rounds";
            if (!clues.isEmpty()) {
                detail = detail + "\n" + clues;
            }
            eventList.addView(card(event.optString("title", "Untitled event"), detail));
        }

        if (artistCount == 0) {
            artistList.addView(body("No tracked artists yet."));
        }
        if (eventCount == 0 && eventList.getChildCount() == 0) {
            eventList.addView(body("No tracked events yet."));
        }
        for (int i = 0; i < sources.length(); i++) {
            JSONObject source = sources.optJSONObject(i);
            if (source == null || source.optBoolean("muted")) {
                continue;
            }
            String detail = "Watch #" + source.optInt("watch_id") + " - " + source.optString("platform", "manual") + "\n" + source.optString("url", "");
            sourceList.addView(removableCard(source.optString("label", "Source"), detail, () -> removeSource(source.optInt("id"))));
        }
        if (sourceList.getChildCount() == 0) {
            sourceList.addView(body("No manual sources."));
        }
        for (int i = 0; i < Math.min(alerts.length(), 10); i++) {
            JSONObject alert = alerts.optJSONObject(i);
            if (alert == null) {
                continue;
            }
            String title = alert.optString("type", "alert");
            String detail = alert.optString("event", alert.optString("keyword", "")) + " " + alert.optString("round", "");
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

    private TextView card(String title, String detail) {
        TextView view = body(title + "\n" + detail);
        view.setPadding(18, 18, 18, 18);
        return view;
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
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.VERTICAL);
        row.setPadding(18, 18, 18, 18);
        row.addView(body(title + "\n" + detail));
        Button remove = new Button(this);
        remove.setText("Remove");
        remove.setOnClickListener(view -> removeAction.run());
        row.addView(remove);
        return row;
    }
}
