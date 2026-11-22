package dev.madroid.footprint;

import android.app.Activity;
import android.graphics.Bitmap;
import android.widget.Toast;
import androidx.exifinterface.media.ExifInterface;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;

final class SdkEntry {
    static void open(Activity activity) {
        ByteArrayOutputStream jpeg = new ByteArrayOutputStream();
        Bitmap bitmap = Bitmap.createBitmap(1, 1, Bitmap.Config.ARGB_8888);
        bitmap.compress(Bitmap.CompressFormat.JPEG, 90, jpeg);
        bitmap.recycle();
        try {
            ExifInterface image = new ExifInterface(new ByteArrayInputStream(jpeg.toByteArray()));
            int width = image.getAttributeInt(ExifInterface.TAG_IMAGE_WIDTH, 0);
            Toast.makeText(activity, "Image width: " + width, Toast.LENGTH_SHORT).show();
        } catch (IOException error) {
            Toast.makeText(activity, "Image could not be read", Toast.LENGTH_SHORT).show();
        }
    }
}
