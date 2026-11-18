package org.dittobuffer.mobile;

import android.content.Context;
import android.util.Log;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.io.File;

/** Chaquopy bridge. One CPython process, two HTTP servers (game + admin). */
public final class ServerRuntime {
    public static final int GAME_PORT = 8445;
    public static final int ADMIN_PORT = 8080;
    public static final int MAP_PORT = 8081;

    private static volatile boolean running;
    private static volatile long startingUntil;
    private static volatile long startedAt;
    private static volatile String lastError = "";
    private static volatile String lastStatus = "";
    // State changes are short; the class monitor must never be held across the
    // multi-GiB payload unpack, or UI calls such as adminUrl() block long
    // enough for Android to declare an input-dispatch ANR and kill the process
    // half-way through extraction.
    private static final Object STATE = new Object();
    private static final Object PYTHON = new Object();

    private ServerRuntime() {}

    public static File root(Context context) {
        File base = context.getExternalFilesDir(null);
        if (base == null) base = context.getFilesDir();
        return new File(base, "server");
    }

    /** Create the on-device server root before Python needs it. */
    public static File ensureRoot(Context context) throws Exception {
        File root = root(context);
        if (!root.isDirectory() && !root.mkdirs()) {
            throw new IllegalStateException("Cannot create " + root);
        }
        return root;
    }

    /** Restart the in-process servers after a platform room change. */
    public static void restart(Context context) {
        boolean wasRunning = running;
        stop();
        if (!wasRunning) return;
        try {
            start(context);
        } catch (Exception error) {
            markStopped();
            lastError = String.valueOf(error);
            Log.w("DittobufferMobile", "restart after platform change", error);
        }
    }

    public static String start(Context context) throws Exception {
        synchronized (STATE) {
            if (running) return "already running";
        }
        markStarting();
        try {
            File root = ensureRoot(context);
            // Packed on-disk payload; deliberately outside every lock.
            Bootstrap.ensure(context, root);
            String value;
            synchronized (PYTHON) {
                if (!Python.isStarted()) {
                    Python.start(new AndroidPlatform(context.getApplicationContext()));
                }
                PyObject module = Python.getInstance().getModule("mobile_server");
                PyObject result = module.callAttr("start", root.getAbsolutePath());
                value = result == null ? "ok" : result.toString();
            }
            synchronized (STATE) {
                running = true;
                startingUntil = 0;
                startedAt = System.currentTimeMillis();
                lastError = "";
                lastStatus = value;
            }
            return value;
        } catch (Exception error) {
            // A failed attempt must release the starting window so Start stays
            // usable instead of being greyed out for ten minutes.
            synchronized (STATE) {
                startingUntil = 0;
                lastError = String.valueOf(error);
            }
            throw error;
        }
    }

    public static void stop() {
        synchronized (PYTHON) {
            if (Python.isStarted()) {
                try {
                    PyObject module = Python.getInstance().getModule("mobile_server");
                    module.callAttr("stop");
                } catch (Throwable error) {
                    Log.w("DittobufferMobile", "python stop", error);
                }
            }
        }
        synchronized (STATE) {
            running = false;
            startingUntil = 0;
            startedAt = 0;
        }
    }

    /** Plain loopback URL for the embedded local admin, with no key or token. */
    public static String adminUrl() {
        String base = "http://127.0.0.1:" + ADMIN_PORT + "/";
        // running only becomes true after PYTHON has been released, so this
        // never waits on the payload unpack.
        if (!running) return base;
        synchronized (PYTHON) {
            try {
                return Python.getInstance().getModule("mobile_server").callAttr("admin_url").toString();
            } catch (Exception error) {
                return base;
            }
        }
    }

    public static boolean isRunning() {
        return running;
    }

    /** True while the Python servers are booting (first run can take minutes). */
    public static void markStarting() {
        startingUntil = System.currentTimeMillis() + 10 * 60 * 1000L;
        // A new attempt should not inherit the previous failure banner.
        lastError = "";
    }

    public static boolean isStarting() {
        return !running && System.currentTimeMillis() < startingUntil;
    }

    public static void markStopped() {
        running = false;
        startingUntil = 0;
        startedAt = 0;
    }

    /** Short human-readable uptime, or an empty string while stopped. */
    public static String uptime() {
        if (!running || startedAt == 0) return "";
        long seconds = Math.max(0, (System.currentTimeMillis() - startedAt) / 1000);
        if (seconds < 60) return seconds + "s";
        long minutes = seconds / 60;
        if (minutes < 60) return minutes + "m";
        return (minutes / 60) + "h " + (minutes % 60) + "m";
    }

    public static String lastError() {
        return lastError;
    }

    public static void setLastError(String value) {
        lastError = value == null ? "" : value;
    }

    public static String lastStatus() {
        return lastStatus;
    }

    public static void setLastStatus(String value) {
        lastStatus = value == null ? "" : value;
    }
}
