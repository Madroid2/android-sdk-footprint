package dev.madroid.footprint;

import android.os.Bundle;

public final class MainActivity extends HostActivity {
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        renderEntry(() -> SdkEntry.open(this));
    }
}
