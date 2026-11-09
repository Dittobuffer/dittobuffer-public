package org.dittobuffer.mobile;

import android.content.Context;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.InputStreamReader;
import java.io.StringWriter;
import java.io.PrintWriter;
import java.util.ArrayList;
import java.util.List;

/** Small helpers for the in-app log view. */
public final class Logs {
    private Logs() {}

    public static File file(Context context, String name) {
        return new File(ServerRuntime.root(context), "logs/" + name);
    }

    public static String tail(Context context, int lines) {
        List<String> rows = new ArrayList<>();
        for (String name : new String[]{"mobile.log", "server.log", "admin.log"}) {
            File file = file(context, name);
            if (!file.isFile()) continue;
            rows.add("── " + name + " ──");
            rows.addAll(tailFile(file, lines));
        }
        if (rows.isEmpty()) return "No log yet. Start the server.";
        return String.join("\n", rows);
    }

    private static List<String> tailFile(File file, int lines) {
        List<String> all = new ArrayList<>();
        try (BufferedReader reader = new BufferedReader(
                new InputStreamReader(new FileInputStream(file), "UTF-8"))) {
            String line;
            while ((line = reader.readLine()) != null) {
                all.add(line);
                if (all.size() > lines * 3) all.remove(0);
            }
        } catch (Exception ignored) {
            return all;
        }
        return all.size() > lines ? all.subList(all.size() - lines, all.size()) : all;
    }

    public static String format(Throwable error) {
        StringWriter writer = new StringWriter();
        error.printStackTrace(new PrintWriter(writer));
        return writer.toString();
    }
}
