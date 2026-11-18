package org.dittobuffer.mobile;

import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.os.Bundle;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.AdapterView;
import android.widget.ArrayAdapter;
import android.widget.EditText;
import android.widget.Spinner;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.appcompat.app.AppCompatDelegate;
import androidx.fragment.app.Fragment;

import com.google.android.material.button.MaterialButton;

/** Settings/about: endpoints, platform room, battery exemption, log copy. */
public final class SettingsFragment extends Fragment {
    private MaterialButton battery;

    @Nullable
    @Override
    public View onCreateView(@NonNull LayoutInflater inflater, @Nullable ViewGroup container,
                             @Nullable Bundle savedInstanceState) {
        View view = inflater.inflate(R.layout.fragment_settings, container, false);
        TextView root = view.findViewById(R.id.settings_root);
        TextView endpoints = view.findViewById(R.id.settings_endpoints);
        TextView status = view.findViewById(R.id.platform_status);
        EditText domain = view.findViewById(R.id.platform_domain);
        root.setText(ServerRuntime.root(requireContext()).getAbsolutePath());
        endpoints.setText("Game server     https://127.0.0.1:8445\n"
                + "Admin console   http://127.0.0.1:8080\n"
                + "Map tile origin http://127.0.0.1:8081/map/tiles\n"
                + "Game client     " + MainActivity.GAME_PACKAGE);
        Switch autostart = view.findViewById(R.id.settings_autostart);
        boolean auto = requireContext().getSharedPreferences(MainActivity.APPEARANCE, Context.MODE_PRIVATE)
                .getBoolean("autostart", true);
        autostart.setChecked(auto);
        autostart.setOnCheckedChangeListener((button, checked) ->
                requireContext().getSharedPreferences(MainActivity.APPEARANCE, Context.MODE_PRIVATE)
                        .edit().putBoolean("autostart", checked).apply());

        Spinner theme = view.findViewById(R.id.settings_theme);
        String[] themeLabels = {"Follow system", "Light", "Dark"};
        int[] themeModes = {AppCompatDelegate.MODE_NIGHT_FOLLOW_SYSTEM,
                AppCompatDelegate.MODE_NIGHT_NO, AppCompatDelegate.MODE_NIGHT_YES};
        int saved = requireContext().getSharedPreferences(MainActivity.APPEARANCE, Context.MODE_PRIVATE)
                .getInt(MainActivity.THEME, AppCompatDelegate.MODE_NIGHT_FOLLOW_SYSTEM);
        int selected = saved == AppCompatDelegate.MODE_NIGHT_NO ? 1
                : saved == AppCompatDelegate.MODE_NIGHT_YES ? 2 : 0;
        theme.setAdapter(new ArrayAdapter<>(requireContext(),
                android.R.layout.simple_spinner_dropdown_item, themeLabels));
        theme.setSelection(selected, false);
        theme.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            @Override public void onItemSelected(AdapterView<?> parent, View item, int position, long id) {
                if (position == selected) return;
                requireContext().getSharedPreferences(MainActivity.APPEARANCE, Context.MODE_PRIVATE)
                        .edit().putInt(MainActivity.THEME, themeModes[position]).apply();
                AppCompatDelegate.setDefaultNightMode(themeModes[position]);
                if (getActivity() != null) getActivity().recreate();
            }
            @Override public void onNothingSelected(AdapterView<?> parent) { }
        });
        domain.setText(PlatformClient.domain(requireContext()));
        status.setText(PlatformClient.isManaged(requireContext())
                ? "Connected to room \"" + PlatformClient.domain(requireContext())
                  + "\". The local admin stays open on this device."
                : "Local server. Type the room name the host shared to join it.");
        view.findViewById(R.id.platform_connect).setOnClickListener(v -> {
            status.setText("Verifying the room signature…");
            PlatformClient.connect(requireContext(), domain.getText().toString(), message ->
                    // connect() finishes on its own thread; UI work must be posted.
                    view.post(() -> {
                        if (getActivity() == null) return;
                        status.setText(message);
                        Toast.makeText(requireContext(), message, Toast.LENGTH_LONG).show();
                        if (getActivity() instanceof MainActivity) {
                            ((MainActivity) getActivity()).refreshPlatformState();
                        }
                    }));
        });
        view.findViewById(R.id.platform_leave).setOnClickListener(v -> {
            if (!PlatformClient.isManaged(requireContext())) {
                status.setText("This app is not connected to a platform room.");
                return;
            }
            PlatformClient.leave(requireContext(), message ->
                    view.post(() -> {
                        if (getActivity() == null) return;
                        status.setText(message);
                        Toast.makeText(requireContext(), message, Toast.LENGTH_LONG).show();
                        if (getActivity() instanceof MainActivity) {
                            ((MainActivity) getActivity()).refreshPlatformState();
                        }
                    }));
        });
        battery = view.findViewById(R.id.btn_settings_battery);
        battery.setOnClickListener(v -> MainActivity.requestBatteryExemption(requireContext()));
        view.findViewById(R.id.btn_settings_admin).setOnClickListener(v -> {
            if (getActivity() != null) {
                ((com.google.android.material.bottomnavigation.BottomNavigationView)
                        getActivity().findViewById(R.id.bottom_nav))
                        .setSelectedItemId(R.id.nav_admin);
            }
        });
        view.findViewById(R.id.btn_settings_copy).setOnClickListener(v -> {
            copy("Dittobuffer log", Logs.tail(requireContext(), 250), "Log copied");
        });
        view.findViewById(R.id.btn_settings_copy_admin).setOnClickListener(v ->
                copy("Dittobuffer admin", ServerRuntime.adminUrl(), "Admin link copied"));
        view.findViewById(R.id.btn_settings_restart).setOnClickListener(v -> {
            ServerService.stop(requireContext());
            ServerRuntime.markStopped();
            Toast.makeText(requireContext(), "Restarting the local server…", Toast.LENGTH_SHORT).show();
            view.postDelayed(() -> ServerService.start(requireContext()), 1200);
        });
        view.findViewById(R.id.btn_settings_clear_logs).setOnClickListener(v -> {
            java.io.File log = new java.io.File(ServerRuntime.root(requireContext()), "logs/mobile.log");
            boolean cleared = !log.exists() || log.delete();
            Toast.makeText(requireContext(), cleared ? "Server log cleared" : "Could not clear the log",
                    Toast.LENGTH_SHORT).show();
        });
        return view;
    }

    @Override
    public void onResume() {
        super.onResume();
        if (battery != null) {
            boolean exempt = MainActivity.isBatteryExempt(requireContext());
            battery.setText(exempt ? "Background running allowed" : "Allow background running");
            battery.setEnabled(!exempt);
        }
    }

    private void copy(String label, String value, String message) {
        ClipboardManager clipboard = (ClipboardManager)
                requireContext().getSystemService(Context.CLIPBOARD_SERVICE);
        clipboard.setPrimaryClip(ClipData.newPlainText(label, value));
        Toast.makeText(requireContext(), message, Toast.LENGTH_SHORT).show();
    }
}
