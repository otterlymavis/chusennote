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
    private static final String KEY_API_TOKEN = "api_token";
    private static final String KEY_CALENDAR_TOKEN = "calendar_token";

    private SecureTokenStore() {
    }

    static String apiToken(Context context) {
        return preferences(context).getString(KEY_API_TOKEN, "");
    }

    static void setApiToken(Context context, String token) {
        preferences(context).edit().putString(KEY_API_TOKEN, token).apply();
        // A cached calendar token belongs to whichever account was signed in
        // when it was minted; drop it so signing out/in never reuses one
        // issued for a different account.
        setCalendarToken(context, "");
    }

    static String calendarToken(Context context) {
        return preferences(context).getString(KEY_CALENDAR_TOKEN, "");
    }

    static void setCalendarToken(Context context, String token) {
        preferences(context).edit().putString(KEY_CALENDAR_TOKEN, token).apply();
    }

    private static SharedPreferences preferences(Context context) {
        Context appContext = context.getApplicationContext();
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
            // Keystore unavailable (rare — a broken emulator image, etc.):
            // fall back to a plain file rather than crashing the app. The
            // token just isn't encrypted at rest in that edge case.
            return appContext.getSharedPreferences(FILE_NAME + "_fallback", Context.MODE_PRIVATE);
        }
    }
}
