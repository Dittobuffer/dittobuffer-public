// Copyright (C) Dittobuffer Contributors
// Copyright (C) chucny
// Copyright (C) gekkedavi
//
// This file is part of Dittobuffer. It is distributed under the GNU General
// Public License v3.0 (see LICENSE). The copyright notices in this file must
// be preserved in all copies, forks, and derivative works. Removing or
// altering these notices violates the license and copyright law.

package org.dittobuffer.mobile;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.os.IBinder;
import android.util.Log;

import androidx.annotation.Nullable;
import androidx.core.app.NotificationCompat;

/** Keeps the local Python server alive while the phone is in a pocket. */
public final class ServerService extends Service {
    public static final String ACTION_START = "org.dittobuffer.mobile.START";
    public static final String ACTION_STOP = "org.dittobuffer.mobile.STOP";
    private static final String CHANNEL = "server";
    private static final int NOTIFICATION_ID = 41;

    private volatile boolean working;
    private Thread worker;

    public static void start(Context context) {
        Intent intent = new Intent(context, ServerService.class).setAction(ACTION_START);
        if (Build.VERSION.SDK_INT >= 26) context.startForegroundService(intent);
        else context.startService(intent);
    }

    public static void stop(Context context) {
        // stopService() is safe when the app is in the background; starting a
        // service just to ask it to stop can throw on modern Android.
        context.stopService(new Intent(context, ServerService.class));
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        createChannel();
        startForeground(NOTIFICATION_ID, notification("Starting local server…"));

        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            shutdown();
            stopSelf();
            return START_NOT_STICKY;
        }

        if (!working && (worker == null || !worker.isAlive())) {
            working = true;
            ServerRuntime.markStarting();
            final Context context = getApplicationContext();
            worker = new Thread(() -> {
                // Unpacking 8 GiB must never compete with the UI thread, or
                // Android reports input-dispatch ANRs and kills the process
                // half-way through the payload.
                android.os.Process.setThreadPriority(android.os.Process.THREAD_PRIORITY_BACKGROUND);
                try {
                    String result = ServerRuntime.start(context);
                    Log.i("DittobufferMobile", "Python server: " + result);
                    ServerRuntime.setLastStatus(result);
                    notifyState(ServerRuntime.isRunning() ? "Server running on 8445" : "Server stopped");
                } catch (Throwable error) {
                    Log.e("DittobufferMobile", "Server start failed", error);
                    ServerRuntime.setLastError(Logs.format(error));
                    notifyState("Server failed: " + error.getClass().getSimpleName());
                } finally {
                    working = false;
                    if (!ServerRuntime.isRunning()) ServerRuntime.markStopped();
                }
            }, "dittobuffer-python");
            worker.start();
        }
        return START_STICKY;
    }

    private void shutdown() {
        try {
            ServerRuntime.stop();
        } catch (Throwable error) {
            Log.w("DittobufferMobile", "Stop failed", error);
        }
        ServerRuntime.markStopped();
    }

    private void notifyState(String text) {
        NotificationManager manager = getSystemService(NotificationManager.class);
        manager.notify(NOTIFICATION_ID, notification(text));
    }

    private Notification notification(String text) {
        PendingIntent open = PendingIntent.getActivity(this, 0,
                new Intent(this, MainActivity.class),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Intent adminIntent = new Intent(this, MainActivity.class)
                .putExtra(MainActivity.EXTRA_TAB, R.id.nav_admin)
                .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent admin = PendingIntent.getActivity(this, 1, adminIntent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Intent gameIntent = new Intent(this, MainActivity.class)
                .putExtra(MainActivity.EXTRA_LAUNCH_GAME, true)
                .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent game = PendingIntent.getActivity(this, 2, gameIntent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        return new NotificationCompat.Builder(this, CHANNEL)
                .setSmallIcon(android.R.drawable.stat_sys_download_done)
                .setContentTitle("Dittobuffer server")
                .setContentText(text)
                .setOngoing(true)
                .setSilent(true)
                .setContentIntent(open)
                .addAction(android.R.drawable.ic_menu_manage, "Admin", admin)
                .addAction(android.R.drawable.ic_media_play, "Game", game)
                .setPriority(NotificationCompat.PRIORITY_LOW)
                .build();
    }

    private void createChannel() {
        if (Build.VERSION.SDK_INT < 26) return;
        NotificationChannel channel = new NotificationChannel(CHANNEL, "Local server",
                NotificationManager.IMPORTANCE_LOW);
        channel.setDescription("Keeps the on-phone Dittobuffer server running");
        getSystemService(NotificationManager.class).createNotificationChannel(channel);
    }

    @Nullable
    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public void onDestroy() {
        shutdown();
        super.onDestroy();
    }
}
