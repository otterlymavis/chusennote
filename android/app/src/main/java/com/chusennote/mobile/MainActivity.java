package com.chusennote.mobile;

import android.app.Activity;
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
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class MainActivity extends Activity {
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private EditText baseUrlInput;
    private LinearLayout artistList;
    private LinearLayout eventList;
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
        baseUrlInput.setText("http://10.0.2.2:8765");
        baseUrlInput.setHint("API base URL");
        root.addView(baseUrlInput);

        Button refresh = new Button(this);
        refresh.setText("Refresh");
        refresh.setOnClickListener(view -> refresh());
        root.addView(refresh);

        root.addView(section("Tracked Artists"));
        artistList = new LinearLayout(this);
        artistList.setOrientation(LinearLayout.VERTICAL);
        root.addView(artistList);

        root.addView(section("Tracked Events"));
        eventList = new LinearLayout(this);
        eventList.setOrientation(LinearLayout.VERTICAL);
        root.addView(eventList);

        return scrollView;
    }

    private void refresh() {
        statusText.setText("Loading...");
        executor.execute(() -> {
            try {
                JSONArray watches = getJsonArray("/api/watchlist");
                JSONArray events = getJsonArray("/api/events");
                mainHandler.post(() -> render(watches, events));
            } catch (Exception error) {
                mainHandler.post(() -> statusText.setText("Could not load chusennote: " + error.getMessage()));
            }
        });
    }

    private JSONArray getJsonArray(String path) throws Exception {
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
            return new JSONArray(body.toString());
        }
    }

    private void render(JSONArray watches, JSONArray events) {
        artistList.removeAllViews();
        eventList.removeAllViews();
        int artistCount = 0;
        int eventCount = 0;

        for (int i = 0; i < watches.length(); i++) {
            JSONObject watch = watches.optJSONObject(i);
            if (watch == null || watch.optBoolean("muted")) {
                continue;
            }
            String kind = watch.optString("kind", "event");
            if ("artist".equals(kind)) {
                artistList.addView(card(watch.optString("keyword"), "Last checked: " + watch.optString("last_checked_at", "never")));
                artistCount++;
            } else {
                eventList.addView(card(watch.optString("keyword"), "Watch #" + watch.optInt("id")));
                eventCount++;
            }
        }

        for (int i = 0; i < events.length(); i++) {
            JSONObject event = events.optJSONObject(i);
            if (event == null || !"event".equals(event.optString("watch_kind"))) {
                continue;
            }
            JSONArray rounds = event.optJSONArray("rounds");
            String detail = event.optString("status", "watching") + " - " + (rounds == null ? 0 : rounds.length()) + " ticket rounds";
            eventList.addView(card(event.optString("title", "Untitled event"), detail));
        }

        if (artistCount == 0) {
            artistList.addView(body("No tracked artists yet."));
        }
        if (eventCount == 0 && eventList.getChildCount() == 0) {
            eventList.addView(body("No tracked events yet."));
        }
        statusText.setText("Loaded " + artistCount + " artists and " + eventCount + " event watches.");
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
}
