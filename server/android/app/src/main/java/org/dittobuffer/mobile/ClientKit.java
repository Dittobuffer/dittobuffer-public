package org.dittobuffer.mobile;

import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;

import androidx.core.content.FileProvider;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.function.Consumer;

/** Installs the patched game client that is bundled inside this APK. */
public final class ClientKit {
    private static final String ASSET = "client/dittobuffer-family.apk";
    private static final Handler MAIN = new Handler(Looper.getMainLooper());

    private ClientKit() {}

    public static boolean installed(Context context) {
        try {
            context.getPackageManager().getPackageInfo(MainActivity.GAME_PACKAGE, 0);
            return true;
        } catch (Exception missing) {
            return false;
        }
    }

    /** Copies the bundled APK to cache off the main thread, then opens the installer. */
    public static void install(Context context, Consumer<String> status) {
        final Context app = context.getApplicationContext();
        new Thread(() -> {
            try {
                File apk = copy(app);
                MAIN.post(() -> {
                    status.accept("Opening the system installer…");
                    Uri uri = FileProvider.getUriForFile(app,
                            app.getPackageName() + ".files", apk);
                    Intent intent = new Intent(Intent.ACTION_VIEW)
                            .setDataAndType(uri, "application/vnd.android.package-archive")
                            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION
                                    | Intent.FLAG_ACTIVITY_NEW_TASK);
                    app.startActivity(intent);
                });
            } catch (Throwable error) {
                MAIN.post(() -> status.accept("Client install failed: " + error));
            }
        }, "client-install").start();
    }

    private static File copy(Context context) throws IOException {
        File dir = new File(context.getCacheDir(), "client");
        if (!dir.isDirectory() && !dir.mkdirs()) throw new IOException("cannot create " + dir);
        File apk = new File(dir, "dittobuffer-family.apk");
        File marker = new File(dir, ".ready");
        if (apk.isFile() && marker.isFile() && apk.length() > 0) return apk;
        try (InputStream in = context.getAssets().open(ASSET);
             OutputStream out = new FileOutputStream(apk)) {
            byte[] buffer = new byte[1 << 20];
            int count;
            while ((count = in.read(buffer)) != -1) out.write(buffer, 0, count);
        }
        //noinspection ResultOfMethodCallIgnored
        marker.createNewFile();
        return apk;
    }
}
