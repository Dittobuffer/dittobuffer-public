package org.dittobuffer.mobile;

import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.provider.Settings;
import android.widget.Toast;

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

    private MaterialToolbar toolbar;
    private static boolean installPrompted;

    @Override
    protected void onCreate(Bundle state) {
        AppCompatDelegate.setDefaultNightMode(AppCompatDelegate.MODE_NIGHT_NO);
        DynamicColors.applyToActivitiesIfAvailable(getApplication());
        super.onCreate(state);
        setContentView(R.layout.activity_main);
        toolbar = findViewById(R.id.toolbar);
        setSupportActionBar(toolbar);

        BottomNavigationView nav = findViewById(R.id.bottom_nav);
        nav.setOnItemSelectedListener(item -> {
            show(item.getItemId());
            return true;
        });
        if (state == null) {
            nav.setSelectedItemId(R.id.nav_home);
        } else {
            int id = state.getInt("tab", R.id.nav_home);
            nav.setSelectedItemId(id);
            show(id);
        }
        refreshPlatformState();
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
    }

    @Override
    protected void onSaveInstanceState(@NonNull Bundle out) {
        super.onSaveInstanceState(out);
        BottomNavigationView nav = findViewById(R.id.bottom_nav);
        out.putInt("tab", nav.getSelectedItemId());
    }

    /** A platform room owns the world, so the local admin console is hidden. */
    public void refreshPlatformState() {
        BottomNavigationView nav = findViewById(R.id.bottom_nav);
        if (nav == null) return;
        boolean managed = PlatformClient.isManaged(this);
        android.view.MenuItem admin = nav.getMenu().findItem(R.id.nav_admin);
        if (admin != null) admin.setVisible(!managed);
        if (managed && nav.getSelectedItemId() == R.id.nav_admin) {
            nav.setSelectedItemId(R.id.nav_home);
        }
    }

    private void show(int id) {
        String title = getString(R.string.app_name);
        if (id == R.id.nav_admin && PlatformClient.isManaged(this)) {
            id = R.id.nav_home;
        }
        Fragment fragment;
        if (id == R.id.nav_server) {
            title += "  ·  Server";
            fragment = new ServerFragment();
        } else if (id == R.id.nav_admin) {
            title += "  ·  Admin";
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
    public static void launchGame(android.content.Context context) {
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

    public static void requestBatteryExemption(android.content.Context context) {
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
