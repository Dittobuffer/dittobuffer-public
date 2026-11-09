package org.dittobuffer.mobile;

import android.content.Context;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;

import java.util.function.Consumer;

/**
 * Joins a Dittobuffer room by name.
 *
 * A room is only a name on HiveMQ: the bundled Python runtime subscribes to
 * that name, receives the host's retained signed document, verifies it and
 * pins the host key. There is no domain to own, no platform URL to configure
 * and no key to paste.
 */
public final class PlatformClient {

    private PlatformClient() {}

    public static boolean isManaged(Context context) {
        return context.getSharedPreferences("platform", Context.MODE_PRIVATE)
                .getBoolean("managed", false);
    }

    public static String domain(Context context) {
        return context.getSharedPreferences("platform", Context.MODE_PRIVATE)
                .getString("domain", "");
    }

    public static void connect(Context context, String roomName, Consumer<String> done) {
        final Context app = context.getApplicationContext();
        final String room = roomName == null ? "" : roomName.trim();
        if (room.length() < 2) {
            done.accept("Type the room name the host shared with you.");
            return;
        }
        new Thread(() -> {
            try {
                if (!Python.isStarted()) {
                    Python.start(new com.chaquo.python.android.AndroidPlatform(app));
                }
                ServerRuntime.ensureRoot(app);
                PyObject module = Python.getInstance().getModule("mobile_server");
                String result = module.callAttr("connect_room",
                        ServerRuntime.root(app).getAbsolutePath(), room).toString();
                String canonical = module.callAttr("room_name", room).toString();
                app.getSharedPreferences("platform", Context.MODE_PRIVATE).edit()
                        .putBoolean("managed", true)
                        .putString("domain", canonical)
                        .apply();
                ServerRuntime.restart(app);
                done.accept(result);
            } catch (Throwable error) {
                done.accept("Could not join that room: " + error.getMessage());
            }
        }, "platform-connect").start();
    }

    public static void leave(Context context, Consumer<String> done) {
        final Context app = context.getApplicationContext();
        new Thread(() -> {
            try {
                if (!Python.isStarted()) {
                    Python.start(new com.chaquo.python.android.AndroidPlatform(app));
                }
                ServerRuntime.ensureRoot(app);
                PyObject module = Python.getInstance().getModule("mobile_server");
                module.callAttr("leave_platform", ServerRuntime.root(app).getAbsolutePath());
                app.getSharedPreferences("platform", Context.MODE_PRIVATE).edit()
                        .putBoolean("managed", false)
                        .putString("domain", "")
                        .apply();
                ServerRuntime.restart(app);
                done.accept("Left the room. The local admin is back.");
            } catch (Throwable error) {
                done.accept("Could not leave the room: " + error.getMessage());
            }
        }, "platform-leave").start();
    }
}
