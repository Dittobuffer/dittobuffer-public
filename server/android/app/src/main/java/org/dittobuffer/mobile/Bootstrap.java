package org.dittobuffer.mobile;

import android.content.Context;

import java.io.BufferedInputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.FileNotFoundException;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;
import java.util.zip.CRC32;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;
import java.util.zip.ZipInputStream;

/**
 * Unpacks the bundled server source and game payload on first run.
 *
 * Both trees are keyed to the installed APK build, so an update always ships
 * new code and new assets. Identical payload files are left untouched, which
 * keeps upgrades fast and preserves a tree pushed over adb.
 */
public final class Bootstrap {
    private static final String SERVER_ASSET = "server-bootstrap.zip";
    private static final String SERVER_MARKER = "bootstrap.version";
    private static final String PAYLOAD_MARKER = "payload.version";
    // Each part stays below 2 GB; every part is optional so a build with a
    // smaller payload still works.
    private static final String[] PAYLOAD_PARTS = {
            "payload-1.zip", "payload-2.zip", "payload-3.zip", "payload-4.zip",
            "payload-5.zip"};

    private Bootstrap() {}

    public static synchronized void ensure(Context context, File root) throws IOException {
        // The marker is the installed APK build, never a constant: a constant
        // keeps matching after an update, so new payload files (a refreshed
        // runtime registry, a recovered bundle) would never be extracted.
        final String build = installedBuild(context);
        // Payload parts also carry the config tree, including the TLS chain.
        // Unpack them first so the authoritative bootstrap files win; otherwise
        // a stale payload certificate can overwrite the newly installed CA.
        if (!build.equals(readMarker(root, PAYLOAD_MARKER))) {
            for (String part : PAYLOAD_PARTS) {
                // Refresh entries whose bytes changed, leave identical ones alone.
                extractPart(context, part, root, false);
            }
            writeMarker(root, PAYLOAD_MARKER, build);
        }

        if (!build.equals(readMarker(root, SERVER_MARKER))) {
            extractPart(context, SERVER_ASSET, root, true);
            writeMarker(root, SERVER_MARKER, build);
        }
    }

    /** Version code plus install time: changes on every APK update. */
    private static String installedBuild(Context context) {
        try {
            android.content.pm.PackageInfo info = context.getPackageManager()
                    .getPackageInfo(context.getPackageName(), 0);
            return info.getLongVersionCode() + "." + info.lastUpdateTime;
        } catch (Exception error) {
            return "unknown";
        }
    }

    /**
     * Opens a payload part from the base APK or from an installed split APK.
     * Split assets are normally visible through AssetManager, but reading the
     * split zip directly is the guaranteed fallback.
     */
    private static void extractPart(Context context, String asset, File root, boolean overwrite)
            throws IOException {
        if (!root.isDirectory() && !root.mkdirs()) {
            throw new IOException("cannot create " + root);
        }
        try (InputStream raw = context.getAssets().open(asset)) {
            extract(raw, root, overwrite);
            return;
        } catch (FileNotFoundException missing) {
            // Not in the base assets; try the split APKs below.
        }
        for (File apk : apkFiles(context)) {
            try (ZipFile zip = new ZipFile(apk)) {
                ZipEntry entry = zip.getEntry("assets/" + asset);
                if (entry == null) continue;
                try (InputStream raw = zip.getInputStream(entry)) {
                    extract(raw, root, overwrite);
                }
                return;
            } catch (IOException ignored) {
                // Not this APK; keep looking.
            }
        }
    }

    private static List<File> apkFiles(Context context) {
        List<File> files = new ArrayList<>();
        files.add(new File(context.getApplicationInfo().sourceDir));
        String[] splits = context.getApplicationInfo().splitSourceDirs;
        if (splits != null) {
            for (String split : splits) files.add(new File(split));
        }
        return files;
    }

    private static void extract(InputStream raw, File root, boolean overwrite)
            throws IOException {
        final String rootPath = root.getCanonicalPath();
        try (InputStream buffered = new BufferedInputStream(raw, 1 << 20);
             ZipInputStream zip = new ZipInputStream(buffered)) {
            byte[] buffer = new byte[1 << 20];
            ZipEntry entry;
            while ((entry = zip.getNextEntry()) != null) {
                File target = new File(root, entry.getName());
                String path = target.getCanonicalPath();
                if (!path.equals(rootPath) && !path.startsWith(rootPath + File.separator)) {
                    throw new IOException("unsafe zip entry " + entry.getName());
                }
                if (entry.isDirectory()) {
                    if (!target.mkdirs() && !target.isDirectory()) throw new IOException("mkdir " + target);
                    continue;
                }
                if (!overwrite && target.isFile() && sameBytes(entry, target)) continue;
                File parent = target.getParentFile();
                if (parent != null && !parent.mkdirs() && !parent.isDirectory()) {
                    throw new IOException("mkdir " + parent);
                }
                try (OutputStream out = new FileOutputStream(target)) {
                    int count;
                    while ((count = zip.read(buffer)) != -1) out.write(buffer, 0, count);
                }
            }
        }
    }

    /** True when the packed entry and the on-device file are byte-identical. */
    private static boolean sameBytes(ZipEntry entry, File target) {
        if (entry.getSize() >= 0 && target.length() != entry.getSize()) return false;
        final long expected = entry.getCrc();
        if (expected < 0) return true;  // size matched and no checksum to compare
        CRC32 crc = new CRC32();
        try (InputStream in = new FileInputStream(target)) {
            byte[] buffer = new byte[1 << 20];
            int count;
            while ((count = in.read(buffer)) != -1) crc.update(buffer, 0, count);
        } catch (IOException error) {
            return false;
        }
        return crc.getValue() == expected;
    }

    private static String readMarker(File root, String name) {
        try {
            return new String(Files.readAllBytes(new File(root, name).toPath()), StandardCharsets.UTF_8).trim();
        } catch (IOException missing) {
            return "";
        }
    }

    private static void writeMarker(File root, String name, String value) throws IOException {
        try (OutputStream out = new FileOutputStream(new File(root, name))) {
            out.write(value.getBytes(StandardCharsets.UTF_8));
        }
    }
}
