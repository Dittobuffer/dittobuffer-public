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
    private static volatile String lastError = "";
    private static volatile String lastStatus = "";

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
    public static synchronized void restart(Context context) {
        boolean wasRunning = running;
        stop();
        if (!wasRunning) return;
        try {
            start(context);
        } catch (Exception error) {
            lastError = String.valueOf(error);
            Log.w("DittobufferMobile", "restart after platform change", error);
        }
    }

    public static synchronized String start(Context context) throws Exception {
        if (running) return "already running";
        File root = ensureRoot(context);
        Bootstrap.ensure(context, root);
        if (!Python.isStarted()) {
            Python.start(new AndroidPlatform(context.getApplicationContext()));
        }
        PyObject module = Python.getInstance().getModule("mobile_server");
        PyObject result = module.callAttr("start", root.getAbsolutePath());
        String value = result == null ? "ok" : result.toString();
        running = true;
        lastError = "";
        lastStatus = value;
        return value;
    }

    public static synchronized void stop() {
        if (!Python.isStarted()) return;
        try {
            PyObject module = Python.getInstance().getModule("mobile_server");
            module.callAttr("stop");
        } catch (Throwable error) {
            Log.w("DittobufferMobile", "python stop", error);
        }
        running = false;
    }

    /** Local capability is passed to the embedded browser, never to status/logs. */
    public static synchronized String adminUrl() {
        String base = "http://127.0.0.1:" + ADMIN_PORT + "/";
        if (!running || !Python.isStarted()) return base;
        try {
            return Python.getInstance().getModule("mobile_server").callAttr("admin_url").toString();
        } catch (Exception error) {
            return base;
        }
    }

    public static boolean isRunning() {
        return running;
    }

    public static void markStopped() {
        running = false;
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
