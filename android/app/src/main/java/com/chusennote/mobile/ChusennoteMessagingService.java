package com.chusennote.mobile;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;

import androidx.annotation.RequiresApi;
import androidx.core.app.NotificationCompat;

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
    private static final String LEGACY_PREF_PUSH_TOKEN = "push_token";
    // Registration must finish before logout detaches the device, and queued
    // registrations must read the cleared credential after logout completes.
    static final Object REGISTRATION_LOCK = new Object();

    static String savedToken(Context context) {
        String token = SecureTokenStore.pushToken(context.getApplicationContext());
        SharedPreferences preferences = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
        String legacyToken = preferences.getString(LEGACY_PREF_PUSH_TOKEN, "");
        if (token.isEmpty() && !legacyToken.isEmpty()) {
            token = legacyToken;
            SecureTokenStore.setPushToken(context.getApplicationContext(), token);
        }
        if (preferences.contains(LEGACY_PREF_PUSH_TOKEN)) {
            preferences.edit().remove(LEGACY_PREF_PUSH_TOKEN).apply();
        }
        return token;
    }

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
        showNotification(getApplicationContext(), title, body);
    }

    /** Builds and posts the same notification used by foreground FCM delivery. */
    static Notification showNotification(Context context, String title, String body) {
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        if (manager == null) {
            return null;
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            ensureNotificationChannel(manager);
        }
        // Foreground FCM messages are delivered to this callback rather than
        // displayed by the system, including on our API 24/25 floor.
        Intent launchIntent = new Intent(context, MainActivity.class)
                .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent contentIntent = PendingIntent.getActivity(
                context,
                0,
                launchIntent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Notification notification = new NotificationCompat.Builder(context, CHANNEL_ID)
                .setSmallIcon(android.R.drawable.ic_dialog_info)
                .setContentTitle(title)
                .setContentText(body)
                .setStyle(new NotificationCompat.BigTextStyle().bigText(body))
                .setContentIntent(contentIntent)
                .setAutoCancel(true)
                .build();
        manager.notify((int) (System.currentTimeMillis() % Integer.MAX_VALUE), notification);
        return notification;
    }

    @RequiresApi(Build.VERSION_CODES.O)
    private static void ensureNotificationChannel(NotificationManager manager) {
        NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID,
                "Ticket reminders",
                NotificationManager.IMPORTANCE_HIGH);
        channel.setDescription("Lottery application, results, payment, and sale reminders.");
        manager.createNotificationChannel(channel);
    }

    /** POST the device token to the backend using the saved base URL. */
    static void registerToken(Context context, String token) {
        if (token == null || token.isEmpty()) {
            return;
        }
        new Thread(() -> {
            synchronized (REGISTRATION_LOCK) {
                registerTokenLocked(context, token);
            }
        }).start();
    }

    private static void registerTokenLocked(Context context, String token) {
        SharedPreferences preferences = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
        SecureTokenStore.setPushToken(context.getApplicationContext(), token);
        preferences.edit().remove(LEGACY_PREF_PUSH_TOKEN).apply();
        String baseUrl = preferences.getString(PREF_BASE_URL, "").trim().replaceAll("/+$", "");
        if (baseUrl.isEmpty()) {
            return;
        }
        String apiToken = SecureTokenStore.apiToken(context.getApplicationContext());
        // An FCM registration token can address this specific device even in
        // anonymous mode, so protect it with the same transport policy as an
        // account bearer token.
        if (!BackendUrlPolicy.permitsCredentialTransport(baseUrl)) {
            return;
        }
        HttpURLConnection connection = null;
        try {
            String body = "token=" + URLEncoder.encode(token, "UTF-8")
                    + "&platform=android"
                    + "&label=" + URLEncoder.encode(android.os.Build.MODEL, "UTF-8");
            URL url = new URL(baseUrl + "/api/devices");
            connection = (HttpURLConnection) url.openConnection();
            connection.setRequestMethod("POST");
            connection.setInstanceFollowRedirects(false);
            connection.setDoOutput(true);
            connection.setConnectTimeout(10000);
            connection.setReadTimeout(10000);
            connection.setRequestProperty("Content-Type", "application/x-www-form-urlencoded");
            if (!apiToken.isEmpty()) {
                connection.setRequestProperty("Authorization", "Bearer " + apiToken);
            }
            try (OutputStream stream = connection.getOutputStream()) {
                stream.write(body.getBytes(StandardCharsets.UTF_8));
            }
            int responseCode = connection.getResponseCode();
            if (responseCode < 200 || responseCode >= 300) {
                return;
            }
        } catch (Exception ignored) {
            // Best effort: a failed registration is retried on the next launch.
        } finally {
            if (connection != null) {
                connection.disconnect();
            }
        }
    }
}
