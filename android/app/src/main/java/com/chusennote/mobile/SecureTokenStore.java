package com.chusennote.mobile;

import android.content.Context;
import android.content.SharedPreferences;

import androidx.security.crypto.EncryptedSharedPreferences;
import androidx.security.crypto.MasterKey;

import java.io.IOException;
import java.security.GeneralSecurityException;

/**
 * Keystore-encrypted storage for the account's API bearer token and the
 * derived calendar-only token. Kept separate from the plain
 * SharedPreferences file used for the (non-sensitive) base URL, since these
 * are full-access credentials.
 */
final class SecureTokenStore {
    private static final String FILE_NAME = "chusennote_secure";
    private static final String LEGACY_FALLBACK_FILE_NAME = FILE_NAME + "_fallback";
    private static final String KEY_API_TOKEN = "api_token";
    private static final String KEY_CALENDAR_TOKEN = "calendar_token";
    private static final String KEY_PUSH_TOKEN = "push_token";
    private static final Object FALLBACK_LOCK = new Object();
    private static String inMemoryApiToken = "";
    private static String inMemoryCalendarToken = "";
    private static String inMemoryPushToken = "";

    private SecureTokenStore() {
    }

    static String apiToken(Context context) {
        SharedPreferences preferences = encryptedPreferences(context);
        synchronized (FALLBACK_LOCK) {
            if (preferences != null) {
                inMemoryApiToken = preferences.getString(KEY_API_TOKEN, "");
            }
            return inMemoryApiToken;
        }
    }

    static void setApiToken(Context context, String token) {
        String safeToken = token == null ? "" : token;
        SharedPreferences preferences = encryptedPreferences(context);
        synchronized (FALLBACK_LOCK) {
            inMemoryApiToken = safeToken;
        }
        if (preferences != null) {
            preferences.edit().putString(KEY_API_TOKEN, safeToken).apply();
        }
        // A cached calendar token belongs to whichever account was signed in
        // when it was minted; drop it so signing out/in never reuses one
        // issued for a different account.
        setCalendarToken(context, "");
    }

    static String calendarToken(Context context) {
        SharedPreferences preferences = encryptedPreferences(context);
        synchronized (FALLBACK_LOCK) {
            if (preferences != null) {
                inMemoryCalendarToken = preferences.getString(KEY_CALENDAR_TOKEN, "");
            }
            return inMemoryCalendarToken;
        }
    }

    static void setCalendarToken(Context context, String token) {
        String safeToken = token == null ? "" : token;
        SharedPreferences preferences = encryptedPreferences(context);
        synchronized (FALLBACK_LOCK) {
            inMemoryCalendarToken = safeToken;
        }
        if (preferences != null) {
            preferences.edit().putString(KEY_CALENDAR_TOKEN, safeToken).apply();
        }
    }

    static String pushToken(Context context) {
        SharedPreferences preferences = encryptedPreferences(context);
        synchronized (FALLBACK_LOCK) {
            if (preferences != null) {
                inMemoryPushToken = preferences.getString(KEY_PUSH_TOKEN, "");
            }
            return inMemoryPushToken;
        }
    }

    static void setPushToken(Context context, String token) {
        String safeToken = token == null ? "" : token;
        SharedPreferences preferences = encryptedPreferences(context);
        synchronized (FALLBACK_LOCK) {
            inMemoryPushToken = safeToken;
        }
        if (preferences != null) {
            preferences.edit().putString(KEY_PUSH_TOKEN, safeToken).apply();
        }
    }

    private static SharedPreferences encryptedPreferences(Context context) {
        Context appContext = context.getApplicationContext();
        // Earlier builds could write full-access tokens to this unencrypted
        // fallback file. Never read it, and remove any residual credential on
        // upgrade before accessing the encrypted store.
        appContext.getSharedPreferences(LEGACY_FALLBACK_FILE_NAME, Context.MODE_PRIVATE)
                .edit()
                .clear()
                .apply();
        try {
            MasterKey masterKey = new MasterKey.Builder(appContext)
                    .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
                    .build();
            return EncryptedSharedPreferences.create(
                    appContext,
                    FILE_NAME,
                    masterKey,
                    EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                    EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM);
        } catch (GeneralSecurityException | IOException error) {
            // Keep the current process usable without ever persisting a token
            // unencrypted. A device with a broken Keystore will require login
            // again after the app process restarts.
            return null;
        }
    }
}
