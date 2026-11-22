package dev.madroid.footprint;

import android.app.Activity;

public class HostActivity extends Activity {
    protected void renderEntry(Runnable action) {
        android.widget.Button catalog = new android.widget.Button(this);
        catalog.setText("Run SDK example");
        catalog.setOnClickListener(v -> action.run());
        setContentView(catalog);
    }
}
