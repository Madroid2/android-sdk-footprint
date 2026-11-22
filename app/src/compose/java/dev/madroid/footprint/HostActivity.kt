package dev.madroid.footprint

import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.material3.Button
import androidx.compose.material3.Text

open class HostActivity : ComponentActivity() {
    protected fun renderEntry(action: Runnable) {
        setContent { Button(onClick = action::run) { Text("Run SDK example") } }
    }
}
