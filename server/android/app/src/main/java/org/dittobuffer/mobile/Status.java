package org.dittobuffer.mobile;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.function.Consumer;

/** Background probes for ports, health JSON and the asset payload. */
public final class Status {
    private static final Handler MAIN = new Handler(Looper.getMainLooper());

    private static final ExecutorService POOL = Executors.newSingleThreadExecutor(r -> {
        Thread t = new Thread(r, "dittobuffer-probe");
        t.setDaemon(true);
        return t;
    });

    public boolean game;
    public boolean admin;
    public boolean map;
    public String health = "";
    public String payload = "";

    public static void probe(Context context, Consumer<Status> callback) {
        final Context app = context.getApplicationContext();
        POOL.execute(() -> {
            Status status = new Status();
            status.game = port(ServerRuntime.GAME_PORT);
            status.admin = port(ServerRuntime.ADMIN_PORT);
            status.map = port(ServerRuntime.MAP_PORT);
            status.health = http("http://127.0.0.1:" + ServerRuntime.GAME_PORT + "/health");
            status.payload = payload(context);
            // Every caller touches views, so always deliver on the main thread.
            MAIN.post(() -> callback.accept(status));
        });
    }

    public static String fetch(String url, int timeoutMs) {
        try {
            HttpURLConnection connection = (HttpURLConnection) new URL(url).openConnection();
            connection.setConnectTimeout(timeoutMs);
            connection.setReadTimeout(timeoutMs);
            connection.setRequestMethod("GET");
            int code = connection.getResponseCode();
            InputStream stream = code >= 400 ? connection.getErrorStream() : connection.getInputStream();
            String body = stream == null ? "" : read(stream);
            connection.disconnect();
            return "HTTP " + code + (body.isEmpty() ? "" : "\n" + body);
        } catch (Exception error) {
            return error.getClass().getSimpleName() + ": " + error.getMessage();
        }
    }

    private static String http(String url) {
        return fetch(url, 1500);
    }

    private static boolean port(int port) {
        try (Socket socket = new Socket()) {
            socket.connect(new InetSocketAddress("127.0.0.1", port), 350);
            return true;
        } catch (Exception error) {
            return false;
        }
    }

    private static String read(InputStream stream) throws Exception {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buffer = new byte[8192];
        int count;
        while ((count = stream.read(buffer)) != -1) {
            out.write(buffer, 0, count);
            if (out.size() > 65536) break;
        }
        return out.toString("UTF-8");
    }

    public static String payload(Context context) {
        File root = ServerRuntime.root(context);
        String version = read(new File(root, "payload.version"));
        boolean bundles = new File(root, "assets/pokemon/bundles").isDirectory();
        boolean recovered = new File(root, "builds/m109-official-pokemon-cache").isDirectory();
        boolean pokemonManifest = new File(root, "builds/m113-reviewed-assets.json").isFile();
        boolean avatarManifest = new File(root, "builds/m10-avatar-assets.json").isFile();
        boolean map = new File(root, "data/maps/osm.xml").isFile();
        boolean cache = new File(root, "assets/client-cache/UnityCache").isDirectory();
        // Bootstrap writes the installed build ("3.<install-time>") into the
        // marker, so a literal "3" never matched and every healthy install was
        // reported as incomplete.
        if (version.startsWith("3") && bundles && recovered && pokemonManifest && avatarManifest) {
            return "Payload ready (v3) — all bundles, recovered models, map extract and client cache"
                    + (map ? "" : " (map extract missing)")
                    + (cache ? "" : " (client cache missing)");
        }
        return "Payload incomplete (v" + version + ") — bundles=" + bundles
                + ", recovered=" + recovered
                + ", pokemon manifest=" + pokemonManifest
                + ", avatar manifest=" + avatarManifest
                + ", map=" + map
                + ", client cache=" + cache
                + ". Reinstall the APK.";
    }

    private static String read(File file) {
        try {
            return new String(java.nio.file.Files.readAllBytes(file.toPath()), StandardCharsets.UTF_8).trim();
        } catch (Exception missing) {
            return "";
        }
    }

    public static String oneLine(String body) {
        return body.replace('\n', ' ').replace('\r', ' ').trim();
    }

    public static byte[] bytes(String text) {
        return text.getBytes(StandardCharsets.UTF_8);
    }
}
