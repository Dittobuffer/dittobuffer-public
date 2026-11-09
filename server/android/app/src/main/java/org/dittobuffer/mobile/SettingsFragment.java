package org.dittobuffer.mobile;

import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.os.Bundle;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.EditText;
import android.widget.TextView;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.fragment.app.Fragment;

/** Settings/about: endpoints, platform room, battery exemption, log copy. */
public final class SettingsFragment extends Fragment {
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
        domain.setText(PlatformClient.domain(requireContext()));
        status.setText(PlatformClient.isManaged(requireContext())
                ? "Connected to room \"" + PlatformClient.domain(requireContext())
                  + "\". The admin console is locked while that room owns the world."
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
        view.findViewById(R.id.btn_settings_battery).setOnClickListener(
                v -> MainActivity.requestBatteryExemption(requireContext()));
        view.findViewById(R.id.btn_settings_admin).setOnClickListener(v -> {
            if (getActivity() != null) {
                ((com.google.android.material.bottomnavigation.BottomNavigationView)
                        getActivity().findViewById(R.id.bottom_nav))
                        .setSelectedItemId(R.id.nav_admin);
            }
        });
        view.findViewById(R.id.btn_settings_copy).setOnClickListener(v -> {
            ClipboardManager clipboard = (ClipboardManager)
                    requireContext().getSystemService(Context.CLIPBOARD_SERVICE);
            clipboard.setPrimaryClip(ClipData.newPlainText("Dittobuffer log",
                    Logs.tail(requireContext(), 250)));
            Toast.makeText(requireContext(), "Log copied", Toast.LENGTH_SHORT).show();
        });
        return view;
    }
}
