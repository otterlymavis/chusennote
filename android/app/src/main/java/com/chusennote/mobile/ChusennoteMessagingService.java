package com.chusennote.mobile;

import android.app.Notification;
import android.app.NotificationManager;
import android.content.Context;
import android.content.SharedPreferences;
import android.os.Build;

import com.google.firebase.messaging.FirebaseMessagingService;
import com.google.firebase.messaging.RemoteMessage;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;

/**
 * Receives Firebase Cloud Messaging tokens and push payloads. The token is
 * registered with the chusennote backend (POST /api/devices) so the server can
 * deliver ticket-date reminders to this device; incoming messages are shown as
 * a notification in the reminders channel.
 */
public class ChusennoteMessagingService extends FirebaseMessagingService {
    static final String CHANNEL_ID = "chusennote_reminders";
    private static final String PREFS_NAME = "chusennote";
    private static final String PREF_BASE_URL = "base_url";

    @Override
    public void onNewToken(String token) {
        registerToken(getApplicationContext(), token);
    }

    @Override
    public void onMessageReceived(RemoteMessage message) {
        String title = "chusennote";
        String body = "";
        if (message.getNotification() != null) {
            if (message.getNotification().getTitle() != null) {
                title = message.getNotification().getTitle();
            }
            if (message.getNotification().getBody() != null) {
                body = message.getNotification().getBody();
            }
        }
        NotificationManager manager = getSystemService(NotificationManager.class);
        // The channel constructor is API 26+. On older devices a notification
        // payload is shown automatically by the system, so only the foreground
        // path here needs to run on O and above.
        if (manager == null || Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return;
        }
        Notification notification = new Notification.Builder(this, CHANNEL_ID)
                .setSmallIcon(android.R.drawable.ic_dialog_info)
                .setContentTitle(title)
                .setContentText(body)
                .setStyle(new Notification.BigTextStyle().bigText(body))
                .setAutoCancel(true)
                .build();
        manager.notify((int) (System.currentTimeMillis() % Integer.MAX_VALUE), notification);
    }

    /** POST the device token to the backend using the saved base URL. */
    static void registerToken(Context context, String token) {
        if (token == null || token.isEmpty()) {
            return;
        }
        SharedPreferences preferences = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
        String baseUrl = preferences.getString(PREF_BASE_URL, "").trim().replaceAll("/+$", "");
        if (baseUrl.isEmpty()) {
            return;
        }
        new Thread(() -> {
            HttpURLConnection connection = null;
            try {
                String body = "token=" + URLEncoder.encode(token, "UTF-8")
                        + "&platform=android"
                        + "&label=" + URLEncoder.encode(android.os.Build.MODEL, "UTF-8");
                URL url = new URL(baseUrl + "/api/devices");
                connection = (HttpURLConnection) url.openConnection();
                connection.setRequestMethod("POST");
                connection.setDoOutput(true);
                connection.setConnectTimeout(10000);
                connection.setReadTimeout(10000);
                connection.setRequestProperty("Content-Type", "application/x-www-form-urlencoded");
                try (OutputStream stream = connection.getOutputStream()) {
                    stream.write(body.getBytes(StandardCharsets.UTF_8));
                }
                connection.getResponseCode();
            } catch (Exception ignored) {
                // Best effort: a failed registration is retried on the next launch.
            } finally {
                if (connection != null) {
                    connection.disconnect();
                }
            }
        }).start();
    }
}
