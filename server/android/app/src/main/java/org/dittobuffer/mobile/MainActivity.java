package org.dittobuffer.mobile;

import android.content.ActivityNotFoundException;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.PowerManager;
import android.provider.Settings;
import android.widget.Toast;

import androidx.activity.OnBackPressedCallback;
import androidx.annotation.NonNull;
import androidx.appcompat.app.AppCompatDelegate;
import androidx.appcompat.app.AppCompatActivity;
import androidx.fragment.app.Fragment;

import com.google.android.material.appbar.MaterialToolbar;
import com.google.android.material.bottomnavigation.BottomNavigationView;
import com.google.android.material.color.DynamicColors;

/** Single-activity shell: pinned Material 3 bottom bar, one section per tab. */
public final class MainActivity extends AppCompatActivity {
    public static final String GAME_PACKAGE = "org.librenlp.pokemongo.dev";
    public static final String GAME_LAUNCHER = "org.dittobuffer.launcher.FamilyActivity";
    public static final String EXTRA_TAB = "tab";
    public static final String EXTRA_LAUNCH_GAME = "launch_game";
    private static final String ACTION_ADMIN = "org.dittobuffer.mobile.OPEN_ADMIN";
    private static final String ACTION_PLAY = "org.dittobuffer.mobile.PLAY";
    private static final String ACTION_START = "org.dittobuffer.mobile.START_SERVER";
    static final String APPEARANCE = "appearance";
    static final String THEME = "theme";
    private static final String LAST_TAB = "last_tab";

    private final Handler handler = new Handler(Looper.getMainLooper());
    private MaterialToolbar toolbar;
    private static boolean installPrompted;

    @Override
    protected void onCreate(Bundle state) {
        applySavedTheme(this);
        DynamicColors.applyToActivitiesIfAvailable(getApplication());
        super.onCreate(state);
        setContentView(R.layout.activity_main);
        toolbar = findViewById(R.id.toolbar);
        setSupportActionBar(toolbar);

        BottomNavigationView nav = findViewById(R.id.bottom_nav);
        nav.setOnItemSelectedListener(item -> {
            show(item.getItemId());
            getSharedPreferences(APPEARANCE, MODE_PRIVATE).edit()
                    .putInt(LAST_TAB, item.getItemId()).apply();
            return true;
        });
        int initial = state != null
                ? state.getInt("tab", R.id.nav_home)
                : getSharedPreferences(APPEARANCE, MODE_PRIVATE)
                        .getInt(LAST_TAB, R.id.nav_home);
        if (nav.getMenu().findItem(initial) == null) initial = R.id.nav_home;
        long selected = nav.getSelectedItemId();
        nav.setSelectedItemId(initial);
        // setSelectedItemId() does not notify when the requested tab was already
        // selected (for example, Home on a cold start).
        if (selected == initial) show(initial);

        refreshPlatformState();
        boolean autoStart = getSharedPreferences(APPEARANCE, MODE_PRIVATE)
                .getBoolean("autostart", true);
        if (autoStart && !ServerRuntime.isRunning() && !ServerRuntime.isStarting()) {
            ServerService.start(this);
        }
        if (android.os.Build.VERSION.SDK_INT >= 33) {
            requestPermissions(new String[]{android.Manifest.permission.POST_NOTIFICATIONS}, 7);
        }
        // One app to install: the companion client is bundled here, so offer
        // the system installer automatically instead of hiding it behind a
        // button. The client's launcher entry is removed in the bundled APK,
        // so only this app appears in the launcher.
        if (!installPrompted && !ClientKit.installed(this)) {
            installPrompted = true;
            ClientKit.install(this, message -> { });
        }
        if (state == null) handleIntent(getIntent());
        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override public void handleOnBackPressed() {
                Fragment current = getSupportFragmentManager().findFragmentById(R.id.container);
                if (current instanceof WebFragment && ((WebFragment) current).canGoBack()) {
                    ((WebFragment) current).goBack();
                } else {
                    finish();
                }
            }
        });
    }

    /** Apply the operator's Light/Dark/System choice before any view inflates. */
    public static void applySavedTheme(Context context) {
        int mode = context.getSharedPreferences(APPEARANCE, MODE_PRIVATE)
                .getInt(THEME, AppCompatDelegate.MODE_NIGHT_FOLLOW_SYSTEM);
        AppCompatDelegate.setDefaultNightMode(mode);
    }

    @Override
    protected void onSaveInstanceState(@NonNull Bundle out) {
        super.onSaveInstanceState(out);
        BottomNavigationView nav = findViewById(R.id.bottom_nav);
        out.putInt("tab", nav.getSelectedItemId());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleIntent(intent);
    }

    @Override
    protected void onDestroy() {
        handler.removeCallbacksAndMessages(null);
        super.onDestroy();
    }

    /** Notification and launcher shortcuts open Admin, Play or Start directly. */
    private void handleIntent(Intent intent) {
        if (intent == null) return;
        String action = intent.getAction();
        int tab = intent.getIntExtra(EXTRA_TAB, 0);
        if (tab != 0) openTab(tab);
        if (ACTION_ADMIN.equals(action)) openTab(R.id.nav_admin);
        else if (ACTION_START.equals(action)) {
            ServerService.start(this);
            openTab(R.id.nav_server);
        } else if (ACTION_PLAY.equals(action)
                || intent.getBooleanExtra(EXTRA_LAUNCH_GAME, false)) {
            playGame();
        }
        intent.removeExtra(EXTRA_LAUNCH_GAME);
    }

    /** Start the server if needed, then launch the game as soon as it is up. */
    private void playGame() {
        if (ServerRuntime.isRunning()) {
            launchGame(this);
            return;
        }
        if (!ServerRuntime.isStarting()) {
            // Mark the attempt before the service's first callback so the Play
            // waiter cannot mistake a cold start for an already-stopped server.
            ServerRuntime.markStarting();
            ServerService.start(this);
        }
        Toast.makeText(this, "Starting the server…", Toast.LENGTH_SHORT).show();
        handler.postDelayed(() -> waitForGame(0), 600);
    }

    private void waitForGame(int attempt) {
        if (isFinishing() || isDestroyed()) return;
        if (ServerRuntime.isRunning()) {
            launchGame(this);
            return;
        }
        if (!ServerRuntime.lastError().isEmpty()) {
            openTab(R.id.nav_home);
            Toast.makeText(this, "The server stopped. Check the Server tab.",
                    Toast.LENGTH_LONG).show();
            return;
        }
        if (attempt >= 80 || !ServerRuntime.isStarting()) {
            openTab(R.id.nav_home);
            Toast.makeText(this, "The server is still starting. Check the Server tab.",
                    Toast.LENGTH_LONG).show();
            return;
        }
        handler.postDelayed(() -> waitForGame(attempt + 1), 750);
    }

    public void openTab(int id) {
        BottomNavigationView nav = findViewById(R.id.bottom_nav);
        if (nav != null && nav.getMenu().findItem(id) != null) nav.setSelectedItemId(id);
    }

    /** The local admin is always available; no room or login restriction. */
    public void refreshPlatformState() {
        BottomNavigationView nav = findViewById(R.id.bottom_nav);
        if (nav == null) return;
        android.view.MenuItem admin = nav.getMenu().findItem(R.id.nav_admin);
        if (admin != null) admin.setVisible(true);
    }

    private void show(int id) {
        String title = getString(R.string.app_name);
        Fragment fragment;
        if (id == R.id.nav_server) {
            title += "  ·  Server";
            fragment = new ServerFragment();
        } else if (id == R.id.nav_admin) {
            title += "  ·  Admin";
            if (!ServerRuntime.isRunning() && !ServerRuntime.isStarting()) {
                ServerService.start(this);
            }
            fragment = WebFragment.newInstance(ServerRuntime.adminUrl(), "Admin console");
        } else if (id == R.id.nav_map) {
            title += "  ·  Map";
            fragment = new MapFragment();
        } else if (id == R.id.nav_settings) {
            title += "  ·  Settings";
            fragment = new SettingsFragment();
        } else {
            fragment = new HomeFragment();
        }
        toolbar.setTitle(title);
        getSupportFragmentManager().beginTransaction()
                .replace(R.id.container, fragment)
                .commitAllowingStateLoss();
    }

    /** Launch only the bundled client. The vanilla package is never referenced. */
    public static void launchGame(Context context) {
        Intent intent = new Intent();
        intent.setClassName(GAME_PACKAGE, GAME_LAUNCHER);
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        try {
            context.startActivity(intent);
        } catch (ActivityNotFoundException | SecurityException error) {
            Toast.makeText(context,
                    "Game client not installed (" + GAME_PACKAGE + "). Install the bundled APK first.",
                    Toast.LENGTH_LONG).show();
        }
    }

    public static boolean isBatteryExempt(Context context) {
        PowerManager manager = (PowerManager) context.getSystemService(Context.POWER_SERVICE);
        return manager != null && manager.isIgnoringBatteryOptimizations(context.getPackageName());
    }

    public static void requestBatteryExemption(Context context) {
        if (isBatteryExempt(context)) {
            Toast.makeText(context, "Background running is already allowed.",
                    Toast.LENGTH_SHORT).show();
            return;
        }
        try {
            Intent intent = new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                    Uri.parse("package:" + context.getPackageName()));
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            context.startActivity(intent);
        } catch (Exception error) {
            Toast.makeText(context, "Open battery settings manually and allow background use.",
                    Toast.LENGTH_LONG).show();
        }
    }
}
