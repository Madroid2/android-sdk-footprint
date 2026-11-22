package dev.madroid.footprint;

import android.app.Activity;
import android.widget.Toast;

final class SdkEntry {
    static void open(Activity activity) {
        Toast.makeText(activity, "Control build", Toast.LENGTH_SHORT).show();
    }
}
