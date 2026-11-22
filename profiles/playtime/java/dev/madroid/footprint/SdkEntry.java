package dev.madroid.footprint;

import android.app.Activity;
import android.widget.Toast;
import io.adjoe.sdk.Playtime;
import io.adjoe.sdk.PlaytimeException;
import io.adjoe.sdk.PlaytimeOptions;
import io.adjoe.sdk.PlaytimeOptionsListener;

final class SdkEntry {
    static void open(Activity activity) {
        // Measurement fixture. No identity or credentials are bundled.
        PlaytimeOptions options = new PlaytimeOptions();
        Playtime.setPlaytimeOptions(options, new PlaytimeOptionsListener() {
            @Override public void onSuccess() {
                activity.runOnUiThread(() -> {
                    if (activity.isFinishing() || activity.isDestroyed()) return;
                    try {
                        Playtime.showCatalog(activity, options);
                    } catch (PlaytimeException error) {
                        showError(activity);
                    }
                });
            }

            @Override public void onError(String message) {
                showError(activity);
            }
        });
    }

    private static void showError(Activity activity) {
        activity.runOnUiThread(() -> Toast.makeText(
            activity, "Catalog unavailable", Toast.LENGTH_SHORT).show());
    }
}
